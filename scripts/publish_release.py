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
from scripts.raster_asset import validate_cog


SINGLE_OBJECT_FORMATS = {"fgb", "pmtiles", "geojson", "ndgeojson", "csv", "cog"}
FORMAT_EXTENSIONS = {
    "fgb": ".fgb",
    "pmtiles": ".pmtiles",
    "geojson": ".geojson",
    "ndgeojson": ".ndgeojson",
    "csv": ".csv",
    "cog": ".tif",
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
    if canonical_format not in SINGLE_OBJECT_FORMATS:
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
    unsupported = [format_name for format_name in available_formats if format_name not in SINGLE_OBJECT_FORMATS]
    if unsupported:
        raise PublishReleaseError(f"unsupported catalog format(s) for publish-release v1: {', '.join(unsupported)}")

    local_artifacts = discover_artifacts(asset_slug, publish_dir, artifact_overrides or {})
    if canonical_format not in local_artifacts:
        raise PublishReleaseError(f"canonical artifact is required: {canonical_format}")

    allow_stale = {normalize_format(format_name) for format_name in allow_stale_formats}
    expected_formats = set(available_formats)
    unexpected = sorted(set(local_artifacts) - expected_formats)
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
    validate_cog_fn = cog_validator or validate_cog

    for format_name in available_formats:
        local_path = local_artifacts.get(format_name)
        if local_path is None:
            continue
        validate_artifact_path(format_name, local_path)
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

    for artifact in plan.artifacts:
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

    for artifact in plan.artifacts:
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

    canonical_artifact = next((artifact for artifact in plan.artifacts if artifact.format == plan.canonical_format), None)
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
            if not path.is_file() or path.stem != asset_slug:
                continue
            format_name = EXTENSION_FORMATS.get(path.suffix.lower())
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


def validate_artifact_path(format_name: str, path: Path) -> None:
    if format_name not in SINGLE_OBJECT_FORMATS:
        raise PublishReleaseError(f"unsupported artifact format for publish-release v1: {format_name}")
    if not path.is_file():
        raise PublishReleaseError(f"artifact does not exist or is not a file: {path}")
    actual_format = EXTENSION_FORMATS.get(path.suffix.lower())
    if actual_format != format_name:
        raise PublishReleaseError(f"artifact extension does not match format {format_name!r}: {path}")
    if path.stat().st_size <= 0:
        raise PublishReleaseError(f"artifact is empty: {path}")


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


def default_schema_reader(path: Path) -> list[dict[str, str]]:
    from scripts.dataset_alerts import schema_for_path

    return schema_for_path(path)


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
