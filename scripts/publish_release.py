#!/usr/bin/env python3
"""Publish prepared local artifacts as a versioned shared dataset release."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.common import release_index
from ingestion.common.runtime import content_type_for
from scripts import release_feature_model
from scripts.raster_asset import validate_cog


DATA_OBJECT_FORMATS = {"fgb", "pmtiles", "geojson", "ndgeojson", "csv", "cog"}
SUPPORTING_RELEASE_FORMATS = {"metadata", "schema", "manifest"}
SINGLE_OBJECT_FORMATS = DATA_OBJECT_FORMATS | SUPPORTING_RELEASE_FORMATS
SUPPORTING_RELEASE_FORMAT_ORDER = ("metadata", "schema", "manifest")
VECTOR_CANONICAL_FORMATS = {"fgb", "geojson", "ndgeojson"}
REQUIRED_VECTOR_BUNDLE_FORMATS = ("fgb", "pmtiles", "metadata", "schema", "manifest")
FORMAT_EXTENSIONS = {
    "fgb": ".fgb",
    "pmtiles": ".pmtiles",
    "geojson": ".geojson",
    "ndgeojson": ".ndgeojson",
    "csv": ".csv",
    "cog": ".tif",
    "metadata": ".metadata.ndjson.gz",
    "schema": ".schema.json",
    "manifest": ".manifest.json",
}
EXTENSION_FORMATS = {
    ".fgb": "fgb",
    ".pmtiles": "pmtiles",
    ".geojson": "geojson",
    ".ndgeojson": "ndgeojson",
    ".csv": "csv",
    ".tif": "cog",
    ".tiff": "cog",
}
SCHEMA_FORMATS = {"fgb", "geojson", "ndgeojson", "csv"}
RUN_RECORD_VERSION = 1


class PublishReleaseError(RuntimeError):
    """Raised when a release cannot be safely published."""


@dataclass(frozen=True)
class PublishArtifact:
    local_path: str
    format: str
    release_uri: str
    latest_uri: str
    size: int
    sha256: str
    content_type: str | None


@dataclass(frozen=True)
class MetadataUpload:
    local_path: str
    uri: str
    size: int
    sha256: str
    content_type: str | None
    current_generation: int | None


@dataclass(frozen=True)
class PublishPlan:
    asset_slug: str
    title: str
    release_date: str
    bucket: str
    asset_root: str
    release_path: str
    run_record_uri: str
    canonical_format: str
    available_formats: tuple[str, ...]
    stale_formats: tuple[str, ...]
    artifacts: tuple[PublishArtifact, ...]
    metadata_uploads: tuple[MetadataUpload, ...]
    remote_generations: dict[str, int | None]
    checks: tuple[str, ...]
    schema_compatibility: dict[str, Any] | None = None
    compatibility_waiver: dict[str, Any] | None = None


@dataclass(frozen=True)
class PublishResult:
    asset_slug: str
    release_date: str
    release_path: str
    release_objects: tuple[dict[str, Any], ...]
    latest_objects: tuple[dict[str, Any], ...]
    run_record: dict[str, Any] | None
    metadata_objects: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]


def load_catalog(path: Path = Path("catalog/shared-datasets-catalog.csv")) -> dict[str, dict[str, str]]:
    with path.open(newline="") as file_obj:
        return {row["asset_slug"]: row for row in csv.DictReader(file_obj) if row.get("asset_slug")}


def parse_release_date(value: str) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise PublishReleaseError(f"release date must be YYYY-MM-DD: {value!r}") from exc
    if parsed.isoformat() != value:
        raise PublishReleaseError(f"release date must be zero-padded YYYY-MM-DD: {value!r}")
    return parsed


def parse_artifact_overrides(values: Iterable[str]) -> dict[str, Path]:
    overrides: dict[str, Path] = {}
    for value in values:
        raw_format, separator, raw_path = value.partition("=")
        if not separator or not raw_format or not raw_path:
            raise PublishReleaseError(f"artifact override must be format=/path/file, got: {value!r}")
        format_name = normalize_format(raw_format)
        if format_name in overrides:
            raise PublishReleaseError(f"duplicate artifact override for format {format_name!r}")
        overrides[format_name] = Path(raw_path)
    return overrides


def build_publish_plan(
    *,
    asset_slug: str,
    release_date: str,
    publish_dir: Path | None,
    artifact_overrides: dict[str, Path] | None = None,
    allow_stale_formats: Iterable[str] = (),
    catalog_path: Path = Path("catalog/shared-datasets-catalog.csv"),
    client: Any,
    readme_path: Path | None = None,
    remote_catalog_path: Path | None = None,
    schema_reader: Callable[[Path], list[dict[str, str]]] | None = None,
    schema_compatibility_checker: Callable[..., Any] | None = None,
    compatibility_waiver_path: Path | None = None,
    compatibility_waiver: dict[str, Any] | None = None,
    cog_validator: Callable[[Path], Any] | None = None,
) -> PublishPlan:
    release = parse_release_date(release_date)
    catalog = load_catalog(catalog_path)
    row = catalog.get(asset_slug)
    if row is None:
        raise PublishReleaseError(f"asset slug is not in the catalog: {asset_slug}")

    canonical_format = normalize_format(row.get("canonical_format", ""))
    if canonical_format == "zarr":
        raise PublishReleaseError("publish-release v1 does not support zarr prefix assets")
    if canonical_format not in DATA_OBJECT_FORMATS:
        raise PublishReleaseError(f"unsupported canonical format for publish-release v1: {canonical_format!r}")

    bucket_name, canonical_object = split_gs_uri(required(row, "canonical_path"))
    latest_marker = "/latest/"
    if latest_marker not in canonical_object:
        raise PublishReleaseError(f"canonical path must be a latest/ object: {row['canonical_path']}")
    asset_root = canonical_object.split(latest_marker, 1)[0]
    latest_root_uri = f"gs://{bucket_name}/{asset_root}/latest"
    release_path = f"gs://{bucket_name}/{asset_root}/releases/{release.isoformat()}/"
    run_record_uri = f"gs://{bucket_name}/{asset_root}/runs/{release.isoformat()}.json"

    available_formats = normalize_available_formats(row, canonical_format)
    if any(format_name == "zarr" for format_name in available_formats):
        raise PublishReleaseError("publish-release v1 does not support zarr assets")
    unsupported = [format_name for format_name in available_formats if format_name not in DATA_OBJECT_FORMATS]
    if unsupported:
        raise PublishReleaseError(f"unsupported catalog format(s) for publish-release v1: {', '.join(unsupported)}")

    local_artifacts = discover_artifacts(asset_slug, publish_dir, artifact_overrides or {})
    if canonical_format not in local_artifacts:
        raise PublishReleaseError(f"canonical artifact is required: {canonical_format}")
    vector_release = canonical_format in VECTOR_CANONICAL_FORMATS
    if vector_release:
        missing_bundle = [format_name for format_name in REQUIRED_VECTOR_BUNDLE_FORMATS if format_name not in local_artifacts]
        if missing_bundle:
            raise PublishReleaseError(
                "vector releases require the complete feature metadata bundle: "
                + ", ".join(REQUIRED_VECTOR_BUNDLE_FORMATS)
                + f"; missing: {', '.join(missing_bundle)}"
            )

    allow_stale = {normalize_format(format_name) for format_name in allow_stale_formats}
    expected_formats = set(available_formats) | ({"pmtiles"} if vector_release else set())
    unexpected = sorted(set(local_artifacts) - expected_formats - SUPPORTING_RELEASE_FORMATS)
    if unexpected:
        raise PublishReleaseError(f"artifact format(s) are not listed for this asset: {', '.join(unexpected)}")
    stale_formats = tuple(format_name for format_name in available_formats if format_name not in local_artifacts)
    blocked_stale = [format_name for format_name in stale_formats if format_name not in allow_stale]
    if blocked_stale:
        raise PublishReleaseError(
            "catalog-listed companion format(s) would become stale: "
            + ", ".join(blocked_stale)
            + ". Use --allow-stale-format for an intentional partial publish."
        )

    checks: list[str] = []
    artifacts: list[PublishArtifact] = []
    latest_generations: dict[str, int | None] = {}
    bucket = client.bucket(bucket_name)
    schema_probe = schema_reader or default_schema_reader
    schema_compatibility_check = schema_compatibility_checker or default_schema_compatibility_checker
    compatibility_waiver_payload = load_compatibility_waiver(compatibility_waiver_path, compatibility_waiver)
    schema_compatibility: dict[str, Any] | None = None
    validate_cog_fn = cog_validator or validate_cog

    data_format_order = tuple(dict.fromkeys((*available_formats, *(("pmtiles",) if vector_release else ()))))
    publish_format_order = (
        *data_format_order,
        *(format_name for format_name in SUPPORTING_RELEASE_FORMAT_ORDER if format_name in local_artifacts),
    )
    for format_name in publish_format_order:
        local_path = local_artifacts.get(format_name)
        if local_path is None:
            continue
        validate_artifact_path(asset_slug, format_name, local_path)
        if format_name == "cog":
            try:
                cog_result = validate_cog_fn(local_path)
            except Exception as exc:  # noqa: BLE001 - normalize validator failures for CLI callers
                raise PublishReleaseError(f"COG validation failed for {local_path}: {exc}") from exc
            if not getattr(cog_result, "valid", False):
                errors = ", ".join(getattr(cog_result, "errors", ()) or ("unknown COG validation error",))
                raise PublishReleaseError(f"COG validation failed for {local_path}: {errors}")
            checks.append(f"validated COG artifact: {local_path}")
        if format_name == canonical_format and format_name in SCHEMA_FORMATS:
            try:
                schema = schema_probe(local_path)
            except Exception as exc:  # noqa: BLE001 - normalize validator failures for CLI callers
                raise PublishReleaseError(f"could not sample canonical schema for {local_path}: {exc}") from exc
            checks.append(f"sampled canonical schema fields: {len(schema)}")
            schema_compatibility = run_schema_compatibility_check(
                checker=schema_compatibility_check,
                asset_slug=asset_slug,
                dataset_path=local_path,
                fields=schema,
                compatibility_waiver=compatibility_waiver_payload,
            )
            if schema_compatibility is not None:
                blocked = len(schema_compatibility.get("blocked_diffs", ()))
                warnings = len(schema_compatibility.get("warning_diffs", ()))
                checks.append(f"schema compatibility checked: blocked={blocked}, warnings={warnings}")

        release_uri = f"{release_path}{asset_slug}{FORMAT_EXTENSIONS[format_name]}"
        latest_uri = (
            row["canonical_path"]
            if format_name == canonical_format
            else f"{latest_root_uri}/{asset_slug}{FORMAT_EXTENSIONS[format_name]}"
        )
        assert_object_missing(bucket, object_name_from_uri(release_uri), label="release object")
        latest_generations[latest_uri] = current_generation(bucket, object_name_from_uri(latest_uri))
        artifacts.append(
            PublishArtifact(
                local_path=str(local_path),
                format=format_name,
                release_uri=release_uri,
                latest_uri=latest_uri,
                size=local_path.stat().st_size,
                sha256=sha256_file(local_path),
                content_type=content_type_for(local_path),
            )
        )
    if vector_release:
        validate_vector_release_bundle(
            asset_slug=asset_slug,
            release=release.isoformat(),
            artifacts=artifacts,
        )

    assert_object_missing(bucket, object_name_from_uri(run_record_uri), label="run record")
    metadata_uploads = tuple(
        build_metadata_uploads(
            bucket=bucket,
            bucket_name=bucket_name,
            asset_root=asset_root,
            readme_path=readme_path,
            remote_catalog_path=remote_catalog_path,
        )
    )

    return PublishPlan(
        asset_slug=asset_slug,
        title=row.get("title") or asset_slug,
        release_date=release.isoformat(),
        bucket=bucket_name,
        asset_root=asset_root,
        release_path=release_path,
        run_record_uri=run_record_uri,
        canonical_format=canonical_format,
        available_formats=available_formats,
        stale_formats=stale_formats,
        artifacts=tuple(artifacts),
        metadata_uploads=metadata_uploads,
        remote_generations=latest_generations,
        checks=tuple(checks),
        schema_compatibility=schema_compatibility,
        compatibility_waiver=compatibility_waiver_payload,
    )


def execute_publish_plan(
    plan: PublishPlan,
    *,
    client: Any,
    source_version: str = "",
    row_count: int | None = None,
    notes: str = "",
    notify: bool = True,
    update_schema_snapshot: bool = True,
    schema_updater: Callable[[str, Path], None] | None = None,
    schema_reader: Callable[[Path], list[dict[str, str]]] | None = None,
    schema_compatibility_checker: Callable[..., Any] | None = None,
    notifier: Callable[[PublishPlan, int | None], None] | None = None,
) -> PublishResult:
    bucket = client.bucket(plan.bucket)
    release_objects: list[dict[str, Any]] = []
    latest_objects: list[dict[str, Any]] = []
    metadata_objects: list[dict[str, Any]] = []
    warnings: list[str] = []

    metadata = {
        "asset_slug": plan.asset_slug,
        "release_date": plan.release_date,
        "published_by": "scripts.publish_release",
    }
    if source_version:
        metadata["source_version"] = source_version

    canonical_artifact = next((artifact for artifact in plan.artifacts if artifact.format == plan.canonical_format), None)
    if plan.schema_compatibility and canonical_artifact and canonical_artifact.format in SCHEMA_FORMATS:
        schema_probe = schema_reader or default_schema_reader
        try:
            fields = schema_probe(Path(canonical_artifact.local_path))
        except Exception as exc:  # noqa: BLE001 - normalize validator failures before remote writes
            raise PublishReleaseError(f"could not resample canonical schema for {canonical_artifact.local_path}: {exc}") from exc
        run_schema_compatibility_check(
            checker=schema_compatibility_checker or default_schema_compatibility_checker,
            asset_slug=plan.asset_slug,
            dataset_path=Path(canonical_artifact.local_path),
            fields=fields,
            compatibility_waiver=plan.compatibility_waiver,
        )

    manifest_artifact = next((artifact for artifact in plan.artifacts if artifact.format == "manifest"), None)
    non_manifest_artifacts = [artifact for artifact in plan.artifacts if artifact.format != "manifest"]

    for artifact in non_manifest_artifacts:
        blob = bucket.blob(object_name_from_uri(artifact.release_uri))
        blob.metadata = {**metadata, "format": artifact.format}
        try:
            blob.upload_from_filename(
                artifact.local_path,
                content_type=artifact.content_type,
                if_generation_match=0,
            )
        except PreconditionFailed as exc:
            raise PublishReleaseError(f"refusing to overwrite release object: {artifact.release_uri}") from exc
        blob.reload()
        release_objects.append(blob_info(artifact.release_uri, blob))

    for artifact in non_manifest_artifacts:
        blob = bucket.blob(object_name_from_uri(artifact.latest_uri))
        blob.metadata = {**metadata, "format": artifact.format}
        expected_generation = plan.remote_generations.get(artifact.latest_uri)
        try:
            blob.upload_from_filename(
                artifact.local_path,
                content_type=artifact.content_type,
                if_generation_match=expected_generation if expected_generation is not None else 0,
            )
        except PreconditionFailed as exc:
            raise PublishReleaseError(f"latest object generation changed before upload: {artifact.latest_uri}") from exc
        blob.reload()
        latest_objects.append(blob_info(artifact.latest_uri, blob))

    if manifest_artifact is not None:
        manifest_payload = final_manifest_payload(
            plan=plan,
            manifest_artifact=manifest_artifact,
            release_objects=release_objects,
            latest_objects=latest_objects,
        )
        manifest_text = json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n"
        manifest_bytes = manifest_text.encode("utf-8")
        manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        for uri, generation_match, target in (
            (manifest_artifact.release_uri, 0, release_objects),
            (
                manifest_artifact.latest_uri,
                plan.remote_generations.get(manifest_artifact.latest_uri)
                if plan.remote_generations.get(manifest_artifact.latest_uri) is not None
                else 0,
                latest_objects,
            ),
        ):
            blob = bucket.blob(object_name_from_uri(uri))
            blob.metadata = {**metadata, "format": manifest_artifact.format}
            try:
                blob.upload_from_string(
                    manifest_text,
                    content_type=manifest_artifact.content_type,
                    if_generation_match=generation_match,
                )
            except PreconditionFailed as exc:
                if uri == manifest_artifact.release_uri:
                    raise PublishReleaseError(f"refusing to overwrite release object: {uri}") from exc
                raise PublishReleaseError(f"latest object generation changed before upload: {uri}") from exc
            blob.reload()
            info = blob_info(uri, blob)
            info["sha256"] = manifest_sha256
            target.append(info)

    for metadata_upload in plan.metadata_uploads:
        blob = bucket.blob(object_name_from_uri(metadata_upload.uri))
        try:
            blob.upload_from_filename(
                metadata_upload.local_path,
                content_type=metadata_upload.content_type,
                if_generation_match=metadata_upload.current_generation
                if metadata_upload.current_generation is not None
                else 0,
            )
        except PreconditionFailed as exc:
            raise PublishReleaseError(f"metadata object generation changed before upload: {metadata_upload.uri}") from exc
        blob.reload()
        metadata_objects.append(blob_info(metadata_upload.uri, blob))

    run_record_payload = build_run_record_payload(
        plan=plan,
        release_objects=release_objects,
        latest_objects=latest_objects,
        source_version=source_version,
        row_count=row_count,
        notes=notes,
    )
    run_record = write_run_record(
        bucket=bucket,
        plan=plan,
        payload=run_record_payload,
    )
    try:
        release_index_info = release_index.record_successful_release(
            bucket,
            plan.asset_slug,
            run_record_payload,
            run_record_info=run_record,
        )
        run_record["release_index"] = release_index_info
    except Exception as exc:  # noqa: BLE001 - data was already published; report metadata repair separately
        warnings.append(f"release index update failed: {exc}")

    if update_schema_snapshot and canonical_artifact and canonical_artifact.format in SCHEMA_FORMATS:
        try:
            updater = schema_updater or default_schema_updater
            updater(plan.asset_slug, Path(canonical_artifact.local_path))
        except Exception as exc:  # noqa: BLE001 - post-publish warning should not mask a successful publish
            warnings.append(f"schema snapshot update failed: {exc}")

    if notify:
        try:
            active_notifier = notifier or default_notifier
            active_notifier(plan, row_count)
        except Exception as exc:  # noqa: BLE001 - notification is non-critical after publish succeeds
            warnings.append(f"upload summary notification failed: {exc}")

    return PublishResult(
        asset_slug=plan.asset_slug,
        release_date=plan.release_date,
        release_path=plan.release_path,
        release_objects=tuple(release_objects),
        latest_objects=tuple(latest_objects),
        run_record=run_record,
        metadata_objects=tuple(metadata_objects),
        warnings=tuple(warnings),
    )


def build_run_record_payload(
    *,
    plan: PublishPlan,
    release_objects: list[dict[str, Any]],
    latest_objects: list[dict[str, Any]],
    source_version: str,
    row_count: int | None,
    notes: str,
) -> dict[str, Any]:
    return {
        "record_version": RUN_RECORD_VERSION,
        "asset_slug": plan.asset_slug,
        "run_date": plan.release_date,
        "published_at": dt.datetime.now(dt.UTC).isoformat(),
        "status": "success",
        "source_version": source_version,
        "release_path": plan.release_path,
        "release_paths": release_objects,
        "latest_paths": latest_objects,
        "row_count": row_count,
        "notes": notes,
        "artifacts": [
            {
                "format": artifact.format,
                "local_path": artifact.local_path,
                "release_uri": artifact.release_uri,
                "latest_uri": artifact.latest_uri,
                "size": artifact.size,
                "sha256": artifact.sha256,
                "content_type": artifact.content_type,
            }
            for artifact in plan.artifacts
        ],
        "stale_formats": list(plan.stale_formats),
        "checks": list(plan.checks),
    }


def plan_to_dict(plan: PublishPlan) -> dict[str, Any]:
    return asdict(plan)


def result_to_dict(result: PublishResult) -> dict[str, Any]:
    return asdict(result)


def normalize_format(value: str) -> str:
    normalized = value.strip().lower()
    aliases = {
        "flatgeobuf": "fgb",
        ".fgb": "fgb",
        ".pmtiles": "pmtiles",
        ".geojson": "geojson",
        ".ndgeojson": "ndgeojson",
        ".csv": "csv",
        ".metadata.ndjson.gz": "metadata",
        "metadata-sidecar": "metadata",
        "metadata_sidecar": "metadata",
        "sidecar": "metadata",
        ".schema.json": "schema",
        ".manifest.json": "manifest",
        "geotiff": "cog",
        "tif": "cog",
        "tiff": "cog",
        ".tif": "cog",
        ".tiff": "cog",
    }
    return aliases.get(normalized, normalized)


def normalize_available_formats(row: dict[str, str], canonical_format: str) -> tuple[str, ...]:
    formats = [normalize_format(value) for value in (row.get("available_formats") or "").split(";") if value.strip()]
    if canonical_format not in formats:
        formats.insert(0, canonical_format)
    deduped: list[str] = []
    for format_name in formats:
        if format_name not in deduped:
            deduped.append(format_name)
    return tuple(deduped)


def required(row: dict[str, str], key: str) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise PublishReleaseError(f"catalog row is missing {key!r}")
    return value


def discover_artifacts(
    asset_slug: str,
    publish_dir: Path | None,
    overrides: dict[str, Path],
) -> dict[str, Path]:
    artifacts: dict[str, Path] = {}
    if publish_dir is not None:
        if not publish_dir.is_dir():
            raise PublishReleaseError(f"publish directory does not exist: {publish_dir}")
        for path in sorted(publish_dir.iterdir()):
            if not path.is_file():
                continue
            format_name = artifact_format_for_path(asset_slug, path)
            if not format_name:
                continue
            if format_name in artifacts:
                raise PublishReleaseError(f"multiple publish-dir artifacts found for format {format_name!r}")
            artifacts[format_name] = path
    elif not overrides:
        raise PublishReleaseError("publish-dir is required unless at least one --artifact override is provided")

    for format_name, path in overrides.items():
        artifacts[format_name] = path
    return artifacts


def validate_artifact_path(asset_slug: str, format_name: str, path: Path) -> None:
    if format_name not in SINGLE_OBJECT_FORMATS:
        raise PublishReleaseError(f"unsupported artifact format for publish-release v1: {format_name}")
    if not path.is_file():
        raise PublishReleaseError(f"artifact does not exist or is not a file: {path}")
    expected_name = f"{asset_slug}{FORMAT_EXTENSIONS[format_name]}"
    if path.name != expected_name:
        raise PublishReleaseError(f"artifact filename must be {expected_name!r} for format {format_name!r}: {path}")
    actual_format = artifact_format_for_path(asset_slug, path)
    if actual_format != format_name:
        raise PublishReleaseError(f"artifact extension does not match format {format_name!r}: {path}")
    if path.stat().st_size <= 0:
        raise PublishReleaseError(f"artifact is empty: {path}")


def load_json_artifact(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise PublishReleaseError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise PublishReleaseError(f"{label} must be a JSON object: {path}")
    return payload


def artifact_by_format(artifacts: Iterable[PublishArtifact]) -> dict[str, PublishArtifact]:
    return {artifact.format: artifact for artifact in artifacts}


def validate_vector_release_bundle(
    *,
    asset_slug: str,
    release: str,
    artifacts: Iterable[PublishArtifact],
) -> None:
    by_format = artifact_by_format(artifacts)
    missing = [format_name for format_name in REQUIRED_VECTOR_BUNDLE_FORMATS if format_name not in by_format]
    if missing:
        raise PublishReleaseError("vector release bundle is missing: " + ", ".join(missing))
    metadata_path = Path(by_format["metadata"].local_path)
    schema_path = Path(by_format["schema"].local_path)
    manifest_path = Path(by_format["manifest"].local_path)
    validation = release_feature_model.validate_sidecar_records(
        release_feature_model.read_metadata_sidecar(metadata_path),
        expected_asset_slug=asset_slug,
        expected_release=release,
    )
    if not validation.valid:
        raise PublishReleaseError("metadata sidecar validation failed: " + "; ".join(validation.errors))
    if validation.feature_count <= 0:
        raise PublishReleaseError("metadata sidecar must contain at least one record")
    schema = load_json_artifact(schema_path, label="release schema")
    release_feature_model.validate_release_schema(schema, expected_asset_slug=asset_slug, expected_release=release)
    manifest = load_json_artifact(manifest_path, label="release manifest")
    manifest_artifacts = release_feature_model.validate_release_manifest(
        manifest,
        expected_asset_slug=asset_slug,
        expected_release=release,
        require_generations=False,
    )
    for format_name in REQUIRED_VECTOR_BUNDLE_FORMATS:
        artifact = by_format[format_name]
        manifest_artifact = manifest_artifacts[format_name]
        if manifest_artifact.get("path") != artifact.release_uri:
            raise PublishReleaseError(f"manifest {format_name} artifact path does not match planned release URI")
        if format_name != "manifest":
            manifest_sha = str(manifest_artifact.get("sha256") or "").split(":", 1)[-1]
            if manifest_sha != artifact.sha256:
                raise PublishReleaseError(f"manifest {format_name} artifact sha256 does not match local file")


def final_manifest_payload(
    *,
    plan: PublishPlan,
    manifest_artifact: PublishArtifact,
    release_objects: list[dict[str, Any]],
    latest_objects: list[dict[str, Any]],
) -> dict[str, Any]:
    template = load_json_artifact(Path(manifest_artifact.local_path), label="release manifest")
    release_info_by_path = {item["path"]: item for item in release_objects}
    latest_info_by_path = {item["path"]: item for item in latest_objects}
    artifacts = []
    for artifact in plan.artifacts:
        entry: dict[str, Any] = {
            "role": artifact.format,
            "format": artifact.format,
            "path": artifact.release_uri,
            "latest_path": artifact.latest_uri,
            "content_type": artifact.content_type,
        }
        release_info = release_info_by_path.get(artifact.release_uri)
        latest_info = latest_info_by_path.get(artifact.latest_uri)
        if artifact.format != "manifest":
            entry.update(
                {
                    "sha256": artifact.sha256,
                    "size": artifact.size,
                }
            )
        if release_info and artifact.format != "manifest":
            entry["generation"] = release_info.get("generation")
        if latest_info:
            entry["latest_generation"] = latest_info.get("generation")
        artifacts.append({key: value for key, value in entry.items() if value is not None})
    payload = dict(template)
    payload["artifacts"] = artifacts
    payload["index_load_status"] = "tracked in index-loads/"
    payload["index_status_policy"] = {
        "mode": "external_index_load_records",
        "path": f"gs://{plan.bucket}/{plan.asset_root}/index-loads/{plan.release_date}/",
    }
    release_feature_model.validate_release_manifest(
        payload,
        expected_asset_slug=plan.asset_slug,
        expected_release=plan.release_date,
        require_generations=True,
    )
    return payload


def artifact_format_for_path(asset_slug: str, path: Path) -> str | None:
    name = path.name
    for format_name, suffix in FORMAT_EXTENSIONS.items():
        if name == f"{asset_slug}{suffix}":
            return format_name
    return EXTENSION_FORMATS.get(path.suffix.lower())


def build_metadata_uploads(
    *,
    bucket: Any,
    bucket_name: str,
    asset_root: str,
    readme_path: Path | None,
    remote_catalog_path: Path | None,
) -> Iterable[MetadataUpload]:
    for local_path, uri in (
        (readme_path, f"gs://{bucket_name}/{asset_root}/README.md" if readme_path else None),
        (
            remote_catalog_path,
            f"gs://{bucket_name}/_catalog/shared-datasets-catalog.csv" if remote_catalog_path else None,
        ),
    ):
        if local_path is None or uri is None:
            continue
        if not local_path.is_file():
            raise PublishReleaseError(f"metadata upload path does not exist or is not a file: {local_path}")
        yield MetadataUpload(
            local_path=str(local_path),
            uri=uri,
            size=local_path.stat().st_size,
            sha256=sha256_file(local_path),
            content_type=content_type_for(local_path),
            current_generation=current_generation(bucket, object_name_from_uri(uri)),
        )


def assert_object_missing(bucket: Any, object_name: str, *, label: str) -> None:
    blob = bucket.blob(object_name)
    try:
        blob.reload()
    except NotFound:
        return
    raise PublishReleaseError(f"{label} already exists: gs://{bucket.name}/{object_name}")


def current_generation(bucket: Any, object_name: str) -> int | None:
    blob = bucket.blob(object_name)
    try:
        blob.reload()
    except NotFound:
        return None
    return int(blob.generation)


def write_run_record(
    *,
    bucket: Any,
    plan: PublishPlan,
    payload: dict[str, Any],
) -> dict[str, Any]:
    blob = bucket.blob(object_name_from_uri(plan.run_record_uri))
    blob.metadata = {"asset_slug": plan.asset_slug, "run_date": plan.release_date}
    try:
        blob.upload_from_string(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            content_type="application/json",
            if_generation_match=0,
        )
    except PreconditionFailed as exc:
        raise PublishReleaseError(f"run record already exists: {plan.run_record_uri}") from exc
    blob.reload()
    return blob_info(plan.run_record_uri, blob)


def blob_info(uri: str, blob: Any) -> dict[str, Any]:
    return {
        "path": uri,
        "generation": int(blob.generation),
        "size": int(blob.size or 0),
    }


def split_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise PublishReleaseError(f"expected gs:// URI, got: {uri}")
    bucket, separator, name = uri[5:].partition("/")
    if not bucket or not separator or not name:
        raise PublishReleaseError(f"expected gs:// object URI, got: {uri}")
    return bucket, name


def object_name_from_uri(uri: str) -> str:
    return split_gs_uri(uri)[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_compatibility_waiver(
    compatibility_waiver_path: Path | None,
    compatibility_waiver: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if compatibility_waiver_path is not None and compatibility_waiver is not None:
        raise PublishReleaseError("use either --compatibility-waiver or compatibility_waiver, not both")
    if compatibility_waiver_path is None:
        return compatibility_waiver
    try:
        from scripts.dataset_alerts import load_compatibility_waiver as load_schema_waiver

        return load_schema_waiver(compatibility_waiver_path)
    except Exception as exc:  # noqa: BLE001 - normalize waiver failures for CLI callers
        raise PublishReleaseError(f"could not load schema compatibility waiver: {exc}") from exc


def schema_compatibility_to_dict(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    if hasattr(result, "to_dict"):
        return result.to_dict()
    if isinstance(result, dict):
        return result
    return {"result": str(result)}


def run_schema_compatibility_check(
    *,
    checker: Callable[..., Any],
    asset_slug: str,
    dataset_path: Path,
    fields: list[dict[str, str]],
    compatibility_waiver: dict[str, Any] | None,
) -> dict[str, Any] | None:
    try:
        result = checker(
            asset_slug=asset_slug,
            dataset_path=dataset_path,
            fields=fields,
            compatibility_waiver=compatibility_waiver,
        )
    except Exception as exc:  # noqa: BLE001 - normalize validator failures for CLI callers
        raise PublishReleaseError(f"schema compatibility check failed: {exc}") from exc
    return schema_compatibility_to_dict(result)


def default_schema_reader(path: Path) -> list[dict[str, str]]:
    from scripts.dataset_alerts import schema_for_path

    return schema_for_path(path)


def default_schema_compatibility_checker(**kwargs: Any) -> Any:
    from scripts.dataset_alerts import check_schema_compatibility

    return check_schema_compatibility(**kwargs)


def default_schema_updater(asset_slug: str, dataset_path: Path) -> None:
    from scripts.dataset_alerts import check_schema

    check_schema(
        asset_slug=asset_slug,
        dataset_path=dataset_path,
        snapshot_uri=None,
        dry_run=False,
        skip_snapshot_upload=False,
    )


def default_notifier(plan: PublishPlan, row_count: int | None) -> None:
    from scripts.dataset_alerts import upload_summary

    canonical_artifact = next((artifact for artifact in plan.artifacts if artifact.format == plan.canonical_format), None)
    upload_summary(
        asset_slug=plan.asset_slug,
        changed_path=[artifact.release_uri for artifact in plan.artifacts]
        + [artifact.latest_uri for artifact in plan.artifacts],
        release_path=plan.release_path,
        row_count=row_count,
        dataset_path=Path(canonical_artifact.local_path) if canonical_artifact else None,
        sample_column=[],
        dry_run=False,
    )
