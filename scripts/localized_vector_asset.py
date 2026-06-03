#!/usr/bin/env python3
"""Validate localization sidecars and build metadata-lookup PMTiles."""

from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import catalog_docs, vector_asset  # noqa: E402
from scripts.pmtiles_zoom import profile_fgb, profile_payload, recommend_maxzoom, validate_detail_hint  # noqa: E402


VALUE_REVIEW_STATES = {"source_provided", "machine_translated", "human_reviewed"}
LOCALIZATION_STORAGE = "localization_csv_v1"
JOIN_KEY = "ext_id"
FALLBACK_FIELD = "name"
FALLBACK_REVIEW_STATE_FIELD = "name_review_state"
LOCALIZATION_SUFFIX = "-localizations.csv"
FIELD_SAFE_LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$")
LOCALIZED_FIELD_RE = re.compile(r"^name_([a-z]{2,3}(?:_[a-z0-9]{2,8})*)$")
NO_CACHE_CONTROL = "no-cache, max-age=0, must-revalidate"
LATEST_DESTINATION_GENERATION_PLACEHOLDER = "<fill-current-generation-or-empty-for-new-object>"


class LocalizedVectorAssetError(ValueError):
    """Raised when localized vector artifacts cannot be built or validated."""


@dataclass(frozen=True)
class LocalizationCsvProfile:
    path: str
    fieldnames: tuple[str, ...]
    row_count: int
    ext_ids: tuple[str, ...]
    locale_fields: tuple[str, ...]
    review_state_fields: tuple[str, ...]
    aggregate_review_states: dict[str, str]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class FgbKeyProfile:
    path: str
    ext_id_field: str
    row_count: int
    ext_ids: tuple[str, ...]
    property_keys: tuple[str, ...]
    fallback_names: dict[str, str]
    errors: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.errors


@dataclass(frozen=True)
class LocalizedPmtilesPlan:
    asset_slug: str
    layer_name: str
    fgb_path: str
    localization_path: str
    output_path: str
    work_dir: str
    profile_path: str
    minzoom: int
    maxzoom_mode: str
    maxzoom: int | None
    source_resolution_meters: float | None
    source_scale_denominator: float | None
    pmtiles_maxzoom: int | None
    pmtiles_maxzoom_reason: str | None
    pmtiles_detail_hint: str | None
    localized_property_fields: tuple[str, ...]
    tippecanoe_extra_args: tuple[str, ...]
    ogr2ogr_bin: str
    tippecanoe_bin: str
    pmtiles_bin: str
    tool_paths: dict[str, str]
    tool_versions: dict[str, str]
    commands: tuple[dict[str, Any], ...]


def as_text(value: Any) -> str:
    return "" if value is None else str(value)


def csv_header(path: Path) -> list[str]:
    if not path.exists():
        raise LocalizedVectorAssetError(f"localization CSV does not exist: {path}")
    with path.open(newline="") as handle:
        try:
            header = next(csv.reader(handle))
        except StopIteration as exc:
            raise LocalizedVectorAssetError(f"localization CSV is empty: {path}") from exc
    return header


def read_csv_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    fieldnames = csv_header(path)
    duplicate_fields = sorted({field for field in fieldnames if fieldnames.count(field) > 1})
    if duplicate_fields:
        raise LocalizedVectorAssetError(f"localization CSV has duplicate column(s): {', '.join(duplicate_fields)}")
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = [{str(key): as_text(value) for key, value in row.items() if key is not None} for row in reader]
    return fieldnames, rows


