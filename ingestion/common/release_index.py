"""Release index helpers for shared dataset version history."""

from __future__ import annotations

import copy
import datetime as dt
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from google.api_core.exceptions import NotFound, PreconditionFailed


SCHEMA_VERSION = 1
RELEASE_INDEX_PREFIX = "_catalog/releases"
RELEASE_INDEX_CONTENT_TYPE = "application/json"
INDEX_LOAD_STATUS = "tracked in index-loads/"
INDEX_STATUS_MODE = "external_index_load_records"

FORMAT_EXTENSIONS = {
    ".metadata.ndjson.gz": "metadata",
    ".schema.json": "schema",
    ".manifest.json": "manifest",
    ".fgb": "fgb",
    ".pmtiles": "pmtiles",
    ".geojson": "geojson",
    ".ndgeojson": "ndgeojson",
    ".csv": "csv",
    ".tif": "cog",
    ".tiff": "cog",
}
LOCALIZED_METADATA_RE = re.compile(r"\.metadata\.(?P<locale>[a-z]{2,3}(?:_[a-z0-9]{2,8})*)\.ndjson\.gz$")


class ReleaseIndexError(RuntimeError):
    """Raised when a release index cannot be read, merged, or written."""


@dataclass(frozen=True)
class LoadedReleaseIndex:
    payload: dict[str, Any]
    generation: int | None


def release_index_object(asset_slug: str) -> str:
    return f"{RELEASE_INDEX_PREFIX}/{asset_slug}.json"


def release_index_uri(bucket_name: str, asset_slug: str) -> str:
    return f"gs://{bucket_name}/{release_index_object(asset_slug)}"


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat()


def empty_release_index(asset_slug: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "asset_slug": asset_slug,
        "updated_at": "",
        "latest_release": None,
        "latest_run": None,
        "releases": [],
    }


def coerce_release_index(payload: dict[str, Any] | None, asset_slug: str) -> dict[str, Any]:
    if payload is None:
        return empty_release_index(asset_slug)
    index = copy.deepcopy(payload)
    if not isinstance(index, dict):
        raise ReleaseIndexError("release index payload must be a JSON object")
    if index.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseIndexError(f"unsupported release index schema_version: {index.get('schema_version')!r}")
    if index.get("asset_slug") != asset_slug:
        raise ReleaseIndexError(f"release index asset_slug does not match {asset_slug!r}")
    index.setdefault("updated_at", "")
    index.setdefault("latest_release", None)
    index.setdefault("latest_run", None)
    releases = index.setdefault("releases", [])
    if not isinstance(releases, list):
        raise ReleaseIndexError("release index releases must be a list")
    return index


def load_release_index(bucket: Any, asset_slug: str) -> LoadedReleaseIndex:
    blob = bucket.blob(release_index_object(asset_slug))
    try:
        blob.reload()
    except NotFound:
        return LoadedReleaseIndex(empty_release_index(asset_slug), None)
    payload = json.loads(blob.download_as_text())
    return LoadedReleaseIndex(coerce_release_index(payload, asset_slug), int(blob.generation))


def write_release_index(
    bucket: Any,
    asset_slug: str,
    payload: dict[str, Any],
    *,
    generation: int | None,
) -> dict[str, Any]:
    index = coerce_release_index(payload, asset_slug)
    blob = bucket.blob(release_index_object(asset_slug))
    blob.metadata = {"asset_slug": asset_slug}
    blob.upload_from_string(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        content_type=RELEASE_INDEX_CONTENT_TYPE,
        if_generation_match=generation if generation is not None else 0,
    )
    blob.reload()
    return {
        "path": f"gs://{bucket.name}/{blob.name}",
        "generation": int(blob.generation),
        "size": int(blob.size or 0),
    }


