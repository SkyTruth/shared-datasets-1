#!/usr/bin/env python3
"""Plan a manual shared-datasets publish without writing remote objects."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import random
import re
import shutil
import shlex
import subprocess
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_BUCKET = "skytruth-shared-datasets-1"
PREVIEW_BUCKET = "skytruth-shared-datasets-1-preview"
WORKFLOW_SCHEMA_VERSION = 1
WORKFLOW_COMMANDS = {"start", "next", "confirm", "status", "render-pr", "render-report", "validate"}
GENERATED_ROW_ID_COLUMN = "shared_datasets_row_id"
GENERATED_ROW_ID_ALGORITHM = "shared-datasets-row-id:v1"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROPOSAL_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PREVIEW_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
FOOTPRINT_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>kb|mb|gb|tb|kib|mib|gib|tib)\b", re.IGNORECASE)
VECTOR_SOURCE_EXTENSIONS = {".shp", ".gpkg", ".geojson", ".json", ".fgb"}
FORMAT_EXTENSIONS = {
    ".fgb": "fgb",
    ".pmtiles": "pmtiles",
    ".geojson": "geojson",
    ".ndgeojson": "ndgeojson",
    ".csv": "csv",
    ".tif": "cog",
    ".tiff": "cog",
}
FORMAT_FILE_EXTENSIONS = {
    "fgb": "fgb",
    "pmtiles": "pmtiles",
    "geojson": "geojson",
    "ndgeojson": "ndgeojson",
    "csv": "csv",
    "cog": "tif",
    "zarr": "zarr",
}
SUPPORTED_CANONICAL_FORMATS = {"fgb", "cog", "zarr", "pmtiles", "geojson", "ndgeojson", "csv"}
PROFILE_RANDOM_SAMPLE_SIZE = int(os.environ.get("SHARED_DATASETS_PROFILE_SAMPLE_SIZE", "10000"))
PROFILE_RANDOM_SEED = int(os.environ.get("SHARED_DATASETS_PROFILE_RANDOM_SEED", "0"))
PROFILE_WITH_GDAL_ENV = "SHARED_DATASETS_PROFILE_WITH_GDAL"
OGR_PROFILE_TIMEOUT_SECONDS = int(os.environ.get("SHARED_DATASETS_OGR_PROFILE_TIMEOUT_SECONDS", "30"))
MAX_IN_MEMORY_GEOJSON_BYTES = 5 * 1024 * 1024
ID_FIELD_RE = re.compile(
    r"(^id$|_id$|^id_|uuid|guid|external.?id|ext.?id|source.?id|objectid|mrgid|wdpaid|primkey|primary.?key|(^|_)key$)",
    re.IGNORECASE,
)
GROUP_FIELD_RE = re.compile(
    r"(^name$|name$|title|label|site|region|zone|area_name|place|locality|unit|country|layer|type|class|status|category|basin|province)",
    re.IGNORECASE,
)
MEASUREMENT_FIELD_RE = re.compile(
    r"(area|length|shape|perimeter|lat|lon|longitude|latitude|rank|zoom|count|width|height|depth|elev|elevation)$",
    re.IGNORECASE,
)
TEMPORAL_FIELD_RE = re.compile(r"(date|time|year|disc|prod)$", re.IGNORECASE)
LOW_INFORMATION_FIELD_RE = re.compile(
    r"(sourceinfo|otherinfo|fieldinfo|comment|comments|note|notes|description|remark|remarks|citation|reference|url|link)",
    re.IGNORECASE,
)
CODE_FIELD_RE = re.compile(r"(^|_)?(fips|cow|cont|iso)?code$|_code$", re.IGNORECASE)
NUMERIC_IDENTIFIER_FIELD_RE = re.compile(r"(num|number)$", re.IGNORECASE)
SENTINEL_VALUES = {
    "not reported",
    "not available",
    "not applicable",
    "unknown",
    "unspecified",
    "unreported",
    "n/a",
    "na",
    "none",
    "null",
    "nil",
    "no data",
    "nodata",
    "-9999",
    "-9999.0",
    "-999",
    "-999.0",
}


class ConciergeError(ValueError):
    """Raised when a publish plan cannot be built."""


class WorkflowError(ValueError):
    """Raised when a stateful publish workflow cannot advance."""


@dataclass(frozen=True)
class TopValueExample:
    value: str
    count: int
    percent: float
    is_sentinel: bool = False


@dataclass(frozen=True)
class FieldProfile:
    name: str
    datatype: str
    distinct_values: int
    distinction_percent: float
    non_empty_values: int
    empty_values: int
    emptiness_percent: float
    top_value_count: int
    domination_percent: float
    skew_ratio: float | None
    duplicate_value_count: int
    duplicate_row_count: int
    sentinel_value_count: int
    sentinel_value_percent: float
    top_examples: list[TopValueExample]
    average_value_length: float | None = None
    role_hint: str = "unlikely"
    hidden_reason: str | None = None


@dataclass(frozen=True)
class FieldRecommendation:
    field: str
    reason: str
    role: str = "candidate"
    datatype: str | None = None
    distinct_values: int | None = None
    non_empty_values: int | None = None
    empty_values: int | None = None
    distinction_percent: float | None = None
    emptiness_percent: float | None = None
    domination_percent: float | None = None
    skew_ratio: float | None = None
    duplicate_value_count: int | None = None
    duplicate_row_count: int | None = None
    sentinel_value_count: int | None = None
    sentinel_value_percent: float | None = None
    top_examples: list[TopValueExample] = dataclass_field(default_factory=list)
    concerns: list[str] = dataclass_field(default_factory=list)
    confidence: str = "medium"


@dataclass(frozen=True)
class GeneratedRowIdOption:
    available: bool
    column: str = GENERATED_ROW_ID_COLUMN
    algorithm: str = GENERATED_ROW_ID_ALGORITHM
    reason: str = (
        "Last-resort per-row geometry-address fallback when no provider ext_id and no curator-approved grouping field is suitable."
    )
    warning: str = (
        "Not a provider or entity ID; stable only while canonical geometry and duplicate-geometry source order remain unchanged."
    )


@dataclass(frozen=True)
class CuratorFieldOptions:
    id_field_candidates: list[FieldRecommendation]
    group_field_candidates: list[FieldRecommendation]
    notes: list[str]
    generated_row_id_option: GeneratedRowIdOption = dataclass_field(default_factory=lambda: GeneratedRowIdOption(False))
    total_rows: int | None = None
    total_columns: int | None = None
    profiled_row_count: int | None = None
    profile_scope: str = "unavailable"
    hidden_unlikely_count: int = 0
    all_fields_profile: list[FieldProfile] = dataclass_field(default_factory=list)


@dataclass(frozen=True)
class ConciergePlan:
    asset_slug: str
    title: str
    category: str
    subcategory: str
    canonical_format: str
    available_formats: list[str]
    asset_root: str
    canonical_path: str
    asset_doc_path: str
    standard_work_dir: str
    publish_dir: str
    release_date: str | None
    blocking_questions: list[str]
    suggested_commands: list[str]
    remote_write_commands: list[str]
    notes: list[str]
    curator_field_options: CuratorFieldOptions
    source_resolution_meters: float | None = None
    source_scale_denominator: float | None = None
    pmtiles_maxzoom: int | None = None
    pmtiles_maxzoom_reason: str | None = None
    pmtiles_detail_hint: str | None = None


@dataclass(frozen=True)
class StepInstruction:
    step_id: str
    title: str
    summary: str
    commands: list[str]
    evidence_schema: dict[str, Any]
    blockers: list[str] = dataclass_field(default_factory=list)
    optional: bool = False


@dataclass(frozen=True)
class StepDefinition:
    step_id: str
    title: str
    summary: str
    evidence_schema: dict[str, Any]
    render_commands: Callable[[dict[str, Any]], list[str]]
    validate_evidence: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    is_required: Callable[[dict[str, Any]], bool] = lambda _state: True
    allow_yes: bool = False
    optional: bool = False


def load_categories(path: Path) -> dict[str, set[str]]:
    payload = yaml.safe_load(path.read_text()) or {}
    categories = payload.get("categories") or {}
    return {
        str(category): set((data.get("subcategories") or {}).keys())
        for category, data in categories.items()
    }


def infer_slug(source: Path) -> str:
    stem = source.name
    for suffix in (".tar.gz", ".zip"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    else:
        stem = source.stem
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", stem).strip("-").lower()
    normalized = re.sub(r"-+", "-", normalized)
    if not normalized or not SLUG_RE.fullmatch(normalized):
        raise ConciergeError("could not infer a lowercase kebab-case asset slug; pass --asset-slug")
    return normalized


def infer_format(source: Path, explicit: str | None) -> str:
    if explicit:
        normalized = explicit.strip().lower()
        if normalized not in SUPPORTED_CANONICAL_FORMATS:
            raise ConciergeError(f"unsupported canonical format: {explicit!r}")
        return normalized
    if source.is_dir() and source.name.lower().endswith(".zarr"):
        return "zarr"
    suffix = source.suffix.lower()
    if suffix in FORMAT_EXTENSIONS:
        return FORMAT_EXTENSIONS[suffix]
    if suffix in VECTOR_SOURCE_EXTENSIONS:
        return "fgb"
    raise ConciergeError(f"could not infer canonical format from {source}; pass --canonical-format")


def validate_taxonomy(category: str, subcategory: str, categories: dict[str, set[str]]) -> None:
    if category not in categories:
        raise ConciergeError(f"unknown category {category!r}")
    if subcategory not in categories[category]:
        raise ConciergeError(f"unknown subcategory {category}/{subcategory}")


def normalize_field_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def field_is_measurement(field: str) -> bool:
    normalized = normalize_field_name(field)
    return bool(MEASUREMENT_FIELD_RE.search(normalized)) and "name" not in normalized


def field_is_id_like(field: str) -> bool:
    normalized = normalize_field_name(field)
    return bool(ID_FIELD_RE.search(normalized)) and not normalized.startswith("metadata")


def field_is_group_like(field: str) -> bool:
    normalized = normalize_field_name(field)
    return bool(GROUP_FIELD_RE.search(normalized)) and not field_is_measurement(normalized)


def field_is_temporal(field: str, datatype: str | None = None) -> bool:
    normalized = normalize_field_name(field)
    return bool(TEMPORAL_FIELD_RE.search(normalized)) or datatype in {"date", "datetime"}


def field_is_low_information(field: str) -> bool:
    normalized = normalize_field_name(field)
    if normalized in {"source_layer", "layer_name"} or normalized.endswith("_id"):
        return False
    return bool(LOW_INFORMATION_FIELD_RE.search(normalized)) or normalized.endswith("source")


def field_is_code_like(field: str) -> bool:
    normalized = normalize_field_name(field)
    return bool(CODE_FIELD_RE.search(normalized))


def field_is_numeric_identifier_like(field: str) -> bool:
    normalized = normalize_field_name(field)
    return bool(NUMERIC_IDENTIFIER_FIELD_RE.search(normalized))


def normalize_profile_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return " ".join(str(value).strip().split())


def normalized_for_matching(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def value_is_sentinel(value: str) -> bool:
    normalized = normalized_for_matching(value)
    if not normalized:
        return False
    if normalized in SENTINEL_VALUES:
        return True
    try:
        numeric = float(normalized.replace(",", ""))
    except ValueError:
        return False
    return numeric in {-9999.0, -999.0}


def percent(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100, 2)


def ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 2)


def looks_like_bool(value: str) -> bool:
    return normalized_for_matching(value) in {"true", "false", "t", "f", "yes", "no", "y", "n", "0", "1"}


def looks_like_int(value: str) -> bool:
    return bool(re.fullmatch(r"[+-]?\d+", value.replace(",", "")))


def looks_like_float(value: str) -> bool:
    try:
        float(value.replace(",", ""))
    except ValueError:
        return False
    return True


def looks_like_datetime(value: str) -> bool:
    candidate = value.strip()
    if "T" not in candidate and " " not in candidate:
        return False
    normalized = candidate.replace("Z", "+00:00")
    try:
        dt.datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return True


def looks_like_date(value: str) -> bool:
    candidate = value.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
        return False
    try:
        dt.date.fromisoformat(candidate)
    except ValueError:
        return False
    return True


def infer_datatype(values: Iterable[str]) -> str:
    samples = [value for value in values if value and not value_is_sentinel(value)]
    if not samples:
        return "empty"
    if all(looks_like_bool(value) for value in samples):
        return "boolean"
    if all(looks_like_int(value) for value in samples):
        return "integer"
    if all(looks_like_float(value) for value in samples):
        return "float"
    if all(looks_like_datetime(value) for value in samples):
        return "datetime"
    if all(looks_like_date(value) for value in samples):
        return "date"
    return "string"


def duplicate_counts(values: list[str]) -> tuple[int, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    repeated = [count for count in counts.values() if count > 1]
    return len(repeated), sum(repeated)


def field_profile_from_counts(
    name: str,
    counts: Counter[str],
    *,
    empty_values: int,
    profiled_row_count: int,
    type_samples: Sequence[str],
    total_value_length: int,
) -> FieldProfile:
    non_empty = sum(counts.values())
    distinct = len(counts)
    duplicate_value_count = sum(1 for count in counts.values() if count > 1)
    duplicate_row_count = sum(count for count in counts.values() if count > 1)
    top_values = counts.most_common(5)
    top_count = top_values[0][1] if top_values else 0
    expected_top_count = non_empty / distinct if distinct else None
    skew_ratio = (top_count / expected_top_count) if expected_top_count else None
    sentinel_count = sum(count for value, count in counts.items() if value_is_sentinel(value))
    top_examples = [
        TopValueExample(
            value=value,
            count=count,
            percent=percent(count, profiled_row_count),
            is_sentinel=value_is_sentinel(value),
        )
        for value, count in top_values
    ]
    average_value_length = round(total_value_length / non_empty, 2) if non_empty else None
    profile = FieldProfile(
        name=name,
        datatype=infer_datatype(type_samples),
        distinct_values=distinct,
        distinction_percent=percent(distinct, profiled_row_count),
        non_empty_values=non_empty,
        empty_values=empty_values,
        emptiness_percent=percent(empty_values, profiled_row_count),
        top_value_count=top_count,
        domination_percent=percent(top_count, profiled_row_count),
        skew_ratio=ratio(skew_ratio),
        duplicate_value_count=duplicate_value_count,
        duplicate_row_count=duplicate_row_count,
        sentinel_value_count=sentinel_count,
        sentinel_value_percent=percent(sentinel_count, profiled_row_count),
        top_examples=top_examples,
        average_value_length=average_value_length,
    )
    role_hint, hidden_reason = classify_field_profile(profile)
    return FieldProfile(
        name=profile.name,
        datatype=profile.datatype,
        distinct_values=profile.distinct_values,
        distinction_percent=profile.distinction_percent,
        non_empty_values=profile.non_empty_values,
        empty_values=profile.empty_values,
        emptiness_percent=profile.emptiness_percent,
        top_value_count=profile.top_value_count,
        domination_percent=profile.domination_percent,
        skew_ratio=profile.skew_ratio,
        duplicate_value_count=profile.duplicate_value_count,
        duplicate_row_count=profile.duplicate_row_count,
        sentinel_value_count=profile.sentinel_value_count,
        sentinel_value_percent=profile.sentinel_value_percent,
        top_examples=profile.top_examples,
        average_value_length=profile.average_value_length,
        role_hint=role_hint,
        hidden_reason=hidden_reason,
    )


def build_field_profiles(rows: Sequence[dict[str, Any]]) -> list[FieldProfile]:
    if not rows:
        return []
    field_names: list[str] = []
    seen_fields: set[str] = set()
    counts_by_field: dict[str, Counter[str]] = {}
    empty_by_field: dict[str, int] = {}
    samples_by_field: dict[str, list[str]] = {}
    length_by_field: dict[str, int] = {}

    for row_index, row in enumerate(rows):
        for field in row.keys():
            if field in seen_fields:
                continue
            seen_fields.add(field)
            field_names.append(field)
            counts_by_field[field] = Counter()
            empty_by_field[field] = row_index
            samples_by_field[field] = []
            length_by_field[field] = 0
        for field in field_names:
            value = normalize_profile_value(row.get(field))
            if not value:
                empty_by_field[field] += 1
                continue
            counts_by_field[field][value] += 1
            length_by_field[field] += len(value)
            if len(samples_by_field[field]) < 1000:
                samples_by_field[field].append(value)

    profiled_row_count = len(rows)
    return [
        field_profile_from_counts(
            field,
            counts_by_field[field],
            empty_values=empty_by_field[field],
            profiled_row_count=profiled_row_count,
            type_samples=samples_by_field[field],
            total_value_length=length_by_field[field],
        )
        for field in field_names
    ]


def profile_row_iter(
    rows: Iterable[dict[str, Any]],
    *,
    sample_size: int = PROFILE_RANDOM_SAMPLE_SIZE,
    random_seed: int = PROFILE_RANDOM_SEED,
) -> tuple[list[dict[str, Any]], int, str]:
    sample: list[dict[str, Any]] = []
    rng = random.Random(random_seed)
    total_rows = 0
    for row in rows:
        total_rows += 1
        row_copy = dict(row)
        if len(sample) < sample_size:
            sample.append(row_copy)
            continue
        replacement_index = rng.randrange(total_rows)
        if replacement_index < sample_size:
            sample[replacement_index] = row_copy
    profile_scope = "full" if total_rows <= sample_size else "random_sample"
    return sample, total_rows, profile_scope


def recommendation_concerns(profile: FieldProfile, *, provider: bool) -> list[str]:
    concerns: list[str] = []
    if profile.empty_values:
        concerns.append(f"{profile.empty_values:,} empty value(s)")
    if profile.sentinel_value_count:
        concerns.append(f"{profile.sentinel_value_count:,} sentinel-like value(s)")
    if profile.duplicate_value_count and provider:
        concerns.append(
            f"{profile.duplicate_value_count:,} duplicate value(s) across {profile.duplicate_row_count:,} row(s)"
        )
    if profile.domination_percent >= 25:
        top = profile.top_examples[0] if profile.top_examples else None
        if top:
            concerns.append(f"top value {top.value!r} has {top.count:,} row(s)")
    if profile.skew_ratio is not None and profile.skew_ratio >= 50:
        concerns.append(f"high skew ratio {profile.skew_ratio:g}")
    if not provider and profile.distinction_percent > 95:
        concerns.append("near-row-unique; usually search-only, not grouping")
    if not provider and (profile.distinction_percent <= 1 or profile.distinct_values <= 5):
        concerns.append("very low distinction; treat as filter/facet, not generated group ID")
    return concerns


def provider_recommendation(profile: FieldProfile) -> FieldRecommendation | None:
    if profile.distinct_values <= 1 or field_is_measurement(profile.name):
        return None
    id_like = field_is_id_like(profile.name)
    if not id_like:
        return None
    concerns = recommendation_concerns(profile, provider=True)
    if profile.emptiness_percent >= 10:
        confidence = "low"
        reason = "ID-like field name, but too many rows are empty for a provider row ID."
    elif profile.distinction_percent >= 95:
        confidence = "high"
        if profile.duplicate_value_count:
            reason = "ID-like field with near-row-unique values; duplicate values need curator review."
        else:
            reason = "ID-like field with row-unique non-empty values."
    elif profile.distinction_percent >= 80:
        confidence = "medium"
        reason = "ID-like field with high distinction, but not row-unique."
    else:
        confidence = "low"
        reason = "ID-like field name, but values are not unique enough for a provider row ID."
    return FieldRecommendation(
        field=profile.name,
        role="provider ext_id candidate",
        reason=reason,
        datatype=profile.datatype,
        distinct_values=profile.distinct_values,
        non_empty_values=profile.non_empty_values,
        empty_values=profile.empty_values,
        distinction_percent=profile.distinction_percent,
        emptiness_percent=profile.emptiness_percent,
        domination_percent=profile.domination_percent,
        skew_ratio=profile.skew_ratio,
        duplicate_value_count=profile.duplicate_value_count,
        duplicate_row_count=profile.duplicate_row_count,
        sentinel_value_count=profile.sentinel_value_count,
        sentinel_value_percent=profile.sentinel_value_percent,
        top_examples=profile.top_examples,
        concerns=concerns,
        confidence=confidence,
    )


def grouping_role(profile: FieldProfile) -> str | None:
    if profile.distinct_values <= 1:
        return None
    if field_is_measurement(profile.name) or profile.datatype == "float":
        return None
    if field_is_id_like(profile.name) and profile.distinction_percent >= 60:
        return None
    if field_is_low_information(profile.name):
        return None
    if field_is_code_like(profile.name):
        return None
    group_like = field_is_group_like(profile.name)
    temporal = field_is_temporal(profile.name, profile.datatype)
    if profile.emptiness_percent >= 10 and not group_like:
        return None
    if profile.sentinel_value_percent >= 10 and not group_like:
        return None
    if profile.datatype == "integer" and not temporal:
        if not group_like or field_is_numeric_identifier_like(profile.name):
            return None
    if group_like or temporal or profile.datatype in {"string", "integer", "date", "datetime", "boolean"}:
        if profile.distinction_percent <= 1 or profile.distinct_values <= 5:
            return "filter field"
        if profile.distinction_percent <= 60:
            return "grouping/search candidate"
        if profile.distinction_percent <= 95:
            return "search candidate"
        return "row-like search field"
    return None


def group_recommendation(profile: FieldProfile) -> FieldRecommendation | None:
    role = grouping_role(profile)
    if not role:
        return None
    concerns = recommendation_concerns(profile, provider=False)
    if profile.domination_percent >= 50:
        confidence = "low"
        reason = "Candidate field, but the top value dominates the rows."
    elif role == "filter field":
        confidence = "medium"
        reason = "Low-cardinality field that may be useful as a filter/facet."
    elif role == "search candidate":
        confidence = "medium"
        reason = "High-cardinality field that may be useful for search; use grouping only with curator approval."
    elif role == "row-like search field":
        confidence = "low"
        reason = "Near-row-unique field; likely search-only rather than a generated group ID."
    else:
        confidence = "high" if field_is_group_like(profile.name) else "medium"
        reason = "Human-readable grouping/search field with repeated values."
    return FieldRecommendation(
        field=profile.name,
        role=role,
        reason=reason,
        datatype=profile.datatype,
        distinct_values=profile.distinct_values,
        non_empty_values=profile.non_empty_values,
        empty_values=profile.empty_values,
        distinction_percent=profile.distinction_percent,
        emptiness_percent=profile.emptiness_percent,
        domination_percent=profile.domination_percent,
        skew_ratio=profile.skew_ratio,
        duplicate_value_count=profile.duplicate_value_count,
        duplicate_row_count=profile.duplicate_row_count,
        sentinel_value_count=profile.sentinel_value_count,
        sentinel_value_percent=profile.sentinel_value_percent,
        top_examples=profile.top_examples,
        concerns=concerns,
        confidence=confidence,
    )


def classify_field_profile(profile: FieldProfile) -> tuple[str, str | None]:
    if provider_recommendation(profile):
        return "provider ext_id candidate", None
    role = grouping_role(profile)
    if role:
        return role, None
    if profile.distinct_values <= 1:
        return "unlikely", "single-value or empty field"
    if field_is_measurement(profile.name) or profile.datatype == "float":
        return "unlikely", "measurement/coordinate field"
    if field_is_low_information(profile.name):
        return "unlikely", "free-text source or notes field"
    if profile.sentinel_value_percent >= 10:
        return "unlikely", "sentinel-dominated field"
    if field_is_id_like(profile.name) and profile.distinction_percent < 80:
        return "unlikely", "ID-like name but low distinction"
    return "unlikely", "does not match provider ID or grouping/search heuristics"


def profile_rows(
    rows: Sequence[dict[str, Any]],
    *,
    total_rows: int | None = None,
    profile_scope: str = "full",
    generated_row_id_available: bool = False,
) -> CuratorFieldOptions:
    if not rows:
        return CuratorFieldOptions(
            [],
            [],
            ["No rows were available for field profiling."],
            total_rows=total_rows or 0,
            total_columns=0,
            profiled_row_count=0,
            profile_scope=profile_scope,
        )
    total = total_rows if total_rows is not None else len(rows)
    profiles = build_field_profiles(rows)
    id_candidates = [candidate for profile in profiles if (candidate := provider_recommendation(profile))]
    group_candidates = [candidate for profile in profiles if (candidate := group_recommendation(profile))]

    id_candidates.sort(key=lambda candidate: (candidate.confidence != "high", -(candidate.distinct_values or 0), candidate.field.lower()))
    group_candidates.sort(
        key=lambda candidate: (
            candidate.confidence != "high",
            candidate.field.lower() != "name",
            candidate.role == "filter field",
            -(candidate.distinct_values or 0),
            candidate.field.lower(),
        )
    )
    profiled_row_count = len(rows)
    if profile_scope == "random_sample":
        notes = [
            f"Profiled a deterministic random sample of {profiled_row_count:,} row(s) from {total:,} total source row(s).",
            "Column statistics are sample estimates; rerun with a larger SHARED_DATASETS_PROFILE_SAMPLE_SIZE if needed.",
        ]
    else:
        notes = [f"Profiled all {profiled_row_count:,} source row(s)."]
    hidden_unlikely_count = max(0, len(profiles) - len({candidate.field for candidate in [*id_candidates, *group_candidates]}))
    notes.append(
        f"{len(profiles):,} column(s) scanned; {hidden_unlikely_count:,} hidden as unlikely in the default decision table."
    )
    if not id_candidates:
        notes.append("No high-likelihood provider row ID field was found.")
    if not group_candidates:
        notes.append("No high-likelihood grouping/search field was found.")
    if generated_row_id_available:
        notes.append(
            f"Fallback {GENERATED_ROW_ID_COLUMN} is available only after the curator rejects provider IDs and generated group IDs."
        )
    return CuratorFieldOptions(
        id_candidates[:8],
        group_candidates[:8],
        notes,
        generated_row_id_option=GeneratedRowIdOption(generated_row_id_available),
        total_rows=total,
        total_columns=len(profiles),
        profiled_row_count=profiled_row_count,
        profile_scope=profile_scope,
        hidden_unlikely_count=hidden_unlikely_count,
        all_fields_profile=profiles,
    )


def read_csv_rows(source: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    rows = []
    for row in iter_csv_rows(source):
        rows.append(row)
        if limit is not None and len(rows) >= limit:
            break
    return rows


def iter_csv_rows(source: Path) -> Iterator[dict[str, Any]]:
    with source.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield dict(row)


def read_geojson_rows(source: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    if source.suffix.lower() == ".ndgeojson":
        rows = []
        for row in iter_ndgeojson_rows(source):
            rows.append(row)
            if limit is not None and len(rows) >= limit:
                break
        return rows

    text = source.read_text()
    stripped = text.lstrip()
    features: list[dict[str, Any]]
    if stripped.startswith("{"):
        payload = json.loads(text)
        features = payload.get("features") if isinstance(payload, dict) else []
        if not isinstance(features, list):
            return []
    else:
        features = [json.loads(line) for line in text.splitlines() if line.strip()]
    rows = []
    for feature in features:
        if isinstance(feature, dict) and isinstance(feature.get("properties"), dict):
            rows.append(dict(feature["properties"]))
            if limit is not None and len(rows) >= limit:
                break
    return rows


def iter_ndgeojson_rows(source: Path) -> Iterator[dict[str, Any]]:
    with source.open() as handle:
        for line in handle:
            if not line.strip():
                continue
            feature = json.loads(line)
            if isinstance(feature, dict) and isinstance(feature.get("properties"), dict):
                yield dict(feature["properties"])


def read_ogr_rows(source: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    if not shutil.which("ogr2ogr"):
        return []
    command = ["ogr2ogr", "-f", "CSV", "/vsistdout/", str(source)]
    if limit is not None:
        command.extend(["-limit", str(limit)])
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=OGR_PROFILE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return []
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    reader = csv.DictReader(io.StringIO(completed.stdout))
    return [{str(key): value for key, value in row.items() if key is not None} for row in reader]


def profile_iterable_rows(
    rows: Iterable[dict[str, Any]],
    *,
    generated_row_id_available: bool = False,
) -> CuratorFieldOptions:
    sampled_rows, total_rows, profile_scope = profile_row_iter(rows)
    return profile_rows(
        sampled_rows,
        total_rows=total_rows,
        profile_scope=profile_scope,
        generated_row_id_available=generated_row_id_available,
    )


def profile_ogr_rows(source: Path, *, timeout_seconds: int = OGR_PROFILE_TIMEOUT_SECONDS) -> CuratorFieldOptions:
    if not shutil.which("ogr2ogr"):
        return CuratorFieldOptions(
            [],
            [],
            [
                "Vector source schema was not profiled with GDAL during planning; set SHARED_DATASETS_PROFILE_WITH_GDAL=1 "
                "or profile the canonical artifact after conversion.",
            ],
        )
    command = ["ogr2ogr", "-f", "CSV", "/vsistdout/", str(source)]
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return CuratorFieldOptions(
            [],
            [],
            [f"Could not profile source attributes with ogr2ogr: timed out after {timeout_seconds} second(s)."],
        )
    if completed.returncode != 0:
        return CuratorFieldOptions(
            [],
            [],
            [f"Could not profile source attributes with ogr2ogr: {completed.stderr.strip() or f'exit code {completed.returncode}'}"],
        )
    if not completed.stdout.strip():
        return CuratorFieldOptions([], [], ["Could not profile source attributes with ogr2ogr: no CSV rows were emitted."])
    reader = csv.DictReader(io.StringIO(completed.stdout))
    rows = ({str(key): value for key, value in row.items() if key is not None} for row in reader)
    options = profile_iterable_rows(rows, generated_row_id_available=True)
    return CuratorFieldOptions(
        options.id_field_candidates,
        options.group_field_candidates,
        [
            *options.notes,
            "OGR attribute profiling uses all rows when at or below the sample threshold; larger sources use a deterministic random sample.",
            "Curator must choose grouping fields before generated IDs are built.",
        ],
        generated_row_id_option=options.generated_row_id_option,
        total_rows=options.total_rows,
        total_columns=options.total_columns,
        profiled_row_count=options.profiled_row_count,
        profile_scope=options.profile_scope,
        hidden_unlikely_count=options.hidden_unlikely_count,
        all_fields_profile=options.all_fields_profile,
    )


def ogrinfo_field_names(source: Path) -> list[str]:
    if not shutil.which("ogrinfo"):
        return []
    completed = subprocess.run(
        ["ogrinfo", "-ro", "-al", "-so", "-json", str(source)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    layers = payload.get("layers") or []
    if not layers:
        return []
    fields = layers[0].get("fields") or []
    return [str(field.get("name")) for field in fields if isinstance(field, dict) and field.get("name")]


def recommend_schema_fields(field_names: Sequence[str]) -> CuratorFieldOptions:
    id_candidates = [
        FieldRecommendation(field=field, reason="ID-like field name; verify uniqueness after conversion.", confidence="medium")
        for field in field_names
        if field_is_id_like(field)
    ]
    group_candidates = [
        FieldRecommendation(field=field, reason="Human-readable grouping/search field name; verify cardinality after conversion.", confidence="medium")
        for field in field_names
        if field_is_group_like(field)
    ]
    notes = ["Inspected source schema only; distinct and duplicate counts must be populated after canonical conversion."]
    if not id_candidates:
        notes.append("No high-likelihood provider row ID field name was found.")
    if not group_candidates:
        notes.append("No high-likelihood grouping/search field name was found.")
    return CuratorFieldOptions(
        id_candidates[:8],
        group_candidates[:8],
        notes,
        generated_row_id_option=GeneratedRowIdOption(True),
        total_columns=len(field_names),
        profile_scope="schema_only",
        hidden_unlikely_count=max(0, len(field_names) - len({candidate.field for candidate in [*id_candidates, *group_candidates]})),
    )


def recommend_curator_field_options(source: Path, canonical_format: str) -> CuratorFieldOptions:
    is_vector_fgb = canonical_format == "fgb"
    try:
        if source.suffix.lower() == ".csv":
            return profile_iterable_rows(iter_csv_rows(source), generated_row_id_available=is_vector_fgb)
        if source.suffix.lower() == ".ndgeojson":
            return profile_iterable_rows(iter_ndgeojson_rows(source), generated_row_id_available=is_vector_fgb)
        if source.suffix.lower() in {".geojson", ".json"}:
            if source.stat().st_size > MAX_IN_MEMORY_GEOJSON_BYTES:
                return CuratorFieldOptions(
                    [],
                    [],
                    [
                        "GeoJSON source is too large for in-memory planning-time profiling; "
                        "profile the canonical artifact after conversion.",
                    ],
                )
            return profile_rows(read_geojson_rows(source, limit=None), generated_row_id_available=is_vector_fgb)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error) as exc:
        return CuratorFieldOptions([], [], [f"Could not profile source attributes: {exc}"])

    if canonical_format == "fgb":
        if os.environ.get(PROFILE_WITH_GDAL_ENV) == "1":
            options = profile_ogr_rows(source)
            if options.profile_scope != "unavailable" or options.id_field_candidates or options.group_field_candidates:
                return options
            field_names = ogrinfo_field_names(source)
            if field_names:
                return recommend_schema_fields(field_names)
            return options
        return CuratorFieldOptions(
            [],
            [],
            [
                f"Vector source schema was not profiled with GDAL during planning; set {PROFILE_WITH_GDAL_ENV}=1 "
                "or profile the canonical artifact after conversion.",
            ],
            generated_row_id_option=GeneratedRowIdOption(True),
        )
    return CuratorFieldOptions(
        [],
        [],
        ["No source attribute profile was available; populate ID and group-field candidates after canonical conversion."],
    )


def build_plan(
    *,
    source: Path,
    asset_slug: str | None,
    title: str | None,
    category: str,
    subcategory: str,
    owner: str,
    source_name: str | None,
    license_text: str | None,
    citation: str | None,
    update_cadence: str,
    canonical_format: str | None,
    access_tier: str,
    bucket: str,
    release_date: str | None,
    source_resolution_meters: float | None = None,
    source_scale_denominator: float | None = None,
    pmtiles_maxzoom: int | None = None,
    pmtiles_maxzoom_reason: str | None = None,
    pmtiles_detail_hint: str | None = None,
    categories_path: Path,
    docs_dir: Path,
) -> ConciergePlan:
    if not source.exists():
        raise ConciergeError(f"source path does not exist: {source}")
    categories = load_categories(categories_path)
    validate_taxonomy(category, subcategory, categories)

    slug = asset_slug or infer_slug(source)
    if not SLUG_RE.fullmatch(slug):
        raise ConciergeError(f"asset slug must be lowercase kebab-case: {slug!r}")
    resolved_format = infer_format(source, canonical_format)
    curator_field_options = recommend_curator_field_options(source, resolved_format)
    if access_tier not in {"public", "private"}:
        raise ConciergeError("access tier must be public or private")
    if source_resolution_meters is not None and source_resolution_meters <= 0:
        raise ConciergeError("source resolution must be positive")
    if source_scale_denominator is not None and source_scale_denominator <= 0:
        raise ConciergeError("source scale denominator must be positive")
    if pmtiles_maxzoom is not None and not pmtiles_maxzoom_reason:
        raise ConciergeError("pmtiles maxzoom requires a reason")
    if pmtiles_detail_hint is not None and pmtiles_detail_hint not in {"coarse", "medium", "detailed"}:
        raise ConciergeError("pmtiles detail hint must be coarse, medium, or detailed")
    if release_date:
        try:
            parsed = dt.date.fromisoformat(release_date)
        except ValueError as exc:
            raise ConciergeError(f"release date must be YYYY-MM-DD: {release_date!r}") from exc
        if parsed.isoformat() != release_date:
            raise ConciergeError(f"release date must be zero-padded YYYY-MM-DD: {release_date!r}")

    dataset_title = title or slug.replace("-", " ").title()
    asset_root = f"{category}/{subcategory}/{slug}"
    ext = FORMAT_FILE_EXTENSIONS[resolved_format]
    canonical_file = f"latest/{slug}.{ext}" if resolved_format != "zarr" else "latest/manifest.json"
    canonical_path = f"gs://{bucket}/{asset_root}/{canonical_file}"
    work_dir = f"$TMPDIR/shared-datasets-1/vector-assets/{slug}"
    publish_dir = f"{work_dir}/publish"
    include_pmtiles = resolved_format == "fgb"
    formats = [resolved_format]
    if include_pmtiles:
        formats.append("pmtiles")

    blocking = []
    if not source_name:
        blocking.append("Confirm source name or URL.")
    if not license_text:
        blocking.append("Confirm license or terms.")
    if not citation:
        blocking.append("Confirm citation for the original source publication.")

    commands = [
        f"UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py validate-path {canonical_path}",
    ]
    if resolved_format == "fgb":
        command_parts = [
            "UV_CACHE_DIR=.uv-cache",
            "uv",
            "run",
            "python",
            "scripts/vector_asset.py",
            "build",
            str(source),
            "--asset-slug",
            slug,
            "--title",
            dataset_title,
            "--description",
            dataset_title + " vector tiles",
            "--maxzoom",
            "auto",
        ]
        if source_resolution_meters is not None:
            command_parts.extend(["--source-resolution-meters", str(source_resolution_meters)])
        if source_scale_denominator is not None:
            command_parts.extend(["--source-scale-denominator", str(source_scale_denominator)])
        if pmtiles_maxzoom is not None:
            command_parts.extend(["--pmtiles-maxzoom", str(pmtiles_maxzoom)])
            command_parts.extend(["--pmtiles-maxzoom-reason", pmtiles_maxzoom_reason or ""])
        if pmtiles_detail_hint:
            command_parts.extend(["--pmtiles-detail-hint", pmtiles_detail_hint])
        commands.append(shell_join(command_parts))
    elif resolved_format == "cog":
        commands.append(f"UV_CACHE_DIR=.uv-cache uv run python scripts/raster_asset.py validate-cog {source}")
    commands.extend(
        [
            "UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate",
            "UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py check",
        ]
    )

    remote_commands = []
    if release_date:
        remote_commands.append(
            "UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py publish-release "
            f"--asset-slug {slug} --release-date {release_date} --publish-dir {publish_dir} --dry-run"
        )
    else:
        remote_commands.append(
            "Use scripts/gcs_asset.py upload with no-clobber or --replace-generation after reviewing generated artifacts."
        )

    notes = [
        "This concierge plan does not write to Cloud Storage.",
        "Review generated docs/catalog diffs before any remote upload.",
        "Curator must choose provider ID candidates and grouping/search fields before publishing generated group IDs.",
    ]
    if resolved_format == "csv":
        notes.append("CSV must remain geometry-free under shared-datasets standards.")
    if include_pmtiles:
        notes.append("FGB vector assets require a PMTiles companion for catalog map preview.")
        notes.append(
            "If the curator chooses a generated group ID, run vector_asset.py build with "
            "--group-id-field FIELD; the helper will write shared_datasets_group_id into the FGB "
            "and require it in PMTiles feature properties."
        )
        notes.append(
            "PMTiles maxzoom is resolved after the canonical FGB is generated and profiled; "
            "the concierge does not assume a fallback zoom."
        )
        if any(
            value is not None
            for value in (
                source_resolution_meters,
                source_scale_denominator,
                pmtiles_maxzoom,
                pmtiles_detail_hint,
            )
        ):
            notes.append("PMTiles source/detail hints were included in the vector build command.")

    return ConciergePlan(
        asset_slug=slug,
        title=dataset_title,
        category=category,
        subcategory=subcategory,
        canonical_format=resolved_format,
        available_formats=formats,
        asset_root=asset_root,
        canonical_path=canonical_path,
        asset_doc_path=str(docs_dir / f"{slug}.md"),
        standard_work_dir=work_dir,
        publish_dir=publish_dir,
        release_date=release_date,
        blocking_questions=blocking,
        suggested_commands=commands,
        remote_write_commands=remote_commands,
        notes=notes,
        curator_field_options=curator_field_options,
        source_resolution_meters=source_resolution_meters,
        source_scale_denominator=source_scale_denominator,
        pmtiles_maxzoom=pmtiles_maxzoom,
        pmtiles_maxzoom_reason=pmtiles_maxzoom_reason,
        pmtiles_detail_hint=pmtiles_detail_hint,
    )


def shell_join(parts: Sequence[str]) -> str:
    if parts and "=" in parts[0]:
        return parts[0] + " " + shlex.join(parts[1:])
    return shlex.join(parts)


def draft_asset_doc(
    plan: ConciergePlan,
    *,
    owner: str,
    source_name: str | None,
    license_text: str | None,
    citation: str | None,
    update_cadence: str,
    access_tier: str,
) -> str:
    canonical_file = plan.canonical_path.split(f"{plan.asset_root}/", 1)[1]
    files = [
        {
            "path": canonical_file,
            "format": plan.canonical_format,
            "role": "canonical",
            "purpose": "Canonical dataset",
        }
    ]
    if "pmtiles" in plan.available_formats:
        files.append(
            {
                "path": f"latest/{plan.asset_slug}.pmtiles",
                "format": "pmtiles",
                "role": "companion",
                "purpose": "Web map tiles generated from the canonical vector dataset",
            }
        )
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "asset_slug": plan.asset_slug,
        "title": plan.title,
        "category": plan.category,
        "subcategory": plan.subcategory,
        "status": "active",
        "access_tier": access_tier,
        "owner": owner,
        "update_cadence": update_cadence,
        "canonical_format": plan.canonical_format,
        "canonical_file": canonical_file,
        "available_formats": plan.available_formats,
        "metadata_paths": ["README.md"],
        "source": source_name or "NEEDS SOURCE CONFIRMATION",
        "license": license_text or "NEEDS LICENSE CONFIRMATION",
        "citation": citation or "NEEDS CITATION CONFIRMATION",
        "notes": "Draft generated by scripts/publishing_concierge.py; review before publishing.",
        "files": files,
    }
    if plan.source_resolution_meters is not None:
        metadata["source_resolution_meters"] = plan.source_resolution_meters
    if plan.source_scale_denominator is not None:
        metadata["source_scale_denominator"] = plan.source_scale_denominator
    if plan.pmtiles_maxzoom is not None:
        metadata["pmtiles_maxzoom"] = plan.pmtiles_maxzoom
        metadata["pmtiles_maxzoom_reason"] = plan.pmtiles_maxzoom_reason
    if plan.pmtiles_detail_hint is not None:
        metadata["pmtiles_detail_hint"] = plan.pmtiles_detail_hint
    body = f"""# {plan.title}

