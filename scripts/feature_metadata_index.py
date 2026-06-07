#!/usr/bin/env python3
"""Load canonical feature metadata sidecars into the rebuildable serving index."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import release_feature_model


DEFAULT_COLLECTION_ROOT = "feature_metadata"
DEFAULT_BATCH_SIZE = 500


class FeatureMetadataIndexError(RuntimeError):
    """Raised when sidecar rows cannot be loaded into the serving index."""


class FeatureMetadataWriter(Protocol):
    def write_batch(
        self,
        *,
        asset_slug: str,
        release: str,
        load_id: str,
        documents: Sequence[Mapping[str, Any]],
    ) -> int:
        ...


@dataclass(frozen=True)
class LoadResult:
    asset_slug: str
    release: str
    sidecar_path: str
    schema_path: str
    manifest_path: str
    document_count: int
    batch_count: int
    dry_run: bool
    deleted_document_count: int = 0


class FirestoreFeatureMetadataWriter:
    def __init__(self, *, collection_root: str = DEFAULT_COLLECTION_ROOT, client: Any = None) -> None:
        self.collection_root = collection_root
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from google.cloud import firestore

            project = os.environ.get("GOOGLE_CLOUD_PROJECT")
            self._client = firestore.Client(project=project) if project else firestore.Client()
        return self._client

    def _features_collection(self, *, asset_slug: str, release: str, load_id: str):
        return (
            self.client.collection(self.collection_root)
            .document(asset_slug)
            .collection("releases")
            .document(release)
            .collection("loads")
            .document(load_id)
            .collection("features")
        )

    def write_batch(
        self,
        *,
        asset_slug: str,
        release: str,
        load_id: str,
        documents: Sequence[Mapping[str, Any]],
    ) -> int:
        batch = self.client.batch()
        for document in documents:
            feature_id = str(document["feature_id"])
            properties = document.get("properties") or {}
            ref = self._features_collection(asset_slug=asset_slug, release=release, load_id=load_id).document(feature_id)
            batch.set(
                ref,
                {
                    "asset_slug": asset_slug,
                    "release": release,
                    "index_load_id": load_id,
                    "feature_id": feature_id,
                    "geometry_hash": document.get("geometry_hash"),
                    "properties_hash": document.get("properties_hash"),
                    "properties": properties,
                    "provenance": document.get("provenance") or {},
                },
            )
        batch.commit()
        return len(documents)


def read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise FeatureMetadataIndexError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise FeatureMetadataIndexError(f"{label} must be a JSON object: {path}")
    return payload


def validate_artifact_generation(
    artifacts_by_role: Mapping[str, Mapping[str, Any]],
    *,
    role: str,
    uri: str | None,
    generation: int | None,
) -> None:
    artifact = artifacts_by_role.get(role)
    if artifact is None:
        raise FeatureMetadataIndexError(f"manifest is missing {role!r} artifact")
    if uri is not None:
        path = artifact.get("path")
        if path != uri:
            raise FeatureMetadataIndexError(f"manifest {role} path does not match {uri}")
    if role != "manifest" and generation is not None and artifact.get("generation") != generation:
        raise FeatureMetadataIndexError(f"manifest {role} generation does not match {generation}")


def validate_sidecar_schema_projection(
    *,
    sidecar_path: Path,
    schema_fields: Mapping[str, release_feature_model.ReleaseSchemaField],
) -> None:
    allowed = set(schema_fields)
    errors: list[str] = []
    for line_number, record in enumerate(release_feature_model.read_metadata_sidecar(sidecar_path), start=1):
        properties = record.get("properties") if isinstance(record.get("properties"), Mapping) else {}
        extra = sorted(set(properties) - allowed)
        if extra:
            errors.append(f"record {line_number} has properties outside the release schema: {', '.join(extra)}")
            if len(errors) >= 10:
                break
    if errors:
        raise FeatureMetadataIndexError("; ".join(errors))


def load_sidecar_to_index(
    *,
    sidecar_path: Path,
    schema_path: Path,
    manifest_path: Path,
    asset_slug: str,
    release: str,
    writer: FeatureMetadataWriter | None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    load_id: str | None = None,
    sidecar_uri: str | None = None,
    sidecar_generation: int | None = None,
    schema_uri: str | None = None,
    schema_generation: int | None = None,
    manifest_uri: str | None = None,
    manifest_generation: int | None = None,
) -> LoadResult:
    if batch_size < 1 or batch_size > DEFAULT_BATCH_SIZE:
        raise FeatureMetadataIndexError(f"batch_size must be between 1 and {DEFAULT_BATCH_SIZE}")
    schema = read_json_object(schema_path, label="release schema")
    manifest = read_json_object(manifest_path, label="release manifest")
    try:
        schema_fields = release_feature_model.validate_release_schema(
            schema,
            expected_asset_slug=asset_slug,
            expected_release=release,
        )
        artifacts_by_role = release_feature_model.validate_release_manifest(
            manifest,
            expected_asset_slug=asset_slug,
            expected_release=release,
            require_generations=True,
        )
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise FeatureMetadataIndexError(str(exc)) from exc
    validate_artifact_generation(
        artifacts_by_role,
        role="metadata",
        uri=sidecar_uri,
        generation=sidecar_generation,
    )
    validate_artifact_generation(
        artifacts_by_role,
        role="schema",
        uri=schema_uri,
        generation=schema_generation,
    )
    validate_artifact_generation(
        artifacts_by_role,
        role="manifest",
        uri=manifest_uri,
        generation=manifest_generation,
    )
    validation = release_feature_model.validate_sidecar_records(
        release_feature_model.read_metadata_sidecar(sidecar_path),
        expected_asset_slug=asset_slug,
        expected_release=release,
    )
    if not validation.valid:
        raise FeatureMetadataIndexError("; ".join(validation.errors))
    if validation.feature_count <= 0:
        raise FeatureMetadataIndexError("metadata sidecar must contain at least one record")
    validate_sidecar_schema_projection(sidecar_path=sidecar_path, schema_fields=schema_fields)
    if not dry_run and writer is None:
        raise FeatureMetadataIndexError("writer is required unless dry_run=True")
    if not dry_run and not load_id:
        raise FeatureMetadataIndexError("load_id is required unless dry_run=True")

    document_count = 0
    batch_count = 0
    deleted_document_count = 0
    batch: list[dict[str, Any]] = []
    for record in release_feature_model.read_metadata_sidecar(sidecar_path):
        batch.append(record)
        if len(batch) >= batch_size:
            batch_count += 1
            if not dry_run and writer is not None:
                document_count += writer.write_batch(asset_slug=asset_slug, release=release, load_id=str(load_id), documents=batch)
            else:
                document_count += len(batch)
            batch = []
    if batch:
        batch_count += 1
        if not dry_run and writer is not None:
            document_count += writer.write_batch(asset_slug=asset_slug, release=release, load_id=str(load_id), documents=batch)
        else:
            document_count += len(batch)
    return LoadResult(
        asset_slug=asset_slug,
        release=release,
        sidecar_path=str(sidecar_path),
        schema_path=str(schema_path),
        manifest_path=str(manifest_path),
        document_count=document_count,
        batch_count=batch_count,
        dry_run=dry_run,
        deleted_document_count=deleted_document_count,
    )


def build_index_load_record(
    result: LoadResult,
    *,
    load_id: str,
    sidecar_uri: str,
    sidecar_generation: int | None = None,
    schema_uri: str | None = None,
    schema_generation: int | None = None,
    manifest_uri: str | None = None,
    manifest_generation: int | None = None,
    firestore_collection_root: str = DEFAULT_COLLECTION_ROOT,
    status: str = "success",
    started_at: str | None = None,
    completed_at: str | None = None,
) -> dict[str, Any]:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()
    return {
        "schema_version": 1,
        "load_id": load_id,
        "asset_slug": result.asset_slug,
        "release": result.release,
        "status": status,
        "started_at": started_at or now,
        "completed_at": completed_at or now,
        "sidecar_uri": sidecar_uri,
        "sidecar_generation": sidecar_generation,
        "schema_uri": schema_uri,
        "schema_generation": schema_generation,
        "manifest_uri": manifest_uri,
        "manifest_generation": manifest_generation,
        "firestore_collection_root": firestore_collection_root,
        "document_count": result.document_count,
        "batch_count": result.batch_count,
        "deleted_document_count": result.deleted_document_count,
        "dry_run": result.dry_run,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Load feature metadata sidecars into Firestore.")
    parser.add_argument("--sidecar", required=True, help="Local {asset-slug}.metadata.ndjson.gz sidecar.")
    parser.add_argument("--schema", required=True, help="Local {asset-slug}.schema.json release schema.")
    parser.add_argument("--manifest", required=True, help="Local {asset-slug}.manifest.json final release manifest.")
    parser.add_argument("--asset-slug", required=True)
    parser.add_argument("--release", required=True, help="Concrete release date, YYYY-MM-DD.")
    parser.add_argument("--collection-root", default=DEFAULT_COLLECTION_ROOT)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true", help="Validate and count records without writing Firestore.")
    parser.add_argument("--load-id", help="Load ID for the optional local index-load record.")
    parser.add_argument("--sidecar-uri", help="Canonical GCS sidecar URI for the optional local index-load record.")
    parser.add_argument("--sidecar-generation", type=int, help="Canonical sidecar generation.")
    parser.add_argument("--schema-uri", help="Canonical GCS schema URI for the optional local index-load record.")
    parser.add_argument("--schema-generation", type=int, help="Canonical schema generation.")
    parser.add_argument("--manifest-uri", help="Canonical GCS manifest URI for the optional local index-load record.")
    parser.add_argument("--manifest-generation", type=int, help="Canonical manifest generation.")
    parser.add_argument("--index-load-record", type=Path, help="Write a local index-load record JSON file.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    writer = None if args.dry_run else FirestoreFeatureMetadataWriter(collection_root=args.collection_root)
    try:
        result = load_sidecar_to_index(
            sidecar_path=Path(args.sidecar),
            schema_path=Path(args.schema),
            manifest_path=Path(args.manifest),
            asset_slug=args.asset_slug,
            release=args.release,
            writer=writer,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
            load_id=args.load_id,
            sidecar_uri=args.sidecar_uri,
            sidecar_generation=args.sidecar_generation,
            schema_uri=args.schema_uri,
            schema_generation=args.schema_generation,
            manifest_uri=args.manifest_uri,
            manifest_generation=args.manifest_generation,
        )
        payload: dict[str, Any] = {"load_result": asdict(result)}
        if args.index_load_record:
            if not all((args.load_id, args.sidecar_uri, args.schema_uri, args.manifest_uri)):
                raise FeatureMetadataIndexError(
                    "--load-id, --sidecar-uri, --schema-uri, and --manifest-uri are required with --index-load-record"
                )
            record = build_index_load_record(
                result,
                load_id=args.load_id,
                sidecar_uri=args.sidecar_uri,
                sidecar_generation=args.sidecar_generation,
                schema_uri=args.schema_uri,
                schema_generation=args.schema_generation,
                manifest_uri=args.manifest_uri,
                manifest_generation=args.manifest_generation,
                firestore_collection_root=args.collection_root,
            )
            args.index_load_record.parent.mkdir(parents=True, exist_ok=True)
            args.index_load_record.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
            payload["index_load_record"] = str(args.index_load_record)
        print(json.dumps(payload, indent=2, sort_keys=True))
    except (FeatureMetadataIndexError, release_feature_model.ReleaseFeatureModelError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