def update_release_index(
    bucket: Any,
    asset_slug: str,
    updater: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    max_attempts: int = 2,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for _attempt in range(max_attempts):
        loaded = load_release_index(bucket, asset_slug)
        next_payload = updater(loaded.payload)
        try:
            return write_release_index(bucket, asset_slug, next_payload, generation=loaded.generation)
        except PreconditionFailed as exc:
            last_error = exc
    raise ReleaseIndexError(f"release index changed while updating {asset_slug}") from last_error


def infer_format(path: str) -> str:
    lowered = path.lower()
    if LOCALIZED_METADATA_RE.search(lowered):
        return "metadata"
    for suffix, format_name in FORMAT_EXTENSIONS.items():
        if lowered.endswith(suffix):
            return format_name
    return "unknown"


def metadata_locale_from_path(path: str) -> str:
    match = LOCALIZED_METADATA_RE.search(path.lower())
    return match.group("locale") if match else ""


def path_from_info(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("path") or "")
    if isinstance(value, str):
        return value
    return ""


def blob_file_entry(
    value: Any,
    *,
    sha256_by_format: dict[str, str] | None = None,
    sha256_by_path: dict[str, str] | None = None,
) -> dict[str, Any] | None:
    if isinstance(value, str):
        value = {"path": value}
    if not isinstance(value, dict):
        raise ReleaseIndexError("release path entries must be JSON objects")
    path = path_from_info(value)
    if not path:
        raise ReleaseIndexError("release path entry is missing path")
    format_name = str(value.get("format") or infer_format(path))
    entry: dict[str, Any] = {
        "path": path,
        "format": format_name,
    }
    locale = metadata_locale_from_path(path)
    if locale:
        entry["role"] = "metadata"
        entry["locale"] = locale
    for key in ("generation", "size", "content_type", "sha256"):
        if value.get(key) is not None:
            entry[key] = value[key]
    sha = (sha256_by_path or {}).get(path)
    if not sha and locale:
        sha = (sha256_by_format or {}).get(f"metadata_{locale}")
    if not sha:
        sha = (sha256_by_format or {}).get(format_name)
    if sha and "sha256" not in entry:
        entry["sha256"] = sha
    return entry


def sha256_by_format(record: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    raw_sha = record.get("sha256")
    if isinstance(raw_sha, dict):
        mapping.update({str(key): str(value) for key, value in raw_sha.items() if value})
    for artifact in record.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        format_name = str(artifact.get("format") or "")
        sha = artifact.get("sha256")
        if format_name and sha:
            mapping[format_name] = str(sha)
    return mapping


def sha256_by_path(record: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for artifact in record.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        path = str(artifact.get("path") or "")
        sha = artifact.get("sha256")
        if path and sha:
            mapping[path] = str(sha)
    return mapping


def files_from_run_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    by_format = sha256_by_format(record)
    by_path = sha256_by_path(record)
    files: list[dict[str, Any]] = []
    release_paths = record.get("release_paths")
    if not isinstance(release_paths, list):
        raise ReleaseIndexError("successful run record is missing release_paths")
    for value in release_paths:
        entry = blob_file_entry(value, sha256_by_format=by_format, sha256_by_path=by_path)
        if entry:
            files.append(entry)
    if not files:
        raise ReleaseIndexError("successful run record release_paths must not be empty")
    return sorted(files, key=lambda item: (item.get("format", ""), item.get("path", "")))


def canonical_metadata_file(files: list[dict[str, Any]]) -> dict[str, Any] | None:
    for file_entry in files:
        if str(file_entry.get("format") or file_entry.get("role") or "") != "metadata":
            continue
        path = str(file_entry.get("path") or "")
        if file_entry.get("locale") or metadata_locale_from_path(path):
            continue
        return file_entry
    return None


def release_file_for_format(files: list[dict[str, Any]], format_name: str) -> dict[str, Any] | None:
    for file_entry in files:
        if str(file_entry.get("format") or file_entry.get("role") or "") == format_name:
            return file_entry
    return None


def index_status_policy_for_release(files: list[dict[str, Any]], release: str) -> dict[str, str] | None:
    metadata_entry = canonical_metadata_file(files)
    if not metadata_entry or not release_file_for_format(files, "schema") or not release_file_for_format(files, "manifest"):
        return None
    path = str(metadata_entry.get("path") or "")
    try:
        bucket_name, object_name = split_gs_uri(path)
    except ReleaseIndexError:
        return None
    marker = f"/releases/{release}/"
    if marker not in object_name:
        return None
    asset_root = object_name.split(marker, 1)[0]
    return {
        "mode": INDEX_STATUS_MODE,
        "path": f"gs://{bucket_name}/{asset_root}/index-loads/{release}/",
    }


def add_index_status_policy(release_entry: dict[str, Any]) -> dict[str, Any]:
    files = release_entry.get("files")
    release = str(release_entry.get("date") or "")
    if not isinstance(files, list) or not release:
        return release_entry
    policy = index_status_policy_for_release(files, release)
    if policy:
        release_entry["index_load_status"] = INDEX_LOAD_STATUS
        release_entry["index_status_policy"] = policy
    return release_entry


def normalize_rebuild_run_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize historical run records enough for release-index rebuilds."""
    if not isinstance(record, dict):
        return None
    normalized = copy.deepcopy(record)

    if normalized.get("schema_version") != SCHEMA_VERSION:
        if normalized.get("record_version") is None:
            return None
        normalized["schema_version"] = SCHEMA_VERSION

    release_date = str(
        normalized.get("release_date")
        or normalized.get("run_date")
        or normalized.get("target_release_date")
        or ""
    )
    if not release_date:
        return None
    normalized["release_date"] = release_date

    if "row_count" not in normalized and "rows" in normalized:
        normalized["row_count"] = normalized.get("rows")

    if not normalized.get("source_version"):
        source_version = (
            normalized.get("source_filename")
            or normalized.get("source_fingerprint_hash")
            or normalized.get("source")
            or ""
        )
        if source_version:
            normalized["source_version"] = str(source_version)

    return normalized


def run_record_path(record: dict[str, Any], run_record_info: dict[str, Any] | None = None) -> str:
    if run_record_info and run_record_info.get("path"):
        return str(run_record_info["path"])
    nested = record.get("run_record")
    if isinstance(nested, dict) and nested.get("path"):
        return str(nested["path"])
    return ""


def run_entry_from_record(
    record: dict[str, Any],
    *,
    run_record_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseIndexError(f"unsupported run record schema_version: {record.get('schema_version')!r}")
    release_date = str(record.get("release_date") or "")
    if not release_date:
        raise ReleaseIndexError("run record is missing release_date")
    entry: dict[str, Any] = {
        "date": release_date,
        "status": str(record.get("status") or ""),
        "source_version": str(record.get("source_version") or ""),
        "reason": str(record.get("reason") or ""),
        "release_path": str(record.get("release_path") or ""),
        "run_record_path": run_record_path(record, run_record_info),
        "rows": record.get("row_count"),
    }
    return {key: value for key, value in entry.items() if value not in ("", None)}


def release_entry_from_record(
    record: dict[str, Any],
    *,
    run_record_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseIndexError(f"unsupported run record schema_version: {record.get('schema_version')!r}")
    if record.get("status") != "success":
        raise ReleaseIndexError("only successful run records can produce release entries")
    if "row_count" not in record:
        raise ReleaseIndexError("successful run record is missing row_count")
    release_date = str(record.get("release_date") or "")
    if not release_date:
        raise ReleaseIndexError("successful run record is missing release_date")
    files = files_from_run_record(record)
    return add_index_status_policy({
        "date": release_date,
        "release_path": str(record.get("release_path") or ""),
        "files": files,
        "run_record_path": run_record_path(record, run_record_info),
        "rows": record.get("row_count"),
        "source_version": str(record.get("source_version") or ""),
    })


def merge_latest_run(
    index: dict[str, Any],
    run_entry: dict[str, Any],
    *,
    updated_at: str | None = None,
) -> dict[str, Any]:
    merged = coerce_release_index(index, str(index.get("asset_slug") or ""))
    merged["latest_run"] = copy.deepcopy(run_entry)
    merged["updated_at"] = updated_at or utc_now_iso()
    return merged


def merge_successful_release(
    index: dict[str, Any],
    release_entry: dict[str, Any],
    *,
    latest_run: dict[str, Any] | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    merged = coerce_release_index(index, str(index.get("asset_slug") or ""))
    release_date = release_entry.get("date")
    if not release_date:
        raise ReleaseIndexError("release entry is missing date")
    releases = [
        copy.deepcopy(item)
        for item in merged.get("releases", [])
        if item.get("date") != release_date
    ]
    releases.append(copy.deepcopy(release_entry))
    releases.sort(key=lambda item: str(item.get("date") or ""), reverse=True)
    merged["releases"] = releases
    merged["latest_release"] = copy.deepcopy(releases[0]) if releases else None
    merged["latest_run"] = copy.deepcopy(latest_run) if latest_run else run_entry_from_release(release_entry)
    merged["updated_at"] = updated_at or utc_now_iso()
    return merged


def run_entry_from_release(release_entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": release_entry.get("date"),
        "status": "success",
        "source_version": release_entry.get("source_version", ""),
        "release_path": release_entry.get("release_path", ""),
        "run_record_path": release_entry.get("run_record_path", ""),
        "rows": release_entry.get("rows"),
    }


def record_successful_release(
    bucket: Any,
    asset_slug: str,
    record: dict[str, Any],
    *,
    run_record_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    release_entry = release_entry_from_record(record, run_record_info=run_record_info)
    latest_run = run_entry_from_record(record, run_record_info=run_record_info)

    def updater(index: dict[str, Any]) -> dict[str, Any]:
        return merge_successful_release(index, release_entry, latest_run=latest_run)

    return update_release_index(bucket, asset_slug, updater)


def record_latest_run(
    bucket: Any,
    asset_slug: str,
    record: dict[str, Any],
    *,
    run_record_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    latest_run = run_entry_from_record(record, run_record_info=run_record_info)

    def updater(index: dict[str, Any]) -> dict[str, Any]:
        return merge_latest_run(index, latest_run)

    return update_release_index(bucket, asset_slug, updater)


def split_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ReleaseIndexError(f"expected gs:// URI, got: {uri}")
    bucket, separator, name = uri[5:].partition("/")
    if not bucket or not separator or not name:
        raise ReleaseIndexError(f"expected gs:// object URI, got: {uri}")
    return bucket, name


def object_name_from_uri(uri: str) -> str:
    return split_gs_uri(uri)[1]


def release_file_entries_from_blobs(bucket_name: str, blobs: list[Any]) -> list[dict[str, Any]]:
    entries = []
    for blob in sorted(blobs, key=lambda item: item.name):
        path = f"gs://{bucket_name}/{blob.name}"
        format_name = infer_format(path)
        entry = {
            "path": path,
            "format": format_name,
            "generation": int(blob.generation),
            "size": int(blob.size or 0),
        }
        metadata = blob.metadata or {}
        sha256 = metadata.get("sha256") or metadata.get(f"{format_name}_sha256")
        if sha256:
            entry["sha256"] = str(sha256)
        entries.append(entry)
    return entries


def release_date_from_object_name(name: str) -> str:
    parts = name.split("/")
    if "releases" not in parts:
        return ""
    index = parts.index("releases")
    if index + 1 >= len(parts):
        return ""
    return parts[index + 1]


def build_index_from_records(
    *,
    asset_slug: str,
    records: list[tuple[dict[str, Any], dict[str, Any] | None]],
) -> dict[str, Any]:
    index = empty_release_index(asset_slug)
    latest_run: dict[str, Any] | None = None
    for record, run_record_info in sorted(records, key=lambda item: str(item[0].get("release_date") or "")):
        run_entry = run_entry_from_record(record, run_record_info=run_record_info)
        if run_entry and (not latest_run or str(run_entry.get("date", "")) >= str(latest_run.get("date", ""))):
            latest_run = run_entry
        if record.get("status") == "success":
            index = merge_successful_release(
                index,
                release_entry_from_record(record, run_record_info=run_record_info),
                latest_run=run_entry,
            )
    if latest_run:
        index = merge_latest_run(index, latest_run)
    return index


def asset_root_from_catalog_row(row: dict[str, str]) -> tuple[str, str]:
    canonical_path = row.get("canonical_path", "")
    bucket_name, object_name = split_gs_uri(canonical_path)
    if "/latest/" not in object_name:
        raise ReleaseIndexError(f"catalog canonical_path must contain /latest/: {canonical_path}")
    return bucket_name, object_name.split("/latest/", 1)[0]


def canonical_format_from_catalog_row(row: dict[str, str]) -> str:
    return str(row.get("canonical_format") or infer_format(row.get("canonical_path", "")))


def has_canonical_release_file(entries: list[dict[str, Any]], canonical_format: str) -> bool:
    return any(str(entry.get("format") or "") == canonical_format for entry in entries)


def rebuild_index_from_bucket(bucket: Any, row: dict[str, str]) -> dict[str, Any]:
    asset_slug = row["asset_slug"]
    bucket_name, asset_root = asset_root_from_catalog_row(row)
    if bucket_name != bucket.name:
        raise ReleaseIndexError(f"catalog bucket {bucket_name!r} does not match client bucket {bucket.name!r}")
    canonical_format = canonical_format_from_catalog_row(row)

    releases_by_date: dict[str, list[Any]] = {}
    for blob in bucket.list_blobs(prefix=f"{asset_root}/releases/"):
        release_date = release_date_from_object_name(blob.name)
        if release_date:
            releases_by_date.setdefault(release_date, []).append(blob)

    records: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    successful_run_dates: set[str] = set()
    for blob in bucket.list_blobs(prefix=f"{asset_root}/runs/"):
        if not blob.name.endswith(".json"):
            continue
        try:
            record = json.loads(blob.download_as_text())
        except (json.JSONDecodeError, NotFound):
            continue
        record = normalize_rebuild_run_record(record)
        if record is None:
            continue
        run_date = str(record.get("release_date") or "")
        if not run_date:
            continue
        if record.get("status") == "success":
            successful_run_dates.add(run_date)
            release_entries = release_file_entries_from_blobs(
                bucket.name,
                releases_by_date.get(run_date, []),
            )
            release_entry_by_path = {entry["path"]: entry for entry in release_entries}
            if record.get("release_paths"):
                record["release_paths"] = [
                    {**file_entry, **release_entry_by_path.get(file_entry["path"], {})}
                    for file_entry in files_from_run_record(record)
                    if file_entry.get("path")
                ]
            else:
                record["release_paths"] = release_entries
        records.append(
            (
                record,
                {
                    "path": f"gs://{bucket.name}/{blob.name}",
                    "generation": int(blob.generation),
                    "size": int(blob.size or 0),
                },
            )
        )
    for release_date, blobs in releases_by_date.items():
        if release_date in successful_run_dates:
            continue
        release_entries = release_file_entries_from_blobs(bucket.name, blobs)
        if canonical_format and not has_canonical_release_file(release_entries, canonical_format):
            continue
        records.append(
            (
                {
                    "schema_version": SCHEMA_VERSION,
                    "release_date": release_date,
                    "status": "success",
                    "release_path": f"gs://{bucket.name}/{asset_root}/releases/{release_date}/",
                    "release_paths": release_entries,
                    "row_count": None,
                },
                None,
            )
        )
    return build_index_from_records(asset_slug=asset_slug, records=records)