<!-- BEGIN GENERATED asset-summary -->
Run `uv run python scripts/catalog_docs.py generate` after reviewing the frontmatter.
<!-- END GENERATED asset-summary -->

## What this is

Draft description. Replace this with a concise explanation of the dataset identity,
source, and intended shared use.

## When to use it

- Use this for ...
- Do not use this for ...

## Files

<!-- BEGIN GENERATED files-table -->
Run `uv run python scripts/catalog_docs.py generate` after reviewing the `files` list.
<!-- END GENERATED files-table -->

## Schema notes

Describe fields, geometry type, CRS, units, join keys, and source quirks.
After building the canonical artifact, populate frontmatter `row_count` and
`data_profile` from that artifact. Include `field_count`, checked identifier
candidates with distinct and duplicate counts, or `identity_candidates: []` with
a short no-candidate note.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `field_name` | type | Needs source confirmation. |

## Update notes

Drafted by `scripts/publishing_concierge.py`. Replace with the real publish
method, source version, release date, checks, and toolchain versions.

## Known caveats

List license, redistribution, completeness, freshness, and quality caveats.
"""
    return "---\n" + yaml.safe_dump(metadata, sort_keys=False, width=120) + "---\n\n" + body


def write_draft_doc(path: Path, text: str, *, overwrite: bool) -> None:
    if path.exists() and not overwrite:
        raise ConciergeError(f"refusing to overwrite existing asset doc: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def run_catalog_check() -> int:
    return subprocess.run(
        ["uv", "run", "python", "scripts/catalog_docs.py", "check"],
        check=False,
    ).returncode


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def standard_work_root() -> Path:
    configured = os.environ.get("SHARED_DATASETS_WORKDIR")
    if configured:
        return Path(configured)
    return Path(os.environ.get("TMPDIR", "/tmp")) / "shared-datasets-1"


def default_state_file(asset_slug: str, proposal_id: str) -> Path:
    return standard_work_root() / "publishing-concierge" / asset_slug / f"{proposal_id}.state.json"


def current_git_branch() -> str | None:
    completed = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        return None
    branch = completed.stdout.strip()
    return branch or None


def read_json_file(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise WorkflowError(f"{label} does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowError(f"{label} must contain a JSON object: {path}")
    return payload


def write_json_file(path: Path, payload: dict[str, Any], *, overwrite: bool = True) -> None:
    if path.exists() and not overwrite:
        raise WorkflowError(f"refusing to overwrite existing workflow state: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    state = read_json_file(path, label="workflow state")
    if state.get("schema_version") != WORKFLOW_SCHEMA_VERSION:
        raise WorkflowError(f"unsupported workflow schema_version: {state.get('schema_version')!r}")
    if state.get("workflow_type") != "first-upload":
        raise WorkflowError(f"unsupported workflow_type: {state.get('workflow_type')!r}")
    return state


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = utc_now()
    write_json_file(path, state)


def require_non_empty_string(evidence: dict[str, Any], key: str) -> str:
    value = evidence.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"evidence.{key} must be a non-empty string")
    return value.strip()


def require_bool(evidence: dict[str, Any], key: str, *, expected: bool | None = None) -> bool:
    value = evidence.get(key)
    if not isinstance(value, bool):
        raise WorkflowError(f"evidence.{key} must be a boolean")
    if expected is not None and value is not expected:
        raise WorkflowError(f"evidence.{key} must be {expected}")
    return value


def require_string_list(evidence: dict[str, Any], key: str, *, allow_empty: bool = False) -> list[str]:
    value = evidence.get(key)
    if isinstance(value, str):
        values = [value.strip()] if value.strip() else []
    elif isinstance(value, list):
        values = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise WorkflowError(f"evidence.{key} must contain only non-empty strings")
            values.append(item.strip())
    else:
        raise WorkflowError(f"evidence.{key} must be a string or list of strings")
    if not allow_empty and not values:
        raise WorkflowError(f"evidence.{key} must not be empty")
    return values


def normalize_generation(value: Any, *, label: str, required: bool) -> str:
    if value in (None, ""):
        if required:
            raise WorkflowError(f"{label} is required")
        return ""
    generation = str(value)
    if not generation.isdigit():
        raise WorkflowError(f"{label} must be numeric")
    return generation


def plan_from_state(state: dict[str, Any]) -> dict[str, Any]:
    plan = state.get("plan")
    if not isinstance(plan, dict):
        raise WorkflowError("workflow state is missing plan")
    return plan


def is_canonical_publish_workflow(state: dict[str, Any]) -> bool:
    return state.get("request_classification", "canonical-publish") == "canonical-publish"


def is_preview_workflow(state: dict[str, Any]) -> bool:
    return state.get("request_classification") == "preview-only"


def canonical_publish_required(state: dict[str, Any]) -> bool:
    return is_canonical_publish_workflow(state)


def preview_upload_required(state: dict[str, Any]) -> bool:
    return is_preview_workflow(state)


def preview_load_required(state: dict[str, Any]) -> bool:
    plan = plan_from_state(state)
    return is_preview_workflow(state) and plan.get("canonical_format") == "fgb" and bool(plan.get("release_date"))


def preview_catalog_refresh_required(state: dict[str, Any]) -> bool:
    return is_preview_workflow(state)


def preview_viewer_verify_required(state: dict[str, Any]) -> bool:
    return is_preview_workflow(state)


def step_record(state: dict[str, Any], step_id: str) -> dict[str, Any]:
    steps = state.setdefault("steps", {})
    record = steps.setdefault(step_id, {"status": "pending"})
    if not isinstance(record, dict):
        raise WorkflowError(f"workflow step record is malformed: {step_id}")
    return record


def step_completed(state: dict[str, Any], step_id: str) -> bool:
    return step_record(state, step_id).get("status") == "completed"


def completed_required_before(state: dict[str, Any], step_id: str) -> bool:
    for step in STEP_DEFINITIONS:
        if step.step_id == step_id:
            return True
        if step.optional or not step.is_required(state):
            continue
        if not step_completed(state, step.step_id):
            return False
    return True


def workflow_steps_by_id() -> dict[str, StepDefinition]:
    return {step.step_id: step for step in STEP_DEFINITIONS}


def current_required_step(state: dict[str, Any]) -> StepDefinition | None:
    for step in STEP_DEFINITIONS:
        if step.optional or not step.is_required(state):
            continue
        if not step_completed(state, step.step_id):
            return step
    return None


def read_asset_doc_frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text()
    match = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    if not match:
        raise WorkflowError(f"asset doc is missing YAML frontmatter: {path}")
    payload = yaml.safe_load(match.group(1)) or {}
    if not isinstance(payload, dict):
        raise WorkflowError(f"asset doc frontmatter must be a mapping: {path}")
    return payload


def pending_publish_prefix(state: dict[str, Any]) -> str:
    plan = plan_from_state(state)
    return f"gs://{state['bucket']}/_scratch/pending-publishes/{plan['asset_slug']}/{state['proposal_id']}/"


def no_cache_control() -> str:
    from scripts import reviewed_dataset_plan

    return reviewed_dataset_plan.NO_CACHE_CONTROL


def workflow_state_payload(
    *,
    plan: ConciergePlan,
    source: Path,
    proposal_id: str,
    request_classification: str,
    owner: str,
    source_name: str | None,
    license_text: str | None,
    citation: str | None,
    update_cadence: str,
    access_tier: str,
    preview_ref: str | None = None,
) -> dict[str, Any]:
    now = utc_now()
    if request_classification == "preview-only":
        release_prefix = f"gs://{PREVIEW_BUCKET}/{plan.asset_root}/releases/{plan.release_date or 'YYYY-MM-DD'}/"
        ref_text = preview_ref or "PREVIEW_REF"
        notes = [
            "The workflow is guide-and-verify only.",
            f"Preview uploads must stay under gs://{PREVIEW_BUCKET}/ and use generation-safe no-clobber writes.",
            "Preview data loading does not use production _scratch/pending-publishes or a shared-datasets-publish-plan PR.",
            f"Preview catalog refresh and viewer verification must be performed against preview ref {ref_text!r}.",
        ]
        remote_write_commands = [
            "Do not use production publish-release, _scratch/pending-publishes, or shared-datasets-publish-plan for preview-only loads.",
            f"Use the preview-upload workflow step to upload the release bundle under {release_prefix}.",
            f"Use the preview-upload workflow step to upload gs://{PREVIEW_BUCKET}/_catalog/releases/{plan.asset_slug}.json and the run record.",
            (
                f"After upload, run `gh workflow run feature-preview-deploy.yml --ref {ref_text} "
                "-f preview_data_mode=preserve` and verify the refreshed viewer catalog. "
                "The production catalog-web-deploy.yml automation runs after reviewed main pushes, "
                "but preview-only bucket uploads do not trigger it."
            ),
        ]
    else:
        notes = [
            "The workflow is guide-and-verify only.",
            "Run `next`, do the requested work/research externally, then submit structured evidence with `confirm`.",
            "The workflow never performs Git operations, scratch uploads, or canonical GCS promotion.",
        ]
        remote_write_commands = plan.remote_write_commands
    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "workflow_type": "first-upload",
        "created_at": now,
        "updated_at": now,
        "source": str(source),
        "proposal_id": proposal_id,
        "request_classification": request_classification,
        "preview_ref": preview_ref,
        "bucket": plan.canonical_path.split("/", 3)[2],
        "plan": asdict(plan),
        "contract": {
            "asset_slug": plan.asset_slug,
            "title": plan.title,
            "category": plan.category,
            "subcategory": plan.subcategory,
            "canonical_format": plan.canonical_format,
            "available_formats": plan.available_formats,
            "asset_root": plan.asset_root,
            "canonical_path": plan.canonical_path,
            "release_date": plan.release_date,
            "access_tier": access_tier,
            "owner": owner,
            "source_name": source_name,
            "license": license_text,
            "citation": citation,
            "update_cadence": update_cadence,
            "preview_ref": preview_ref,
        },
        "steps": {step.step_id: {"status": "pending"} for step in STEP_DEFINITIONS},
        "generated_commands": {
            "suggested": plan.suggested_commands,
            "remote_write": remote_write_commands,
        },
        "publish_plan": None,
        "pr_body": None,
        "notes": notes,
    }


def estimated_footprint_gb(text: str) -> float | None:
    matches = list(FOOTPRINT_RE.finditer(text))
    if not matches:
        return None
    total = 0.0
    binary_units = {"kib": 1 / (1024 * 1024), "mib": 1 / 1024, "gib": 1.0, "tib": 1024.0}
    decimal_units = {"kb": 1 / 1_000_000, "mb": 1 / 1000, "gb": 1.0, "tb": 1000.0}
    for match in matches:
        value = float(match.group("value"))
        unit = match.group("unit").lower()
        total += value * (binary_units.get(unit) or decimal_units[unit])
    return total


def detect_existing_asset(plan: ConciergePlan, *, catalog_path: Path = Path("catalog/shared-datasets-catalog.csv")) -> list[str]:
    matches = []
    doc_path = Path(plan.asset_doc_path)
    if doc_path.exists():
        matches.append(str(doc_path))
    if catalog_path.exists():
        try:
            with catalog_path.open(newline="") as handle:
                for row in csv.DictReader(handle):
                    if row.get("asset_slug") == plan.asset_slug:
                        matches.append(str(catalog_path))
                        break
        except csv.Error as exc:
            raise WorkflowError(f"could not inspect catalog for duplicate asset slug: {exc}") from exc
    return matches


def render_catalog_web_command() -> str:
    return (
        'WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}" '
        "UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py --out \"$WORK_ROOT/catalog-web\""
    )


def commands_for_resolve_metadata(_state: dict[str, Any]) -> list[str]:
    return ["Research source documentation, license/terms, citation, steward, intended consumers, and update expectations."]


def commands_for_settle_contract(state: dict[str, Any]) -> list[str]:
    plan = plan_from_state(state)
    return [
        f"Review asset contract in workflow state for {plan['asset_slug']}.",
        f"UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py validate-path {plan['canonical_path']}",
    ]


def commands_for_profile_fields(state: dict[str, Any]) -> list[str]:
    return [
        "Review `plan.curator_field_options` in `status --json` output.",
        "Choose whether ext_id comes from a selected provider ID, selected group ID, or the always-present feature_id fallback.",
        "If planning-time profile was unavailable, profile the canonical artifact after conversion before confirming.",
    ]


def commands_for_translation_decision(_state: dict[str, Any]) -> list[str]:
    return [
        "Ask the maintainer which locales and metadata fields should be autogenerated, or record an explicit no-translation decision.",
    ]


def commands_for_build_artifacts(state: dict[str, Any]) -> list[str]:
    return list(plan_from_state(state).get("suggested_commands") or [])


def commands_for_validate_artifacts(_state: dict[str, Any]) -> list[str]:
    return [
        "Run the relevant local validators for every artifact.",
        "For PMTiles, confirm magic bytes, run `pmtiles verify`, inspect `pmtiles show`, and decode a representative tile.",
    ]


def preview_release_prefix(state: dict[str, Any]) -> str:
    plan = plan_from_state(state)
    release_date = plan.get("release_date") or "YYYY-MM-DD"
    return f"gs://{PREVIEW_BUCKET}/{plan['asset_root']}/releases/{release_date}/"


def commands_for_preview_upload(state: dict[str, Any]) -> list[str]:
    plan = plan_from_state(state)
    release_prefix = preview_release_prefix(state)
    return [
        f"Inspect the feature-preview-index-load workflow for preview ref {state.get('preview_ref') or 'PREVIEW_REF'} before upload and align sidecar/schema/manifest suffixes with that workflow.",
        f"Confirm every planned destination starts with gs://{PREVIEW_BUCKET}/.",
        f"Upload release artifacts under {release_prefix} with no-clobber generation preconditions.",
        (
            "GOOGLE_CLOUD_PROJECT=shared-datasets-1 SHARED_DATASETS_BUCKET="
            f"{PREVIEW_BUCKET} SHARED_DATASETS_ALLOW_CANONICAL_MUTATION=1 UV_CACHE_DIR=.uv-cache "
            "uv run python scripts/gcs_asset.py upload LOCAL_PATH "
            f"{release_prefix}{plan['asset_slug']}.EXT --content-type TYPE --cache-control CACHE_CONTROL"
        ),
        f"Upload the release index to gs://{PREVIEW_BUCKET}/_catalog/releases/{plan['asset_slug']}.json.",
        f"Upload the run record under gs://{PREVIEW_BUCKET}/{plan['asset_root']}/runs/{plan.get('release_date') or 'YYYY-MM-DD'}.json.",
        "Stat every uploaded preview object and record exact generations in evidence JSON.",
    ]


def uploaded_preview_objects_by_role(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    uploaded = step_record(state, "preview-upload").get("evidence", {}).get("uploaded_objects", [])
    by_role = {}
    for obj in uploaded:
        if isinstance(obj, dict) and obj.get("role"):
            by_role[str(obj["role"])] = obj
    return by_role


def commands_for_preview_load(state: dict[str, Any]) -> list[str]:
    plan = plan_from_state(state)
    preview_ref = state.get("preview_ref") or "PREVIEW_REF"
    by_role = uploaded_preview_objects_by_role(state)
    sidecar = by_role.get("feature-metadata-sidecar", {})
    schema = by_role.get("schema", {})
    manifest = by_role.get("manifest", {})
    if sidecar and schema and manifest:
        return [
            f"Re-check `.github/workflows/feature-preview-index-load.yml` for preview ref {preview_ref} before dispatching.",
            f"gh workflow run feature-preview-index-load.yml --ref {preview_ref} "
            f"-f ref={preview_ref} -f asset_slug={plan['asset_slug']} -f release={plan['release_date']} "
            f"-f sidecar_uri={sidecar['uri']} -f sidecar_generation={sidecar['generation']} "
            f"-f schema_uri={schema['uri']} -f schema_generation={schema['generation']} "
            f"-f manifest_uri={manifest['uri']} -f manifest_generation={manifest['generation']}",
            "Record the workflow run URL/status and verify the preview catalog viewer refreshed against the preview bucket.",
        ]
    return [
        "After preview-upload evidence is recorded, dispatch Feature preview index load with sidecar/schema/manifest URIs and generations.",
        "Use only preview-bucket URIs as workflow inputs, then record the workflow run URL/status.",
    ]


def commands_for_preview_catalog_refresh(state: dict[str, Any]) -> list[str]:
    preview_ref = state.get("preview_ref") or "PREVIEW_REF"
    return [
        (
            "Confirm this is a preview-only refresh: production catalog web deployment is automatic "
            "via `.github/workflows/catalog-web-deploy.yml` after reviewed pushes to main that touch "
            "catalog/docs/web-generator inputs, but preview-only GCS uploads do not trigger that workflow."
        ),
        f"Run `gh workflow run feature-preview-deploy.yml --ref {preview_ref} -f preview_data_mode=preserve` after preview-upload succeeds.",
        "Wait for the run to finish successfully, especially the Collect preview release indexes, Build preview catalog web bundle, and Publish preview catalog web bundle steps.",
        f"Stat gs://{PREVIEW_BUCKET}/_catalog/web/catalog.json and record its generation, updated time, and generated_at value.",
    ]


def commands_for_preview_viewer_verify(state: dict[str, Any]) -> list[str]:
    plan = plan_from_state(state)
    return [
        f"Download gs://{PREVIEW_BUCKET}/_catalog/web/catalog.json.",
        f"Verify catalog.json contains assets[].slug == {plan['asset_slug']!r}.",
        f"Verify the {plan.get('release_date') or 'selected'} version contains every uploaded release artifact URI, including PMTiles when present.",
        "Record the extracted catalog asset object as evidence.",
    ]


def commands_for_document_asset(state: dict[str, Any]) -> list[str]:
    plan = plan_from_state(state)
    return [
        f"Create or update {plan['asset_doc_path']} with source/license/citation, admission evidence, files, schema, row count, and data profile.",
        "UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate",
    ]


def commands_for_catalog_outputs(_state: dict[str, Any]) -> list[str]:
    return [
        "UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate",
        "UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py check",
        'UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py export-readmes --output-dir "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/readmes"',
    ]


def commands_for_catalog_web(_state: dict[str, Any]) -> list[str]:
    return [render_catalog_web_command()]


def commands_for_stage_scratch(state: dict[str, Any]) -> list[str]:
    prefix = pending_publish_prefix(state)
    return [
        f"Stage every publish candidate under {prefix} with no-clobber uploads.",
        "Use `UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload LOCAL_PATH SCRATCH_URI --content-type TYPE --cache-control CACHE_CONTROL` where metadata is required.",
        "Record each staged source URI and generation in evidence JSON.",
    ]


def commands_for_stat_destinations(state: dict[str, Any]) -> list[str]:
    staged = step_record(state, "stage-scratch").get("evidence", {}).get("staged_objects", [])
    commands = []
    for obj in staged:
        if isinstance(obj, dict) and obj.get("destination_uri"):
            commands.append(f"UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat {obj['destination_uri']}")
    return commands or ["Stat each intended canonical destination and record the current generation or explicit absence."]


def commands_for_pr_ready(_state: dict[str, Any]) -> list[str]:
    return ["Run `python scripts/publishing_concierge.py render-pr --state-file STATE` and use the rendered body for the reviewed PR."]


def commands_for_post_merge(_state: dict[str, Any]) -> list[str]:
    return ["After merge/promotion, verify promoted objects, catalog freshness, PMTiles access, alert state, and retained temp directories."]


def validate_resolve_metadata(_state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    normalized = {
        "source_name": require_non_empty_string(evidence, "source_name"),
        "license": require_non_empty_string(evidence, "license"),
        "citation": require_non_empty_string(evidence, "citation"),
        "steward": require_non_empty_string(evidence, "steward"),
        "source_version_date": require_non_empty_string(evidence, "source_version_date"),
        "update_cadence": require_non_empty_string(evidence, "update_cadence"),
        "intended_consumers": require_string_list(evidence, "intended_consumers"),
        "shared_datasets_rationale": require_non_empty_string(evidence, "shared_datasets_rationale"),
        "alternatives_considered": require_non_empty_string(evidence, "alternatives_considered"),
        "deprecation_exit_policy": require_non_empty_string(evidence, "deprecation_exit_policy"),
        "estimated_published_footprint": require_non_empty_string(evidence, "estimated_published_footprint"),
    }
    return normalized


def validate_settle_contract(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    plan = plan_from_state(state)
    expected = {
        "confirmed_asset_slug": plan["asset_slug"],
        "confirmed_category": plan["category"],
        "confirmed_subcategory": plan["subcategory"],
        "confirmed_canonical_format": plan["canonical_format"],
    }
    normalized: dict[str, Any] = {}
    for key, expected_value in expected.items():
        value = require_non_empty_string(evidence, key)
        if value != expected_value:
            raise WorkflowError(f"evidence.{key} must match concierge plan value {expected_value!r}")
        normalized[key] = value
    release_layout = require_non_empty_string(evidence, "release_layout")
    if release_layout not in {"latest-only", "versioned"}:
        raise WorkflowError("evidence.release_layout must be 'latest-only' or 'versioned'")
    if plan.get("release_date") and release_layout != "versioned":
        raise WorkflowError("release_date is set, so evidence.release_layout must be 'versioned'")
    access_tier = require_non_empty_string(evidence, "access_tier")
    if access_tier not in {"public", "private"}:
        raise WorkflowError("evidence.access_tier must be public or private")
    flags = evidence.get("exception_flags")
    if not isinstance(flags, dict):
        raise WorkflowError("evidence.exception_flags must be an object of explicit boolean approvals")
    required_flags = [
        "public_access_approved",
        "new_top_level_category_approved",
        "new_canonical_format_approved",
        "large_data_exception_approved",
        "incompatible_schema_change_approved",
        "move_or_delete_releases_approved",
        "unsafe_overwrite_approved",
        "infrastructure_mutation_approved",
    ]
    normalized_flags = {}
    for flag in required_flags:
        value = flags.get(flag)
        if not isinstance(value, bool):
            raise WorkflowError(f"evidence.exception_flags.{flag} must be a boolean")
        normalized_flags[flag] = value
    if access_tier == "public" and not normalized_flags["public_access_approved"]:
        raise WorkflowError("public access requires evidence.exception_flags.public_access_approved=true")
    metadata = step_record(state, "resolve-metadata").get("evidence", {})
    footprint_gb = estimated_footprint_gb(str(metadata.get("estimated_published_footprint", "")))
    if footprint_gb is not None and footprint_gb >= 10 and not normalized_flags["large_data_exception_approved"]:
        raise WorkflowError("estimated published footprint is >= 10 GB; large_data_exception_approved must be true")
    normalized.update(
        {
            "release_layout": release_layout,
            "access_tier": access_tier,
            "exception_flags": normalized_flags,
            "estimated_footprint_gb": footprint_gb,
            "notes": str(evidence.get("notes", "")).strip(),
        }
    )
    return normalized


def validate_profile_fields(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    require_bool(evidence, "decision_table_present", expected=True)
    profile_scope = require_non_empty_string(evidence, "profile_scope")
    provider = require_non_empty_string(evidence, "provider_id_decision")
    if provider not in {"use-provider-id", "none-suitable"}:
        raise WorkflowError("evidence.provider_id_decision must be use-provider-id or none-suitable")
    group = require_non_empty_string(evidence, "generated_group_id_decision")
    if group not in {"not-needed", "approved"}:
        raise WorkflowError("evidence.generated_group_id_decision must be not-needed or approved")
    row = require_non_empty_string(evidence, "generated_row_id_decision")
    if row not in {"not-needed", "approved", "rejected"}:
        raise WorkflowError("evidence.generated_row_id_decision must be not-needed, approved, or rejected")
    ext_id_decision = require_non_empty_string(evidence, "ext_id_decision")
    if ext_id_decision not in {"provider-id", "group-id", "feature-id"}:
        raise WorkflowError("evidence.ext_id_decision must be provider-id, group-id, or feature-id")
    normalized = {
        "decision_table_present": True,
        "profile_scope": profile_scope,
        "provider_id_decision": provider,
        "provider_id_fields": require_string_list(evidence, "provider_id_fields", allow_empty=provider != "use-provider-id"),
        "generated_group_id_decision": group,
        "group_id_fields": require_string_list(evidence, "group_id_fields", allow_empty=group != "approved"),
        "generated_row_id_decision": row,
        "ext_id_decision": ext_id_decision,
        "ext_id_fields": require_string_list(evidence, "ext_id_fields", allow_empty=ext_id_decision == "feature-id"),
        "search_fields": require_string_list(evidence, "search_fields", allow_empty=True),
        "notes": str(evidence.get("notes", "")).strip(),
    }
    if group == "approved" and row == "approved":
        raise WorkflowError("generated group ID and generated row ID cannot both be approved")
    if provider == "use-provider-id" and (group == "approved" or row == "approved"):
        raise WorkflowError("do not approve generated IDs when a provider ID is selected")
    if ext_id_decision == "provider-id" and provider != "use-provider-id":
        raise WorkflowError("evidence.ext_id_decision=provider-id requires provider_id_decision=use-provider-id")
    if ext_id_decision == "group-id" and group != "approved":
        raise WorkflowError("evidence.ext_id_decision=group-id requires generated_group_id_decision=approved")
    if ext_id_decision == "feature-id" and normalized["ext_id_fields"]:
        raise WorkflowError("evidence.ext_id_fields must be empty when ext_id_decision=feature-id")
    return normalized


def translation_decision_required(state: dict[str, Any]) -> bool:
    plan = plan_from_state(state)
    return plan.get("canonical_format") == "fgb" and bool(plan.get("release_date"))


def validate_translation_decision(_state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    decision = require_non_empty_string(evidence, "decision")
    if decision not in {"autogenerate", "none"}:
        raise WorkflowError("evidence.decision must be autogenerate or none")
    locales = require_string_list(evidence, "locales", allow_empty=decision != "autogenerate")
    fields = require_string_list(evidence, "fields", allow_empty=decision != "autogenerate")
    return {
        "decision": decision,
        "locales": locales,
        "fields": fields,
        "notes": str(evidence.get("notes", "")).strip(),
    }


def required_artifact_formats(state: dict[str, Any]) -> set[str]:
    plan = plan_from_state(state)
    required = {str(plan["canonical_format"])}
    if "pmtiles" in plan.get("available_formats", []):
        required.add("pmtiles")
    if plan.get("canonical_format") == "fgb" and plan.get("release_date"):
        required.update({"metadata_sidecar_v1", "release_schema_v1", "release_manifest_v1"})
    return required


def validate_build_artifacts(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    artifacts = evidence.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise WorkflowError("evidence.artifacts must be a non-empty list")
    seen_formats: set[str] = set()
    normalized = []
    for index, artifact in enumerate(artifacts, start=1):
        if not isinstance(artifact, dict):
            raise WorkflowError(f"evidence.artifacts[{index}] must be an object")
        path = Path(require_non_empty_string(artifact, "path"))
        fmt = require_non_empty_string(artifact, "format")
        role = require_non_empty_string(artifact, "role")
        if not path.exists():
            raise WorkflowError(f"evidence.artifacts[{index}].path does not exist: {path}")
        if fmt != "zarr" and path.is_dir():
            raise WorkflowError(f"evidence.artifacts[{index}].path must be a file unless format is zarr: {path}")
        if fmt == "zarr" and not path.is_dir():
            raise WorkflowError(f"evidence.artifacts[{index}].path must be a directory for zarr: {path}")
        seen_formats.add(fmt)
        normalized.append(
            {
                "path": str(path),
                "format": fmt,
                "role": role,
                "destination_uri": str(artifact.get("destination_uri", "")).strip(),
            }
        )
    missing = sorted(required_artifact_formats(state) - seen_formats)
    if missing:
        raise WorkflowError(f"missing required artifact format(s): {', '.join(missing)}")
    return {"artifacts": normalized, "notes": str(evidence.get("notes", "")).strip()}


def validate_validate_artifacts(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    commands = require_string_list(evidence, "commands_run")
    summary = require_non_empty_string(evidence, "validation_summary")
    all_passed = require_bool(evidence, "all_passed", expected=True)
    tool_versions = evidence.get("tool_versions")
    if not isinstance(tool_versions, dict) or not tool_versions:
        raise WorkflowError("evidence.tool_versions must be a non-empty object with resolved tool paths/versions or explicit not-applicable notes")
    normalized: dict[str, Any] = {
        "commands_run": commands,
        "validation_summary": summary,
        "all_passed": all_passed,
        "tool_versions": tool_versions,
    }
    required_formats = required_artifact_formats(state)
    if "fgb" in required_formats:
        if not any("ogrinfo" in command.lower() or "gdal" in command.lower() for command in commands):
            raise WorkflowError("evidence.commands_run must include canonical vector validation with GDAL/OGR")
        gdal = evidence.get("gdal")
        if not isinstance(gdal, dict):
            raise WorkflowError("evidence.gdal must be an object for FGB/vector assets")
        normalized_gdal = {
            "ogr2ogr": require_non_empty_string(gdal, "ogr2ogr"),
            "ogrinfo": require_non_empty_string(gdal, "ogrinfo"),
        }
        for key in (
            "ogrinfo_summary_passed",
            "feature_count_checked",
            "geometry_type_checked",
            "crs_checked",
            "field_schema_checked",
        ):
            normalized_gdal[key] = require_bool(gdal, key, expected=True)
        normalized["gdal"] = normalized_gdal
    if "pmtiles" in required_formats:
        if not any("pmtiles" in command.lower() for command in commands):
            raise WorkflowError("evidence.commands_run must include PMTiles validation")
        pmtiles = evidence.get("pmtiles")
        if not isinstance(pmtiles, dict):
            raise WorkflowError("evidence.pmtiles must be an object for PMTiles assets")
        for key in ("magic_bytes_confirmed", "verify_passed", "show_inspected", "decoded_tile_checked"):
            if pmtiles.get(key) is not True:
                raise WorkflowError(f"evidence.pmtiles.{key} must be true")
        normalized["pmtiles"] = {
            "magic_bytes_confirmed": True,
            "verify_passed": True,
            "show_inspected": True,
            "decoded_tile_checked": True,
            "decoded_properties": pmtiles.get("decoded_properties", []),
            "notes": str(pmtiles.get("notes", "")).strip(),
        }
    return normalized


def normalize_preview_role(role: str) -> str:
    aliases = {
        "companion": "pmtiles",
        "metadata_sidecar_v1": "feature-metadata-sidecar",
        "release_schema_v1": "schema",
        "release_manifest_v1": "manifest",
        "release_index_v1": "release-index",
        "run_record_v1": "run-record",
    }
    return aliases.get(role, role)


def required_preview_upload_roles(state: dict[str, Any]) -> set[str]:
    plan = plan_from_state(state)
    roles = {"canonical", "release-index", "run-record"}
    if "pmtiles" in plan.get("available_formats", []):
        roles.add("pmtiles")
    if plan.get("canonical_format") == "fgb" and plan.get("release_date"):
        roles.update({"feature-metadata-sidecar", "schema", "manifest"})
    return roles


def expected_preview_upload_uri(state: dict[str, Any], role: str) -> str:
    plan = plan_from_state(state)
    release_prefix = preview_release_prefix(state)
    asset_slug = plan["asset_slug"]
    release_date = plan.get("release_date") or "YYYY-MM-DD"
    if role == "canonical":
        extension = FORMAT_FILE_EXTENSIONS.get(str(plan["canonical_format"]), str(plan["canonical_format"]))
        return f"{release_prefix}{asset_slug}.{extension}"
    if role == "pmtiles":
        return f"{release_prefix}{asset_slug}.pmtiles"
    if role == "feature-metadata-sidecar":
        return f"{release_prefix}{asset_slug}.metadata.ndjson.gz"
    if role == "schema":
        return f"{release_prefix}{asset_slug}.schema.json"
    if role == "manifest":
        return f"{release_prefix}{asset_slug}.manifest.json"
    if role == "release-index":
        return f"gs://{PREVIEW_BUCKET}/_catalog/releases/{asset_slug}.json"
    if role == "run-record":
        return f"gs://{PREVIEW_BUCKET}/{plan['asset_root']}/runs/{release_date}.json"
    raise WorkflowError(f"unsupported preview upload role: {role}")


def validate_preview_upload_metadata(index: int, role: str, content_type: str, cache_control: str) -> None:
    if role == "pmtiles":
        if content_type != "application/vnd.pmtiles":
            raise WorkflowError(f"evidence.uploaded_objects[{index}].content_type must be application/vnd.pmtiles")
        if cache_control != no_cache_control():
            raise WorkflowError(f"evidence.uploaded_objects[{index}].cache_control must be {no_cache_control()!r}")
    if role in {"schema", "manifest", "release-index", "run-record"}:
        if content_type != "application/json":
            raise WorkflowError(f"evidence.uploaded_objects[{index}].content_type must be application/json")
        if cache_control != no_cache_control():
            raise WorkflowError(f"evidence.uploaded_objects[{index}].cache_control must be {no_cache_control()!r}")


def validate_preview_upload(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    objects = evidence.get("uploaded_objects")
    if not isinstance(objects, list) or not objects:
        raise WorkflowError("evidence.uploaded_objects must be a non-empty list")
    prefix = f"gs://{PREVIEW_BUCKET}/"
    allowed_roles = required_preview_upload_roles(state)
    normalized = []
    seen_uris = set()
    seen_roles = set()
    for index, obj in enumerate(objects, start=1):
        if not isinstance(obj, dict):
            raise WorkflowError(f"evidence.uploaded_objects[{index}] must be an object")
        uri = require_non_empty_string(obj, "uri")
        if not uri.startswith(prefix):
            raise WorkflowError(f"evidence.uploaded_objects[{index}].uri must start with {prefix}")
        if "/_scratch/pending-publishes/" in uri:
            raise WorkflowError(f"evidence.uploaded_objects[{index}].uri must not use the production scratch publish path")
        if uri in seen_uris:
            raise WorkflowError(f"duplicate preview upload uri: {uri}")
        seen_uris.add(uri)
        role = normalize_preview_role(require_non_empty_string(obj, "role"))
        if role not in allowed_roles:
            raise WorkflowError(f"evidence.uploaded_objects[{index}].role is not required for this preview workflow: {role}")
        if role in seen_roles:
            raise WorkflowError(f"duplicate preview upload role: {role}")
        expected_uri = expected_preview_upload_uri(state, role)
        if uri != expected_uri:
            raise WorkflowError(f"evidence.uploaded_objects[{index}].uri must be {expected_uri}")
        seen_roles.add(role)
        generation = normalize_generation(
            obj.get("generation"),
            label=f"evidence.uploaded_objects[{index}].generation",
            required=True,
        )
        content_type = str(obj.get("content_type", "") or "")
        cache_control = str(obj.get("cache_control", "") or "")
        validate_preview_upload_metadata(index, role, content_type, cache_control)
        normalized.append(
            {
                "uri": uri,
                "generation": generation,
                "role": role,
                "content_type": content_type,
                "cache_control": cache_control,
            }
        )
    missing = sorted(required_preview_upload_roles(state) - seen_roles)
    if missing:
        raise WorkflowError(f"missing required preview upload role(s): {', '.join(missing)}")
    return {
        "uploaded_objects": normalized,
        "notes": str(evidence.get("notes", "")).strip(),
    }


def validate_preview_load(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    plan = plan_from_state(state)
    asset_slug = require_non_empty_string(evidence, "asset_slug")
    if asset_slug != plan["asset_slug"]:
        raise WorkflowError(f"evidence.asset_slug must match concierge plan value {plan['asset_slug']!r}")
    release = require_non_empty_string(evidence, "release")
    if release != plan.get("release_date"):
        raise WorkflowError(f"evidence.release must match concierge plan release_date {plan.get('release_date')!r}")
    workflow_name = require_non_empty_string(evidence, "workflow_name")
    if workflow_name not in {"feature-preview-index-load.yml", "Feature preview index load"}:
        raise WorkflowError("evidence.workflow_name must be feature-preview-index-load.yml or Feature preview index load")
    require_bool(evidence, "workflow_inputs_checked_against_preview_ref", expected=True)
    inputs = evidence.get("inputs")
    if not isinstance(inputs, dict):
        raise WorkflowError("evidence.inputs must be an object")
    prefix = f"gs://{PREVIEW_BUCKET}/"
    normalized_inputs = {}
    for key in ("sidecar_uri", "schema_uri", "manifest_uri"):
        uri = require_non_empty_string(inputs, key)
        if not uri.startswith(prefix):
            raise WorkflowError(f"evidence.inputs.{key} must start with {prefix}")
        normalized_inputs[key] = uri
    for key in ("sidecar_generation", "schema_generation", "manifest_generation"):
        normalized_inputs[key] = normalize_generation(
            inputs.get(key),
            label=f"evidence.inputs.{key}",
            required=True,
        )
    uploaded = uploaded_preview_objects_by_role(state)
    for role, input_prefix in (
        ("feature-metadata-sidecar", "sidecar"),
        ("schema", "schema"),
        ("manifest", "manifest"),
    ):
        uploaded_object = uploaded.get(role)
        if not uploaded_object:
            raise WorkflowError(f"preview-load requires uploaded preview object role {role}")
        uri_key = f"{input_prefix}_uri"
        generation_key = f"{input_prefix}_generation"
        if normalized_inputs[uri_key] != uploaded_object.get("uri"):
            raise WorkflowError(f"evidence.inputs.{uri_key} must match preview-upload {role} uri")
        if normalized_inputs[generation_key] != uploaded_object.get("generation"):
            raise WorkflowError(f"evidence.inputs.{generation_key} must match preview-upload {role} generation")
    dispatched_ref = require_non_empty_string(evidence, "dispatched_ref")
    preview_ref = str(state.get("preview_ref") or "").strip()
    if preview_ref and dispatched_ref != preview_ref:
        raise WorkflowError(f"evidence.dispatched_ref must match preview_ref {preview_ref!r}")
    status = require_non_empty_string(evidence, "status").lower()
    if status not in {"success", "completed", "succeeded"}:
        raise WorkflowError("evidence.status must be success, completed, or succeeded")
    return {
        "workflow_name": workflow_name,
        "workflow_run_url": require_non_empty_string(evidence, "workflow_run_url"),
        "status": status,
        "dispatched_ref": dispatched_ref,
        "asset_slug": asset_slug,
        "release": release,
        "workflow_inputs_checked_against_preview_ref": True,
        "inputs": normalized_inputs,
        "viewer_refresh_verified": require_bool(evidence, "viewer_refresh_verified", expected=True),
        "notes": str(evidence.get("notes", "")).strip(),
    }


def validate_preview_catalog_refresh(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    preview_ref = str(state.get("preview_ref") or "").strip()
    workflow_name = require_non_empty_string(evidence, "workflow_name")
    if workflow_name not in {"feature-preview-deploy.yml", "Deploy Feature Branch to Preview"}:
        raise WorkflowError("evidence.workflow_name must be feature-preview-deploy.yml or Deploy Feature Branch to Preview")
    dispatched_ref = require_non_empty_string(evidence, "dispatched_ref")
    if preview_ref and dispatched_ref != preview_ref:
        raise WorkflowError(f"evidence.dispatched_ref must match preview_ref {preview_ref!r}")
    preview_data_mode = require_non_empty_string(evidence, "preview_data_mode")
    if preview_data_mode != "preserve":
        raise WorkflowError("evidence.preview_data_mode must be preserve")
    conclusion = require_non_empty_string(evidence, "conclusion")
    if conclusion != "success":
        raise WorkflowError("evidence.conclusion must be success")
    catalog_uri = require_non_empty_string(evidence, "catalog_json_uri")
    expected_catalog_uri = f"gs://{PREVIEW_BUCKET}/_catalog/web/catalog.json"
    if catalog_uri != expected_catalog_uri:
        raise WorkflowError(f"evidence.catalog_json_uri must be {expected_catalog_uri}")
    return {
        "workflow_name": workflow_name,
        "workflow_run_url": require_non_empty_string(evidence, "workflow_run_url"),
        "workflow_run_id": require_non_empty_string(evidence, "workflow_run_id"),
        "dispatched_ref": dispatched_ref,
        "preview_data_mode": preview_data_mode,
        "conclusion": conclusion,
        "catalog_json_uri": catalog_uri,
        "catalog_json_generation": normalize_generation(
            evidence.get("catalog_json_generation"),
            label="evidence.catalog_json_generation",
            required=True,
        ),
        "catalog_json_updated_at": require_non_empty_string(evidence, "catalog_json_updated_at"),
        "catalog_generated_at": require_non_empty_string(evidence, "catalog_generated_at"),
        "notes": str(evidence.get("notes", "")).strip(),
    }


def validate_preview_viewer_verify(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    plan = plan_from_state(state)
    require_bool(evidence, "asset_slug_present", expected=True)
    catalog_uri = require_non_empty_string(evidence, "catalog_json_uri")
    expected_catalog_uri = f"gs://{PREVIEW_BUCKET}/_catalog/web/catalog.json"
    if catalog_uri != expected_catalog_uri:
        raise WorkflowError(f"evidence.catalog_json_uri must be {expected_catalog_uri}")
    catalog_generation = normalize_generation(
        evidence.get("catalog_json_generation"),
        label="evidence.catalog_json_generation",
        required=True,
    )
    refreshed_generation = step_record(state, "preview-catalog-refresh").get("evidence", {}).get("catalog_json_generation")
    if refreshed_generation and catalog_generation != refreshed_generation:
        raise WorkflowError("evidence.catalog_json_generation must match preview-catalog-refresh evidence")
    asset = evidence.get("catalog_asset")
    if not isinstance(asset, dict):
        raise WorkflowError("evidence.catalog_asset must be the extracted catalog asset object")
    if asset.get("slug") != plan["asset_slug"]:
        raise WorkflowError(f"evidence.catalog_asset.slug must be {plan['asset_slug']!r}")
    versions = asset.get("versions")
    if not isinstance(versions, list) or not versions:
        raise WorkflowError("evidence.catalog_asset.versions must be a non-empty list")
    release_date = plan.get("release_date")
    selected_version = None
    for version in versions:
        if isinstance(version, dict) and (not release_date or version.get("date") == release_date):
            selected_version = version
            break
    if selected_version is None:
        raise WorkflowError(f"evidence.catalog_asset.versions must include release {release_date!r}")
    files = selected_version.get("files")
    if not isinstance(files, list) or not files:
        raise WorkflowError("evidence.catalog_asset selected version must include files")
    catalog_file_uris = {str(file_obj.get("path")) for file_obj in files if isinstance(file_obj, dict)}
    uploaded = step_record(state, "preview-upload").get("evidence", {}).get("uploaded_objects", [])
    required_uploaded_uris = {
        obj["uri"]
        for obj in uploaded
        if isinstance(obj, dict) and obj.get("role") not in {"release-index", "run-record"}
    }
    missing = sorted(required_uploaded_uris - catalog_file_uris)
    if missing:
        raise WorkflowError("catalog asset is missing uploaded release artifact URI(s): " + ", ".join(missing))
    if any(isinstance(obj, dict) and obj.get("role") == "pmtiles" for obj in uploaded):
        if asset.get("has_pmtiles") is not True:
            raise WorkflowError("evidence.catalog_asset.has_pmtiles must be true when PMTiles were uploaded")
        pmtiles_path = str(asset.get("pmtiles_path") or selected_version.get("pmtiles_path") or "")
        if not pmtiles_path.startswith(f"gs://{PREVIEW_BUCKET}/"):
            raise WorkflowError("evidence.catalog_asset.pmtiles_path must point at the preview bucket")
    return {
        "catalog_json_uri": catalog_uri,
        "catalog_json_generation": catalog_generation,
        "asset_slug_present": True,
        "asset_count": int(evidence.get("asset_count", 0)),
        "catalog_asset": asset,
        "verified_uploaded_uris": sorted(required_uploaded_uris),
        "notes": str(evidence.get("notes", "")).strip(),
    }


def validate_document_asset(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    plan = plan_from_state(state)
    path = Path(str(evidence.get("asset_doc_path") or plan["asset_doc_path"]))
    if not path.exists():
        raise WorkflowError(f"asset doc does not exist: {path}")
    metadata = read_asset_doc_frontmatter(path)
    required = {
        "asset_slug": plan["asset_slug"],
        "category": plan["category"],
        "subcategory": plan["subcategory"],
        "canonical_format": plan["canonical_format"],
    }
    for key, expected in required.items():
        if metadata.get(key) != expected:
            raise WorkflowError(f"{path}: frontmatter {key!r} must be {expected!r}")
    for key in ("source", "license", "citation"):
        value = str(metadata.get(key, "")).strip()
        if not value or value.startswith("NEEDS "):
            raise WorkflowError(f"{path}: frontmatter {key!r} must be populated")
    require_bool(evidence, "admission_complete", expected=True)
    require_bool(evidence, "source_license_citation_complete", expected=True)
    require_bool(evidence, "schema_or_properties_complete", expected=True)
    if plan.get("canonical_format") in {"fgb", "csv"}:
        require_bool(evidence, "data_profile_complete", expected=True)
    return {
        "asset_doc_path": str(path),
        "admission_complete": True,
        "source_license_citation_complete": True,
        "schema_or_properties_complete": True,
        "data_profile_complete": bool(evidence.get("data_profile_complete", False)),
    }


def validate_catalog_outputs(_state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    require_bool(evidence, "generate_ran", expected=True)
    require_bool(evidence, "check_passed", expected=True)
    require_bool(evidence, "readmes_exported", expected=True)
    readmes_dir = Path(require_non_empty_string(evidence, "readmes_dir"))
    if not readmes_dir.exists() or not readmes_dir.is_dir():
        raise WorkflowError(f"evidence.readmes_dir must be an existing directory: {readmes_dir}")
    return {
        "generate_ran": True,
        "check_passed": True,
        "readmes_exported": True,
        "readmes_dir": str(readmes_dir),
        "commands_run": evidence.get("commands_run", []),
    }


def catalog_web_required(_state: dict[str, Any]) -> bool:
    return True


def validate_catalog_web(_state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    require_bool(evidence, "built", expected=True)
    catalog_json = Path(require_non_empty_string(evidence, "catalog_json_path"))
    if not catalog_json.exists() or not catalog_json.is_file():
        raise WorkflowError(f"evidence.catalog_json_path must be an existing file: {catalog_json}")
    content_type = require_non_empty_string(evidence, "content_type")
    if content_type != "application/json":
        raise WorkflowError("evidence.content_type must be application/json")
    cache_control = require_non_empty_string(evidence, "cache_control")
    if cache_control != no_cache_control():
        raise WorkflowError(f"evidence.cache_control must be {no_cache_control()!r}")
    return {
        "built": True,
        "catalog_json_path": str(catalog_json),
        "content_type": content_type,
        "cache_control": cache_control,
    }


def validate_stage_scratch(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    prefix = pending_publish_prefix(state)
    objects = evidence.get("staged_objects")
    if not isinstance(objects, list) or not objects:
        raise WorkflowError("evidence.staged_objects must be a non-empty list")
    normalized = []
    destinations = set()
    for index, obj in enumerate(objects, start=1):
        if not isinstance(obj, dict):
            raise WorkflowError(f"evidence.staged_objects[{index}] must be an object")
        source_uri = require_non_empty_string(obj, "source_uri")
        if not source_uri.startswith(prefix):
            raise WorkflowError(f"evidence.staged_objects[{index}].source_uri must start with {prefix}")
        source_generation = normalize_generation(
            obj.get("source_generation"),
            label=f"evidence.staged_objects[{index}].source_generation",
            required=True,
        )
        destination_uri = require_non_empty_string(obj, "destination_uri")
        if destination_uri in destinations:
            raise WorkflowError(f"duplicate destination_uri in staged objects: {destination_uri}")
        destinations.add(destination_uri)
        content_type = str(obj.get("content_type", "") or "")
        cache_control = str(obj.get("cache_control", "") or "")
        if destination_uri.endswith(".pmtiles"):
            if content_type != "application/vnd.pmtiles":
                raise WorkflowError(f"evidence.staged_objects[{index}].content_type must be application/vnd.pmtiles")
            if cache_control != no_cache_control():
                raise WorkflowError(f"evidence.staged_objects[{index}].cache_control must be {no_cache_control()!r}")
        if destination_uri.endswith("_catalog/web/catalog.json") or destination_uri.endswith("/_catalog/web/catalog.json"):
            if content_type != "application/json":
                raise WorkflowError(f"evidence.staged_objects[{index}].content_type must be application/json")
            if cache_control != no_cache_control():
                raise WorkflowError(f"evidence.staged_objects[{index}].cache_control must be {no_cache_control()!r}")
        normalized.append(
            {
                "source_uri": source_uri,
                "source_generation": source_generation,
                "destination_uri": destination_uri,
                "content_type": content_type,
                "cache_control": cache_control,
            }
        )
    return {"staged_objects": normalized}


def validate_stat_destinations(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    staged = step_record(state, "stage-scratch").get("evidence", {}).get("staged_objects", [])
    staged_destinations = {obj["destination_uri"] for obj in staged if isinstance(obj, dict) and obj.get("destination_uri")}
    destinations = evidence.get("destinations")
    if not isinstance(destinations, list) or not destinations:
        raise WorkflowError("evidence.destinations must be a non-empty list")
    normalized = []
    seen = set()
    for index, destination in enumerate(destinations, start=1):
        if not isinstance(destination, dict):
            raise WorkflowError(f"evidence.destinations[{index}] must be an object")
        uri = require_non_empty_string(destination, "destination_uri")
        if uri not in staged_destinations:
            raise WorkflowError(f"evidence.destinations[{index}].destination_uri was not staged: {uri}")
        seen.add(uri)
        normalized.append(
            {
                "destination_uri": uri,
                "destination_generation": normalize_generation(
                    destination.get("destination_generation", ""),
                    label=f"evidence.destinations[{index}].destination_generation",
                    required=False,
                ),
                "status": str(destination.get("status", "") or "").strip(),
            }
        )
    missing = sorted(staged_destinations - seen)
    if missing:
        raise WorkflowError(f"missing destination stat evidence for: {', '.join(missing)}")
    return {"destinations": normalized}


def build_publish_plan_from_state(state: dict[str, Any]) -> dict[str, Any]:
    from scripts import reviewed_dataset_plan

    plan = plan_from_state(state)
    staged = step_record(state, "stage-scratch").get("evidence", {}).get("staged_objects", [])
    destinations = {
        item["destination_uri"]: item.get("destination_generation", "")
        for item in step_record(state, "stat-destinations").get("evidence", {}).get("destinations", [])
    }
    promotions = []
    for item in staged:
        destination_uri = item["destination_uri"]
        promotions.append(
            {
                "source_uri": item["source_uri"],
                "source_generation": item["source_generation"],
                "destination_uri": destination_uri,
                "destination_generation": destinations.get(destination_uri, ""),
                "content_type": item.get("content_type", ""),
                "cache_control": item.get("cache_control", ""),
            }
        )
    publish_plan = {
        "asset_slug": plan["asset_slug"],
        "proposal_id": state["proposal_id"],
        "promotions": promotions,
    }
    return reviewed_dataset_plan.normalize_publish_plan(publish_plan, bucket=state["bucket"])


def validate_pr_ready(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    for step in STEP_DEFINITIONS:
        if step.step_id == "pr-ready":
            break
        if step.optional or not step.is_required(state):
            continue
        if not step_completed(state, step.step_id):
            raise WorkflowError(f"cannot complete pr-ready before {step.step_id}")
    require_bool(evidence, "reviewed_pr_body", expected=True)
    publish_plan = build_publish_plan_from_state(state)
    pr_body = render_pr_body_from_state(state, publish_plan=publish_plan)
    return {"reviewed_pr_body": True, "publish_plan": publish_plan, "pr_body": pr_body}


def validate_post_merge(_state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    require_bool(evidence, "promoted_objects_verified", expected=True)
    require_bool(evidence, "catalog_freshness_verified", expected=True)
    alert_state = require_non_empty_string(evidence, "alert_state")
    retained_temp_dirs = evidence.get("retained_temp_dirs", [])
    if not isinstance(retained_temp_dirs, list):
        raise WorkflowError("evidence.retained_temp_dirs must be a list")
    return {
        "promoted_objects_verified": True,
        "catalog_freshness_verified": True,
        "alert_state": alert_state,
        "retained_temp_dirs": retained_temp_dirs,
        "notes": str(evidence.get("notes", "")).strip(),
    }


STEP_DEFINITIONS: tuple[StepDefinition, ...] = (
    StepDefinition(
        "resolve-metadata",
        "Resolve source metadata and admission context",
        "Research and record source identity, terms, citation, stewardship, intended consumers, and admission rationale.",
        {
            "source_name": "string",
            "license": "string",
            "citation": "string",
            "steward": "string",
            "source_version_date": "string",
            "update_cadence": "string",
            "intended_consumers": ["string"],
            "shared_datasets_rationale": "string",
            "alternatives_considered": "string",
            "deprecation_exit_policy": "string",
            "estimated_published_footprint": "string",
        },
        commands_for_resolve_metadata,
        validate_resolve_metadata,
    ),
    StepDefinition(
        "settle-contract",
        "Settle asset contract and exception decisions",
        "Confirm the planned slug, taxonomy, format, release layout, access tier, and every exceptional approval flag.",
        {
            "confirmed_asset_slug": "string matching plan.asset_slug",
            "confirmed_category": "string matching plan.category",
            "confirmed_subcategory": "string matching plan.subcategory",
            "confirmed_canonical_format": "string matching plan.canonical_format",
            "release_layout": "latest-only|versioned",
            "access_tier": "public|private",
            "exception_flags": {
                "public_access_approved": "boolean",
                "new_top_level_category_approved": "boolean",
                "new_canonical_format_approved": "boolean",
                "large_data_exception_approved": "boolean",
                "incompatible_schema_change_approved": "boolean",
                "move_or_delete_releases_approved": "boolean",
                "unsafe_overwrite_approved": "boolean",
                "infrastructure_mutation_approved": "boolean",
            },
        },
        commands_for_settle_contract,
        validate_settle_contract,
    ),
    StepDefinition(
        "profile-fields",
        "Profile provider IDs and grouping/search fields",
        "Record the decision table and explicit generated-ID decisions before any generated IDs are added.",
        {
            "decision_table_present": True,
            "profile_scope": "full|random_sample|schema_only|canonical-artifact|other",
            "provider_id_decision": "use-provider-id|none-suitable",
            "provider_id_fields": ["string"],
            "generated_group_id_decision": "not-needed|approved",
            "group_id_fields": ["string"],
            "generated_row_id_decision": "not-needed|approved|rejected",
            "ext_id_decision": "provider-id|group-id|feature-id",
            "ext_id_fields": ["string"],
            "search_fields": ["string"],
        },
        commands_for_profile_fields,
        validate_profile_fields,
    ),
    StepDefinition(
        "translation-decision",
        "Resolve first-upload translation choices",
        "For release-oriented vector assets, record locales and fields to autogenerate or an explicit no-translation decision.",
        {
            "decision": "autogenerate|none",
            "locales": ["string"],
            "fields": ["string"],
        },
        commands_for_translation_decision,
        validate_translation_decision,
        is_required=translation_decision_required,
    ),
    StepDefinition(
        "build-artifacts",
        "Build publishable local artifacts",
        "Build every required canonical and companion artifact outside the repo tree and record their local paths.",
        {"artifacts": [{"path": "existing local path", "format": "format id", "role": "role"}]},
        commands_for_build_artifacts,
        validate_build_artifacts,
    ),
    StepDefinition(
        "validate-artifacts",
        "Validate local artifacts",
        "Record validation commands and results; PMTiles evidence is mandatory when PMTiles are present.",
        {
            "commands_run": ["string"],
            "validation_summary": "string",
            "all_passed": True,
            "gdal": {
                "ogr2ogr": "resolved path and version",
                "ogrinfo": "resolved path and version",
                "ogrinfo_summary_passed": True,
                "feature_count_checked": True,
                "geometry_type_checked": True,
                "crs_checked": True,
                "field_schema_checked": True,
            },
            "pmtiles": {
                "magic_bytes_confirmed": True,
                "verify_passed": True,
                "show_inspected": True,
                "decoded_tile_checked": True,
            },
        },
        commands_for_validate_artifacts,
        validate_validate_artifacts,
    ),
    StepDefinition(
        "preview-upload",
        "Upload disposable preview release bundle",
        "Upload the validated release bundle directly to the preview bucket and record exact object generations.",
        {
            "uploaded_objects": [
                {
                    "uri": f"gs://{PREVIEW_BUCKET}/...",
                    "generation": "numeric string",
                    "role": "canonical|pmtiles|feature-metadata-sidecar|schema|manifest|release-index|run-record",
                    "content_type": "optional string",
                    "cache_control": "optional string",
                }
            ]
        },
        commands_for_preview_upload,
        validate_preview_upload,
        is_required=preview_upload_required,
    ),
    StepDefinition(
        "preview-load",
        "Dispatch preview index load",
        "Dispatch Feature preview index load with preview-bucket sidecar/schema/manifest URIs and generations.",
        {
            "workflow_name": "feature-preview-index-load.yml",
            "workflow_run_url": "string",
            "status": "success|completed|succeeded",
            "dispatched_ref": "string matching preview_ref",
            "workflow_inputs_checked_against_preview_ref": True,
            "asset_slug": "string matching plan.asset_slug",
            "release": "string matching plan.release_date",
            "inputs": {
                "sidecar_uri": f"gs://{PREVIEW_BUCKET}/...",
                "sidecar_generation": "numeric string",
                "schema_uri": f"gs://{PREVIEW_BUCKET}/...",
                "schema_generation": "numeric string",
                "manifest_uri": f"gs://{PREVIEW_BUCKET}/...",
                "manifest_generation": "numeric string",
            },
            "viewer_refresh_verified": True,
        },
        commands_for_preview_load,
        validate_preview_load,
        is_required=preview_load_required,
    ),
    StepDefinition(
        "preview-catalog-refresh",
        "Refresh preview catalog viewer bundle",
        "Redeploy the preview branch in preserve mode so _catalog/web/catalog.json is rebuilt from preview release indexes; preview uploads do not trigger the production catalog web deploy.",
        {
            "workflow_name": "feature-preview-deploy.yml",
            "workflow_run_url": "string",
            "workflow_run_id": "string",
            "dispatched_ref": "string matching preview_ref",
            "preview_data_mode": "preserve",
            "conclusion": "success",
            "catalog_json_uri": f"gs://{PREVIEW_BUCKET}/_catalog/web/catalog.json",
            "catalog_json_generation": "numeric string",
            "catalog_json_updated_at": "string",
            "catalog_generated_at": "string",
        },
        commands_for_preview_catalog_refresh,
        validate_preview_catalog_refresh,
        is_required=preview_catalog_refresh_required,
    ),
    StepDefinition(
        "preview-viewer-verify",
        "Verify refreshed preview viewer catalog",
        "Verify the refreshed viewer catalog contains the asset release and every uploaded release artifact URI.",
        {
            "catalog_json_uri": f"gs://{PREVIEW_BUCKET}/_catalog/web/catalog.json",
            "catalog_json_generation": "numeric string matching preview-catalog-refresh",
            "asset_slug_present": True,
            "asset_count": "integer",
            "catalog_asset": "assets[] object for plan.asset_slug",
        },
        commands_for_preview_viewer_verify,
        validate_preview_viewer_verify,
        is_required=preview_viewer_verify_required,
    ),
    StepDefinition(
        "document-asset",
        "Write asset documentation",
        "Create/update the asset doc with admission evidence, source metadata, file table, schema, and profile fields.",
        {
            "asset_doc_path": "optional path; defaults to plan.asset_doc_path",
            "admission_complete": True,
            "source_license_citation_complete": True,
            "schema_or_properties_complete": True,
            "data_profile_complete": True,
        },
        commands_for_document_asset,
        validate_document_asset,
        is_required=canonical_publish_required,
    ),
    StepDefinition(
        "catalog-outputs",
        "Regenerate catalog docs and bucket READMEs",
        "Confirm generated docs/catalog outputs and exported bucket README content are current.",
        {
            "generate_ran": True,
            "check_passed": True,
            "readmes_exported": True,
            "readmes_dir": "existing directory",
        },
        commands_for_catalog_outputs,
        validate_catalog_outputs,
        is_required=canonical_publish_required,
    ),
    StepDefinition(
        "catalog-web",
        "Rebuild catalog web output",
        "Build catalog web output and record catalog.json path plus required cache metadata.",
        {
            "built": True,
            "catalog_json_path": "existing file",
            "content_type": "application/json",
            "cache_control": no_cache_control(),
        },
        commands_for_catalog_web,
        validate_catalog_web,
        is_required=canonical_publish_required,
    ),
    StepDefinition(
        "stage-scratch",
        "Stage reviewed publish candidates under _scratch",
        "After the agent uploads scratch candidates externally, record source URIs, source generations, and destinations.",
        {
            "staged_objects": [
                {
                    "source_uri": "gs://bucket/_scratch/pending-publishes/{asset_slug}/{proposal_id}/object",
                    "source_generation": "numeric string",
                    "destination_uri": "gs://bucket/canonical/object",
                    "content_type": "optional string",
                    "cache_control": "optional string",
                }
            ]
        },
        commands_for_stage_scratch,
        validate_stage_scratch,
        is_required=canonical_publish_required,
    ),
    StepDefinition(
        "stat-destinations",
        "Record canonical destination generations",
        "Stat every intended canonical destination and record its current generation or absence expectation.",
        {"destinations": [{"destination_uri": "string", "destination_generation": "numeric string or empty"}]},
        commands_for_stat_destinations,
        validate_stat_destinations,
        is_required=canonical_publish_required,
    ),
    StepDefinition(
        "pr-ready",
        "Render and validate reviewed PR publish plan",
        "Validate the assembled publish plan and render the PR body. The agent must review the body before confirming.",
        {"reviewed_pr_body": True},
        commands_for_pr_ready,
        validate_pr_ready,
        is_required=canonical_publish_required,
        allow_yes=False,
    ),
    StepDefinition(
        "post-merge-verify",
        "Verify promoted production objects after merge",
        "Optional follow-up after protected promotion finishes.",
        {
            "promoted_objects_verified": True,
            "catalog_freshness_verified": True,
            "alert_state": "sent|skipped|uncertain",
            "retained_temp_dirs": ["string"],
        },
        commands_for_post_merge,
        validate_post_merge,
        optional=True,
    ),
)


def render_instruction(state: dict[str, Any], step: StepDefinition) -> StepInstruction:
    blockers = []
    if not completed_required_before(state, step.step_id):
        blockers.append("Earlier required steps are still incomplete.")
    return StepInstruction(
        step_id=step.step_id,
        title=step.title,
        summary=step.summary,
        commands=step.render_commands(state),
        evidence_schema=step.evidence_schema,
        blockers=blockers,
        optional=step.optional,
    )


def print_instruction(instruction: StepInstruction, *, as_json: bool) -> None:
    payload = asdict(instruction)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"Current step: {instruction.step_id} - {instruction.title}")
    print(instruction.summary)
    if instruction.blockers:
        print("\nBlockers:")
        for blocker in instruction.blockers:
            print(f"- {blocker}")
    if instruction.commands:
        print("\nSuggested commands or actions:")
        for command in instruction.commands:
            print(f"- {command}")
    print("\nRequired evidence JSON:")
    print(json.dumps(instruction.evidence_schema, indent=2, sort_keys=True))


def render_status(state: dict[str, Any]) -> dict[str, Any]:
    current = current_required_step(state)
    steps = []
    for step in STEP_DEFINITIONS:
        required = step.is_required(state)
        record = step_record(state, step.step_id)
        steps.append(
            {
                "step_id": step.step_id,
                "title": step.title,
                "required": required,
                "optional": step.optional,
                "status": record.get("status", "pending"),
                "completed_at": record.get("completed_at"),
            }
        )
    return {
        "asset_slug": plan_from_state(state)["asset_slug"],
        "proposal_id": state["proposal_id"],
        "request_classification": state.get("request_classification", "canonical-publish"),
        "state_file": state.get("state_file"),
        "current_step": current.step_id if current else None,
        "ready_for_pr": step_completed(state, "pr-ready"),
        "ready_for_preview": is_preview_workflow(state) and current is None,
        "steps": steps,
    }


def print_status(state: dict[str, Any], *, as_json: bool) -> None:
    payload = render_status(state)
    if as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(f"Workflow: {payload['asset_slug']} / {payload['proposal_id']} ({payload['request_classification']})")
    print(f"Current step: {payload['current_step'] or 'complete'}")
    for step in payload["steps"]:
        marker = "required" if step["required"] and not step["optional"] else "optional"
        print(f"- {step['step_id']}: {step['status']} ({marker})")


def render_pr_body_from_state(state: dict[str, Any], *, publish_plan: dict[str, Any] | None = None) -> str:
    plan = plan_from_state(state)
    metadata = step_record(state, "resolve-metadata").get("evidence", {})
    validation = step_record(state, "validate-artifacts").get("evidence", {})
    catalog = step_record(state, "catalog-outputs").get("evidence", {})
    publish_plan = publish_plan or build_publish_plan_from_state(state)
    body = f"""## Summary

