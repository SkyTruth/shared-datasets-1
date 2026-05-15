#!/usr/bin/env python3
"""Plan a manual shared-datasets publish without writing remote objects."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

import yaml


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
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
ID_FIELD_RE = re.compile(r"(^id$|_id$|^id_|uuid|guid|external.?id|ext.?id|source.?id|objectid|mrgid|wdpaid)", re.IGNORECASE)
GROUP_FIELD_RE = re.compile(r"(^name$|name$|title|label|site|region|zone|area_name|place|locality|unit)", re.IGNORECASE)
MEASUREMENT_FIELD_RE = re.compile(r"(area|length|shape|perimeter|lat|lon|longitude|latitude|date|time|rank|zoom|count)$", re.IGNORECASE)


class ConciergeError(ValueError):
    """Raised when a publish plan cannot be built."""


@dataclass(frozen=True)
class FieldRecommendation:
    field: str
    reason: str
    distinct_values: int | None = None
    non_empty_values: int | None = None
    duplicate_value_count: int | None = None
    duplicate_row_count: int | None = None
    confidence: str = "medium"


@dataclass(frozen=True)
class CuratorFieldOptions:
    id_field_candidates: list[FieldRecommendation]
    group_field_candidates: list[FieldRecommendation]
    notes: list[str]


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
        aliases = {"flatgeobuf": "fgb", "geotiff": "cog", "tif": "cog", "tiff": "cog"}
        normalized = aliases.get(normalized, normalized)
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


def duplicate_counts(values: list[str]) -> tuple[int, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    repeated = [count for count in counts.values() if count > 1]
    return len(repeated), sum(repeated)


def profile_rows(rows: Sequence[dict[str, Any]]) -> CuratorFieldOptions:
    if not rows:
        return CuratorFieldOptions([], [], ["No rows were available for field profiling."])
    fields = list(rows[0].keys())
    row_count = len(rows)
    id_candidates: list[FieldRecommendation] = []
    group_candidates: list[FieldRecommendation] = []
    for field in fields:
        values = [
            " ".join(str(row.get(field) or "").strip().split())
            for row in rows
            if str(row.get(field) or "").strip()
        ]
        distinct = len(set(values))
        duplicate_value_count, duplicate_row_count = duplicate_counts(values)
        non_empty = len(values)
        id_like = field_is_id_like(field)
        group_like = field_is_group_like(field)
        if id_like and distinct and distinct == non_empty and non_empty >= max(1, row_count * 0.8):
            id_candidates.append(
                FieldRecommendation(
                    field=field,
                    reason="ID-like field name with unique non-empty values.",
                    distinct_values=distinct,
                    non_empty_values=non_empty,
                    duplicate_value_count=duplicate_value_count,
                    duplicate_row_count=duplicate_row_count,
                    confidence="high",
                )
            )
        elif id_like and distinct:
            id_candidates.append(
                FieldRecommendation(
                    field=field,
                    reason="ID-like field name, but values are not unique enough for a provider row ID.",
                    distinct_values=distinct,
                    non_empty_values=non_empty,
                    duplicate_value_count=duplicate_value_count,
                    duplicate_row_count=duplicate_row_count,
                    confidence="low",
                )
            )
        if group_like and distinct > 1:
            group_candidates.append(
                FieldRecommendation(
                    field=field,
                    reason="Human-readable grouping/search field.",
                    distinct_values=distinct,
                    non_empty_values=non_empty,
                    duplicate_value_count=duplicate_value_count,
                    duplicate_row_count=duplicate_row_count,
                    confidence="high" if distinct < non_empty else "medium",
                )
            )
        elif not id_like and not field_is_measurement(field) and 1 < distinct < non_empty:
            group_candidates.append(
                FieldRecommendation(
                    field=field,
                    reason="Non-measurement field with repeated values that may be useful for grouping/filtering.",
                    distinct_values=distinct,
                    non_empty_values=non_empty,
                    duplicate_value_count=duplicate_value_count,
                    duplicate_row_count=duplicate_row_count,
                    confidence="medium",
                )
            )

    id_candidates.sort(key=lambda candidate: (candidate.confidence != "high", -(candidate.distinct_values or 0), candidate.field.lower()))
    group_candidates.sort(
        key=lambda candidate: (
            candidate.confidence != "high",
            candidate.field.lower() != "name",
            -(candidate.distinct_values or 0),
            candidate.field.lower(),
        )
    )
    notes = [f"Profiled {row_count} row(s) exactly from source attributes."]
    if not id_candidates:
        notes.append("No high-likelihood provider row ID field was found.")
    if not group_candidates:
        notes.append("No high-likelihood grouping/search field was found.")
    return CuratorFieldOptions(id_candidates[:8], group_candidates[:8], notes)


def read_csv_rows(source: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    with source.open(newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for row in reader:
            rows.append(dict(row))
            if limit is not None and len(rows) >= limit:
                break
        return rows


def read_geojson_rows(source: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
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
    return CuratorFieldOptions(id_candidates[:8], group_candidates[:8], notes)


def recommend_curator_field_options(source: Path, canonical_format: str) -> CuratorFieldOptions:
    try:
        if source.suffix.lower() == ".csv":
            return profile_rows(read_csv_rows(source))
        if source.suffix.lower() in {".geojson", ".json", ".ndgeojson"}:
            return profile_rows(read_geojson_rows(source))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, csv.Error) as exc:
        return CuratorFieldOptions([], [], [f"Could not profile source attributes: {exc}"])

    if canonical_format == "fgb":
        if os.environ.get("SHARED_DATASETS_PROFILE_WITH_GDAL") == "1":
            field_names = ogrinfo_field_names(source)
            if field_names:
                return recommend_schema_fields(field_names)
        return CuratorFieldOptions(
            [],
            [],
            [
                "Vector source schema was not profiled with GDAL during planning; set SHARED_DATASETS_PROFILE_WITH_GDAL=1 "
                "or profile the canonical artifact after conversion.",
            ],
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
    with_pmtiles: bool,
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
        if with_pmtiles:
            notes.append("--with-pmtiles is retained for compatibility; PMTiles is automatic for FGB assets.")
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
    elif with_pmtiles:
        notes.append("--with-pmtiles has no effect outside vector FGB assets.")

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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
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
    parser.add_argument(
        "--with-pmtiles",
        action="store_true",
        help="Compatibility flag; PMTiles companions are planned automatically for vector FGB assets.",
    )
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
    parser.add_argument("--write-draft-doc", action="store_true", help="Write docs/assets/{asset_slug}.md locally.")
    parser.add_argument("--overwrite-doc", action="store_true", help="Allow replacing an existing draft asset doc.")
    parser.add_argument("--run-catalog-check", action="store_true", help="Run catalog_docs.py check after planning.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        plan = build_plan(
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
            with_pmtiles=args.with_pmtiles,
            source_resolution_meters=args.source_resolution_meters,
            source_scale_denominator=args.source_scale_denominator,
            pmtiles_maxzoom=args.pmtiles_maxzoom,
            pmtiles_maxzoom_reason=args.pmtiles_maxzoom_reason,
            pmtiles_detail_hint=args.pmtiles_detail_hint,
            categories_path=args.categories,
            docs_dir=args.docs_dir,
        )
        if args.write_draft_doc:
            write_draft_doc(
                Path(plan.asset_doc_path),
                draft_asset_doc(
                    plan,
                    owner=args.owner,
                    source_name=args.source_name,
                    license_text=args.license_text,
                    citation=args.citation,
                    update_cadence=args.update_cadence,
                    access_tier=args.access_tier,
                ),
                overwrite=args.overwrite_doc,
            )
        payload = asdict(plan)
        if args.write_draft_doc:
            payload["draft_doc_written"] = plan.asset_doc_path
        print(json.dumps(payload, indent=2, sort_keys=True))
        if args.run_catalog_check:
            return run_catalog_check()
        return 0
    except ConciergeError as exc:
        print(f"publishing-concierge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
