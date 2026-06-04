#!/usr/bin/env python3
"""Generate catalog and managed asset documentation from docs/assets/*.md."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
SCHEMA_VERSION = 1
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)
H2_RE_TEMPLATE = r"(?ms)^## {heading}\s*\n.*?(?=^## |\Z)"
ASSET_SUMMARY_BLOCK_RE = re.compile(
    r"(?ms)\n*<!-- BEGIN GENERATED asset-summary -->.*?<!-- END GENERATED asset-summary -->\n*"
)

CATALOG_COLUMNS = [
    "asset_slug",
    "title",
    "category",
    "subcategory",
    "status",
    "lifecycle_reason",
    "lifecycle_date",
    "successor_asset_slug",
    "consumer_guidance",
    "access_tier",
    "owner",
    "update_cadence",
    "canonical_path",
    "canonical_format",
    "available_formats",
    "metadata_paths",
    "localized_name_locales",
    "localized_name_review_states",
    "has_pmtiles",
    "has_geojson",
    "has_csv",
    "source",
    "license",
    "citation",
    "notes",
]

FRONTMATTER_KEYS = [
    "schema_version",
    "asset_slug",
    "title",
    "category",
    "subcategory",
    "status",
    "lifecycle_reason",
    "lifecycle_date",
    "successor_asset_slug",
    "consumer_guidance",
    "access_tier",
    "owner",
    "update_cadence",
    "canonical_format",
    "canonical_file",
    "available_formats",
    "metadata_paths",
    "source",
    "source_url",
    "license",
    "citation",
    "license_flags",
    "notes",
    "admission",
    "bounds",
    "geometry_type",
    "row_count",
    "data_profile",
    "search_fields",
    "localized_names",
    "feature_metadata",
    "generated_group_id",
    "generated_row_id",
    "source_resolution_meters",
    "source_scale_denominator",
    "pmtiles_maxzoom",
    "pmtiles_maxzoom_reason",
    "pmtiles_detail_hint",
    "files",
]

OPTIONAL_DISCOVERY_FIELDS = [
    "source_url",
    "license_flags",
    "bounds",
    "geometry_type",
    "row_count",
    "data_profile",
    "search_fields",
    "localized_names",
    "feature_metadata",
    "generated_group_id",
    "generated_row_id",
    "source_resolution_meters",
    "source_scale_denominator",
    "pmtiles_maxzoom",
    "pmtiles_maxzoom_reason",
    "pmtiles_detail_hint",
]

LIFECYCLE_FIELDS = [
    "lifecycle_reason",
    "lifecycle_date",
    "successor_asset_slug",
    "consumer_guidance",
]
LIFECYCLE_STATUSES = {"active", "deprecated", "superseded", "retired"}
NON_ACTIVE_LIFECYCLE_REQUIRED_FIELDS = ["lifecycle_reason", "lifecycle_date", "consumer_guidance"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
IDENTITY_CANDIDATE_STATUSES = {"unique", "non_unique", "unknown", "not_applicable"}
GENERATED_GROUP_ID_ALGORITHM = "shared-datasets-group-id:v1"
GENERATED_GROUP_ID_COLUMN = "shared_datasets_group_id"
GENERATED_ROW_ID_ALGORITHM = "shared-datasets-row-id:v1"
GENERATED_ROW_ID_COLUMN = "shared_datasets_row_id"
LOCALIZED_NAMES_PROPERTY_TEMPLATE = "name_{locale_code}"
LOCALIZED_NAMES_LOCALE_CODE_FORMAT = "bcp47_field_safe"
LOCALIZED_NAME_STORAGE = "localization_csv_v1"
LOCALIZED_NAMES_JOIN_KEY = "ext_id"
LOCALIZED_NAMES_FALLBACK_FIELD = "name"
LOCALIZED_NAMES_LOCALIZATION_SUFFIX = "-localizations.csv"
LOCALIZED_NAME_REVIEW_STATES = {"source_provided", "machine_translated", "human_reviewed", "mixed"}
FEATURE_METADATA_STORAGE = "metadata_sidecar_v1"
FEATURE_METADATA_INDEX_BACKEND = "firestore"
FEATURE_METADATA_FEATURE_ID_COLUMN = "feature_id"
FEATURE_METADATA_FEATURE_HASH_COLUMN = "feature_hash"
FIELD_SAFE_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$")
BODY_LOCALIZED_NAME_FIELD_RE = re.compile(r"`name_[a-z]{2,3}(?:_[a-z0-9]{2,8})*`")

REQUIRED_SCALAR_FIELDS = [
    "asset_slug",
    "title",
    "category",
    "subcategory",
    "status",
    "access_tier",
    "owner",
    "update_cadence",
    "canonical_format",
    "canonical_file",
    "source",
    "license",
    "citation",
]

REQUIRED_SECTIONS = [
    "## What this is",
    "## Files",
    "## Schema notes",
    "## Properties / columns",
    "## Update notes",
]

APPROVED_CANONICAL_FORMATS = {"fgb", "cog", "zarr", "pmtiles", "geojson", "ndgeojson", "csv"}
PUBLISHED_ROLES = {"canonical", "companion"}
FILE_ROLES = {"canonical", "companion", "release", "run-record", "source", "preview", "metadata", "localization"}
ACCESS_TIERS = {"public", "private"}
DISALLOWED_CADENCE_DETAIL_RE = re.compile(r"\b(skip|skipped|unchanged|no[- ]?change)\b", re.IGNORECASE)


@dataclass
class FileEntry:
    path: str
    format: str
    role: str
    purpose: str


@dataclass
class AssetDoc:
    path: Path
    metadata: dict[str, Any]
    body: str
    warnings: list[str] = field(default_factory=list)

    @property
    def slug(self) -> str:
        return str(self.metadata["asset_slug"])


class CatalogDocsError(ValueError):
    """Raised when generated catalog/docs inputs are invalid."""


def load_categories(path: Path) -> dict[str, set[str]]:
    payload = yaml.safe_load(path.read_text()) or {}
    categories = payload.get("categories") or {}
    return {
        str(category): set((data.get("subcategories") or {}).keys())
        for category, data in categories.items()
    }


def load_catalog_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="") as handle:
        return {row["asset_slug"]: row for row in csv.DictReader(handle) if row.get("asset_slug")}


def split_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise CatalogDocsError(f"{path}: missing YAML frontmatter")
    payload = yaml.safe_load(match.group(1)) or {}
    if not isinstance(payload, dict):
        raise CatalogDocsError(f"{path}: frontmatter must be a YAML mapping")
    return payload, match.group(2)


def as_text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat") and not isinstance(value, str):
        return value.isoformat()
    return str(value)


def normalize_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        delimiter = ";" if ";" in value else ","
        return [part.strip() for part in value.split(delimiter) if part.strip()]
    if isinstance(value, (list, tuple)):
        return [as_text(part).strip() for part in value if as_text(part).strip()]
    raise CatalogDocsError(f"expected list or delimited string, got {type(value).__name__}")


def infer_format(path: str, canonical_format: str | None = None) -> str:
    lowered = path.lower()
    if lowered == "latest/manifest.json" and canonical_format == "zarr":
        return "zarr"
    if lowered.endswith(".metadata.ndjson.gz"):
        return "ndjson_gzip"
    if lowered.endswith(".schema.json") or lowered.endswith(".manifest.json"):
        return "json"
    for suffix, format_name in (
        (".ndgeojson", "ndgeojson"),
        (".geojson", "geojson"),
        (".pmtiles", "pmtiles"),
        (".fgb", "fgb"),
        (".csv", "csv"),
        (".tiff", "cog"),
        (".tif", "cog"),
        (".json", "json"),
        (".png", "png"),
        (".jpg", "jpg"),
        (".jpeg", "jpg"),
        (".webp", "webp"),
    ):
        if lowered.endswith(suffix):
            return format_name
    return "unknown"


def infer_role(path: str, canonical_file: str) -> str:
    if path == canonical_file:
        return "canonical"
    if path.endswith((".metadata.ndjson.gz", ".schema.json", ".manifest.json")):
        return "metadata"
    if path.startswith("latest/"):
        return "companion"
    if path.startswith("releases/"):
        return "release"
    if path.startswith("runs/"):
        return "run-record"
    if path.startswith("source/") or path.startswith("sources/") or path.startswith("archive/"):
        return "source"
    if path.startswith("previews/"):
        return "preview"
    return "metadata"


def normalize_file_entries(raw_files: Any, canonical_file: str, canonical_format: str) -> list[dict[str, str]]:
    if not isinstance(raw_files, list):
        raise CatalogDocsError("files must be a list")
    normalized = []
    for index, raw_entry in enumerate(raw_files, start=1):
        if not isinstance(raw_entry, dict):
            raise CatalogDocsError(f"files[{index}] must be a mapping")
        path = as_text(raw_entry.get("path")).strip()
        purpose = as_text(raw_entry.get("purpose")).strip()
        format_name = as_text(raw_entry.get("format")).strip() or infer_format(path, canonical_format)
        role = as_text(raw_entry.get("role")).strip() or infer_role(path, canonical_file)
        if not path:
            raise CatalogDocsError(f"files[{index}] is missing path")
        if not purpose:
            raise CatalogDocsError(f"files[{index}] is missing purpose")
        normalized.append(
            {
                "path": path,
                "format": format_name,
                "role": role,
                "purpose": purpose,
            }
        )
    return normalized


def normalize_metadata(
    *,
    path: Path,
    raw: dict[str, Any],
    body: str,
    categories: dict[str, set[str]],
) -> tuple[dict[str, Any], list[str]]:
    metadata: dict[str, Any] = {}
    warnings: list[str] = []

    metadata["schema_version"] = raw.get("schema_version")

    for key in REQUIRED_SCALAR_FIELDS:
        value = raw.get(key)
        metadata[key] = as_text(value).strip()

    notes = raw.get("notes")
    metadata["notes"] = as_text(notes).strip()

    for key in LIFECYCLE_FIELDS:
        value = raw.get(key)
        if value is not None:
            text = as_text(value).strip()
            if text:
                metadata[key] = text

    if "admission" in raw:
        metadata["admission"] = raw["admission"]

    for key in OPTIONAL_DISCOVERY_FIELDS:
        if key == "localized_names":
            continue
        if key in raw:
            metadata[key] = raw[key]
    if "localized_names" in raw:
        localized_names = normalize_localized_names(raw["localized_names"], path=path)
        if localized_names:
            metadata["localized_names"] = localized_names

    available_formats = normalize_list(raw.get("available_formats"))
    metadata["available_formats"] = available_formats

    metadata_paths = normalize_list(raw.get("metadata_paths"))
    if not metadata_paths:
        metadata_paths = ["README.md"]
    metadata["metadata_paths"] = metadata_paths

    files = raw.get("files")
    metadata["files"] = normalize_file_entries(files, metadata["canonical_file"], metadata["canonical_format"])

    validate_metadata(path, metadata, categories)
    rendered_body = render_body(path, metadata, body)
    validate_body(path, metadata, rendered_body)
    lowered_body = rendered_body.lower()
    if re.search(r"\bneeds?\s+source confirmation\b", lowered_body) or "needing source confirmation" in lowered_body:
        warnings.append(f"{path}: property descriptions include source-confirmation placeholders")
    return metadata, warnings


def validate_metadata(path: Path, metadata: dict[str, Any], categories: dict[str, set[str]]) -> None:
    for key in REQUIRED_SCALAR_FIELDS:
        if not metadata.get(key):
            raise CatalogDocsError(f"{path}: missing required frontmatter field {key!r}")
    if metadata.get("schema_version") != SCHEMA_VERSION:
        raise CatalogDocsError(f"{path}: schema_version must be {SCHEMA_VERSION}")
    if metadata.get("access_tier") not in ACCESS_TIERS:
        raise CatalogDocsError(f"{path}: access_tier must be one of: {', '.join(sorted(ACCESS_TIERS))}")
    validate_lifecycle_metadata(path, metadata)
    if DISALLOWED_CADENCE_DETAIL_RE.search(metadata["update_cadence"]):
        raise CatalogDocsError(
            f"{path}: update_cadence must describe schedule only; unchanged-source skip behavior is the default for cron jobs"
        )
    slug = metadata["asset_slug"]
    if not SLUG_RE.fullmatch(slug):
        raise CatalogDocsError(f"{path}: asset_slug must be lowercase kebab-case")
    if path.stem != slug:
        raise CatalogDocsError(f"{path}: filename stem must match asset_slug {slug!r}")
    category = metadata["category"]
    subcategory = metadata["subcategory"]
    if category not in categories:
        raise CatalogDocsError(f"{path}: unknown category {category!r}")
    if subcategory not in categories[category]:
        raise CatalogDocsError(f"{path}: unknown subcategory {category}/{subcategory}")
    if metadata["canonical_format"] not in APPROVED_CANONICAL_FORMATS:
        raise CatalogDocsError(f"{path}: unsupported canonical_format {metadata['canonical_format']!r}")
    if not metadata["canonical_file"].startswith("latest/"):
        raise CatalogDocsError(f"{path}: canonical_file must be under latest/")
    if "README.md" not in metadata["metadata_paths"]:
        raise CatalogDocsError(f"{path}: metadata_paths must include README.md")

    canonical_entries = [entry for entry in metadata["files"] if entry["path"] == metadata["canonical_file"]]
    if len(canonical_entries) != 1:
        raise CatalogDocsError(f"{path}: files must contain exactly one entry for canonical_file")
    canonical_entry = canonical_entries[0]
    if canonical_entry["role"] != "canonical":
        raise CatalogDocsError(f"{path}: canonical_file entry must use role 'canonical'")
    if canonical_entry["format"] != metadata["canonical_format"]:
        raise CatalogDocsError(f"{path}: canonical_file format must match canonical_format")
    for entry in metadata["files"]:
        if entry["role"] not in FILE_ROLES:
            raise CatalogDocsError(f"{path}: file {entry['path']} has unsupported role {entry['role']!r}")
    latest_formats = published_latest_formats(metadata["files"])
    if latest_formats != metadata["available_formats"]:
        raise CatalogDocsError(
            f"{path}: available_formats {metadata['available_formats']} must match latest canonical/companion file formats {latest_formats}"
        )
    localized_names = metadata.get("localized_names")
    if isinstance(localized_names, dict):
        localization_file = localized_names.get("localization_file")
        localization_entries = [entry for entry in metadata["files"] if entry["path"] == localization_file]
        if len(localization_entries) != 1:
            raise CatalogDocsError(f"{path}: files must contain exactly one entry for localized_names.localization_file")
        if localization_entries[0]["role"] != "localization":
            raise CatalogDocsError(f"{path}: localization file entry must use role 'localization'")
        if localization_entries[0]["format"] != "csv":
            raise CatalogDocsError(f"{path}: localization file entry must use format 'csv'")
    feature_metadata = metadata.get("feature_metadata")
    if isinstance(feature_metadata, dict):
        for key in ("sidecar_file", "schema_file", "manifest_file"):
            file_path = feature_metadata.get(key)
            entries = [entry for entry in metadata["files"] if entry["path"] == file_path]
            if len(entries) != 1:
                raise CatalogDocsError(f"{path}: files must contain exactly one entry for feature_metadata.{key}")
            if entries[0]["role"] != "metadata":
                raise CatalogDocsError(f"{path}: feature metadata file entry must use role 'metadata'")
    validate_optional_discovery_metadata(path, metadata)


def validate_lifecycle_metadata(path: Path, metadata: dict[str, Any]) -> None:
    status = metadata["status"]
    if status not in LIFECYCLE_STATUSES:
        allowed = ", ".join(sorted(LIFECYCLE_STATUSES))
        raise CatalogDocsError(f"{path}: status must be one of: {allowed}")

    lifecycle_date = metadata.get("lifecycle_date", "")
    if lifecycle_date and not DATE_RE.fullmatch(lifecycle_date):
        raise CatalogDocsError(f"{path}: lifecycle_date must be YYYY-MM-DD")

    successor = metadata.get("successor_asset_slug", "")
    if successor and not SLUG_RE.fullmatch(successor):
        raise CatalogDocsError(f"{path}: successor_asset_slug must be lowercase kebab-case")
    if successor == metadata["asset_slug"]:
        raise CatalogDocsError(f"{path}: successor_asset_slug must not match asset_slug")

    if status == "active":
        return

    missing = [key for key in NON_ACTIVE_LIFECYCLE_REQUIRED_FIELDS if not metadata.get(key)]
    if missing:
        raise CatalogDocsError(f"{path}: non-active assets require {', '.join(missing)}")
    if status == "superseded" and not successor:
        raise CatalogDocsError(f"{path}: superseded assets require successor_asset_slug")


def profile_int(
    value: Any,
    *,
    label: str,
    path: Path,
    required: bool = False,
    row_count: int | None = None,
) -> int | None:
    if value in (None, ""):
        if required:
            raise CatalogDocsError(f"{path}: {label} is required")
        return None
    try:
        numeric = int(value)
    except (TypeError, ValueError) as error:
        raise CatalogDocsError(f"{path}: {label} must be an integer") from error
    if numeric < 0:
        raise CatalogDocsError(f"{path}: {label} must be non-negative")
    if row_count is not None and numeric > row_count:
        raise CatalogDocsError(f"{path}: {label} must not exceed row_count")
    return numeric


def validate_identity_candidate(candidate: Any, *, index: int, path: Path, row_count: int | None) -> None:
    context = f"data_profile.identity_candidates[{index}]"
    if not isinstance(candidate, dict):
        raise CatalogDocsError(f"{path}: {context} must be a mapping")
    if not as_text(candidate.get("field")).strip():
        raise CatalogDocsError(f"{path}: {context}.field is required")
    status = as_text(candidate.get("status")).strip().lower().replace("-", "_")
    if status not in IDENTITY_CANDIDATE_STATUSES:
        allowed = ", ".join(sorted(IDENTITY_CANDIDATE_STATUSES))
        raise CatalogDocsError(f"{path}: {context}.status must be one of: {allowed}")
    duplicate_value_count = profile_int(
        candidate.get("duplicate_value_count"),
        label=f"{context}.duplicate_value_count",
        path=path,
        required=True,
        row_count=row_count,
    )
    duplicate_row_count = profile_int(
        candidate.get("duplicate_row_count"),
        label=f"{context}.duplicate_row_count",
        path=path,
        required=True,
        row_count=row_count,
    )
    profile_int(
        candidate.get("distinct_values"),
        label=f"{context}.distinct_values",
        path=path,
        required=True,
        row_count=row_count,
    )
    if status == "unique" and (duplicate_value_count or duplicate_row_count):
        raise CatalogDocsError(f"{path}: {context} is marked unique but has duplicate counts")


def validate_most_unique_field(value: Any, *, path: Path, row_count: int | None) -> None:
    if value in (None, ""):
        return
    context = "data_profile.most_unique_field"
    if not isinstance(value, dict):
        raise CatalogDocsError(f"{path}: {context} must be a mapping")
    if not as_text(value.get("field")).strip():
        raise CatalogDocsError(f"{path}: {context}.field is required")
    profile_int(
        value.get("distinct_values"),
        label=f"{context}.distinct_values",
        path=path,
        required=True,
        row_count=row_count,
    )


def validate_search_fields(value: Any, *, path: Path, row_count: int | None) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, list):
        raise CatalogDocsError(f"{path}: search_fields must be a list")
    for index, entry in enumerate(value, start=1):
        context = f"search_fields[{index}]"
        if isinstance(entry, str):
            if not entry.strip():
                raise CatalogDocsError(f"{path}: {context} must be non-empty")
            continue
        if not isinstance(entry, dict):
            raise CatalogDocsError(f"{path}: {context} must be a mapping or string")
        if not as_text(entry.get("field")).strip():
            raise CatalogDocsError(f"{path}: {context}.field is required")
        profile_int(
            entry.get("distinct_values"),
            label=f"{context}.distinct_values",
            path=path,
            row_count=row_count,
        )
        if "notes" in entry and entry["notes"] not in (None, "") and not as_text(entry["notes"]).strip():
            raise CatalogDocsError(f"{path}: {context}.notes must be non-empty when provided")


def normalize_locale_code(value: Any, *, path: Path, context: str) -> str:
    locale_code = as_text(value).strip()
    if not locale_code:
        raise CatalogDocsError(f"{path}: {context} is required")
    if locale_code != locale_code.lower() or "-" in locale_code or not FIELD_SAFE_LOCALE_RE.fullmatch(locale_code):
        raise CatalogDocsError(
            f"{path}: {context} must be a field-safe BCP 47 locale code such as en, pt_br, or zh_hans"
        )
    return locale_code


def normalize_localized_names(value: Any, *, path: Path) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict):
        raise CatalogDocsError(f"{path}: localized_names must be a mapping")
    storage = as_text(value.get("storage")).strip()
    if storage != LOCALIZED_NAME_STORAGE:
        raise CatalogDocsError(f"{path}: localized_names.storage must be {LOCALIZED_NAME_STORAGE!r}")
    join_key = as_text(value.get("join_key")).strip()
    if join_key != LOCALIZED_NAMES_JOIN_KEY:
        raise CatalogDocsError(f"{path}: localized_names.join_key must be {LOCALIZED_NAMES_JOIN_KEY!r}")
    localization_file = as_text(value.get("localization_file")).strip()
    if not localization_file:
        raise CatalogDocsError(f"{path}: localized_names.localization_file is required")
    if not localization_file.startswith("latest/") or not localization_file.endswith(LOCALIZED_NAMES_LOCALIZATION_SUFFIX):
        raise CatalogDocsError(
            f"{path}: localized_names.localization_file must be latest/{{asset-slug}}{LOCALIZED_NAMES_LOCALIZATION_SUFFIX}"
        )
    property_template = as_text(value.get("property_template")).strip()
    if property_template != LOCALIZED_NAMES_PROPERTY_TEMPLATE:
        raise CatalogDocsError(
            f"{path}: localized_names.property_template must be {LOCALIZED_NAMES_PROPERTY_TEMPLATE!r}"
        )
    locale_code_format = as_text(value.get("locale_code_format")).strip()
    if locale_code_format != LOCALIZED_NAMES_LOCALE_CODE_FORMAT:
        raise CatalogDocsError(
            f"{path}: localized_names.locale_code_format must be {LOCALIZED_NAMES_LOCALE_CODE_FORMAT!r}"
        )
    fallback_field = as_text(value.get("fallback_field")).strip()
    if fallback_field != LOCALIZED_NAMES_FALLBACK_FIELD:
        raise CatalogDocsError(f"{path}: localized_names.fallback_field must be {LOCALIZED_NAMES_FALLBACK_FIELD!r}")
    raw_translations = value.get("translations", [])
    if not isinstance(raw_translations, list):
        raise CatalogDocsError(f"{path}: localized_names.translations must be a list")

    translations: list[dict[str, str]] = []
    seen_locales: set[str] = set()
    seen_fields: set[str] = set()
    seen_review_state_fields: set[str] = set()
    for index, raw_translation in enumerate(raw_translations, start=1):
        context = f"localized_names.translations[{index}]"
        if not isinstance(raw_translation, dict):
            raise CatalogDocsError(f"{path}: {context} must be a mapping")
        locale_code = normalize_locale_code(raw_translation.get("locale_code"), path=path, context=f"{context}.locale_code")
        field = as_text(raw_translation.get("field")).strip()
        expected_field = f"name_{locale_code}"
        if field != expected_field:
            raise CatalogDocsError(f"{path}: {context}.field must be {expected_field!r}")
        review_state_field = as_text(raw_translation.get("review_state_field")).strip()
        expected_review_state_field = f"{expected_field}_review_state"
        if review_state_field != expected_review_state_field:
            raise CatalogDocsError(f"{path}: {context}.review_state_field must be {expected_review_state_field!r}")
        if locale_code in seen_locales:
            raise CatalogDocsError(f"{path}: localized_names locale_code {locale_code!r} is duplicated")
        if field in seen_fields:
            raise CatalogDocsError(f"{path}: localized_names field {field!r} is duplicated")
        if review_state_field in seen_review_state_fields:
            raise CatalogDocsError(f"{path}: localized_names review_state_field {review_state_field!r} is duplicated")
        seen_locales.add(locale_code)
        seen_fields.add(field)
        seen_review_state_fields.add(review_state_field)
        translation = {"locale_code": locale_code, "field": field, "review_state_field": review_state_field}
        if "label" in raw_translation:
            label = as_text(raw_translation.get("label")).strip()
            if not label:
                raise CatalogDocsError(f"{path}: {context}.label must be non-empty when provided")
            translation["label"] = label
        review_state = as_text(raw_translation.get("review_state")).strip()
        if not review_state:
            raise CatalogDocsError(f"{path}: {context}.review_state is required")
        if review_state not in LOCALIZED_NAME_REVIEW_STATES:
            allowed = ", ".join(sorted(LOCALIZED_NAME_REVIEW_STATES))
            raise CatalogDocsError(f"{path}: {context}.review_state must be one of: {allowed}")
        translation["review_state"] = review_state
        translations.append(translation)

    normalized: dict[str, Any] = {
        "storage": storage,
        "join_key": join_key,
        "localization_file": localization_file,
        "property_template": property_template,
        "locale_code_format": locale_code_format,
        "fallback_field": fallback_field,
    }
    fallback_locale = as_text(value.get("fallback_locale")).strip()
    if fallback_locale:
        fallback_locale = normalize_locale_code(fallback_locale, path=path, context="localized_names.fallback_locale")
        normalized["fallback_locale"] = fallback_locale
    normalized["translations"] = translations
    return normalized


def localized_name_locales(metadata: dict[str, Any]) -> list[str]:
    localized_names = metadata.get("localized_names")
    if not isinstance(localized_names, dict):
        return []
    translations = localized_names.get("translations")
    if not isinstance(translations, list):
        return []
    return [translation["locale_code"] for translation in translations if isinstance(translation, dict) and translation.get("locale_code")]


def localized_name_review_states(metadata: dict[str, Any]) -> list[str]:
    localized_names = metadata.get("localized_names")
    if not isinstance(localized_names, dict):
        return []
    translations = localized_names.get("translations")
    if not isinstance(translations, list):
        return []
    return [
        f"{translation['locale_code']}:{translation['review_state']}"
        for translation in translations
        if isinstance(translation, dict) and translation.get("locale_code") and translation.get("review_state")
    ]


def validate_localized_names(value: Any, *, path: Path) -> None:
    if value in (None, ""):
        return
    normalize_localized_names(value, path=path)


def validate_feature_metadata(value: Any, *, path: Path, asset_slug: str) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, dict):
        raise CatalogDocsError(f"{path}: feature_metadata must be a mapping")
    if as_text(value.get("storage")).strip() != FEATURE_METADATA_STORAGE:
        raise CatalogDocsError(f"{path}: feature_metadata.storage must be {FEATURE_METADATA_STORAGE!r}")
    if as_text(value.get("index_backend")).strip() != FEATURE_METADATA_INDEX_BACKEND:
        raise CatalogDocsError(f"{path}: feature_metadata.index_backend must be {FEATURE_METADATA_INDEX_BACKEND!r}")
    if as_text(value.get("feature_id_column")).strip() != FEATURE_METADATA_FEATURE_ID_COLUMN:
        raise CatalogDocsError(f"{path}: feature_metadata.feature_id_column must be {FEATURE_METADATA_FEATURE_ID_COLUMN!r}")
    if as_text(value.get("feature_hash_column")).strip() != FEATURE_METADATA_FEATURE_HASH_COLUMN:
        raise CatalogDocsError(f"{path}: feature_metadata.feature_hash_column must be {FEATURE_METADATA_FEATURE_HASH_COLUMN!r}")
    expected_files = {
        "sidecar_file": f"latest/{asset_slug}.metadata.ndjson.gz",
        "schema_file": f"latest/{asset_slug}.schema.json",
        "manifest_file": f"latest/{asset_slug}.manifest.json",
    }
    for key, expected in expected_files.items():
        if as_text(value.get(key)).strip() != expected:
            raise CatalogDocsError(f"{path}: feature_metadata.{key} must be {expected!r}")
    if value.get("provenance_default") is not True:
        raise CatalogDocsError(f"{path}: feature_metadata.provenance_default must be true")


def validate_generated_group_id(value: Any, *, path: Path, row_count: int | None) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, dict):
        raise CatalogDocsError(f"{path}: generated_group_id must be a mapping")
    column = as_text(value.get("column")).strip()
    if column != GENERATED_GROUP_ID_COLUMN:
        raise CatalogDocsError(f"{path}: generated_group_id.column must be {GENERATED_GROUP_ID_COLUMN}")
    algorithm = as_text(value.get("algorithm")).strip()
    if algorithm != GENERATED_GROUP_ID_ALGORITHM:
        raise CatalogDocsError(f"{path}: generated_group_id.algorithm must be {GENERATED_GROUP_ID_ALGORITHM}")
    grouping_fields = value.get("grouping_fields")
    if not isinstance(grouping_fields, list) or not [as_text(field).strip() for field in grouping_fields if as_text(field).strip()]:
        raise CatalogDocsError(f"{path}: generated_group_id.grouping_fields must be a non-empty list")
    token_length = profile_int(
        value.get("token_length"),
        label="generated_group_id.token_length",
        path=path,
        required=True,
    )
    if token_length is not None and token_length < 8:
        raise CatalogDocsError(f"{path}: generated_group_id.token_length must be at least 8")
    profile_int(
        value.get("group_count"),
        label="generated_group_id.group_count",
        path=path,
        required=True,
        row_count=row_count,
    )
    profile_int(
        value.get("blank_group_count"),
        label="generated_group_id.blank_group_count",
        path=path,
        row_count=row_count,
    )
    if not as_text(value.get("stability")).strip():
        raise CatalogDocsError(f"{path}: generated_group_id.stability is required")


def validate_generated_row_id(value: Any, *, path: Path, row_count: int | None) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, dict):
        raise CatalogDocsError(f"{path}: generated_row_id must be a mapping")
    column = as_text(value.get("column")).strip()
    if column != GENERATED_ROW_ID_COLUMN:
        raise CatalogDocsError(f"{path}: generated_row_id.column must be {GENERATED_ROW_ID_COLUMN}")
    algorithm = as_text(value.get("algorithm")).strip()
    if algorithm != GENERATED_ROW_ID_ALGORITHM:
        raise CatalogDocsError(f"{path}: generated_row_id.algorithm must be {GENERATED_ROW_ID_ALGORITHM}")
    token_length = profile_int(
        value.get("token_length"),
        label="generated_row_id.token_length",
        path=path,
        required=True,
    )
    if token_length is not None and token_length < 8:
        raise CatalogDocsError(f"{path}: generated_row_id.token_length must be at least 8")
    profile_int(
        value.get("row_count"),
        label="generated_row_id.row_count",
        path=path,
        required=True,
        row_count=row_count,
    )
    profile_int(
        value.get("duplicate_geometry_row_count"),
        label="generated_row_id.duplicate_geometry_row_count",
        path=path,
        row_count=row_count,
    )
    profile_int(
        value.get("duplicate_geometry_digest_count"),
        label="generated_row_id.duplicate_geometry_digest_count",
        path=path,
        row_count=row_count,
    )
    if not as_text(value.get("stability")).strip():
        raise CatalogDocsError(f"{path}: generated_row_id.stability is required")
    if not as_text(value.get("warning")).strip():
        raise CatalogDocsError(f"{path}: generated_row_id.warning is required")


def validate_data_profile(path: Path, metadata: dict[str, Any], *, row_count: int | None) -> None:
    profile = metadata.get("data_profile")
    if profile in (None, ""):
        return
    if not isinstance(profile, dict):
        raise CatalogDocsError(f"{path}: data_profile must be a mapping")
    profile_int(profile.get("field_count"), label="data_profile.field_count", path=path, required=True)
    candidates = profile.get("identity_candidates", [])
    if candidates in (None, ""):
        candidates = []
    if not isinstance(candidates, list):
        raise CatalogDocsError(f"{path}: data_profile.identity_candidates must be a list")
    for index, candidate in enumerate(candidates, start=1):
        validate_identity_candidate(candidate, index=index, path=path, row_count=row_count)
    validate_most_unique_field(profile.get("most_unique_field"), path=path, row_count=row_count)
    if "notes" in profile and profile["notes"] not in (None, "") and not as_text(profile["notes"]).strip():
        raise CatalogDocsError(f"{path}: data_profile.notes must be non-empty when provided")


def validate_optional_discovery_metadata(path: Path, metadata: dict[str, Any]) -> None:
    row_count: int | None = None
    if "bounds" in metadata and metadata["bounds"] not in (None, ""):
        bounds = metadata["bounds"]
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise CatalogDocsError(f"{path}: bounds must be [min_lon, min_lat, max_lon, max_lat]")
        try:
            min_lon, min_lat, max_lon, max_lat = [float(value) for value in bounds]
        except (TypeError, ValueError) as error:
            raise CatalogDocsError(f"{path}: bounds values must be numbers") from error
        if not (-180 <= min_lon <= 180 and -180 <= max_lon <= 180 and -90 <= min_lat <= 90 and -90 <= max_lat <= 90):
            raise CatalogDocsError(f"{path}: bounds must be valid WGS84 longitude/latitude values")
        if min_lon > max_lon or min_lat > max_lat:
            raise CatalogDocsError(f"{path}: bounds minimums must not exceed maximums")
    if "row_count" in metadata and metadata["row_count"] not in (None, ""):
        try:
            row_count = int(metadata["row_count"])
        except (TypeError, ValueError) as error:
            raise CatalogDocsError(f"{path}: row_count must be an integer") from error
        if row_count < 0:
            raise CatalogDocsError(f"{path}: row_count must be non-negative")
    validate_data_profile(path, metadata, row_count=row_count)
    validate_search_fields(metadata.get("search_fields"), path=path, row_count=row_count)
    validate_localized_names(metadata.get("localized_names"), path=path)
    validate_feature_metadata(metadata.get("feature_metadata"), path=path, asset_slug=metadata["asset_slug"])
    localized_names = metadata.get("localized_names")
    if isinstance(localized_names, dict):
        expected_localization_file = f"latest/{metadata['asset_slug']}{LOCALIZED_NAMES_LOCALIZATION_SUFFIX}"
        if localized_names.get("localization_file") != expected_localization_file:
            raise CatalogDocsError(
                f"{path}: localized_names.localization_file must be {expected_localization_file!r}"
            )
    if metadata.get("generated_group_id") not in (None, "") and metadata.get("generated_row_id") not in (None, ""):
        raise CatalogDocsError(f"{path}: generated_group_id and generated_row_id are mutually exclusive")
    validate_generated_group_id(metadata.get("generated_group_id"), path=path, row_count=row_count)
    validate_generated_row_id(metadata.get("generated_row_id"), path=path, row_count=row_count)
    if "source_resolution_meters" in metadata and metadata["source_resolution_meters"] not in (None, ""):
        try:
            resolution = float(metadata["source_resolution_meters"])
        except (TypeError, ValueError) as error:
            raise CatalogDocsError(f"{path}: source_resolution_meters must be a number") from error
        if resolution <= 0:
            raise CatalogDocsError(f"{path}: source_resolution_meters must be positive")
    if "source_scale_denominator" in metadata and metadata["source_scale_denominator"] not in (None, ""):
        try:
            scale = float(metadata["source_scale_denominator"])
        except (TypeError, ValueError) as error:
            raise CatalogDocsError(f"{path}: source_scale_denominator must be a number") from error
        if scale <= 0:
            raise CatalogDocsError(f"{path}: source_scale_denominator must be positive")
    if "pmtiles_maxzoom" in metadata and metadata["pmtiles_maxzoom"] not in (None, ""):
        try:
            maxzoom = int(metadata["pmtiles_maxzoom"])
        except (TypeError, ValueError) as error:
            raise CatalogDocsError(f"{path}: pmtiles_maxzoom must be an integer") from error
        if maxzoom < 0:
            raise CatalogDocsError(f"{path}: pmtiles_maxzoom must be non-negative")
        if not metadata.get("pmtiles_maxzoom_reason"):
            raise CatalogDocsError(f"{path}: pmtiles_maxzoom requires pmtiles_maxzoom_reason")
    if "pmtiles_detail_hint" in metadata and metadata["pmtiles_detail_hint"] not in (None, ""):
        if str(metadata["pmtiles_detail_hint"]) not in {"coarse", "medium", "detailed"}:
            raise CatalogDocsError(f"{path}: pmtiles_detail_hint must be coarse, medium, or detailed")
    if "license_flags" in metadata and metadata["license_flags"] not in (None, ""):
        flags = metadata["license_flags"]
        if isinstance(flags, str):
            return
        if not isinstance(flags, list) or not all(as_text(flag).strip() for flag in flags):
            raise CatalogDocsError(f"{path}: license_flags must be a list or delimited string")


def validate_body(path: Path, metadata: dict[str, Any], body: str) -> None:
    if BODY_LOCALIZED_NAME_FIELD_RE.search(body) and not metadata.get("localized_names"):
        raise CatalogDocsError(f"{path}: localized_names is required when translated name_* fields are documented")
    if metadata.get("status") != "active":
        return
    missing = [section for section in REQUIRED_SECTIONS if section not in body]
    if missing:
        raise CatalogDocsError(f"{path}: missing required section(s): {', '.join(missing)}")


def published_latest_formats(files: list[dict[str, str]]) -> list[str]:
    formats: list[str] = []
    for entry in files:
        if entry["role"] not in PUBLISHED_ROLES or not entry["path"].startswith("latest/"):
            continue
        format_name = entry["format"]
        if format_name not in formats:
            formats.append(format_name)
    return formats


def read_asset_docs(
    *,
    docs_dir: Path,
    categories: dict[str, set[str]],
) -> list[AssetDoc]:
    docs: list[AssetDoc] = []
    seen: set[str] = set()
    for path in sorted(docs_dir.glob("*.md")):
        if path.name == "index.md":
            continue
        raw, body = split_frontmatter(path.read_text(), path)
        slug = as_text(raw.get("asset_slug")).strip()
        if slug in seen:
            raise CatalogDocsError(f"{path}: duplicate asset_slug {slug!r}")
        metadata, warnings = normalize_metadata(
            path=path,
            raw=raw,
            body=body,
            categories=categories,
        )
        seen.add(metadata["asset_slug"])
        docs.append(AssetDoc(path=path, metadata=metadata, body=body, warnings=warnings))
    return docs


def asset_root(metadata: dict[str, Any]) -> str:
    return f"{metadata['category']}/{metadata['subcategory']}/{metadata['asset_slug']}"


def canonical_gs_uri(metadata: dict[str, Any], bucket: str) -> str:
    return f"gs://{bucket}/{asset_root(metadata)}/{metadata['canonical_file']}"


def catalog_row(metadata: dict[str, Any], bucket: str) -> dict[str, str]:
    formats = metadata["available_formats"]
    return {
        "asset_slug": metadata["asset_slug"],
        "title": metadata["title"],
        "category": metadata["category"],
        "subcategory": metadata["subcategory"],
        "status": metadata["status"],
        "lifecycle_reason": metadata.get("lifecycle_reason", ""),
        "lifecycle_date": metadata.get("lifecycle_date", ""),
        "successor_asset_slug": metadata.get("successor_asset_slug", ""),
        "consumer_guidance": metadata.get("consumer_guidance", ""),
        "access_tier": metadata["access_tier"],
        "owner": metadata["owner"],
        "update_cadence": metadata["update_cadence"],
        "canonical_path": canonical_gs_uri(metadata, bucket),
        "canonical_format": metadata["canonical_format"],
        "available_formats": ";".join(formats),
        "metadata_paths": ";".join(metadata["metadata_paths"]),
        "localized_name_locales": ";".join(localized_name_locales(metadata)),
        "localized_name_review_states": ";".join(localized_name_review_states(metadata)),
        "has_pmtiles": str("pmtiles" in formats).lower(),
        "has_geojson": str("geojson" in formats).lower(),
        "has_csv": str("csv" in formats).lower(),
        "source": metadata["source"],
        "license": metadata["license"],
        "citation": metadata["citation"],
        "notes": metadata.get("notes", ""),
    }


def render_catalog_csv(docs: Sequence[AssetDoc], bucket: str) -> str:
    from io import StringIO

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=CATALOG_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for doc in sorted(docs, key=lambda item: (item.metadata["category"], item.metadata["subcategory"], item.slug)):
        writer.writerow(catalog_row(doc.metadata, bucket))
    return output.getvalue()


def markdown_link(path: str, label: str) -> str:
    return f"[{label}]({path})"


def render_index(docs: Sequence[AssetDoc]) -> str:
    lines = [
        "# Shared Dataset Asset Index",
        "",
        "<!-- GENERATED by scripts/catalog_docs.py; do not edit by hand. -->",
        "",
    ]
    current_category = None
    for doc in sorted(docs, key=lambda item: (item.metadata["category"], item.metadata["subcategory"], item.slug)):
        metadata = doc.metadata
        if metadata["category"] != current_category:
            if current_category is not None:
                lines.append("")
            current_category = metadata["category"]
            lines.extend(
                [
                    f"## {current_category}",
                    "",
                    "| Asset | Subcategory | Status | Access tier | Formats | Canonical file |",
                    "|---|---|---|---|---|---|",
                ]
            )
        formats = ";".join(metadata["available_formats"])
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_link(f"{metadata['asset_slug']}.md", metadata["title"]),
                    metadata["subcategory"],
                    metadata["status"],
                    metadata["access_tier"],
                    f"`{formats}`",
                    f"`{metadata['canonical_file']}`",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def summary_block(metadata: dict[str, Any]) -> str:
    formats = ", ".join(f"`{format_name}`" for format_name in metadata["available_formats"])
    lines = [
        "<!-- BEGIN GENERATED asset-summary -->",
        f"- **Status:** {metadata['status']}",
    ]
    if metadata["status"] != "active":
        lines.extend(
            [
                f"- **Lifecycle date:** {metadata.get('lifecycle_date', '')}",
                f"- **Lifecycle reason:** {metadata.get('lifecycle_reason', '')}",
                f"- **Consumer guidance:** {metadata.get('consumer_guidance', '')}",
            ]
        )
        if metadata.get("successor_asset_slug"):
            lines.append(f"- **Successor asset:** `{metadata['successor_asset_slug']}`")
    lines.extend(
        [
            f"- **Access tier:** {metadata['access_tier']}",
            f"- **Owner:** {metadata['owner']}",
            f"- **Update cadence:** {metadata['update_cadence']}",
            f"- **Canonical file:** `{metadata['canonical_file']}`",
            f"- **Available formats:** {formats}",
            f"- **Source:** {metadata['source']}",
            f"- **License / terms:** {metadata['license']}",
            f"- **Citation:** {metadata['citation']}",
            "<!-- END GENERATED asset-summary -->",
        ]
    )
    return "\n".join(lines)


def files_table_block(metadata: dict[str, Any]) -> str:
    lines = [
        "<!-- BEGIN GENERATED files-table -->",
        "| File | Format | Role | Purpose |",
        "|---|---|---|---|",
    ]
    for entry in metadata["files"]:
        lines.append(f"| `{entry['path']}` | `{entry['format']}` | `{entry['role']}` | {entry['purpose']} |")
    lines.append("<!-- END GENERATED files-table -->")
    return "\n".join(lines)


def replace_summary(body: str, metadata: dict[str, Any]) -> str:
    body = ASSET_SUMMARY_BLOCK_RE.sub("\n\n", body)
    heading = f"# {metadata['title']}"
    if re.search(r"(?m)^# .+$", body):
        body = re.sub(r"(?m)^# .+$", heading, body, count=1)
    else:
        body = f"{heading}\n\n{body.lstrip()}"
    pattern = r"(?ms)(^# [^\n]*\n)(.*?)(?=^## |\Z)"
    replacement = r"\1\n" + summary_block(metadata) + "\n\n"
    return re.sub(pattern, replacement, body, count=1)


def replace_files_section(body: str, metadata: dict[str, Any]) -> str:
    body = body.replace("<!-- END GENERATED files-table -->## ", "<!-- END GENERATED files-table -->\n\n## ")
    replacement = "## Files\n\n" + files_table_block(metadata) + "\n\n"
    pattern = H2_RE_TEMPLATE.format(heading=re.escape("Files"))
    if re.search(pattern, body):
        return re.sub(pattern, replacement, body, count=1)
    schema_index = body.find("## Schema notes")
    if schema_index >= 0:
        return body[:schema_index].rstrip() + "\n\n" + replacement + body[schema_index:].lstrip()
    return body.rstrip() + "\n\n" + replacement.rstrip() + "\n"


def render_body(path: Path, metadata: dict[str, Any], body: str) -> str:
    rendered = replace_summary(body, metadata)
    rendered = replace_files_section(rendered, metadata)
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def render_frontmatter(metadata: dict[str, Any]) -> str:
    ordered = {key: metadata[key] for key in FRONTMATTER_KEYS if key in metadata}
    return yaml.safe_dump(ordered, sort_keys=False, width=120)


def render_asset_doc(doc: AssetDoc) -> str:
    return "---\n" + render_frontmatter(doc.metadata) + "---\n\n" + render_body(doc.path, doc.metadata, doc.body).lstrip().rstrip() + "\n"


def write_if_changed(path: Path, text: str) -> bool:
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True


def compare_file(path: Path, expected: str, errors: list[str]) -> None:
    actual = path.read_text() if path.exists() else ""
    if actual != expected:
        errors.append(f"{path}: generated content is stale")


def validate_catalog_slug_set(catalog_path: Path, docs: Sequence[AssetDoc], errors: list[str]) -> None:
    rows = load_catalog_rows(catalog_path)
    catalog_slugs = set(rows)
    doc_slugs = {doc.slug for doc in docs}
    missing_docs = sorted(catalog_slugs - doc_slugs)
    extra_docs = sorted(doc_slugs - catalog_slugs)
    if missing_docs:
        errors.append(f"{catalog_path}: catalog rows missing docs: {', '.join(missing_docs)}")
    if extra_docs:
        errors.append(f"{catalog_path}: docs missing catalog rows: {', '.join(extra_docs)}")


def check_outputs(
    *,
    docs: Sequence[AssetDoc],
    catalog_path: Path,
    index_path: Path,
    bucket: str,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    validate_catalog_slug_set(catalog_path, docs, errors)
    compare_file(catalog_path, render_catalog_csv(docs, bucket), errors)
    compare_file(index_path, render_index(docs), errors)
    for doc in docs:
        compare_file(doc.path, render_asset_doc(doc), errors)
        warnings.extend(doc.warnings)
    return errors, warnings


def generate_outputs(
    *,
    docs: Sequence[AssetDoc],
    catalog_path: Path,
    index_path: Path,
    bucket: str,
) -> list[Path]:
    changed: list[Path] = []
    for doc in docs:
        if write_if_changed(doc.path, render_asset_doc(doc)):
            changed.append(doc.path)
    if write_if_changed(catalog_path, render_catalog_csv(docs, bucket)):
        changed.append(catalog_path)
    if write_if_changed(index_path, render_index(docs)):
        changed.append(index_path)
    return changed


def export_readmes(docs: Sequence[AssetDoc], output_dir: Path) -> list[Path]:
    changed: list[Path] = []
    for doc in docs:
        target = output_dir / asset_root(doc.metadata) / "README.md"
        text = render_body(doc.path, doc.metadata, doc.body)
        if write_if_changed(target, text):
            changed.append(target)
    return changed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-dir", type=Path, default=Path("docs/assets"))
    parser.add_argument("--catalog", type=Path, default=Path("catalog/shared-datasets-catalog.csv"))
    parser.add_argument("--categories", type=Path, default=Path("catalog/categories.yaml"))
    parser.add_argument("--index", type=Path, default=Path("docs/assets/index.md"))
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check", help="Fail if generated catalog/docs outputs are stale.")
    subparsers.add_parser("generate", help="Update generated catalog/docs outputs.")
    export_parser = subparsers.add_parser("export-readmes", help="Export bucket-ready README files.")
    export_parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def command_context(args: argparse.Namespace) -> list[AssetDoc]:
    categories = load_categories(args.categories)
    return read_asset_docs(
        docs_dir=args.docs_dir,
        categories=categories,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        docs = command_context(args)
        if args.command == "generate":
            changed = generate_outputs(docs=docs, catalog_path=args.catalog, index_path=args.index, bucket=args.bucket)
            for path in changed:
                print(f"updated {path}")
            print(f"generated {len(docs)} asset doc(s)")
            return 0
        if args.command == "check":
            errors, warnings = check_outputs(docs=docs, catalog_path=args.catalog, index_path=args.index, bucket=args.bucket)
            for warning in warnings:
                print(f"warning: {warning}", file=sys.stderr)
            if errors:
                for error in errors:
                    print(f"error: {error}", file=sys.stderr)
                return 1
            print(f"catalog/docs are current for {len(docs)} asset doc(s)")
            return 0
        if args.command == "export-readmes":
            changed = export_readmes(docs, args.output_dir)
            for path in changed:
                print(f"exported {path}")
            print(f"exported README files for {len(docs)} asset doc(s)")
            return 0
    except CatalogDocsError as exc:
        print(f"catalog-docs: {exc}", file=sys.stderr)
        return 1
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