Publish new shared dataset `{plan['asset_slug']}`: {plan['title']}.

## Validation

- Artifact validation: {validation.get('validation_summary', 'Recorded in concierge workflow state.')}
- Commands run: {', '.join(validation.get('commands_run', [])) or 'Recorded in concierge workflow state.'}
- Catalog docs check passed: {catalog.get('check_passed', False)}
- Remote object generations are encoded in the publish plan below.

## Dataset Admission

- Intended consumer(s): {', '.join(metadata.get('intended_consumers', []))}
- Why this belongs in shared-datasets instead of project storage, scratch storage, or direct upstream access: {metadata.get('shared_datasets_rationale', '')}
- Source, license, and citation status: Source: {metadata.get('source_name', '')}; license/terms: {metadata.get('license', '')}; citation: {metadata.get('citation', '')}
- Named steward: {metadata.get('steward', '')}
- Update expectations: {metadata.get('update_cadence', '')}
- Estimated published footprint, including canonical files, companion artifacts, and expected release copies: {metadata.get('estimated_published_footprint', '')}
- Large-data exception, required when the proposed published footprint is >= 10 GB: See contract exception flags in concierge workflow state.
- Alternatives considered: {metadata.get('alternatives_considered', '')}
- Deprecation or exit policy: {metadata.get('deprecation_exit_policy', '')}

