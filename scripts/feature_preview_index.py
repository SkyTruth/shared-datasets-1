#!/usr/bin/env python3
"""Load preview feature metadata sidecars into Firestore."""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_COLLECTION_ROOT = "feature_preview_index"
DEFAULT_BATCH_SIZE = 450
FEATURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class FeaturePreviewIndexError(RuntimeError):
    """Raised when sidecar rows cannot be loaded into the serving index."""


@dataclass(frozen=True)
class LoadResult:
    asset_slug: str
    release: str
    sidecar_path: str
    document_count: int
    batch_count: int
    dry_run: bool


class FirestoreFeaturePreviewWriter:
    def __init__(self, *, collection_root: str = DEFAULT_COLLECTION_ROOT, client: Any = None) -> None:
        self.collection_root = collection_root
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from google.cloud import firestore

            project = os.environ.get("GOOGLE_CLOUD_PROJECT")
            database = os.environ.get("FEATURE_PREVIEW_FIRESTORE_DATABASE") or None
            kwargs = {"project": project} if project else {}
            if database:
                kwargs["database"] = database
            self._client = firestore.Client(**kwargs)
        return self._client

    def write_batch(self, asset_slug: str, release: str, documents: Sequence[Mapping[str, Any]]) -> int:
        batch = self.client.batch()
        features = (
            self.client.collection(self.collection_root)
            .document(asset_slug)
            .collection("releases")
            .document(release)
            .collection("features")
        )
        for document in documents:
            feature_id = str(document["feature_id"])
            batch.set(
                features.document(feature_id),
                {
                    "asset_slug": asset_slug,
                    "release": release,
                    "feature_id": feature_id,
                    "feature_hash": document.get("feature_hash"),
                    "properties": dict(document.get("properties") or {}),
                    "provenance": dict(document.get("provenance") or {}),
                },
            )
        batch.commit()
        return len(documents)


def read_sidecar(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FeaturePreviewIndexError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(record, dict):
                raise FeaturePreviewIndexError(f"{path}:{line_number}: sidecar row must be a JSON object")
            records.append(record)
    return records


def validate_records(records: Sequence[Mapping[str, Any]], *, asset_slug: str, release: str) -> None:
    seen: set[str] = set()
    errors: list[str] = []
    for index, record in enumerate(records, start=1):
        if record.get("asset_slug") not in (None, asset_slug):
            errors.append(f"record {index} asset_slug does not match {asset_slug}")
        if record.get("release") not in (None, release):
            errors.append(f"record {index} release does not match {release}")
        feature_id = str(record.get("feature_id") or "")
        if not FEATURE_ID_RE.fullmatch(feature_id):
            errors.append(f"record {index} has invalid feature_id")
        if feature_id in seen:
            errors.append(f"duplicate feature_id: {feature_id}")
        seen.add(feature_id)
        if not isinstance(record.get("properties"), Mapping):
            errors.append(f"record {index} properties must be an object")
        if not isinstance(record.get("provenance", {}), Mapping):
            errors.append(f"record {index} provenance must be an object")
    if errors:
        raise FeaturePreviewIndexError("; ".join(errors))


def load_sidecar_to_index(
    *,
    sidecar_path: Path,
    asset_slug: str,
    release: str,
    writer: FirestoreFeaturePreviewWriter | None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
) -> LoadResult:
    if batch_size < 1 or batch_size > 500:
        raise FeaturePreviewIndexError("batch_size must be between 1 and 500")
    if writer is None and not dry_run:
        raise FeaturePreviewIndexError("writer is required unless dry_run=True")

    records = read_sidecar(sidecar_path)
    validate_records(records, asset_slug=asset_slug, release=release)
    batch_count = 0
    if not dry_run:
        assert writer is not None
        for offset in range(0, len(records), batch_size):
            writer.write_batch(asset_slug, release, records[offset : offset + batch_size])
            batch_count += 1
    return LoadResult(
        asset_slug=asset_slug,
        release=release,
        sidecar_path=str(sidecar_path),
        document_count=len(records),
        batch_count=batch_count,
        dry_run=dry_run,
    )


def build_index_load_record(args: argparse.Namespace, result: LoadResult, *, started_at: dt.datetime) -> dict[str, Any]:
    completed_at = dt.datetime.now(dt.UTC).replace(microsecond=0)
    return {
        "schema_version": 1,
        "status": "success",
        "load_id": args.load_id,
        "asset_slug": args.asset_slug,
        "release": args.release,
        "started_at": started_at.replace(microsecond=0).isoformat(),
        "completed_at": completed_at.isoformat(),
        "firestore_collection_root": args.collection_root,
        "sidecar_uri": args.sidecar_uri,
        "sidecar_generation": args.sidecar_generation,
        "schema_uri": args.schema_uri,
        "schema_generation": args.schema_generation,
        "manifest_uri": args.manifest_uri,
        "manifest_generation": args.manifest_generation,
        "result": asdict(result),
    }


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sidecar", required=True, type=Path)
    parser.add_argument("--schema", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--asset-slug", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--collection-root", default=DEFAULT_COLLECTION_ROOT)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--load-id")
    parser.add_argument("--sidecar-uri")
    parser.add_argument("--sidecar-generation")
    parser.add_argument("--schema-uri")
    parser.add_argument("--schema-generation")
    parser.add_argument("--manifest-uri")
    parser.add_argument("--manifest-generation")
    parser.add_argument("--index-load-record", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    started_at = dt.datetime.now(dt.UTC)
    try:
        for path in (args.sidecar, args.schema, args.manifest):
            if not path.exists():
                raise FeaturePreviewIndexError(f"missing input file: {path}")
        if args.index_load_record and not (args.load_id and args.sidecar_uri):
            raise FeaturePreviewIndexError("--load-id and --sidecar-uri are required with --index-load-record")
        writer = None if args.dry_run else FirestoreFeaturePreviewWriter(collection_root=args.collection_root)
        result = load_sidecar_to_index(
            sidecar_path=args.sidecar,
            asset_slug=args.asset_slug,
            release=args.release,
            writer=writer,
            batch_size=args.batch_size,
            dry_run=args.dry_run,
        )
        if args.index_load_record:
            args.index_load_record.parent.mkdir(parents=True, exist_ok=True)
            args.index_load_record.write_text(
                json.dumps(build_index_load_record(args, result, started_at=started_at), indent=2, sort_keys=True)
                + "\n"
            )
        print(json.dumps(asdict(result), sort_keys=True))
        return 0
    except FeaturePreviewIndexError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
