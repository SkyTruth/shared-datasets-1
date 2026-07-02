#!/usr/bin/env python3
"""Field profiling and curator field recommendations for the publishing concierge.

Owns the field identity/grouping/translation recommendation engine, its
source-file readers, and the profile dataclasses. Extracted from
publishing_concierge.py, which re-exposes the pieces its plan builder needs.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import random
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


GENERATED_FEATURE_ID_COLUMN = "feature_id"
GENERATED_FEATURE_ID_ALGORITHM = "shared-datasets-generated-feature-id:v1"
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
TRANSLATION_FIELD_RE = re.compile(
    r"(^name$|name|title|label|designation|desig|description|desc|status|type|class|category|country|region|zone|site|place|locality|basin|province)",
    re.IGNORECASE,
)
LONG_TEXT_TRANSLATION_FIELD_RE = re.compile(r"(description|desc|note|notes|remark|remarks|comment|comments)", re.IGNORECASE)
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
class GeneratedFeatureIdOption:
    available: bool
    column: str = GENERATED_FEATURE_ID_COLUMN
    algorithm: str = GENERATED_FEATURE_ID_ALGORITHM
    reason: str = (
        "Generated decimal feature_id fallback when no URL-safe source field candidate is suitable."
    )
    warning: str = (
        "Generated IDs are assigned by the release feature model and carried forward by the configured identity key."
    )


@dataclass(frozen=True)
class CuratorFieldOptions:
    id_field_candidates: list[FieldRecommendation]
    group_field_candidates: list[FieldRecommendation]
    notes: list[str]
    generated_feature_id_option: GeneratedFeatureIdOption = dataclass_field(default_factory=lambda: GeneratedFeatureIdOption(False))
    translation_field_candidates: list[FieldRecommendation] = dataclass_field(default_factory=list)
    total_rows: int | None = None
    total_columns: int | None = None
    profiled_row_count: int | None = None
    profile_scope: str = "unavailable"
    hidden_unlikely_count: int = 0
    all_fields_profile: list[FieldProfile] = dataclass_field(default_factory=list)


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


def field_is_translation_like(field: str) -> bool:
    normalized = normalize_field_name(field)
    return bool(TRANSLATION_FIELD_RE.search(normalized)) and not field_is_measurement(normalized)


def field_is_long_text_translation_like(field: str) -> bool:
    return bool(LONG_TEXT_TRANSLATION_FIELD_RE.search(normalize_field_name(field)))


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


def recommendation_concerns(profile: FieldProfile, *, source_id: bool) -> list[str]:
    concerns: list[str] = []
    if profile.empty_values:
        concerns.append(f"{profile.empty_values:,} empty value(s)")
    if profile.sentinel_value_count:
        concerns.append(f"{profile.sentinel_value_count:,} sentinel-like value(s)")
    if profile.duplicate_value_count and source_id:
        concerns.append(
            f"{profile.duplicate_value_count:,} duplicate value(s) across {profile.duplicate_row_count:,} row(s)"
        )
    if profile.domination_percent >= 25:
        top = profile.top_examples[0] if profile.top_examples else None
        if top:
            concerns.append(f"top value {top.value!r} has {top.count:,} row(s)")
    if profile.skew_ratio is not None and profile.skew_ratio >= 50:
        concerns.append(f"high skew ratio {profile.skew_ratio:g}")
    if not source_id and profile.distinction_percent > 95:
        concerns.append("near-row-unique; usually search-only, not grouping")
    if not source_id and (profile.distinction_percent <= 1 or profile.distinct_values <= 5):
        concerns.append("very low distinction; treat as filter/facet")
    return concerns


def source_id_recommendation(profile: FieldProfile) -> FieldRecommendation | None:
    if profile.distinct_values <= 1 or field_is_measurement(profile.name):
        return None
    id_like = field_is_id_like(profile.name)
    if not id_like:
        return None
    concerns = recommendation_concerns(profile, source_id=True)
    if profile.emptiness_percent >= 10:
        confidence = "low"
        reason = "ID-like field name, but too many rows are empty for a source feature ID."
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
        reason = "ID-like field name, but values are not unique enough for a source feature ID."
    return FieldRecommendation(
        field=profile.name,
        role="source feature_id candidate",
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
    concerns = recommendation_concerns(profile, source_id=False)
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
        reason = "Near-row-unique field; likely search-only rather than a generated identity key."
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


def translation_recommendation(profile: FieldProfile) -> FieldRecommendation | None:
    if profile.datatype != "string" or profile.non_empty_values <= 0:
        return None
    if field_is_measurement(profile.name) or field_is_id_like(profile.name):
        return None
    if field_is_code_like(profile.name) or field_is_temporal(profile.name, profile.datatype):
        return None

    normalized = normalize_field_name(profile.name)
    long_text = field_is_long_text_translation_like(profile.name)
    translation_like = field_is_translation_like(profile.name) or grouping_role(profile) is not None
    if field_is_low_information(profile.name) and not long_text:
        return None
    if not translation_like and not long_text:
        return None

    concerns = recommendation_concerns(profile, source_id=False)
    if profile.distinct_values <= 1:
        concerns.append("single distinct value; translation may not be worth generated sidecar rows")
    if profile.average_value_length is not None and profile.average_value_length >= 240:
        concerns.append(f"long average value length {profile.average_value_length:g}; review rate limits and output quality")
    elif profile.average_value_length is not None and profile.average_value_length >= 120:
        concerns.append(f"moderate average value length {profile.average_value_length:g}; spot-check machine translation quality")

    if normalized in {"name", "name_eng", "title", "label"} or normalized.endswith("_name"):
        confidence = "high"
        reason = "Human-readable display label; strong default for localized metadata sidecars."
    elif long_text:
        confidence = "low"
        reason = "Longer descriptive text can be translated, but should be explicitly approved and reviewed."
    else:
        confidence = "medium"
        reason = "Human-readable string field that may be useful in localized metadata sidecars."

    return FieldRecommendation(
        field=profile.name,
        role="deep-translator candidate",
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
    if source_id_recommendation(profile):
        return "source feature_id candidate", None
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
    return "unlikely", "does not match source field ID or grouping/search heuristics"


def profile_rows(
    rows: Sequence[dict[str, Any]],
    *,
    total_rows: int | None = None,
    profile_scope: str = "full",
    generated_feature_id_available: bool = False,
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
    id_candidates = [candidate for profile in profiles if (candidate := source_id_recommendation(profile))]
    group_candidates = [candidate for profile in profiles if (candidate := group_recommendation(profile))]
    translation_candidates = [candidate for profile in profiles if (candidate := translation_recommendation(profile))]

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
    translation_candidates.sort(
        key=lambda candidate: (
            candidate.confidence != "high",
            candidate.confidence == "low",
            candidate.field.lower() != "name" and not candidate.field.lower().endswith("_name"),
            -(candidate.non_empty_values or 0),
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
        notes.append("No high-likelihood source feature ID field was found.")
    if not group_candidates:
        notes.append("No high-likelihood grouping/search field was found.")
    if translation_candidates:
        notes.append(
            "Translation field candidates are suggestions only; confirm exact locales and fields in the translation-decision step."
        )
    else:
        notes.append("No high-likelihood machine-translation field was found.")
    if generated_feature_id_available:
        notes.append(
            f"Fallback {GENERATED_FEATURE_ID_COLUMN} is available when no URL-safe source field ID is suitable."
        )
    return CuratorFieldOptions(
        id_candidates[:8],
        group_candidates[:8],
        notes,
        generated_feature_id_option=GeneratedFeatureIdOption(generated_feature_id_available),
        translation_field_candidates=translation_candidates[:8],
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
    generated_feature_id_available: bool = False,
) -> CuratorFieldOptions:
    sampled_rows, total_rows, profile_scope = profile_row_iter(rows)
    return profile_rows(
        sampled_rows,
        total_rows=total_rows,
        profile_scope=profile_scope,
        generated_feature_id_available=generated_feature_id_available,
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
    options = profile_iterable_rows(rows, generated_feature_id_available=True)
    return CuratorFieldOptions(
        options.id_field_candidates,
        options.group_field_candidates,
        [
            *options.notes,
            "OGR attribute profiling uses all rows when at or below the sample threshold; larger sources use a deterministic random sample.",
            "Curator must choose grouping fields before generated IDs are built.",
        ],
        generated_feature_id_option=options.generated_feature_id_option,
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
        notes.append("No high-likelihood source feature ID field name was found.")
    if not group_candidates:
        notes.append("No high-likelihood grouping/search field name was found.")
    return CuratorFieldOptions(
        id_candidates[:8],
        group_candidates[:8],
        notes,
        generated_feature_id_option=GeneratedFeatureIdOption(True),
        total_columns=len(field_names),
        profile_scope="schema_only",
        hidden_unlikely_count=max(0, len(field_names) - len({candidate.field for candidate in [*id_candidates, *group_candidates]})),
    )


def recommend_curator_field_options(source: Path, canonical_format: str) -> CuratorFieldOptions:
    is_vector_fgb = canonical_format == "fgb"
    try:
        if source.suffix.lower() == ".csv":
            return profile_iterable_rows(iter_csv_rows(source), generated_feature_id_available=is_vector_fgb)
        if source.suffix.lower() == ".ndgeojson":
            return profile_iterable_rows(iter_ndgeojson_rows(source), generated_feature_id_available=is_vector_fgb)
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
            return profile_rows(read_geojson_rows(source, limit=None), generated_feature_id_available=is_vector_fgb)
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
            generated_feature_id_option=GeneratedFeatureIdOption(True),
        )
    return CuratorFieldOptions(
        [],
        [],
        ["No source attribute profile was available; populate ID and group-field candidates after canonical conversion."],
    )
