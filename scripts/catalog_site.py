#!/usr/bin/env python3
"""Build the static shared-datasets catalog web preview."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import catalog_csv


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_SITE_PREFIX = "_catalog/web"
DEFAULT_PMTILES_CDN_BASE_URL = "https://tiles.skytruth.org/pmtiles"
APPROVED_FORMATS = {"fgb", "cog", "zarr", "pmtiles", "geojson", "ndgeojson", "csv"}
SYNTHETIC_CANONICAL_FORMAT_PREFERENCE = ("fgb", "cog", "zarr", "csv", "geojson", "ndgeojson")
ACCESS_TIERS = {"public", "private", "internal"}
LIFECYCLE_STATUSES = {"active", "deprecated", "superseded", "retired"}
REQUIRED_FIELDS = [
    "asset_slug",
    "title",
    "category",
    "subcategory",
    "status",
    "access_tier",
    "owner",
    "update_cadence",
    "canonical_path",
    "canonical_format",
    "available_formats",
    "metadata_paths",
    "source",
    "license",
    "citation",
]
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\n.*?\n---\n?(.*)\Z", re.DOTALL)
FRONTMATTER_BLOCK_RE = re.compile(r"\A---\n(?P<yaml>.*?)\n---\n?(?P<body>.*)\Z", re.DOTALL)
SECTION_RE = re.compile(r"(?ms)^## (?P<title>[^\n]+)\n(?P<body>.*?)(?=^## |\Z)")
MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")
MARKDOWN_TABLE_RE = re.compile(r"^\s*\|.*\|\s*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REFERENTIAL_TERMS_RE = re.compile(r"\bsee\b.{0,80}\bterms\b")
IDENTITY_CANDIDATE_STATUSES = {"unique", "non_unique", "unknown", "not_applicable"}
FEATURE_METADATA_STORAGE = "metadata_sidecar_v1"
FEATURE_METADATA_FEATURE_ID_COLUMN = "feature_id"
FEATURE_METADATA_GEOMETRY_HASH_COLUMN = "geometry_hash"
FEATURE_METADATA_PROPERTIES_HASH_COLUMN = "properties_hash"
COLORIZER_METADATA_SCHEMA_VERSION = 1


class CatalogSiteError(ValueError):
    """Raised when catalog site inputs are invalid."""


@dataclass(frozen=True)
class CatalogVersion:
    date: str
    canonical_path: str
    public_url: str
    pmtiles_path: str | None
    pmtiles_url: str | None
    available_formats: list[str]
    files: list[dict[str, Any]] = field(default_factory=list)
    source_version: str = ""
    rows: int | None = None
    release_path: str = ""
    run_record_path: str = ""
    canonical_sha256: str = ""
    pmtiles_sha256: str = ""


@dataclass(frozen=True)
class CatalogAsset:
    slug: str
    title: str
    category: str
    subcategory: str
    status: str
    lifecycle_reason: str
    lifecycle_date: str
    successor_asset_slug: str
    consumer_guidance: str
    access_tier: str
    owner: str
    update_cadence: str
    canonical_path: str
    canonical_format: str
    available_formats: list[str]
    metadata_paths: list[str]
    files: list[dict[str, Any]]
    feature_identity: dict[str, Any] | None
    has_pmtiles: bool
    has_geojson: bool
    has_csv: bool
    last_updated: str
    latest_release: dict[str, Any] | None
    latest_run: dict[str, Any] | None
    release_index_updated_at: str
    source: str
    license: str
    citation: str
    notes: str
    bounds: list[float] | None
    geometry_type: str | None
    row_count: int | None
    data_profile: dict[str, Any] | None
    search_fields: list[dict[str, Any]]
    feature_metadata: dict[str, Any] | None
    colorizer_metadata: dict[str, Any]
    source_url: str | None
    public_url: str
    pmtiles_path: str | None
    pmtiles_url: str | None
    canonical_sha256: str
    pmtiles_sha256: str
    docs_path: str
    docs_url: str
    release_index_url: str
    description: str
    license_flags: list[str]
    versions: list[CatalogVersion]
    sort_key: str


def load_categories(path: Path) -> dict[str, dict[str, str]]:
    payload = yaml.safe_load(path.read_text()) or {}
    categories = payload.get("categories") or {}
    if not isinstance(categories, dict):
        raise CatalogSiteError(f"{path}: categories must be a mapping")
    result: dict[str, dict[str, str]] = {}
    for category, data in categories.items():
        subcategories = (data or {}).get("subcategories") or {}
        if not isinstance(subcategories, dict):
            raise CatalogSiteError(f"{path}: category {category!r} subcategories must be a mapping")
        result[str(category)] = {str(key): str(value) for key, value in subcategories.items()}
    return result


def load_catalog_rows(path: Path) -> list[dict[str, str]]:
    try:
        return catalog_csv.read_catalog_rows(path)
    except catalog_csv.CatalogCsvError as exc:
        raise CatalogSiteError(str(exc)) from exc


def split_semicolon(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(";") if part.strip()]


def split_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise CatalogSiteError(f"expected gs:// URI, got {uri!r}")
    rest = uri[5:]
    bucket, separator, object_name = rest.partition("/")
    if not bucket or not separator or not object_name:
        raise CatalogSiteError(f"expected gs:// object URI, got {uri!r}")
    return bucket, object_name


def gs_to_https(uri: str) -> str:
    bucket, object_name = split_gs_uri(uri)
    return f"https://storage.googleapis.com/{bucket}/{quote(object_name)}"


def pmtiles_cdn_url(slug: str, access_tier: str) -> str:
    return f"{DEFAULT_PMTILES_CDN_BASE_URL}/{quote(access_tier)}/{quote(slug)}.pmtiles"


def latest_root(uri: str) -> str:
    if "/latest/" not in uri:
        raise CatalogSiteError(f"canonical path must include /latest/: {uri}")
    return uri.split("/latest/", 1)[0] + "/latest"


def path_for_format(canonical_path: str, slug: str, format_name: str) -> str:
    if format_name == "zarr":
        raise CatalogSiteError("cannot infer companion Zarr paths from the static catalog")
    extensions = {
        "fgb": ".fgb",
        "pmtiles": ".pmtiles",
        "geojson": ".geojson",
        "ndgeojson": ".ndgeojson",
        "csv": ".csv",
        "cog": ".tif",
    }
    extension = extensions.get(format_name)
    if extension is None:
        raise CatalogSiteError(f"unsupported format {format_name!r}")
    return f"{latest_root(canonical_path)}/{slug}{extension}"


def strip_markdown(text: str) -> str:
    text = MARKDOWN_LINK_RE.sub(r"\1", text)
    text = INLINE_CODE_RE.sub(r"\1", text)
    text = re.sub(r"[*_>#]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def read_doc_metadata(path: Path) -> dict[str, Any]:
    raw = path.read_text()
    match = FRONTMATTER_BLOCK_RE.match(raw)
    if not match:
        return {}
    payload = yaml.safe_load(match.group("yaml")) or {}
    if not isinstance(payload, dict):
        raise CatalogSiteError(f"{path}: frontmatter must be a mapping")
    return payload


def optional_text(metadata: dict[str, Any], key: str, *, doc_path: Path) -> str | None:
    if key not in metadata or metadata[key] is None:
        return None
    value = str(metadata[key]).strip()
    return value or None


def optional_int(metadata: dict[str, Any], key: str, *, doc_path: Path) -> int | None:
    if key not in metadata or metadata[key] is None or metadata[key] == "":
        return None
    try:
        value = int(metadata[key])
    except (TypeError, ValueError) as error:
        raise CatalogSiteError(f"{doc_path}: {key} must be an integer") from error
    if value < 0:
        raise CatalogSiteError(f"{doc_path}: {key} must be non-negative")
    return value


def optional_bounds(metadata: dict[str, Any], *, doc_path: Path) -> list[float] | None:
    if "bounds" not in metadata or metadata["bounds"] in (None, ""):
        return None
    raw_bounds = metadata["bounds"]
    if not isinstance(raw_bounds, (list, tuple)) or len(raw_bounds) != 4:
        raise CatalogSiteError(f"{doc_path}: bounds must be [min_lon, min_lat, max_lon, max_lat]")
    try:
        bounds = [float(value) for value in raw_bounds]
    except (TypeError, ValueError) as error:
        raise CatalogSiteError(f"{doc_path}: bounds values must be numbers") from error
    min_lon, min_lat, max_lon, max_lat = bounds
    if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180 and -90 <= min_lat <= 90 and -90 <= max_lat <= 90):
        raise CatalogSiteError(f"{doc_path}: bounds must be valid WGS84 longitude/latitude values")
    if min_lon > max_lon or min_lat > max_lat:
        raise CatalogSiteError(f"{doc_path}: bounds minimums must not exceed maximums")
    return bounds


def profile_int(
    value: Any,
    *,
    label: str,
    doc_path: Path,
    required: bool = False,
    row_count: int | None = None,
) -> int | None:
    if value in (None, ""):
        if required:
            raise CatalogSiteError(f"{doc_path}: {label} is required")
        return None
    try:
        numeric = int(value)
    except (TypeError, ValueError) as error:
        raise CatalogSiteError(f"{doc_path}: {label} must be an integer") from error
    if numeric < 0:
        raise CatalogSiteError(f"{doc_path}: {label} must be non-negative")
    if row_count is not None and numeric > row_count:
        raise CatalogSiteError(f"{doc_path}: {label} must not exceed row_count")
    return numeric


def normalize_identity_candidate(
    candidate: Any,
    *,
    index: int,
    doc_path: Path,
    row_count: int | None,
) -> dict[str, Any]:
    context = f"data_profile.identity_candidates[{index}]"
    if not isinstance(candidate, dict):
        raise CatalogSiteError(f"{doc_path}: {context} must be a mapping")
    field = str(candidate.get("field") or "").strip()
    if not field:
        raise CatalogSiteError(f"{doc_path}: {context}.field is required")
    status = str(candidate.get("status") or "").strip().lower().replace("-", "_")
    if status not in IDENTITY_CANDIDATE_STATUSES:
        allowed = ", ".join(sorted(IDENTITY_CANDIDATE_STATUSES))
        raise CatalogSiteError(f"{doc_path}: {context}.status must be one of: {allowed}")
    distinct_values = profile_int(
        candidate.get("distinct_values"),
        label=f"{context}.distinct_values",
        doc_path=doc_path,
        required=True,
        row_count=row_count,
    )
    duplicate_value_count = profile_int(
        candidate.get("duplicate_value_count"),
        label=f"{context}.duplicate_value_count",
        doc_path=doc_path,
        required=True,
        row_count=row_count,
    )
    duplicate_row_count = profile_int(
        candidate.get("duplicate_row_count"),
        label=f"{context}.duplicate_row_count",
        doc_path=doc_path,
        required=True,
        row_count=row_count,
    )
    if status == "unique" and (duplicate_value_count or duplicate_row_count):
        raise CatalogSiteError(f"{doc_path}: {context} is marked unique but has duplicate counts")
    normalized = {
        "field": field,
        "distinct_values": distinct_values,
        "duplicate_value_count": duplicate_value_count,
        "duplicate_row_count": duplicate_row_count,
        "status": status,
    }
    notes = str(candidate.get("notes") or "").strip()
    if notes:
        normalized["notes"] = notes
    return normalized


def normalize_most_unique_field(
    value: Any,
    *,
    doc_path: Path,
    row_count: int | None,
) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    context = "data_profile.most_unique_field"
    if not isinstance(value, dict):
        raise CatalogSiteError(f"{doc_path}: {context} must be a mapping")
    field = str(value.get("field") or "").strip()
    if not field:
        raise CatalogSiteError(f"{doc_path}: {context}.field is required")
    distinct_values = profile_int(
        value.get("distinct_values"),
        label=f"{context}.distinct_values",
        doc_path=doc_path,
        required=True,
        row_count=row_count,
    )
    normalized: dict[str, Any] = {"field": field, "distinct_values": distinct_values}
    notes = str(value.get("notes") or "").strip()
    if notes:
        normalized["notes"] = notes
    return normalized


def normalize_search_field(
    value: Any,
    *,
    index: int,
    doc_path: Path,
    row_count: int | None,
) -> dict[str, Any]:
    context = f"search_fields[{index}]"
    if isinstance(value, str):
        field = value.strip()
        if not field:
            raise CatalogSiteError(f"{doc_path}: {context} must be non-empty")
        return {"field": field}
    if not isinstance(value, dict):
        raise CatalogSiteError(f"{doc_path}: {context} must be a mapping or string")
    field = str(value.get("field") or "").strip()
    if not field:
        raise CatalogSiteError(f"{doc_path}: {context}.field is required")
    normalized: dict[str, Any] = {"field": field}
    distinct_values = profile_int(
        value.get("distinct_values"),
        label=f"{context}.distinct_values",
        doc_path=doc_path,
        row_count=row_count,
    )
    if distinct_values is not None:
        normalized["distinct_values"] = distinct_values
    notes = str(value.get("notes") or "").strip()
    if notes:
        normalized["notes"] = notes
    return normalized


def optional_search_fields(metadata: dict[str, Any], *, row_count: int | None, doc_path: Path) -> list[dict[str, Any]]:
    raw_fields = metadata.get("search_fields")
    if raw_fields in (None, ""):
        return []
    if not isinstance(raw_fields, list):
        raise CatalogSiteError(f"{doc_path}: search_fields must be a list")
    return [
        normalize_search_field(value, index=index, doc_path=doc_path, row_count=row_count)
        for index, value in enumerate(raw_fields, start=1)
    ]


def optional_feature_identity(metadata: dict[str, Any], *, doc_path: Path) -> dict[str, Any] | None:
    value = metadata.get("feature_identity")
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise CatalogSiteError(f"{doc_path}: feature_identity must be a mapping")
    strategy = str(value.get("strategy") or "").strip()
    if strategy not in {"source_field", "generated_sequence_source_fields", "generated_sequence_content_hash"}:
        raise CatalogSiteError(f"{doc_path}: feature_identity.strategy is unsupported")
    source_fields = value.get("source_fields", [])
    if not isinstance(source_fields, list):
        raise CatalogSiteError(f"{doc_path}: feature_identity.source_fields must be a list")
    normalized: dict[str, Any] = {
        "strategy": strategy,
        "source_fields": [str(field).strip() for field in source_fields if str(field).strip()],
    }
    if strategy == "source_field" and not normalized["source_fields"]:
        raise CatalogSiteError(f"{doc_path}: feature_identity.source_fields is required for source_field strategy")
    if strategy == "generated_sequence_source_fields" and not (1 <= len(normalized["source_fields"]) <= 2):
        raise CatalogSiteError(
            f"{doc_path}: feature_identity.source_fields must contain one or two fields for generated_sequence_source_fields"
        )
    if strategy.startswith("generated_sequence"):
        generated_type = str(value.get("generated_id_type") or "").strip()
        if generated_type != "monotonic_integer_string":
            raise CatalogSiteError(f"{doc_path}: feature_identity.generated_id_type must be monotonic_integer_string")
        normalized["generated_id_type"] = generated_type
        assignment_key = value.get("assignment_key", [])
        if not isinstance(assignment_key, list):
            raise CatalogSiteError(f"{doc_path}: feature_identity.assignment_key must be a list")
        normalized["assignment_key"] = [str(part).strip() for part in assignment_key if str(part).strip()]
        if strategy == "generated_sequence_source_fields" and normalized["assignment_key"] != normalized["source_fields"]:
            raise CatalogSiteError(f"{doc_path}: feature_identity.assignment_key must match source_fields")
        if strategy == "generated_sequence_content_hash" and normalized["assignment_key"] != ["geometry_hash", "properties_hash"]:
            raise CatalogSiteError(f"{doc_path}: feature_identity.assignment_key must be geometry_hash and properties_hash")
    return normalized


def optional_feature_metadata(metadata: dict[str, Any], *, asset_slug: str, doc_path: Path) -> dict[str, Any] | None:
    value = metadata.get("feature_metadata")
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise CatalogSiteError(f"{doc_path}: feature_metadata must be a mapping")
    expected = {
        "storage": FEATURE_METADATA_STORAGE,
        "feature_id_column": FEATURE_METADATA_FEATURE_ID_COLUMN,
        "geometry_hash_column": FEATURE_METADATA_GEOMETRY_HASH_COLUMN,
        "properties_hash_column": FEATURE_METADATA_PROPERTIES_HASH_COLUMN,
        "sidecar_file": f"latest/{asset_slug}.metadata.ndjson.gz",
        "schema_file": f"latest/{asset_slug}.schema.json",
        "manifest_file": f"latest/{asset_slug}.manifest.json",
    }
    normalized: dict[str, Any] = {}
    for key, expected_value in expected.items():
        actual = str(value.get(key) or "").strip()
        if actual != expected_value:
            raise CatalogSiteError(f"{doc_path}: feature_metadata.{key} must be {expected_value!r}")
        normalized[key] = actual
    if value.get("provenance_default") is not True:
        raise CatalogSiteError(f"{doc_path}: feature_metadata.provenance_default must be true")
    normalized["provenance_default"] = True
    return normalized


def colorizer_metadata_for_asset(
    *,
    has_pmtiles: bool,
    feature_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    if not has_pmtiles:
        return {
            "schema_version": COLORIZER_METADATA_SCHEMA_VERSION,
            "source": "none",
            "field_source": "none",
        }
    if feature_metadata:
        return {
            "schema_version": COLORIZER_METADATA_SCHEMA_VERSION,
            "source": "metadata_sidecar_schema",
            "field_source": "feature_metadata.schema_file",
            "schema_file": str(feature_metadata["schema_file"]),
            "feature_id_property": FEATURE_METADATA_FEATURE_ID_COLUMN,
        }
    return {
        "schema_version": COLORIZER_METADATA_SCHEMA_VERSION,
        "source": "pmtiles_vector_layers",
        "field_source": "pmtiles.vector_layers.fields",
    }


def optional_data_profile(metadata: dict[str, Any], *, row_count: int | None, doc_path: Path) -> dict[str, Any] | None:
    raw_profile = metadata.get("data_profile")
    if raw_profile in (None, ""):
        return None
    if not isinstance(raw_profile, dict):
        raise CatalogSiteError(f"{doc_path}: data_profile must be a mapping")

    normalized: dict[str, Any] = {}
    field_count = profile_int(
        raw_profile.get("field_count"),
        label="data_profile.field_count",
        doc_path=doc_path,
        required=True,
    )
    normalized["field_count"] = field_count

    raw_candidates = raw_profile.get("identity_candidates", [])
    if raw_candidates in (None, ""):
        raw_candidates = []
    if not isinstance(raw_candidates, list):
        raise CatalogSiteError(f"{doc_path}: data_profile.identity_candidates must be a list")
    candidates = [
        normalize_identity_candidate(candidate, index=index, doc_path=doc_path, row_count=row_count)
        for index, candidate in enumerate(raw_candidates, start=1)
    ]
    if candidates or "identity_candidates" in raw_profile:
        normalized["identity_candidates"] = candidates

    most_unique = normalize_most_unique_field(
        raw_profile.get("most_unique_field"),
        doc_path=doc_path,
        row_count=row_count,
    )
    if most_unique:
        normalized["most_unique_field"] = most_unique

    notes = str(raw_profile.get("notes") or "").strip()
    if notes:
        normalized["notes"] = notes

    return normalized or None


def frontmatter_license_flags(metadata: dict[str, Any], *, doc_path: Path) -> list[str]:
    if "license_flags" not in metadata or metadata["license_flags"] in (None, ""):
        return []
    raw_flags = metadata["license_flags"]
    if isinstance(raw_flags, str):
        flags = split_semicolon(raw_flags) if ";" in raw_flags else [part.strip() for part in raw_flags.split(",") if part.strip()]
    elif isinstance(raw_flags, (list, tuple)):
        flags = [str(flag).strip() for flag in raw_flags if str(flag).strip()]
    else:
        raise CatalogSiteError(f"{doc_path}: license_flags must be a list or delimited string")
    return flags


def merged_license_flags(license_text: str, metadata: dict[str, Any], *, doc_path: Path) -> list[str]:
    flags: list[str] = []
    for flag in [*license_flags(license_text), *frontmatter_license_flags(metadata, doc_path=doc_path)]:
        if flag not in flags:
            flags.append(flag)
    return flags


def load_release_index(release_index_dir: Path | None, slug: str) -> dict[str, Any] | None:
    if release_index_dir is None:
        return None
    path = release_index_dir / f"{slug}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise CatalogSiteError(f"{path}: invalid release index JSON") from error
    if not isinstance(payload, dict):
        raise CatalogSiteError(f"{path}: release index must be a JSON object")
    if payload.get("schema_version") != 1:
        raise CatalogSiteError(f"{path}: release index schema_version must be 1")
    if payload.get("asset_slug") != slug:
        raise CatalogSiteError(f"{path}: release index asset_slug does not match {slug!r}")
    return payload


def load_release_index_path(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise CatalogSiteError(f"{path}: invalid release index JSON") from error
    if not isinstance(payload, dict):
        raise CatalogSiteError(f"{path}: release index must be a JSON object")
    if payload.get("schema_version") != 1:
        raise CatalogSiteError(f"{path}: release index schema_version must be 1")
    slug = str(payload.get("asset_slug") or "").strip()
    if not SLUG_RE.fullmatch(slug):
        raise CatalogSiteError(f"{path}: release index asset_slug must be lowercase kebab-case")
    if path.stem != slug:
        raise CatalogSiteError(f"{path}: release index filename must match asset_slug {slug!r}")
    return payload


def load_all_release_indexes(release_index_dir: Path | None) -> list[dict[str, Any]]:
    if release_index_dir is None or not release_index_dir.exists():
        return []
    return [load_release_index_path(path) for path in sorted(release_index_dir.glob("*.json"))]


def release_file_path(file_entry: Any) -> str:
    if not isinstance(file_entry, dict):
        return ""
    path = str(file_entry.get("path") or "").strip()
    return path if path.startswith("gs://") else ""


def release_file_sha256(file_entry: Any) -> str:
    if not isinstance(file_entry, dict):
        return ""
    value = str(file_entry.get("sha256") or "").strip()
    return value if re.fullmatch(r"[a-fA-F0-9]{64}", value) else ""


def release_files(files: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for file_entry in files:
        if not isinstance(file_entry, dict):
            continue
        path = release_file_path(file_entry)
        if not path:
            continue
        normalized_entry = {str(key): value for key, value in file_entry.items() if str(key)}
        normalized_entry["path"] = path
        normalized.append(normalized_entry)
    return normalized


def doc_latest_files(
    metadata: dict[str, Any],
    *,
    canonical_path: str,
    feature_metadata: dict[str, Any] | None,
    doc_path: Path,
) -> list[dict[str, Any]]:
    bucket, object_name = split_gs_uri(canonical_path)
    root_prefix = object_name.rsplit("/latest/", 1)[0]
    if not root_prefix or root_prefix == object_name:
        raise CatalogSiteError(f"{doc_path}: canonical_file must be under latest/")

    raw_files = metadata.get("files") or []
    if not isinstance(raw_files, list):
        raise CatalogSiteError(f"{doc_path}: files must be a list")

    normalized: list[dict[str, Any]] = []
    for file_entry in raw_files:
        if not isinstance(file_entry, dict):
            raise CatalogSiteError(f"{doc_path}: files entries must be mappings")
        path = str(file_entry.get("path") or "").strip()
        if path.startswith("gs://"):
            file_bucket, file_object = split_gs_uri(path)
            if file_bucket != bucket or not file_object.startswith(f"{root_prefix}/latest/"):
                continue
            full_path = path
        elif path.startswith("latest/"):
            full_path = f"gs://{bucket}/{root_prefix}/{path}"
        else:
            continue
        normalized_entry = {str(key): value for key, value in file_entry.items() if str(key)}
        normalized_entry["path"] = full_path
        normalized.append(normalized_entry)

    if feature_metadata:
        required = (
            ("sidecar_file", "metadata", "metadata"),
            ("schema_file", "schema", "metadata"),
            ("manifest_file", "manifest", "metadata"),
        )
        entries_by_path = {entry["path"]: entry for entry in normalized}
        for key, format_name, role in required:
            relative_path = str(feature_metadata[key]).strip()
            full_path = f"gs://{bucket}/{root_prefix}/{relative_path}"
            existing_entry = entries_by_path.get(full_path)
            if existing_entry is not None:
                existing_entry["format"] = format_name
                existing_entry["role"] = role
            else:
                new_entry = {"path": full_path, "format": format_name, "role": role}
                normalized.append(new_entry)
                entries_by_path[full_path] = new_entry

    return normalized


def basename(path: str) -> str:
    return next(reversed([part for part in path.split("/") if part]), "")


def release_file_for_format(files: list[Any], format_name: str, preferred_path: str = "") -> dict[str, Any] | None:
    preferred_name = basename(preferred_path)
    if preferred_name:
        for file_entry in files:
            if not isinstance(file_entry, dict):
                continue
            if str(file_entry.get("format") or "").strip() == format_name and release_file_path(file_entry).endswith(
                f"/{preferred_name}"
            ):
                return file_entry
    for file_entry in files:
        if isinstance(file_entry, dict) and str(file_entry.get("format") or "").strip() == format_name:
            return file_entry
    return None


def release_index_latest_release(release_index: dict[str, Any]) -> dict[str, Any]:
    latest_release = release_index.get("latest_release")
    if not isinstance(latest_release, dict):
        raise CatalogSiteError(f"{release_index.get('asset_slug')}: release index latest_release must be an object")
    latest_date = str(latest_release.get("date") or "").strip()
    if not DATE_RE.fullmatch(latest_date):
        raise CatalogSiteError(f"{release_index.get('asset_slug')}: release index latest_release.date must be YYYY-MM-DD")
    return latest_release


def infer_canonical_format(files: list[Any], *, asset_slug: str) -> str:
    file_formats = {
        str(file_entry.get("format") or "").strip()
        for file_entry in files
        if isinstance(file_entry, dict) and str(file_entry.get("format") or "").strip()
    }
    for format_name in SYNTHETIC_CANONICAL_FORMAT_PREFERENCE:
        if format_name in file_formats:
            return format_name
    raise CatalogSiteError(f"{asset_slug}: release index latest release does not include an approved canonical file")


def release_index_asset_root(canonical_path: str, release_date: str, asset_slug: str) -> tuple[str, str]:
    _bucket, object_name = split_gs_uri(canonical_path)
    marker = f"/{asset_slug}/releases/{release_date}/"
    if marker not in object_name:
        raise CatalogSiteError(f"{asset_slug}: canonical preview path must include {marker}")
    root = object_name.split(marker, 1)[0]
    parts = root.split("/")
    if len(parts) != 2:
        raise CatalogSiteError(f"{asset_slug}: preview release path must start with category/subcategory")
    return parts[0], parts[1]


def humanize_slug(slug: str) -> str:
    return " ".join(part.upper() if len(part) <= 3 else part.capitalize() for part in slug.split("-"))


def release_formats_from_files(available_formats: list[str], files: list[Any]) -> list[str]:
    file_formats = {
        str(file_entry.get("format") or "").strip()
        for file_entry in files
        if isinstance(file_entry, dict) and str(file_entry.get("format") or "").strip()
    }
    return [format_name for format_name in available_formats if format_name in file_formats]


def release_versions_from_index(
    *,
    release_index: dict[str, Any] | None,
    canonical_path: str,
    canonical_format: str,
    available_formats: list[str],
    pmtiles_path: str | None,
) -> list[CatalogVersion]:
    if not release_index:
        return []
    releases = release_index.get("releases") or []
    if not isinstance(releases, list):
        raise CatalogSiteError(f"release index for {release_index.get('asset_slug')!r}: releases must be a list")

    versions: list[CatalogVersion] = []
    seen_dates: set[str] = set()
    for release in releases:
        if not isinstance(release, dict):
            raise CatalogSiteError(f"release index for {release_index.get('asset_slug')!r}: release entries must be objects")
        date = str(release.get("date") or "").strip()
        if not DATE_RE.fullmatch(date):
            raise CatalogSiteError(f"release index for {release_index.get('asset_slug')!r}: release date must be YYYY-MM-DD")
        if date in seen_dates:
            raise CatalogSiteError(f"release index for {release_index.get('asset_slug')!r}: duplicate release date {date}")
        files = release.get("files") or []
        if not isinstance(files, list):
            raise CatalogSiteError(f"release index for {release_index.get('asset_slug')!r}: release files must be a list")
        canonical_file = release_file_for_format(files, canonical_format, canonical_path)
        release_canonical_path = release_file_path(canonical_file)
        if not release_canonical_path:
            raise CatalogSiteError(
                f"release index for {release_index.get('asset_slug')!r}: release {date} is missing canonical {canonical_format} file"
            )
        release_pmtiles_file = release_file_for_format(files, "pmtiles", pmtiles_path or "")
        release_pmtiles_path = release_file_path(release_pmtiles_file)
        row_count = release.get("rows")
        if not isinstance(row_count, int):
            row_count = None
        normalized_files = release_files(files)
        seen_dates.add(date)
        versions.append(
            CatalogVersion(
                date=date,
                canonical_path=release_canonical_path,
                public_url=gs_to_https(release_canonical_path),
                pmtiles_path=release_pmtiles_path or None,
                pmtiles_url=gs_to_https(release_pmtiles_path) if release_pmtiles_path else None,
                available_formats=release_formats_from_files(available_formats, files),
                files=normalized_files,
                source_version=str(release.get("source_version") or ""),
                rows=row_count,
                release_path=str(release.get("release_path") or ""),
                run_record_path=str(release.get("run_record_path") or ""),
                canonical_sha256=release_file_sha256(canonical_file),
                pmtiles_sha256=release_file_sha256(release_pmtiles_file),
            )
        )
    return sorted(versions, key=lambda version: version.date, reverse=True)


def read_description(path: Path) -> str:
    raw = path.read_text()
    match = FRONTMATTER_RE.match(raw)
    body = match.group(1) if match else raw
    for section in SECTION_RE.finditer(body):
        if section.group("title").strip().lower() != "what this is":
            continue
        paragraph: list[str] = []
        for line in section.group("body").splitlines():
            stripped = line.strip()
            if not stripped:
                if paragraph:
                    break
                continue
            if MARKDOWN_TABLE_RE.match(stripped):
                continue
            if stripped.startswith("<!--") or stripped.startswith("|---"):
                continue
            paragraph.append(stripped)
        description = strip_markdown(" ".join(paragraph))
        if description:
            return description
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "-", "<!--", "|")):
            return strip_markdown(stripped)
    return ""


def license_flags(license_text: str) -> list[str]:
    lowered = license_text.lower()
    flags: list[str] = []
    if "non-commercial" in lowered or "noncommercial" in lowered or "nc" in lowered:
        flags.append("non-commercial")
    if "no redistribution" in lowered or "permission" in lowered:
        flags.append("redistribution-limited")
    if "no explicit license" in lowered or REFERENTIAL_TERMS_RE.search(lowered):
        flags.append("confirm-license")
    if "public domain" in lowered or "public u.s. government work" in lowered:
        flags.append("open")
    return flags


def validate_row(
    *,
    row: dict[str, str],
    row_number: int,
    categories: dict[str, dict[str, str]],
    seen: set[str],
    docs_dir: Path,
) -> None:
    prefix = f"catalog row {row_number}"
    for field_name in REQUIRED_FIELDS:
        if not (row.get(field_name) or "").strip():
            raise CatalogSiteError(f"{prefix}: missing required field {field_name!r}")
    slug = row["asset_slug"].strip()
    if slug in seen:
        raise CatalogSiteError(f"{prefix}: duplicate asset_slug {slug!r}")
    if not SLUG_RE.fullmatch(slug):
        raise CatalogSiteError(f"{prefix}: asset_slug must be lowercase kebab-case")
    status = row["status"].strip()
    if status not in LIFECYCLE_STATUSES:
        allowed = ", ".join(sorted(LIFECYCLE_STATUSES))
        raise CatalogSiteError(f"{prefix}: status must be one of: {allowed}")
    if status != "active":
        for field_name in ("lifecycle_reason", "lifecycle_date", "consumer_guidance"):
            if not (row.get(field_name) or "").strip():
                raise CatalogSiteError(f"{prefix}: non-active assets require {field_name!r}")
        lifecycle_date = (row.get("lifecycle_date") or "").strip()
        if not DATE_RE.fullmatch(lifecycle_date):
            raise CatalogSiteError(f"{prefix}: lifecycle_date must be YYYY-MM-DD")
    successor = (row.get("successor_asset_slug") or "").strip()
    if status == "superseded" and not successor:
        raise CatalogSiteError(f"{prefix}: superseded assets require successor_asset_slug")
    if successor and (not SLUG_RE.fullmatch(successor) or successor == slug):
        raise CatalogSiteError(f"{prefix}: successor_asset_slug must be a different lowercase kebab-case asset slug")
    access_tier = row["access_tier"].strip()
    if access_tier not in ACCESS_TIERS:
        raise CatalogSiteError(f"{prefix}: access_tier must be one of: {', '.join(sorted(ACCESS_TIERS))}")
    category = row["category"].strip()
    subcategory = row["subcategory"].strip()
    if category not in categories:
        raise CatalogSiteError(f"{prefix}: unknown category {category!r}")
    if subcategory not in categories[category]:
        raise CatalogSiteError(f"{prefix}: unknown subcategory {category}/{subcategory}")
    canonical_format = row["canonical_format"].strip()
    if canonical_format not in APPROVED_FORMATS:
        raise CatalogSiteError(f"{prefix}: unsupported canonical_format {canonical_format!r}")
    formats = split_semicolon(row.get("available_formats", ""))
    if canonical_format not in formats:
        raise CatalogSiteError(f"{prefix}: available_formats must include canonical_format")
    unsupported = sorted(set(formats) - APPROVED_FORMATS)
    if unsupported:
        raise CatalogSiteError(f"{prefix}: unsupported available format(s): {', '.join(unsupported)}")
    bucket, object_name = split_gs_uri(row["canonical_path"].strip())
    expected_prefix = f"{category}/{subcategory}/{slug}/latest/"
    if not object_name.startswith(expected_prefix):
        raise CatalogSiteError(f"{prefix}: canonical_path must be under {expected_prefix}")
    if not bucket:
        raise CatalogSiteError(f"{prefix}: canonical_path is missing bucket")
    docs_path = docs_dir / f"{slug}.md"
    if not docs_path.exists():
        raise CatalogSiteError(f"{prefix}: unresolved docs link {docs_path}")


def asset_from_row(row: dict[str, str], docs_dir: Path, release_index_dir: Path | None = None) -> CatalogAsset:
    slug = row["asset_slug"].strip()
    canonical_path = row["canonical_path"].strip()
    canonical_format = row["canonical_format"].strip()
    formats = split_semicolon(row["available_formats"])
    access_tier = row["access_tier"].strip()
    metadata_paths = split_semicolon(row["metadata_paths"])
    pmtiles_path = path_for_format(canonical_path, slug, "pmtiles") if "pmtiles" in formats else None
    docs_path = f"docs/assets/{slug}.md"
    doc_path = docs_dir / f"{slug}.md"
    doc_metadata = read_doc_metadata(doc_path)
    last_updated = row.get("last_updated", "").strip()
    release_index = load_release_index(release_index_dir, slug)
    versions = release_versions_from_index(
        release_index=release_index,
        canonical_path=canonical_path,
        canonical_format=canonical_format,
        available_formats=formats,
        pmtiles_path=pmtiles_path,
    )
    if release_index is not None and not versions:
        raise CatalogSiteError(f"{slug}: release index does not include any usable releases")
    latest_release = release_index.get("latest_release") if release_index else None
    latest_run = release_index.get("latest_run") if release_index else None
    release_index_updated_at = str(release_index.get("updated_at") or "") if release_index else ""
    latest_release_date = ""
    if isinstance(latest_release, dict):
        latest_release_date = str(latest_release.get("date") or "")
    elif release_index is not None:
        raise CatalogSiteError(f"{slug}: release index latest_release must be an object")
    if release_index is not None and not DATE_RE.fullmatch(latest_release_date):
        raise CatalogSiteError(f"{slug}: release index latest_release.date must be YYYY-MM-DD")
    effective_last_updated = latest_release_date or last_updated
    latest_version = None
    if latest_release_date:
        latest_version = next((version for version in versions if version.date == latest_release_date), None)
        if latest_version is None:
            raise CatalogSiteError(
                f"{slug}: release index latest_release.date {latest_release_date!r} does not match a release entry"
            )
    row_count = optional_int(doc_metadata, "row_count", doc_path=doc_path)
    if doc_metadata.get("feature_metadata") not in (None, "") and doc_metadata.get("feature_identity") in (None, ""):
        raise CatalogSiteError(f"{doc_path}: feature_identity is required when feature_metadata is declared")
    feature_identity = optional_feature_identity(doc_metadata, doc_path=doc_path)
    feature_metadata = optional_feature_metadata(doc_metadata, asset_slug=slug, doc_path=doc_path)
    has_pmtiles = "pmtiles" in formats
    return CatalogAsset(
        slug=slug,
        title=row["title"].strip(),
        category=row["category"].strip(),
        subcategory=row["subcategory"].strip(),
        status=row["status"].strip(),
        lifecycle_reason=row.get("lifecycle_reason", "").strip(),
        lifecycle_date=row.get("lifecycle_date", "").strip(),
        successor_asset_slug=row.get("successor_asset_slug", "").strip(),
        consumer_guidance=row.get("consumer_guidance", "").strip(),
        access_tier=access_tier,
        owner=row["owner"].strip(),
        update_cadence=row["update_cadence"].strip(),
        canonical_path=canonical_path,
        canonical_format=canonical_format,
        available_formats=formats,
        metadata_paths=metadata_paths,
        files=doc_latest_files(
            doc_metadata,
            canonical_path=canonical_path,
            feature_metadata=feature_metadata,
            doc_path=doc_path,
        ),
        feature_identity=feature_identity,
        has_pmtiles=has_pmtiles,
        has_geojson="geojson" in formats,
        has_csv="csv" in formats,
        last_updated=effective_last_updated,
        latest_release=latest_release if isinstance(latest_release, dict) else None,
        latest_run=latest_run if isinstance(latest_run, dict) else None,
        release_index_updated_at=release_index_updated_at,
        source=row["source"].strip(),
        license=row["license"].strip(),
        citation=row["citation"].strip(),
        notes=row.get("notes", "").strip(),
        bounds=optional_bounds(doc_metadata, doc_path=doc_path),
        geometry_type=optional_text(doc_metadata, "geometry_type", doc_path=doc_path),
        row_count=row_count,
        data_profile=optional_data_profile(doc_metadata, row_count=row_count, doc_path=doc_path),
        search_fields=optional_search_fields(doc_metadata, row_count=row_count, doc_path=doc_path),
        feature_metadata=feature_metadata,
        colorizer_metadata=colorizer_metadata_for_asset(
            has_pmtiles=has_pmtiles,
            feature_metadata=feature_metadata,
        ),
        source_url=optional_text(doc_metadata, "source_url", doc_path=doc_path),
        public_url=gs_to_https(canonical_path),
        pmtiles_path=pmtiles_path,
        pmtiles_url=pmtiles_cdn_url(slug, access_tier) if pmtiles_path else None,
        canonical_sha256=latest_version.canonical_sha256 if latest_version else "",
        pmtiles_sha256=latest_version.pmtiles_sha256 if latest_version else "",
        docs_path=docs_path,
        docs_url=docs_path,
        release_index_url=f"../releases/{quote(slug)}.json",
        description=read_description(doc_path),
        license_flags=merged_license_flags(row["license"].strip(), doc_metadata, doc_path=doc_path),
        versions=versions,
        sort_key=f"{effective_last_updated}|{slug}",
    )


def synthetic_asset_from_release_index(release_index: dict[str, Any]) -> CatalogAsset:
    slug = str(release_index.get("asset_slug") or "").strip()
    if not SLUG_RE.fullmatch(slug):
        raise CatalogSiteError("release-index-only asset_slug must be lowercase kebab-case")
    latest_release = release_index_latest_release(release_index)
    latest_release_date = str(latest_release.get("date") or "").strip()
    files = latest_release.get("files") or []
    if not isinstance(files, list):
        raise CatalogSiteError(f"{slug}: release index latest_release.files must be a list")
    canonical_format = infer_canonical_format(files, asset_slug=slug)
    canonical_file = release_file_for_format(files, canonical_format)
    canonical_path = release_file_path(canonical_file)
    if not canonical_path:
        raise CatalogSiteError(f"{slug}: release index latest release is missing canonical {canonical_format} file")
    category, subcategory = release_index_asset_root(canonical_path, latest_release_date, slug)
    available_formats = sorted(
        {
            str(file_entry.get("format") or "").strip()
            for file_entry in files
            if isinstance(file_entry, dict)
            and str(file_entry.get("format") or "").strip() in APPROVED_FORMATS
        }
    )
    if canonical_format not in available_formats:
        available_formats.append(canonical_format)
    pmtiles_file = release_file_for_format(files, "pmtiles")
    pmtiles_path = release_file_path(pmtiles_file)
    versions = release_versions_from_index(
        release_index=release_index,
        canonical_path=canonical_path,
        canonical_format=canonical_format,
        available_formats=available_formats,
        pmtiles_path=pmtiles_path or None,
    )
    latest_version = next((version for version in versions if version.date == latest_release_date), None)
    if latest_version is None:
        raise CatalogSiteError(f"{slug}: release index latest_release.date does not match a release entry")
    metadata_paths = [
        release_file_path(file_entry)
        for file_entry in files
        if isinstance(file_entry, dict) and str(file_entry.get("format") or "").strip() in {"metadata", "schema", "manifest"}
    ]
    row_count = latest_version.rows
    title = humanize_slug(slug)
    has_pmtiles = bool(latest_version.pmtiles_path)
    return CatalogAsset(
        slug=slug,
        title=title,
        category=category,
        subcategory=subcategory,
        status="active",
        lifecycle_reason="",
        lifecycle_date="",
        successor_asset_slug="",
        consumer_guidance="",
        access_tier="private",
        owner="Preview",
        update_cadence="manual",
        canonical_path=latest_version.canonical_path,
        canonical_format=canonical_format,
        available_formats=latest_version.available_formats or available_formats,
        metadata_paths=metadata_paths,
        files=latest_version.files,
        feature_identity=None,
        has_pmtiles=has_pmtiles,
        has_geojson="geojson" in available_formats,
        has_csv="csv" in available_formats,
        last_updated=latest_release_date,
        latest_release=latest_release,
        latest_run=release_index.get("latest_run") if isinstance(release_index.get("latest_run"), dict) else None,
        release_index_updated_at=str(release_index.get("updated_at") or ""),
        source=str(latest_release.get("source_version") or "Preview release index"),
        license="Preview-only release index metadata",
        citation="Preview-only release index metadata",
        notes="Preview-only catalog entry synthesized from a preview release index.",
        bounds=None,
        geometry_type=None,
        row_count=row_count,
        data_profile=None,
        search_fields=[],
        feature_metadata=None,
        colorizer_metadata=colorizer_metadata_for_asset(
            has_pmtiles=has_pmtiles,
            feature_metadata=None,
        ),
        source_url=None,
        public_url=latest_version.public_url,
        pmtiles_path=latest_version.pmtiles_path,
        pmtiles_url=latest_version.pmtiles_url,
        canonical_sha256=latest_version.canonical_sha256,
        pmtiles_sha256=latest_version.pmtiles_sha256,
        docs_path="",
        docs_url="",
        release_index_url=f"../releases/{quote(slug)}.json",
        description="Preview-only catalog entry synthesized from a preview release index.",
        license_flags=[],
        versions=versions,
        sort_key=f"{latest_release_date}|{slug}",
    )


def release_index_exists(release_index_dir: Path | None, slug: str) -> bool:
    return release_index_dir is not None and (release_index_dir / f"{slug}.json").exists()


def latest_version_for_asset(asset: CatalogAsset) -> CatalogVersion | None:
    latest_date = ""
    if isinstance(asset.latest_release, dict):
        latest_date = str(asset.latest_release.get("date") or "")
    if latest_date:
        for version in asset.versions:
            if version.date == latest_date:
                return version
        return None
    return None


def asset_with_latest_from_release_index(asset: CatalogAsset) -> CatalogAsset | None:
    latest_version = latest_version_for_asset(asset)
    if latest_version is None:
        return None
    formats = latest_version.available_formats or asset.available_formats
    has_pmtiles = "pmtiles" in formats
    pmtiles_path = latest_version.pmtiles_path if has_pmtiles else None
    return replace(
        asset,
        canonical_path=latest_version.canonical_path,
        available_formats=formats,
        files=latest_version.files,
        has_pmtiles=has_pmtiles,
        has_geojson="geojson" in formats,
        has_csv="csv" in formats,
        public_url=latest_version.public_url,
        pmtiles_path=pmtiles_path,
        pmtiles_url=pmtiles_cdn_url(asset.slug, asset.access_tier) if pmtiles_path else None,
        colorizer_metadata=colorizer_metadata_for_asset(
            has_pmtiles=has_pmtiles,
            feature_metadata=asset.feature_metadata,
        ),
        canonical_sha256=latest_version.canonical_sha256 or asset.canonical_sha256,
        pmtiles_sha256=latest_version.pmtiles_sha256 or asset.pmtiles_sha256,
        row_count=latest_version.rows if latest_version.rows is not None else asset.row_count,
    )


def build_catalog_payload(
    *,
    catalog_path: Path,
    categories_path: Path,
    docs_dir: Path,
    bucket: str,
    site_prefix: str,
    release_index_dir: Path | None = Path("_catalog/releases"),
    release_index_assets_only: bool = False,
    latest_from_release_index: bool = False,
    allow_release_index_only_assets: bool = False,
    force_access_tier: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    if force_access_tier is not None and force_access_tier not in ACCESS_TIERS:
        raise CatalogSiteError(f"force_access_tier must be one of: {', '.join(sorted(ACCESS_TIERS))}")
    categories = load_categories(categories_path)
    rows = load_catalog_rows(catalog_path)
    effective_release_index_dir = release_index_dir
    if effective_release_index_dir is not None and not effective_release_index_dir.is_absolute():
        effective_release_index_dir = catalog_path.parent.parent / effective_release_index_dir
    seen: set[str] = set()
    assets: list[CatalogAsset] = []
    for index, row in enumerate(rows, start=2):
        slug = row.get("asset_slug", "").strip()
        if release_index_assets_only and not release_index_exists(effective_release_index_dir, slug):
            continue
        validate_row(row=row, row_number=index, categories=categories, seen=seen, docs_dir=docs_dir)
        seen.add(slug)
        asset = asset_from_row(row, docs_dir, release_index_dir=effective_release_index_dir)
        if latest_from_release_index:
            asset = asset_with_latest_from_release_index(asset)
            if asset is None:
                continue
        if force_access_tier is not None:
            asset = replace(asset, access_tier=force_access_tier)
        assets.append(asset)
    if allow_release_index_only_assets:
        if not release_index_assets_only:
            raise CatalogSiteError("allow_release_index_only_assets requires release_index_assets_only")
        for release_index in load_all_release_indexes(effective_release_index_dir):
            slug = str(release_index.get("asset_slug") or "").strip()
            if slug in seen:
                continue
            asset = synthetic_asset_from_release_index(release_index)
            if force_access_tier is not None:
                asset = replace(asset, access_tier=force_access_tier)
            assets.append(asset)
            seen.add(slug)
    assets.sort(key=lambda asset: asset.sort_key, reverse=True)
    return {
        "schema_version": 1,
        "generated_at": generated_at or dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "bucket": bucket,
        "site_prefix": site_prefix.strip("/"),
        "source_catalog": str(catalog_path),
        "categories": categories,
        "formats": sorted(APPROVED_FORMATS),
        "assets": [asdict(asset) for asset in assets],
    }


def copy_static_files(source_dir: Path, out_dir: Path) -> list[Path]:
    copied: list[Path] = []
    for name in ("index.html", "styles.css", "app.js", "map-preview.js"):
        src = source_dir / name
        if not src.exists():
            raise CatalogSiteError(f"missing static source file: {src}")
        dst = out_dir / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def copy_docs(docs_dir: Path, out_dir: Path) -> list[Path]:
    copied: list[Path] = []
    target_dir = out_dir / "docs/assets"
    target_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(docs_dir.glob("*.md")):
        dst = target_dir / src.name
        shutil.copy2(src, dst)
        copied.append(dst)
    return copied


def build_site(
    *,
    catalog_path: Path,
    categories_path: Path,
    docs_dir: Path,
    static_dir: Path,
    out_dir: Path,
    bucket: str,
    site_prefix: str,
    release_index_dir: Path | None = Path("_catalog/releases"),
    release_index_assets_only: bool = False,
    latest_from_release_index: bool = False,
    allow_release_index_only_assets: bool = False,
    force_access_tier: str | None = None,
    generated_at: str | None = None,
) -> list[Path]:
    payload = build_catalog_payload(
        catalog_path=catalog_path,
        categories_path=categories_path,
        docs_dir=docs_dir,
        bucket=bucket,
        site_prefix=site_prefix,
        release_index_dir=release_index_dir,
        release_index_assets_only=release_index_assets_only,
        latest_from_release_index=latest_from_release_index,
        allow_release_index_only_assets=allow_release_index_only_assets,
        force_access_tier=force_access_tier,
        generated_at=generated_at,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    written = copy_static_files(static_dir, out_dir)
    written.extend(copy_docs(docs_dir, out_dir))
    catalog_json = out_dir / "catalog.json"
    catalog_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    written.append(catalog_json)
    return written


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the static shared-datasets catalog site.")
    parser.add_argument("--catalog", type=Path, default=Path("catalog/shared-datasets-catalog.csv"))
    parser.add_argument("--categories", type=Path, default=Path("catalog/categories.yaml"))
    parser.add_argument("--docs-dir", type=Path, default=Path("docs/assets"))
    parser.add_argument("--static-dir", type=Path, default=Path("web/catalog"))
    parser.add_argument("--out", type=Path, required=True, help="Output directory for the static web bundle.")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--site-prefix", default=DEFAULT_SITE_PREFIX)
    parser.add_argument(
        "--release-index-dir",
        type=Path,
        default=Path("_catalog/releases"),
        help="Optional local directory of _catalog/releases/*.json files to merge into catalog.json.",
    )
    parser.add_argument(
        "--release-index-assets-only",
        action="store_true",
        help="Include only assets with a local release-index JSON file.",
    )
    parser.add_argument(
        "--latest-from-release-index",
        action="store_true",
        help="Use the release-index latest release as each asset's top-level latest reference.",
    )
    parser.add_argument(
        "--allow-release-index-only-assets",
        action="store_true",
        help="Preview-only: include assets found only in local release-index JSON files.",
    )
    parser.add_argument(
        "--force-access-tier",
        choices=sorted(ACCESS_TIERS),
        help="Override emitted asset access_tier values, for private preview deployments.",
    )
    parser.add_argument("--generated-at", help="Override generated_at timestamp for deterministic tests.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    written = build_site(
        catalog_path=args.catalog,
        categories_path=args.categories,
        docs_dir=args.docs_dir,
        static_dir=args.static_dir,
        out_dir=args.out,
        bucket=args.bucket,
        site_prefix=args.site_prefix,
        release_index_dir=args.release_index_dir,
        release_index_assets_only=args.release_index_assets_only,
        latest_from_release_index=args.latest_from_release_index,
        allow_release_index_only_assets=args.allow_release_index_only_assets,
        force_access_tier=args.force_access_tier,
        generated_at=args.generated_at,
    )
    print(json.dumps({"output": str(args.out), "files": [str(path) for path in written]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