## Publish Plan

```shared-datasets-publish-plan
{json.dumps(publish_plan, indent=2, sort_keys=True)}
```
"""
    return body


def render_completion_report_from_state(state: dict[str, Any]) -> str:
    plan = plan_from_state(state)
    validation = step_record(state, "validate-artifacts").get("evidence", {})
    gdal = validation.get("gdal") if isinstance(validation.get("gdal"), dict) else {}
    gdal_lines = ""
    if gdal:
        gdal_lines = (
            "\n"
            f"- GDAL/OGR: ogr2ogr={gdal.get('ogr2ogr', 'not recorded')}; "
            f"ogrinfo={gdal.get('ogrinfo', 'not recorded')}\n"
            f"- GDAL checks: ogrinfo_summary={gdal.get('ogrinfo_summary_passed', False)}, "
            f"feature_count={gdal.get('feature_count_checked', False)}, "
            f"geometry_type={gdal.get('geometry_type_checked', False)}, "
            f"crs={gdal.get('crs_checked', False)}, "
            f"field_schema={gdal.get('field_schema_checked', False)}"
        )
    if is_preview_workflow(state):
        uploaded = step_record(state, "preview-upload").get("evidence", {}).get("uploaded_objects", [])
        preview_load = step_record(state, "preview-load").get("evidence", {})
        catalog_refresh = step_record(state, "preview-catalog-refresh").get("evidence", {})
        viewer_verify = step_record(state, "preview-viewer-verify").get("evidence", {})
        remote_paths = [f"- {obj['uri']} (generation {obj['generation']}, role {obj['role']})" for obj in uploaded]
        retained = []
        followup_state = f"""## Preview Load State

