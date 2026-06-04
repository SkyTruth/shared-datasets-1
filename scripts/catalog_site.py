#!/usr/bin/env python3
"""Build the static shared-datasets catalog web preview."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import shutil
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import quote

import yaml


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
DEFAULT_SITE_PREFIX = "_catalog/web"
DEFAULT_PMTILES_CDN_BASE_URL = "https://tiles.skytruth.org/pmtiles"
APPROVED_FORMATS = {"fgb", "cog", "zarr", "pmtiles", "geojson", "ndgeojson", "csv"}
ACCESS_TIERS = {"public", "private"}
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
GENERATED_GROUP_ID_ALGORITHM = "shared-datasets-group-id:v1"
GENERATED_GROUP_ID_COLUMN = "shared_datasets_group_id"
GENERATED_ROW_ID_ALGORITHM = "shared-datasets-row-id:v1"
GENERATED_ROW_ID_COLUMN = "shared_datasets_row_id"
FEATURE_METADATA_STORAGE = "metadata_sidecar_v1"
FEATURE_METADATA_INDEX_BACKEND = "firestore"
FEATURE_METADATA_FEATURE_ID_COLUMN = "feature_id"
FEATURE_METADATA_FEATURE_HASH_COLUMN = "feature_hash"
LOCALIZED_NAMES_PROPERTY_TEMPLATE = "name_{locale_code}"
LOCALIZED_NAMES_LOCALE_CODE_FORMAT = "bcp47_field_safe"
LOCALIZED_NAME_STORAGE = "localization_csv_v1"
LOCALIZED_NAMES_JOIN_KEY = "ext_id"
LOCALIZED_NAMES_FALLBACK_FIELD = "name"
LOCALIZED_NAMES_LOCALIZATION_SUFFIX = "-localizations.csv"
LOCALIZED_NAME_REVIEW_STATES = {"source_provided", "machine_translated", "human_reviewed", "mixed"}
FIELD_SAFE_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$")


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
    localized_name_locales: list[str]
    localized_name_review_states: list[str]
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
    localized_names: dict[str, Any] | None
    feature_metadata: dict[str, Any] | None
    generated_group_id: dict[str, Any] | None
    generated_row_id: dict[str, Any] | None
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
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise CatalogSiteError(f"{path}: catalog has no header row")
        return [dict(row) for row in reader]


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


def normalize_locale_code(value: Any, *, context: str, doc_path: Path) -> str:
    locale_code = str(value or "").strip()
    if not locale_code:
        raise CatalogSiteError(f"{doc_path}: {context} is required")
    if locale_code != locale_code.lower() or "-" in locale_code or not FIELD_SAFE_LOCALE_RE.fullmatch(locale_code):
        raise CatalogSiteError(
            f"{doc_path}: {context} must be a field-safe BCP 47 locale code such as en, pt_br, or zh_hans"
        )
    return locale_code


def optional_localized_names(metadata: dict[str, Any], *, doc_path: Path) -> dict[str, Any] | None:
    raw_names = metadata.get("localized_names")
    if raw_names in (None, ""):
        return None
    if not isinstance(raw_names, dict):
        raise CatalogSiteError(f"{doc_path}: localized_names must be a mapping")
    storage = str(raw_names.get("storage") or "").strip()
    if storage != LOCALIZED_NAME_STORAGE:
        raise CatalogSiteError(f"{doc_path}: localized_names.storage must be {LOCALIZED_NAME_STORAGE!r}")
    join_key = str(raw_names.get("join_key") or "").strip()
    if join_key != LOCALIZED_NAMES_JOIN_KEY:
        raise CatalogSiteError(f"{doc_path}: localized_names.join_key must be {LOCALIZED_NAMES_JOIN_KEY!r}")
    localization_file = str(raw_names.get("localization_file") or "").strip()
    if not localization_file:
        raise CatalogSiteError(f"{doc_path}: localized_names.localization_file is required")
    if not localization_file.startswith("latest/") or not localization_file.endswith(LOCALIZED_NAMES_LOCALIZATION_SUFFIX):
        raise CatalogSiteError(
            f"{doc_path}: localized_names.localization_file must be latest/{{asset-slug}}{LOCALIZED_NAMES_LOCALIZATION_SUFFIX}"
        )
    property_template = str(raw_names.get("property_template") or "").strip()
    if property_template != LOCALIZED_NAMES_PROPERTY_TEMPLATE:
        raise CatalogSiteError(
            f"{doc_path}: localized_names.property_template must be {LOCALIZED_NAMES_PROPERTY_TEMPLATE!r}"
        )
    locale_code_format = str(raw_names.get("locale_code_format") or "").strip()
    if locale_code_format != LOCALIZED_NAMES_LOCALE_CODE_FORMAT:
        raise CatalogSiteError(
            f"{doc_path}: localized_names.locale_code_format must be {LOCALIZED_NAMES_LOCALE_CODE_FORMAT!r}"
        )
    fallback_field = str(raw_names.get("fallback_field") or "").strip()
    if fallback_field != LOCALIZED_NAMES_FALLBACK_FIELD:
        raise CatalogSiteError(f"{doc_path}: localized_names.fallback_field must be {LOCALIZED_NAMES_FALLBACK_FIELD!r}")
    raw_translations = raw_names.get("translations", [])
    if not isinstance(raw_translations, list):
        raise CatalogSiteError(f"{doc_path}: localized_names.translations must be a list")

    translations: list[dict[str, str]] = []
    seen_locales: set[str] = set()
    seen_fields: set[str] = set()
    seen_review_state_fields: set[str] = set()
    for index, raw_translation in enumerate(raw_translations, start=1):
        context = f"localized_names.translations[{index}]"
        if not isinstance(raw_translation, dict):
            raise CatalogSiteError(f"{doc_path}: {context} must be a mapping")
        locale_code = normalize_locale_code(raw_translation.get("locale_code"), context=f"{context}.locale_code", doc_path=doc_path)
        field = str(raw_translation.get("field") or "").strip()
        expected_field = f"name_{locale_code}"
        if field != expected_field:
            raise CatalogSiteError(f"{doc_path}: {context}.field must be {expected_field!r}")
        review_state_field = str(raw_translation.get("review_state_field") or "").strip()
        expected_review_state_field = f"{expected_field}_review_state"
        if review_state_field != expected_review_state_field:
            raise CatalogSiteError(f"{doc_path}: {context}.review_state_field must be {expected_review_state_field!r}")
        if locale_code in seen_locales:
            raise CatalogSiteError(f"{doc_path}: localized_names locale_code {locale_code!r} is duplicated")
        if field in seen_fields:
            raise CatalogSiteError(f"{doc_path}: localized_names field {field!r} is duplicated")
        if review_state_field in seen_review_state_fields:
            raise CatalogSiteError(f"{doc_path}: localized_names review_state_field {review_state_field!r} is duplicated")
        seen_locales.add(locale_code)
        seen_fields.add(field)
        seen_review_state_fields.add(review_state_field)
        translation = {"locale_code": locale_code, "field": field, "review_state_field": review_state_field}
        label = str(raw_translation.get("label") or "").strip()
        if label:
            translation["label"] = label
        elif "label" in raw_translation:
            raise CatalogSiteError(f"{doc_path}: {context}.label must be non-empty when provided")
        review_state = str(raw_translation.get("review_state") or "").strip()
        if not review_state:
            raise CatalogSiteError(f"{doc_path}: {context}.review_state is required")
        if review_state not in LOCALIZED_NAME_REVIEW_STATES:
            allowed = ", ".join(sorted(LOCALIZED_NAME_REVIEW_STATES))
            raise CatalogSiteError(f"{doc_path}: {context}.review_state must be one of: {allowed}")
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
    fallback_locale = str(raw_names.get("fallback_locale") or "").strip()
    if fallback_locale:
        fallback_locale = normalize_locale_code(fallback_locale, context="localized_names.fallback_locale", doc_path=doc_path)
        normalized["fallback_locale"] = fallback_locale
    normalized["available_locales"] = [translation["locale_code"] for translation in translations]
    normalized["translations"] = translations
    return normalized


def optional_feature_metadata(metadata: dict[str, Any], *, asset_slug: str, doc_path: Path) -> dict[str, Any] | None:
    value = metadata.get("feature_metadata")
    if value in (None, ""):
        return None
    if not isinstance(value, dict):
        raise CatalogSiteError(f"{doc_path}: feature_metadata must be a mapping")
    expected = {
        "storage": FEATURE_METADATA_STORAGE,
        "index_backend": FEATURE_METADATA_INDEX_BACKEND,
        "feature_id_column": FEATURE_METADATA_FEATURE_ID_COLUMN,
        "feature_hash_column": FEATURE_METADATA_FEATURE_HASH_COLUMN,
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


def localized_name_review_states(localized_names: dict[str, Any] | None) -> list[str]:
    if not localized_names:
        return []
    translations = localized_names.get("translations")
    if not isinstance(translations, list):
        return []
    return [
        f"{translation['locale_code']}:{translation['review_state']}"
        for translation in translations
        if isinstance(translation, dict) and translation.get("locale_code") and translation.get("review_state")
    ]


def optional_generated_group_id(metadata: dict[str, Any], *, row_count: int | None, doc_path: Path) -> dict[str, Any] | None:
    raw_group_id = metadata.get("generated_group_id")
    if raw_group_id in (None, ""):
        return None
    if not isinstance(raw_group_id, dict):
        raise CatalogSiteError(f"{doc_path}: generated_group_id must be a mapping")
    column = str(raw_group_id.get("column") or "").strip()
    if column != GENERATED_GROUP_ID_COLUMN:
        raise CatalogSiteError(f"{doc_path}: generated_group_id.column must be {GENERATED_GROUP_ID_COLUMN}")
    algorithm = str(raw_group_id.get("algorithm") or "").strip()
    if algorithm != GENERATED_GROUP_ID_ALGORITHM:
        raise CatalogSiteError(f"{doc_path}: generated_group_id.algorithm must be {GENERATED_GROUP_ID_ALGORITHM}")
    raw_fields = raw_group_id.get("grouping_fields")
    if not isinstance(raw_fields, list):
        raise CatalogSiteError(f"{doc_path}: generated_group_id.grouping_fields must be a non-empty list")
    grouping_fields = [str(field).strip() for field in raw_fields if str(field).strip()]
    if not grouping_fields:
        raise CatalogSiteError(f"{doc_path}: generated_group_id.grouping_fields must be a non-empty list")
    token_length = profile_int(
        raw_group_id.get("token_length"),
        label="generated_group_id.token_length",
        doc_path=doc_path,
        required=True,
    )
    if token_length is not None and token_length < 8:
        raise CatalogSiteError(f"{doc_path}: generated_group_id.token_length must be at least 8")
    group_count = profile_int(
        raw_group_id.get("group_count"),
        label="generated_group_id.group_count",
        doc_path=doc_path,
        required=True,
        row_count=row_count,
    )
    blank_group_count = profile_int(
        raw_group_id.get("blank_group_count"),
        label="generated_group_id.blank_group_count",
        doc_path=doc_path,
        row_count=row_count,
    )
    stability = str(raw_group_id.get("stability") or "").strip()
    if not stability:
        raise CatalogSiteError(f"{doc_path}: generated_group_id.stability is required")
    normalized: dict[str, Any] = {
        "column": column,
        "algorithm": algorithm,
        "grouping_fields": grouping_fields,
        "token_length": token_length,
        "group_count": group_count,
        "stability": stability,
    }
    if blank_group_count is not None:
        normalized["blank_group_count"] = blank_group_count
    notes = str(raw_group_id.get("notes") or "").strip()
    if notes:
        normalized["notes"] = notes
    return normalized


def optional_generated_row_id(metadata: dict[str, Any], *, row_count: int | None, doc_path: Path) -> dict[str, Any] | None:
    raw_row_id = metadata.get("generated_row_id")
    if raw_row_id in (None, ""):
        return None
    if metadata.get("generated_group_id") not in (None, ""):
        raise CatalogSiteError(f"{doc_path}: generated_group_id and generated_row_id are mutually exclusive")
    if not isinstance(raw_row_id, dict):
        raise CatalogSiteError(f"{doc_path}: generated_row_id must be a mapping")
    column = str(raw_row_id.get("column") or "").strip()
    if column != GENERATED_ROW_ID_COLUMN:
        raise CatalogSiteError(f"{doc_path}: generated_row_id.column must be {GENERATED_ROW_ID_COLUMN}")
    algorithm = str(raw_row_id.get("algorithm") or "").strip()
    if algorithm != GENERATED_ROW_ID_ALGORITHM:
        raise CatalogSiteError(f"{doc_path}: generated_row_id.algorithm must be {GENERATED_ROW_ID_ALGORITHM}")
    token_length = profile_int(
        raw_row_id.get("token_length"),
        label="generated_row_id.token_length",
        doc_path=doc_path,
        required=True,
    )
    if token_length is not None and token_length < 8:
        raise CatalogSiteError(f"{doc_path}: generated_row_id.token_length must be at least 8")
    generated_row_count = profile_int(
        raw_row_id.get("row_count"),
        label="generated_row_id.row_count",
        doc_path=doc_path,
        required=True,
        row_count=row_count,
    )
    duplicate_geometry_row_count = profile_int(
        raw_row_id.get("duplicate_geometry_row_count"),
        label="generated_row_id.duplicate_geometry_row_count",
        doc_path=doc_path,
        row_count=row_count,
    )
    duplicate_geometry_digest_count = profile_int(
        raw_row_id.get("duplicate_geometry_digest_count"),
        label="generated_row_id.duplicate_geometry_digest_count",
        doc_path=doc_path,
        row_count=row_count,
    )
    stability = str(raw_row_id.get("stability") or "").strip()
    if not stability:
        raise CatalogSiteError(f"{doc_path}: generated_row_id.stability is required")
    warning = str(raw_row_id.get("warning") or "").strip()
    if not warning:
        raise CatalogSiteError(f"{doc_path}: generated_row_id.warning is required")
    normalized: dict[str, Any] = {
        "column": column,
        "algorithm": algorithm,
        "token_length": token_length,
        "row_count": generated_row_count,
        "stability": stability,
        "warning": warning,
    }
    if duplicate_geometry_row_count is not None:
        normalized["duplicate_geometry_row_count"] = duplicate_geometry_row_count
    if duplicate_geometry_digest_count is not None:
        normalized["duplicate_geometry_digest_count"] = duplicate_geometry_digest_count
    notes = str(raw_row_id.get("notes") or "").strip()
    if notes:
        normalized["notes"] = notes
    return normalized


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
    for field in REQUIRED_FIELDS:
        if not (row.get(field) or "").strip():
            raise CatalogSiteError(f"{prefix}: missing required field {field!r}")
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
        for field in ("lifecycle_reason", "lifecycle_date", "consumer_guidance"):
            if not (row.get(field) or "").strip():
                raise CatalogSiteError(f"{prefix}: non-active assets require {field!r}")
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
    localized_names = optional_localized_names(doc_metadata, doc_path=doc_path)
    if localized_names:
        expected_localization_file = f"latest/{slug}{LOCALIZED_NAMES_LOCALIZATION_SUFFIX}"
        if localized_names.get("localization_file") != expected_localization_file:
            raise CatalogSiteError(
                f"{doc_path}: localized_names.localization_file must be {expected_localization_file!r}"
            )
    localized_name_locales = localized_names["available_locales"] if localized_names else []
    localized_name_review_state_values = localized_name_review_states(localized_names)
    catalog_localized_name_locales = split_semicolon(row.get("localized_name_locales", ""))
    if localized_name_locales != catalog_localized_name_locales:
        raise CatalogSiteError(f"{doc_path}: localized_name_locales must match localized_names translations")
    catalog_localized_name_review_states = split_semicolon(row.get("localized_name_review_states", ""))
    if localized_name_review_state_values != catalog_localized_name_review_states:
        raise CatalogSiteError(f"{doc_path}: localized_name_review_states must match localized_names translations")
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
        localized_name_locales=localized_name_locales,
        localized_name_review_states=localized_name_review_state_values,
        has_pmtiles="pmtiles" in formats,
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
        localized_names=localized_names,
        feature_metadata=optional_feature_metadata(doc_metadata, asset_slug=slug, doc_path=doc_path),
        generated_group_id=optional_generated_group_id(doc_metadata, row_count=row_count, doc_path=doc_path),
        generated_row_id=optional_generated_row_id(doc_metadata, row_count=row_count, doc_path=doc_path),
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
    pmtiles_path = latest_version.pmtiles_path if "pmtiles" in formats else None
    return replace(
        asset,
        canonical_path=latest_version.canonical_path,
        available_formats=formats,
        has_pmtiles="pmtiles" in formats,
        has_geojson="geojson" in formats,
        has_csv="csv" in formats,
        public_url=latest_version.public_url,
        pmtiles_path=pmtiles_path,
        pmtiles_url=latest_version.pmtiles_url if pmtiles_path else None,
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
        force_access_tier=args.force_access_tier,
        generated_at=args.generated_at,
    )
    print(json.dumps({"output": str(args.out), "files": [str(path) for path in written]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