def locale_fields_from_header(fieldnames: Sequence[str]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    locale_fields: dict[str, str] = {}
    review_state_fields: dict[str, str] = {}
    errors: list[str] = []
    required = {JOIN_KEY, FALLBACK_FIELD, FALLBACK_REVIEW_STATE_FIELD}
    for field in fieldnames:
        if field in required:
            continue
        if field.endswith("_review_state"):
            base_field = field[: -len("_review_state")]
            if not LOCALIZED_FIELD_RE.fullmatch(base_field):
                errors.append(f"unsupported review-state column: {field}")
                continue
            review_state_fields[base_field] = field
            continue
        match = LOCALIZED_FIELD_RE.fullmatch(field)
        if not match:
            errors.append(f"unsupported localization column: {field}")
            continue
        locale_code = match.group(1)
        if not FIELD_SAFE_LOCALE_RE.fullmatch(locale_code):
            errors.append(f"invalid field-safe locale code in column: {field}")
            continue
        locale_fields[field] = locale_code

    for field in sorted(locale_fields):
        expected_review_field = f"{field}_review_state"
        if review_state_fields.get(field) != expected_review_field:
            errors.append(f"localized column {field} requires paired review-state column {expected_review_field}")
    for field in sorted(review_state_fields):
        if field not in locale_fields:
            errors.append(f"review-state column {review_state_fields[field]} has no paired localized value column {field}")
    return locale_fields, review_state_fields, errors


def aggregate_review_state(states: Iterable[str]) -> str:
    unique_states = sorted({state for state in states if state})
    if not unique_states:
        return ""
    if len(unique_states) == 1:
        return unique_states[0]
    return "mixed"


def validate_localization_csv(path: Path) -> tuple[LocalizationCsvProfile, list[dict[str, str]]]:
    fieldnames, rows = read_csv_rows(path)
    errors: list[str] = []
    warnings: list[str] = []
    for required in (JOIN_KEY, FALLBACK_FIELD, FALLBACK_REVIEW_STATE_FIELD):
        if required not in fieldnames:
            errors.append(f"localization CSV is missing required column {required!r}")
    locale_fields, review_state_fields, header_errors = locale_fields_from_header(fieldnames)
    errors.extend(header_errors)

    seen_ext_ids: dict[str, int] = {}
    aggregate_states: dict[str, list[str]] = {field: [] for field in locale_fields}
    ext_ids: list[str] = []
    for row_number, row in enumerate(rows, start=2):
        ext_id = as_text(row.get(JOIN_KEY)).strip()
        if not ext_id:
            errors.append(f"row {row_number}: ext_id is required")
        elif ext_id in seen_ext_ids:
            errors.append(f"row {row_number}: duplicate ext_id {ext_id!r}; first seen on row {seen_ext_ids[ext_id]}")
        else:
            seen_ext_ids[ext_id] = row_number
            ext_ids.append(ext_id)

        fallback = as_text(row.get(FALLBACK_FIELD)).strip()
        fallback_state = as_text(row.get(FALLBACK_REVIEW_STATE_FIELD)).strip()
        if not fallback:
            errors.append(f"row {row_number}: name is required")
        if not fallback_state:
            errors.append(f"row {row_number}: name_review_state is required")
        elif fallback_state not in VALUE_REVIEW_STATES:
            errors.append(f"row {row_number}: name_review_state must be one of {', '.join(sorted(VALUE_REVIEW_STATES))}")

        for field, locale_code in sorted(locale_fields.items()):
            review_field = review_state_fields[field]
            value = as_text(row.get(field)).strip()
            review_state = as_text(row.get(review_field)).strip()
            if value and not review_state:
                errors.append(f"row {row_number}: {review_field} is required when {field} is populated")
            elif not value and review_state:
                errors.append(f"row {row_number}: {review_field} must be blank when {field} is blank")
            elif review_state and review_state not in VALUE_REVIEW_STATES:
                errors.append(f"row {row_number}: {review_field} must be one of {', '.join(sorted(VALUE_REVIEW_STATES))}")
            if value and review_state:
                aggregate_states[field].append(review_state)

    for field, values in aggregate_states.items():
        if not values and field in locale_fields:
            warnings.append(f"{field} has no nonblank localized values")

    aggregates = {locale_fields[field]: aggregate_review_state(states) for field, states in aggregate_states.items()}
    profile = LocalizationCsvProfile(
        path=str(path),
        fieldnames=tuple(fieldnames),
        row_count=len(rows),
        ext_ids=tuple(ext_ids),
        locale_fields=tuple(sorted(locale_fields)),
        review_state_fields=tuple(review_state_fields[field] for field in sorted(review_state_fields)),
        aggregate_review_states=aggregates,
        errors=tuple(errors),
        warnings=tuple(dict.fromkeys(warnings)),
    )
    return profile, rows


def load_asset_localized_names(asset_doc: Path) -> dict[str, Any]:
    metadata, _body = catalog_docs.split_frontmatter(asset_doc.read_text(), asset_doc)
    localized_names = catalog_docs.normalize_localized_names(metadata.get("localized_names"), path=asset_doc)
    if not localized_names:
        raise LocalizedVectorAssetError(f"{asset_doc}: localized_names metadata is required")
    if localized_names.get("storage") != LOCALIZATION_STORAGE:
        raise LocalizedVectorAssetError(f"{asset_doc}: localized_names.storage must be {LOCALIZATION_STORAGE!r}")
    return localized_names


def validate_metadata_matches_csv(
    *,
    localized_names: dict[str, Any],
    csv_profile: LocalizationCsvProfile,
    csv_path: Path,
) -> list[str]:
    errors: list[str] = []
    expected_name = Path(str(localized_names["localization_file"])).name
    if csv_path.name != expected_name:
        errors.append(f"localization CSV filename {csv_path.name!r} does not match asset-doc localization_file {expected_name!r}")

    declared_by_field = {
        str(translation["field"]): translation
        for translation in localized_names.get("translations", [])
        if isinstance(translation, dict)
    }
    csv_fields = set(csv_profile.locale_fields)
    declared_fields = set(declared_by_field)
    missing = sorted(declared_fields - csv_fields)
    extra = sorted(csv_fields - declared_fields)
    if missing:
        errors.append(f"localization CSV is missing declared localized column(s): {', '.join(missing)}")
    if extra:
        errors.append(f"localization CSV has localized column(s) not declared in asset doc: {', '.join(extra)}")

    for field, translation in sorted(declared_by_field.items()):
        expected_review_field = f"{field}_review_state"
        actual_review_field = str(translation.get("review_state_field") or "")
        if actual_review_field != expected_review_field:
            errors.append(f"asset-doc translation {field} must use review_state_field {expected_review_field}")
        locale_code = str(translation.get("locale_code") or "")
        expected_state = csv_profile.aggregate_review_states.get(locale_code, "")
        actual_state = str(translation.get("review_state") or "")
        if expected_state and actual_state != expected_state:
            errors.append(
                f"asset-doc translation {field} review_state {actual_state!r} does not match CSV aggregate {expected_state!r}"
            )
    return errors


def iter_ogr_features(
    source: Path,
    *,
    source_layer: str | None = None,
    ogr2ogr_bin: str = "ogr2ogr",
) -> Iterable[dict[str, Any]]:
    command = [ogr2ogr_bin, "-f", "GeoJSONSeq", "-t_srs", "EPSG:4326", "/vsistdout/", str(source)]
    if source_layer:
        command.append(source_layer)
    process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdout is None:
        raise LocalizedVectorAssetError(f"could not stream features with {ogr2ogr_bin}: missing stdout pipe")
    try:
        for line_number, line in enumerate(process.stdout, start=1):
            if not line.strip():
                continue
            try:
                feature = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LocalizedVectorAssetError(f"OGR GeoJSONSeq line {line_number} is not valid JSON: {exc}") from exc
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise LocalizedVectorAssetError(f"OGR GeoJSONSeq line {line_number} is not a Feature object")
            yield feature
    finally:
        stderr = process.stderr.read() if process.stderr is not None else ""
        returncode = process.wait()
    if returncode != 0:
        raise LocalizedVectorAssetError(f"{ogr2ogr_bin} failed while streaming {source}: {stderr.strip()}")


def load_fgb_key_profile(
    fgb: Path,
    *,
    ext_id_field: str = JOIN_KEY,
    fallback_name_field: str | None = None,
    source_layer: str | None = None,
    ogr2ogr_bin: str = "ogr2ogr",
) -> FgbKeyProfile:
    errors: list[str] = []
    ext_ids: list[str] = []
    seen: dict[str, int] = {}
    fallback_names: dict[str, str] = {}
    property_keys: set[str] = set()

    for feature_index, feature in enumerate(
        iter_ogr_features(fgb, source_layer=source_layer, ogr2ogr_bin=ogr2ogr_bin),
        start=1,
    ):
        properties = feature.get("properties") or {}
        if not isinstance(properties, dict):
            errors.append(f"feature {feature_index}: properties must be an object")
            continue
        property_keys.update(str(key) for key in properties.keys())
        ext_id = as_text(properties.get(ext_id_field)).strip()
        if not ext_id:
            errors.append(f"feature {feature_index}: {ext_id_field} is required")
        elif ext_id in seen:
            errors.append(f"feature {feature_index}: duplicate {ext_id_field} {ext_id!r}; first seen on feature {seen[ext_id]}")
        else:
            seen[ext_id] = feature_index
            ext_ids.append(ext_id)
        if fallback_name_field:
            fallback = as_text(properties.get(fallback_name_field)).strip()
            if not fallback:
                errors.append(f"feature {feature_index}: fallback name field {fallback_name_field!r} is required")
            elif ext_id:
                fallback_names[ext_id] = fallback

    return FgbKeyProfile(
        path=str(fgb),
        ext_id_field=ext_id_field,
        row_count=len(ext_ids),
        ext_ids=tuple(ext_ids),
        property_keys=tuple(sorted(property_keys)),
        fallback_names=fallback_names,
        errors=tuple(errors),
    )


def validate_localizations(
    *,
    fgb: Path,
    localizations: Path,
    asset_doc: Path,
    source_layer: str | None = None,
    ogr2ogr_bin: str = "ogr2ogr",
) -> dict[str, Any]:
    localized_names = load_asset_localized_names(asset_doc)
    csv_profile, _rows = validate_localization_csv(localizations)
    fgb_profile = load_fgb_key_profile(
        fgb,
        ext_id_field=str(localized_names["join_key"]),
        source_layer=source_layer,
        ogr2ogr_bin=ogr2ogr_bin,
    )
    errors = [*csv_profile.errors, *fgb_profile.errors]
    errors.extend(validate_metadata_matches_csv(localized_names=localized_names, csv_profile=csv_profile, csv_path=localizations))

    fgb_ids = set(fgb_profile.ext_ids)
    localization_ids = set(csv_profile.ext_ids)
    missing = sorted(fgb_ids - localization_ids)
    orphaned = sorted(localization_ids - fgb_ids)
    if missing:
        errors.append(f"localization CSV is missing {len(missing)} ext_id value(s) present in the FGB")
    if orphaned:
        errors.append(f"localization CSV has {len(orphaned)} ext_id value(s) not present in the FGB")

    return {
        "valid": not errors,
        "errors": errors,
        "warnings": list(csv_profile.warnings),
        "fgb": asdict(fgb_profile),
        "localizations": asdict(csv_profile),
        "missing_ext_ids": missing[:20],
        "missing_ext_id_count": len(missing),
        "orphan_ext_ids": orphaned[:20],
        "orphan_ext_id_count": len(orphaned),
    }


def seed_localizations(
    *,
    fgb: Path,
    localizations: Path,
    ext_id_field: str,
    fallback_name_field: str,
    source_layer: str | None = None,
    ogr2ogr_bin: str = "ogr2ogr",
    dry_run: bool = False,
) -> dict[str, Any]:
    fgb_profile = load_fgb_key_profile(
        fgb,
        ext_id_field=ext_id_field,
        fallback_name_field=fallback_name_field,
        source_layer=source_layer,
        ogr2ogr_bin=ogr2ogr_bin,
    )
    if fgb_profile.errors:
        raise LocalizedVectorAssetError("; ".join(fgb_profile.errors))

    if localizations.exists():
        csv_profile, rows = validate_localization_csv(localizations)
        if csv_profile.errors:
            raise LocalizedVectorAssetError("; ".join(csv_profile.errors))
        fieldnames = list(csv_profile.fieldnames)
    else:
        rows = []
        fieldnames = [JOIN_KEY, FALLBACK_FIELD, FALLBACK_REVIEW_STATE_FIELD]

    existing_by_id = {as_text(row.get(JOIN_KEY)).strip(): row for row in rows if as_text(row.get(JOIN_KEY)).strip()}
    additions: list[dict[str, str]] = []
    drift: list[dict[str, str]] = []
    for ext_id in fgb_profile.ext_ids:
        fallback = fgb_profile.fallback_names[ext_id]
        existing = existing_by_id.get(ext_id)
        if existing is None:
            additions.append(
                {
                    **{field: "" for field in fieldnames},
                    JOIN_KEY: ext_id,
                    FALLBACK_FIELD: fallback,
                    FALLBACK_REVIEW_STATE_FIELD: "source_provided",
                }
            )
            continue
        existing_name = as_text(existing.get(FALLBACK_FIELD)).strip()
        if existing_name and existing_name != fallback:
            drift.append({"ext_id": ext_id, "localization_name": existing_name, "source_name": fallback})

    orphaned = sorted(set(existing_by_id) - set(fgb_profile.ext_ids))
    if additions and not dry_run:
        localizations.parent.mkdir(parents=True, exist_ok=True)
        with localizations.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            writer.writerows(additions)

    return {
        "dry_run": dry_run,
        "localizations": str(localizations),
        "input_rows": len(rows),
        "fgb_rows": fgb_profile.row_count,
        "appended_rows": len(additions),
        "fallback_drift_count": len(drift),
        "fallback_drift_examples": drift[:20],
        "orphan_ext_id_count": len(orphaned),
        "orphan_ext_ids": orphaned[:20],
        "fieldnames": fieldnames,
    }


def localized_output_fields(csv_profile: LocalizationCsvProfile) -> tuple[str, ...]:
    return (FALLBACK_FIELD, *csv_profile.locale_fields)


def metadata_lookup_feature(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.get("properties") or {}
    if not isinstance(properties, dict):
        raise LocalizedVectorAssetError("feature properties must be an object")
    feature_id = as_text(properties.get(vector_asset.FEATURE_ID_COLUMN)).strip()
    if not feature_id:
        raise LocalizedVectorAssetError("feature is missing feature_id during PMTiles metadata projection")
    ext_id = as_text(properties.get(JOIN_KEY)).strip()
    if not ext_id:
        raise LocalizedVectorAssetError("feature is missing ext_id during PMTiles metadata projection")
    next_feature = dict(feature)
    next_feature["properties"] = {vector_asset.FEATURE_ID_COLUMN: feature_id, JOIN_KEY: ext_id}
    return next_feature


def build_pmtiles_plan(
    *,
    fgb: Path,
    localizations: Path,
    asset_slug: str,
    output: Path,
    work_dir: Path | None = None,
    layer_name: str | None = None,
    minzoom: int = 0,
    maxzoom: str | int | None = vector_asset.AUTO_MAXZOOM,
    maxzoom_reason: str | None = None,
    source_resolution_meters: float | None = None,
    source_scale_denominator: float | None = None,
    pmtiles_maxzoom: int | None = None,
    pmtiles_maxzoom_reason: str | None = None,
    pmtiles_detail_hint: str | None = None,
    tippecanoe_extra_args: Sequence[str] = (),
    ogr2ogr_bin: str = "ogr2ogr",
    tippecanoe_bin: str = "tippecanoe",
    pmtiles_bin: str = "pmtiles",
    allow_repo_output: bool = False,
    allow_low_maxzoom: bool = False,
    allow_high_maxzoom: bool = False,
    allow_point_dropping: bool = False,
) -> LocalizedPmtilesPlan:
    vector_asset.validate_asset_slug(asset_slug)
    if not fgb.exists():
        raise FileNotFoundError(f"FGB does not exist: {fgb}")
    if not localizations.exists():
        raise FileNotFoundError(f"localization CSV does not exist: {localizations}")
    if minzoom < 0:
        raise LocalizedVectorAssetError("zoom range must satisfy 0 <= minzoom <= maxzoom")
    resolved_maxzoom, maxzoom_mode = vector_asset.parse_maxzoom(maxzoom)
    if resolved_maxzoom is not None:
        vector_asset.validate_manual_maxzoom(
            maxzoom=resolved_maxzoom,
            minzoom=minzoom,
            maxzoom_reason=maxzoom_reason,
            allow_low_maxzoom=allow_low_maxzoom,
            allow_high_maxzoom=allow_high_maxzoom,
        )
    vector_asset.validate_explicit_pmtiles_maxzoom(
        pmtiles_maxzoom=pmtiles_maxzoom,
        pmtiles_maxzoom_reason=pmtiles_maxzoom_reason,
        allow_low_maxzoom=allow_low_maxzoom,
        allow_high_maxzoom=allow_high_maxzoom,
    )
    if source_resolution_meters is not None and source_resolution_meters <= 0:
        raise LocalizedVectorAssetError("source resolution must be positive")
    if source_scale_denominator is not None and source_scale_denominator <= 0:
        raise LocalizedVectorAssetError("source scale denominator must be positive")
    pmtiles_detail_hint = validate_detail_hint(pmtiles_detail_hint)

    csv_profile, _rows = validate_localization_csv(localizations)
    if csv_profile.errors:
        raise LocalizedVectorAssetError("; ".join(csv_profile.errors))
    required_properties = (vector_asset.FEATURE_ID_COLUMN, JOIN_KEY)
    effective_tippecanoe_args = (*vector_asset.DEFAULT_TIPPECANOE_ARGS, *tippecanoe_extra_args)
    vector_asset.validate_tippecanoe_args(
        effective_tippecanoe_args,
        allow_point_dropping=allow_point_dropping,
        required_properties=required_properties,
    )

    work = work_dir or vector_asset.default_work_dir(asset_slug)
    profile_path = work / "profiles" / "localized-pmtiles-profile.json"
    vector_asset.ensure_local_output_path(output, label="PMTiles output", allow_repo_output=allow_repo_output)
    vector_asset.ensure_local_output_path(work, label="work directory", allow_repo_output=allow_repo_output)
    layer = layer_name or vector_asset.slug_to_layer_name(asset_slug)
    tippecanoe_command = [
        tippecanoe_bin,
        "-f",
        "-q",
        "--projection=EPSG:4326",
        "--minimum-zoom",
        str(minzoom),
        "--maximum-zoom",
        "<resolved-after-profile>" if resolved_maxzoom is None else str(resolved_maxzoom),
        "-o",
        str(output),
        "-l",
        layer,
        "-n",
        asset_slug.replace("-", " ").title(),
        "-N",
        f"{asset_slug.replace('-', ' ').title()} metadata lookup vector tiles",
        *effective_tippecanoe_args,
    ]
    return LocalizedPmtilesPlan(
        asset_slug=asset_slug,
        layer_name=layer,
        fgb_path=str(fgb),
        localization_path=str(localizations),
        output_path=str(output),
        work_dir=str(work),
        profile_path=str(profile_path),
        minzoom=minzoom,
        maxzoom_mode=maxzoom_mode,
        maxzoom=resolved_maxzoom,
        source_resolution_meters=source_resolution_meters,
        source_scale_denominator=source_scale_denominator,
        pmtiles_maxzoom=pmtiles_maxzoom,
        pmtiles_maxzoom_reason=pmtiles_maxzoom_reason,
        pmtiles_detail_hint=pmtiles_detail_hint,
        localized_property_fields=localized_output_fields(csv_profile),
        tippecanoe_extra_args=tuple(effective_tippecanoe_args),
        ogr2ogr_bin=ogr2ogr_bin,
        tippecanoe_bin=tippecanoe_bin,
        pmtiles_bin=pmtiles_bin,
        tool_paths=vector_asset.tool_paths(
            ogr2ogr_bin=ogr2ogr_bin,
            tippecanoe_bin=tippecanoe_bin,
            pmtiles_bin=pmtiles_bin,
        ),
        tool_versions=vector_asset.tool_versions(
            ogr2ogr_bin=ogr2ogr_bin,
            tippecanoe_bin=tippecanoe_bin,
            pmtiles_bin=pmtiles_bin,
        ),
        commands=(
            {
                "kind": "metadata_lookup_pipeline_source",
                "argv": [ogr2ogr_bin, "-f", "GeoJSONSeq", "-t_srs", "EPSG:4326", "/vsistdout/", str(fgb)],
            },
            {"kind": "metadata_lookup_pipeline_sink", "argv": tippecanoe_command},
        ),
    )


def run_metadata_lookup_pipeline(
    plan: LocalizedPmtilesPlan,
    *,
    resolved_maxzoom: int,
) -> None:
    source_command = [plan.ogr2ogr_bin, "-f", "GeoJSONSeq", "-t_srs", "EPSG:4326", "/vsistdout/", plan.fgb_path]
    sink_command = [
        plan.tippecanoe_bin,
        "-f",
        "-q",
        "--projection=EPSG:4326",
        "--minimum-zoom",
        str(plan.minzoom),
        "--maximum-zoom",
        str(resolved_maxzoom),
        "-o",
        plan.output_path,
        "-l",
        plan.layer_name,
        "-n",
        plan.asset_slug.replace("-", " ").title(),
        "-N",
        f"{plan.asset_slug.replace('-', ' ').title()} metadata lookup vector tiles",
        *plan.tippecanoe_extra_args,
    ]
    source = subprocess.Popen(source_command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if source.stdout is None:
        raise LocalizedVectorAssetError(f"pipeline source did not expose stdout: {shlex.join(source_command)}")
    try:
        sink = subprocess.Popen(
            sink_command,
            text=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except Exception:
        source.kill()
        source.wait()
        raise
    assert sink.stdin is not None
    try:
        for line in source.stdout:
            if not line.strip():
                continue
            feature = json.loads(line)
            sink.stdin.write(
                json.dumps(
                    metadata_lookup_feature(feature),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
    except Exception:
        source.kill()
        sink.kill()
        source.wait()
        sink.wait()
        raise
    finally:
        try:
            sink.stdin.close()
        except BrokenPipeError:
            pass

    sink_stdout = sink.stdout.read() if sink.stdout is not None else ""
    sink_stderr = sink.stderr.read() if sink.stderr is not None else ""
    source_stderr = source.stderr.read() if source.stderr is not None else ""
    source_returncode = source.wait()
    sink_returncode = sink.wait()
    if source_returncode != 0 or sink_returncode != 0:
        details = [
            "metadata lookup PMTiles streaming pipeline failed.",
            f"source command: {shlex.join(source_command)}",
            f"sink command: {shlex.join(sink_command)}",
        ]
        if source_returncode != 0:
            details.append(f"source exited {source_returncode}: {source_stderr.strip() or 'no stderr'}")
        if sink_returncode != 0:
            details.append(f"sink exited {sink_returncode}: {sink_stderr.strip() or 'no stderr'}")
        if sink_stdout.strip():
            details.append(f"sink stdout: {sink_stdout.strip()}")
        raise LocalizedVectorAssetError(" ".join(details))


def validate_pmtiles_properties(
    *,
    pmtiles_path: Path,
    required_properties: Sequence[str],
    pmtiles_bin: str,
    profile: Any,
    decode_zoom: int,
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    pmtiles_verify: str | None = None
    if shutil.which(pmtiles_bin):
        completed = subprocess.run(
            [pmtiles_bin, "verify", str(pmtiles_path)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode == 0:
            pmtiles_verify = "passed"
        else:
            pmtiles_verify = "failed"
            errors.append(f"pmtiles verify failed: {completed.stderr.strip() or completed.stdout.strip()}")
    else:
        warnings.append(f"could not run pmtiles verify because {pmtiles_bin!r} is not on PATH")

    decoded = vector_asset.decoded_pmtiles_property_summary(
        pmtiles_path,
        zoom=decode_zoom,
        bounds=profile.bounds if profile else None,
    )
    decoded_feature_count = None
    decoded_property_keys: tuple[str, ...] = ()
    decoded_tile = None
    if decoded is None:
        message = "could not decode a PMTiles sample because tippecanoe-decode is unavailable or no sample tile decoded"
        warnings.append(message)
        if required_properties:
            errors.append("could not verify required PMTiles properties with tippecanoe-decode")
    else:
        decoded_feature_count, decoded_property_keys, decoded_tile_values = decoded
        decoded_tile = "/".join(str(value) for value in decoded_tile_values)
        missing = sorted(set(required_properties) - set(decoded_property_keys))
        if missing:
            errors.append(f"decoded PMTiles sample is missing required propert{'y' if len(missing) == 1 else 'ies'}: {', '.join(missing)}")
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "pmtiles_verify": pmtiles_verify,
        "decoded_feature_count": decoded_feature_count,
        "decoded_property_keys": list(decoded_property_keys),
        "decoded_tile": decoded_tile,
    }


def build_pmtiles(plan: LocalizedPmtilesPlan, *, overwrite: bool = False) -> dict[str, Any]:
    vector_asset.require_executable(plan.ogr2ogr_bin)
    vector_asset.require_executable(plan.tippecanoe_bin)
    output = Path(plan.output_path)
    if output.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite existing PMTiles output: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    Path(plan.profile_path).parent.mkdir(parents=True, exist_ok=True)
    if overwrite and output.exists():
        output.unlink()

    csv_profile, _rows = validate_localization_csv(Path(plan.localization_path))
    if csv_profile.errors:
        raise LocalizedVectorAssetError("; ".join(csv_profile.errors))
    fgb_profile = load_fgb_key_profile(Path(plan.fgb_path), ext_id_field=JOIN_KEY, ogr2ogr_bin=plan.ogr2ogr_bin)
    if fgb_profile.errors:
        raise LocalizedVectorAssetError("; ".join(fgb_profile.errors))
    missing = sorted(set(fgb_profile.ext_ids) - set(csv_profile.ext_ids))
    if missing:
        raise LocalizedVectorAssetError(f"localization CSV is missing {len(missing)} ext_id value(s) present in the FGB")
    orphaned = sorted(set(csv_profile.ext_ids) - set(fgb_profile.ext_ids))

    profile = profile_fgb(Path(plan.fgb_path), ogr2ogr_bin=plan.ogr2ogr_bin)
    if plan.maxzoom is None:
        recommendation = recommend_maxzoom(
            profile,
            source_resolution_meters=plan.source_resolution_meters,
            source_scale_denominator=plan.source_scale_denominator,
            pmtiles_maxzoom=plan.pmtiles_maxzoom,
            pmtiles_maxzoom_reason=plan.pmtiles_maxzoom_reason,
            pmtiles_detail_hint=plan.pmtiles_detail_hint,
        )
    else:
        recommendation = vector_asset.ZoomRecommendation(
            status="recommended",
            maxzoom=plan.maxzoom,
            confidence="high",
            reason="manual maxzoom override",
            evidence={"source": "manual_maxzoom", "maxzoom": plan.maxzoom},
        )
    if recommendation.maxzoom is None or recommendation.status != "recommended":
        raise LocalizedVectorAssetError(f"could not resolve PMTiles maxzoom: {recommendation.reason}")

    run_metadata_lookup_pipeline(
        plan,
        resolved_maxzoom=recommendation.maxzoom,
    )
    validation = validate_pmtiles_properties(
        pmtiles_path=output,
        required_properties=(vector_asset.FEATURE_ID_COLUMN, JOIN_KEY),
        pmtiles_bin=plan.pmtiles_bin,
        profile=profile,
        decode_zoom=plan.minzoom,
    )
    if orphaned:
        validation["warnings"].append(
            f"localization CSV has {len(orphaned)} ext_id value(s) not present in the FGB; ignored for this build"
        )
    payload = profile_payload(profile, recommendation)
    payload.update(
        {
            "asset_slug": plan.asset_slug,
            "localization_storage": LOCALIZATION_STORAGE,
            "localization_file": plan.localization_path,
            "localized_property_fields": list(plan.localized_property_fields),
            "validation": validation,
        }
    )
    Path(plan.profile_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    if not validation["valid"]:
        raise LocalizedVectorAssetError("; ".join(validation["errors"]))
    return {
        "pmtiles_path": plan.output_path,
        "profile_path": plan.profile_path,
        "recommendation": payload.get("recommendation"),
        "validation": validation,
        "localized_property_fields": list(plan.localized_property_fields),
    }


def load_catalog_row(catalog_path: Path, asset_slug: str) -> dict[str, str]:
    with catalog_path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("asset_slug") == asset_slug:
                return dict(row)
    raise LocalizedVectorAssetError(f"asset slug is not in the catalog: {asset_slug}")


def split_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise LocalizedVectorAssetError(f"expected gs:// URI, got {uri!r}")
    bucket, separator, name = uri[5:].partition("/")
    if not bucket or not separator or not name:
        raise LocalizedVectorAssetError(f"expected gs:// object URI, got {uri!r}")
    return bucket, name


def promotion(
    *,
    source_uri: str,
    destination_uri: str,
    source_generation: str,
    destination_generation: str = "",
    content_type: str = "",
    cache_control: str = "",
) -> dict[str, str]:
    return {
        "source_uri": source_uri,
        "source_generation": source_generation,
        "destination_uri": destination_uri,
        "destination_generation": destination_generation,
        "content_type": content_type,
        "cache_control": cache_control,
    }


def latest_destination_generation(value: str | None) -> str:
    return LATEST_DESTINATION_GENERATION_PLACEHOLDER if value is None else value


def draft_publish_plan(
    *,
    asset_slug: str,
    release_date: str,
    proposal_id: str,
    catalog_path: Path,
    bucket_name: str | None,
    translation_only: bool,
    source_generation: str,
    fgb_source_generation: str | None = None,
    localization_source_generation: str | None = None,
    pmtiles_source_generation: str | None = None,
    latest_fgb_destination_generation: str | None = None,
    latest_localization_destination_generation: str | None = None,
    latest_pmtiles_destination_generation: str | None = None,
) -> dict[str, Any]:
    row = load_catalog_row(catalog_path, asset_slug)
    bucket, canonical_name = split_gs_uri(row["canonical_path"])
    if bucket_name and bucket_name != bucket:
        raise LocalizedVectorAssetError(f"catalog bucket {bucket!r} does not match requested bucket {bucket_name!r}")
    if "/latest/" not in canonical_name:
        raise LocalizedVectorAssetError("catalog canonical_path must contain /latest/")
    asset_root = canonical_name.split("/latest/", 1)[0]
    base = f"gs://{bucket}/{asset_root}"
    scratch = f"gs://{bucket}/_scratch/pending-publishes/{asset_slug}/{proposal_id}"
    fgb_source = f"{scratch}/{asset_slug}.fgb"
    localization_source = f"{scratch}/{asset_slug}-localizations.csv"
    pmtiles_source = f"{scratch}/{asset_slug}.pmtiles"
    fgb_generation = fgb_source_generation or source_generation
    localization_generation = localization_source_generation or source_generation
    pmtiles_generation = pmtiles_source_generation or source_generation
    promotions: list[dict[str, str]] = []
    release_fgb = f"{base}/releases/{release_date}/{asset_slug}.fgb"
    if not translation_only:
        promotions.append(
            promotion(
                source_uri=fgb_source,
                source_generation=fgb_generation,
                destination_uri=f"{base}/latest/{asset_slug}.fgb",
                destination_generation=latest_destination_generation(latest_fgb_destination_generation),
                content_type="application/octet-stream",
            )
        )
    promotions.append(
        promotion(
            source_uri=fgb_source,
            source_generation=fgb_generation,
            destination_uri=release_fgb,
            content_type="application/octet-stream",
        )
    )
    promotions.append(
        promotion(
            source_uri=localization_source,
            source_generation=localization_generation,
            destination_uri=f"{base}/latest/{asset_slug}-localizations.csv",
            destination_generation=latest_destination_generation(latest_localization_destination_generation),
            content_type="text/csv",
        )
    )
    promotions.append(
        promotion(
            source_uri=localization_source,
            source_generation=localization_generation,
            destination_uri=f"{base}/releases/{release_date}/{asset_slug}-localizations.csv",
            content_type="text/csv",
        )
    )
    promotions.append(
        promotion(
            source_uri=pmtiles_source,
            source_generation=pmtiles_generation,
            destination_uri=f"{base}/latest/{asset_slug}.pmtiles",
            destination_generation=latest_destination_generation(latest_pmtiles_destination_generation),
            content_type="application/vnd.pmtiles",
            cache_control=NO_CACHE_CONTROL,
        )
    )
    promotions.append(
        promotion(
            source_uri=pmtiles_source,
            source_generation=pmtiles_generation,
            destination_uri=f"{base}/releases/{release_date}/{asset_slug}.pmtiles",
            content_type="application/vnd.pmtiles",
            cache_control=NO_CACHE_CONTROL,
        )
    )
    return {"asset_slug": asset_slug, "proposal_id": proposal_id, "promotions": promotions}


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_validate_localizations(args: argparse.Namespace) -> int:
    result = validate_localizations(
        fgb=args.fgb,
        localizations=args.localizations,
        asset_doc=args.asset_doc,
        source_layer=args.source_layer,
        ogr2ogr_bin=args.ogr2ogr_bin,
    )
    print_json(result)
    return 0 if result["valid"] else 2


def cmd_seed_localizations(args: argparse.Namespace) -> int:
    result = seed_localizations(
        fgb=args.fgb,
        localizations=args.localizations,
        ext_id_field=args.ext_id_field,
        fallback_name_field=args.fallback_name_field,
        source_layer=args.source_layer,
        ogr2ogr_bin=args.ogr2ogr_bin,
        dry_run=args.dry_run,
    )
    print_json(result)
    return 0


def cmd_build_pmtiles(args: argparse.Namespace) -> int:
    plan = build_pmtiles_plan(
        fgb=args.fgb,
        localizations=args.localizations,
        asset_slug=args.asset_slug,
        output=args.output,
        work_dir=args.work_dir,
        layer_name=args.layer_name,
        minzoom=args.minzoom,
        maxzoom=args.maxzoom,
        maxzoom_reason=args.maxzoom_reason,
        source_resolution_meters=args.source_resolution_meters,
        source_scale_denominator=args.source_scale_denominator,
        pmtiles_maxzoom=args.pmtiles_maxzoom,
        pmtiles_maxzoom_reason=args.pmtiles_maxzoom_reason,
        pmtiles_detail_hint=args.pmtiles_detail_hint,
        tippecanoe_extra_args=args.tippecanoe_arg,
        ogr2ogr_bin=args.ogr2ogr_bin,
        tippecanoe_bin=args.tippecanoe_bin,
        pmtiles_bin=args.pmtiles_bin,
        allow_repo_output=args.allow_repo_output,
        allow_low_maxzoom=args.allow_low_maxzoom,
        allow_high_maxzoom=args.allow_high_maxzoom,
        allow_point_dropping=args.allow_point_dropping,
    )
    if args.dry_run:
        print_json(asdict(plan))
        return 0
    result = build_pmtiles(plan, overwrite=args.overwrite)
    print_json(result)
    return 0


def cmd_draft_publish_plan(args: argparse.Namespace) -> int:
    result = draft_publish_plan(
        asset_slug=args.asset_slug,
        release_date=args.release_date,
        proposal_id=args.proposal_id,
        catalog_path=args.catalog,
        bucket_name=args.bucket,
        translation_only=args.translation_only,
        source_generation=args.source_generation,
        fgb_source_generation=args.fgb_source_generation,
        localization_source_generation=args.localization_source_generation,
        pmtiles_source_generation=args.pmtiles_source_generation,
        latest_fgb_destination_generation=args.latest_fgb_destination_generation,
        latest_localization_destination_generation=args.latest_localization_destination_generation,
        latest_pmtiles_destination_generation=args.latest_pmtiles_destination_generation,
    )
    print_json(result)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate-localizations", help="Validate localization CSV, asset doc metadata, and FGB ext_id coverage.")
    validate_parser.add_argument("--fgb", type=Path, required=True)
    validate_parser.add_argument("--localizations", type=Path, required=True)
    validate_parser.add_argument("--asset-doc", type=Path, required=True)
    validate_parser.add_argument("--source-layer")
    validate_parser.add_argument("--ogr2ogr-bin", default="ogr2ogr")
    validate_parser.set_defaults(func=cmd_validate_localizations)

    seed_parser = subparsers.add_parser("seed-localizations", help="Append missing ext_id rows to a localization CSV.")
    seed_parser.add_argument("--fgb", type=Path, required=True)
    seed_parser.add_argument("--ext-id-field", required=True)
    seed_parser.add_argument("--fallback-name-field", required=True)
    seed_parser.add_argument("--localizations", type=Path, required=True)
    seed_parser.add_argument("--source-layer")
    seed_parser.add_argument("--ogr2ogr-bin", default="ogr2ogr")
    seed_parser.add_argument("--dry-run", action="store_true")
    seed_parser.set_defaults(func=cmd_seed_localizations)

    build_parser_ = subparsers.add_parser(
        "build-pmtiles",
        help="Build metadata-lookup PMTiles from a canonical FGB while validating localization CSV coverage.",
    )
    build_parser_.add_argument("--fgb", type=Path, required=True)
    build_parser_.add_argument("--localizations", type=Path, required=True)
    build_parser_.add_argument("--asset-slug", required=True)
    build_parser_.add_argument("--output", type=Path, required=True)
    build_parser_.add_argument("--work-dir", type=Path)
    build_parser_.add_argument("--layer-name")
    build_parser_.add_argument("--minzoom", type=int, default=0)
    build_parser_.add_argument("--maxzoom", default=vector_asset.AUTO_MAXZOOM)
    build_parser_.add_argument("--maxzoom-reason")
    build_parser_.add_argument("--source-resolution-meters", type=float)
    build_parser_.add_argument("--source-scale-denominator", type=float)
    build_parser_.add_argument("--pmtiles-maxzoom", type=int)
    build_parser_.add_argument("--pmtiles-maxzoom-reason")
    build_parser_.add_argument("--pmtiles-detail-hint", choices=["coarse", "medium", "detailed"])
    build_parser_.add_argument("--tippecanoe-arg", action="append", default=[])
    build_parser_.add_argument("--ogr2ogr-bin", default="ogr2ogr")
    build_parser_.add_argument("--tippecanoe-bin", default="tippecanoe")
    build_parser_.add_argument("--pmtiles-bin", default="pmtiles")
    build_parser_.add_argument("--overwrite", action="store_true")
    build_parser_.add_argument("--allow-repo-output", action="store_true")
    build_parser_.add_argument("--allow-low-maxzoom", action="store_true")
    build_parser_.add_argument("--allow-high-maxzoom", action="store_true")
    build_parser_.add_argument("--allow-point-dropping", action="store_true")
    build_parser_.add_argument("--dry-run", action="store_true")
    build_parser_.set_defaults(func=cmd_build_pmtiles)

    draft_parser = subparsers.add_parser("draft-publish-plan", help="Draft reviewed promotion JSON for localized vector assets.")
    draft_parser.add_argument("--asset-slug", required=True)
    draft_parser.add_argument("--release-date", required=True)
    draft_parser.add_argument("--proposal-id", required=True)
    draft_parser.add_argument("--catalog", type=Path, default=Path("catalog/shared-datasets-catalog.csv"))
    draft_parser.add_argument("--bucket")
    draft_parser.add_argument("--translation-only", action="store_true")
    draft_parser.add_argument(
        "--source-generation",
        default="<fill-after-staging>",
        help="Scratch source generation to place in every drafted promotion. Replace the default before PR use.",
    )
    draft_parser.add_argument("--fgb-source-generation", help="Scratch source generation for the staged FGB.")
    draft_parser.add_argument("--localization-source-generation", help="Scratch source generation for the staged localization CSV.")
    draft_parser.add_argument("--pmtiles-source-generation", help="Scratch source generation for the staged PMTiles.")
    draft_parser.add_argument("--latest-fgb-destination-generation", help="Current generation for latest/{asset-slug}.fgb, or empty when creating a new object.")
    draft_parser.add_argument(
        "--latest-localization-destination-generation",
        help="Current generation for latest/{asset-slug}-localizations.csv, or empty when creating a new object.",
    )
    draft_parser.add_argument("--latest-pmtiles-destination-generation", help="Current generation for latest/{asset-slug}.pmtiles, or empty when creating a new object.")
    draft_parser.set_defaults(func=cmd_draft_publish_plan)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (LocalizedVectorAssetError, OSError, ValueError, json.JSONDecodeError, subprocess.SubprocessError, yaml.YAMLError) as exc:
        print(f"localized-vector-asset failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