- Workflow: {preview_load.get('workflow_name', 'not recorded')}
- Workflow run: {preview_load.get('workflow_run_url', 'not recorded')}
- Dispatch ref: {preview_load.get('dispatched_ref', 'not recorded')}
- Status: {preview_load.get('status', 'not recorded')}
- Workflow inputs checked against preview ref: {preview_load.get('workflow_inputs_checked_against_preview_ref', False)}
- Viewer refresh verified: {preview_load.get('viewer_refresh_verified', False)}

## Preview Catalog Refresh

- Workflow: {catalog_refresh.get('workflow_name', 'not recorded')}
- Workflow run: {catalog_refresh.get('workflow_run_url', 'not recorded')}
- Dispatch ref: {catalog_refresh.get('dispatched_ref', 'not recorded')}
- Preview data mode: {catalog_refresh.get('preview_data_mode', 'not recorded')}
- Catalog JSON generation: {catalog_refresh.get('catalog_json_generation', 'not recorded')}
- Catalog generated at: {catalog_refresh.get('catalog_generated_at', 'not recorded')}
- Viewer catalog asset present: {viewer_verify.get('asset_slug_present', False)}
- Verified uploaded release artifact URIs: {len(viewer_verify.get('verified_uploaded_uris', []))}
"""
    else:
        publish_plan = state.get("publish_plan") or build_publish_plan_from_state(state)
        post_merge = step_record(state, "post-merge-verify").get("evidence", {})
        remote_paths = []
        for promotion in publish_plan.get("promotions", []):
            remote_paths.append(
                f"- {promotion['destination_uri']} (source generation {promotion['source_generation']}, "
                f"destination generation expectation {promotion.get('destination_generation') or 'absent'})"
            )
        retained = post_merge.get("retained_temp_dirs") or []
        followup_state = f"""## Post-Merge State

- Promoted objects verified: {post_merge.get('promoted_objects_verified', False)}
- Catalog freshness verified: {post_merge.get('catalog_freshness_verified', False)}
- Upload alert state: {post_merge.get('alert_state', 'not recorded')}
"""
    retained_text = "\n".join(f"- {path}" for path in retained) if retained else "- None recorded."
    body = f"""## Completion Report

Asset: `{plan['asset_slug']}`  
Proposal: `{state['proposal_id']}`  
Request classification: `{state.get('request_classification', 'canonical-publish')}`

## Validation

- Artifact validation: {validation.get('validation_summary', 'Not recorded.')}
- Commands run: {', '.join(validation.get('commands_run', [])) or 'Not recorded.'}
- Toolchain versions: {json.dumps(validation.get('tool_versions', {}), sort_keys=True)}
{gdal_lines}

## Remote Paths

{chr(10).join(remote_paths) if remote_paths else '- No publish-plan promotions recorded.'}

{followup_state}

## Retained Local Work Directories

{retained_text}
"""
    return body


def validate_state_for_pr(state: dict[str, Any]) -> dict[str, Any]:
    errors = []
    for step in STEP_DEFINITIONS:
        if step.optional or not step.is_required(state):
            continue
        if step.step_id == "pr-ready":
            continue
        if not step_completed(state, step.step_id):
            errors.append(f"{step.step_id} is incomplete")
    publish_plan = None
    if not errors and is_canonical_publish_workflow(state):
        try:
            publish_plan = build_publish_plan_from_state(state)
        except Exception as exc:  # noqa: BLE001 - surface validator details without hiding other status.
            errors.append(str(exc))
    return {
        "ready_for_pr": is_canonical_publish_workflow(state) and not errors,
        "ready_for_preview": is_preview_workflow(state) and not errors,
        "errors": errors,
        "publish_plan": publish_plan,
    }


def add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("source", type=Path, help="Local source file or directory to inspect.")
    parser.add_argument("--asset-slug", help="Lowercase kebab-case asset slug. Inferred from source name when omitted.")
    parser.add_argument("--title", help="Human-readable dataset title. Inferred from slug when omitted.")
    parser.add_argument("--category", required=True, help="Top-level bucket category.")
    parser.add_argument("--subcategory", required=True, help="Bucket subcategory.")
    parser.add_argument("--owner", default="SkyTruth")
    parser.add_argument("--source-name", help="Source name, URL, or version.")
    parser.add_argument("--license", dest="license_text", help="License or terms summary.")
    parser.add_argument("--citation", help="Preferred citation for the original source publication.")
    parser.add_argument("--update-cadence", default="manual")
    parser.add_argument("--canonical-format", help="Canonical format override.")
    parser.add_argument("--access-tier", default="public", choices=["public", "private"])
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--release-date", help="Optional intended release date in YYYY-MM-DD form.")
    parser.add_argument("--source-resolution-meters", type=float, help="Optional source resolution hint for PMTiles auto maxzoom.")
    parser.add_argument("--source-scale-denominator", type=float, help="Optional source scale denominator hint for PMTiles auto maxzoom.")
    parser.add_argument("--pmtiles-maxzoom", type=int, help="Optional explicit PMTiles maxzoom hint.")
    parser.add_argument("--pmtiles-maxzoom-reason", help="Required with --pmtiles-maxzoom.")
    parser.add_argument(
        "--pmtiles-detail-hint",
        choices=["coarse", "medium", "detailed"],
        help="Optional semantic display-detail hint for PMTiles auto maxzoom.",
    )
    parser.add_argument("--categories", type=Path, default=Path("catalog/categories.yaml"))
    parser.add_argument("--docs-dir", type=Path, default=Path("docs/assets"))


def plan_from_args(args: argparse.Namespace) -> ConciergePlan:
    return build_plan(
        source=args.source,
        asset_slug=args.asset_slug,
        title=args.title,
        category=args.category,
        subcategory=args.subcategory,
        owner=args.owner,
        source_name=args.source_name,
        license_text=args.license_text,
        citation=args.citation,
        update_cadence=args.update_cadence,
        canonical_format=args.canonical_format,
        access_tier=args.access_tier,
        bucket=args.bucket,
        release_date=args.release_date,
        source_resolution_meters=args.source_resolution_meters,
        source_scale_denominator=args.source_scale_denominator,
        pmtiles_maxzoom=args.pmtiles_maxzoom,
        pmtiles_maxzoom_reason=args.pmtiles_maxzoom_reason,
        pmtiles_detail_hint=args.pmtiles_detail_hint,
        categories_path=args.categories,
        docs_dir=args.docs_dir,
    )


def command_start(args: argparse.Namespace) -> int:
    if args.request_classification not in {"canonical-publish", "preview-only"}:
        raise WorkflowError(
            "the stateful first-upload workflow only handles canonical-publish and preview-only requests; "
            "use a scratch-only or diagnostic workflow for this request classification"
        )
    if args.request_classification == "preview-only":
        if args.bucket not in {DEFAULT_BUCKET, PREVIEW_BUCKET}:
            raise WorkflowError(f"preview-only workflow requires --bucket {PREVIEW_BUCKET} or omitted --bucket")
        args.bucket = PREVIEW_BUCKET
        if not args.release_date:
            raise WorkflowError("preview-only workflow requires --release-date for the disposable preview release bundle")
        args.preview_ref = args.preview_ref or current_git_branch()
        if not args.preview_ref:
            raise WorkflowError("preview-only workflow requires --preview-ref when the current git branch cannot be detected")
        if not PREVIEW_REF_RE.fullmatch(args.preview_ref):
            raise WorkflowError("preview-only --preview-ref may contain only letters, digits, dots, underscores, hyphens, and slashes")
    elif args.bucket == PREVIEW_BUCKET:
        raise WorkflowError(f"canonical-publish workflow must not use preview bucket {PREVIEW_BUCKET}")
    if not args.proposal_id or not PROPOSAL_RE.fullmatch(args.proposal_id):
        raise WorkflowError("start requires --proposal-id with only letters, digits, dots, underscores, and hyphens")
    plan = plan_from_args(args)
    if args.request_classification == "canonical-publish":
        duplicates = detect_existing_asset(plan)
        if duplicates and not args.allow_existing_asset:
            raise WorkflowError(
                "first-upload workflow found existing asset slug evidence; use an existing-dataset workflow or pass "
                f"--allow-existing-asset after review: {', '.join(duplicates)}"
            )
    state = workflow_state_payload(
        plan=plan,
        source=args.source,
        proposal_id=args.proposal_id,
        request_classification=args.request_classification,
        owner=args.owner,
        source_name=args.source_name,
        license_text=args.license_text,
        citation=args.citation,
        update_cadence=args.update_cadence,
        access_tier=args.access_tier,
        preview_ref=args.preview_ref if args.request_classification == "preview-only" else None,
    )
    state_file = args.state_file or default_state_file(plan.asset_slug, args.proposal_id)
    state["state_file"] = str(state_file)
    write_json_file(state_file, state, overwrite=args.overwrite_state)
    print(json.dumps({"state_file": str(state_file), "current_step": current_required_step(state).step_id}, indent=2, sort_keys=True))
    return 0


def command_next(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    step = current_required_step(state)
    if step is None:
        message = "All required preview steps are complete." if is_preview_workflow(state) else "All required pre-PR steps are complete."
        print(json.dumps({"complete": True, "message": message}, indent=2, sort_keys=True) if args.json else message)
        return 0
    print_instruction(render_instruction(state, step), as_json=args.json)
    return 0


def command_status(args: argparse.Namespace) -> int:
    print_status(load_state(args.state_file), as_json=args.json)
    return 0


def command_confirm(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    steps = workflow_steps_by_id()
    if args.step not in steps:
        raise WorkflowError(f"unknown step: {args.step}")
    step = steps[args.step]
    current = current_required_step(state)
    can_confirm_optional = step.optional and current is None
    if current is None and not can_confirm_optional and args.step != "post-merge-verify":
        raise WorkflowError("all required pre-PR steps are already complete")
    if current is not None and args.step != current.step_id:
        raise WorkflowError(f"cannot confirm {args.step}; current required step is {current.step_id}")
    if args.yes:
        if not step.allow_yes:
            raise WorkflowError(f"{step.step_id} requires structured evidence; --yes is not allowed")
        evidence = {}
    elif args.evidence_json:
        evidence = read_json_file(args.evidence_json, label="evidence JSON")
    else:
        raise WorkflowError("confirm requires --evidence-json for this step")
    normalized = step.validate_evidence(state, evidence)
    record = step_record(state, step.step_id)
    record.update(
        {
            "status": "completed",
            "completed_at": utc_now(),
            "evidence": normalized,
        }
    )
    if step.step_id == "pr-ready":
        state["publish_plan"] = normalized["publish_plan"]
        state["pr_body"] = normalized["pr_body"]
    save_state(args.state_file, state)
    next_step = current_required_step(state)
    print(
        json.dumps(
            {
                "completed_step": step.step_id,
                "next_step": next_step.step_id if next_step else None,
                "ready_for_pr": step_completed(state, "pr-ready"),
                "ready_for_preview": is_preview_workflow(state) and next_step is None,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def command_render_pr(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    if is_preview_workflow(state):
        raise WorkflowError("preview-only workflows do not render production publish PRs; use render-report")
    result = validate_state_for_pr(state)
    if not result["ready_for_pr"]:
        raise WorkflowError("workflow is not ready for PR: " + "; ".join(result["errors"]))
    body = render_pr_body_from_state(state, publish_plan=result["publish_plan"])
    print(body, end="" if body.endswith("\n") else "\n")
    return 0


def command_render_report(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    result = validate_state_for_pr(state)
    ready = result["ready_for_pr"] or result.get("ready_for_preview", False)
    if not ready:
        raise WorkflowError("workflow is not ready for completion report: " + "; ".join(result["errors"]))
    if not state.get("publish_plan"):
        state["publish_plan"] = result["publish_plan"]
    print(render_completion_report_from_state(state), end="")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    state = load_state(args.state_file)
    result = validate_state_for_pr(state)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ready_for_pr"] or result.get("ready_for_preview", False) else 1


def build_workflow_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guide a first-upload shared-datasets publish as a state machine.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start", help="Create a state file for a first-upload workflow.")
    add_plan_arguments(start)
    start.add_argument(
        "--request-classification",
        required=True,
        choices=["canonical-publish", "preview-only", "scratch-only", "diagnostic-only"],
        help="Explicitly classify the maintainer request before starting.",
    )
    start.add_argument("--proposal-id", required=True, help="Stable proposal id, usually pr-123, issue-123, or a branch slug.")
    start.add_argument(
        "--preview-ref",
        help="Feature branch, tag, or SHA used for preview-only catalog refresh and viewer verification. Defaults to the current git branch.",
    )
    start.add_argument("--state-file", type=Path, help="Optional state file path. Defaults under the standard temp workspace.")
    start.add_argument("--allow-existing-asset", action="store_true", help="Allow continuing after duplicate asset evidence is found.")
    start.add_argument("--overwrite-state", action="store_true", help="Allow replacing an existing state file.")
    start.set_defaults(func=command_start)

    next_parser = subparsers.add_parser("next", help="Print the single next required step.")
    next_parser.add_argument("--state-file", type=Path, required=True)
    next_parser.add_argument("--json", action="store_true")
    next_parser.set_defaults(func=command_next)

    confirm = subparsers.add_parser("confirm", help="Validate evidence and mark the current step complete.")
    confirm.add_argument("--state-file", type=Path, required=True)
    confirm.add_argument("--step", required=True)
    confirm.add_argument("--evidence-json", type=Path)
    confirm.add_argument("--yes", action="store_true", help="Only allowed for steps that explicitly require no evidence.")
    confirm.set_defaults(func=command_confirm)

    status = subparsers.add_parser("status", help="Show workflow status.")
    status.add_argument("--state-file", type=Path, required=True)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)

    render_pr = subparsers.add_parser("render-pr", help="Render a PR body with fenced publish plan.")
    render_pr.add_argument("--state-file", type=Path, required=True)
    render_pr.set_defaults(func=command_render_pr)

    render_report = subparsers.add_parser("render-report", help="Render a final completion report scaffold.")
    render_report.add_argument("--state-file", type=Path, required=True)
    render_report.set_defaults(func=command_render_report)

    validate = subparsers.add_parser("validate", help="Validate state readiness for PR rendering.")
    validate.add_argument("--state-file", type=Path, required=True)
    validate.set_defaults(func=command_validate)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    parser = build_workflow_parser()
    args = parser.parse_args(argv_list)
    try:
        return args.func(args)
    except (ConciergeError, WorkflowError) as exc:
        print(f"publishing-concierge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
