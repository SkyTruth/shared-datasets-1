#!/usr/bin/env python3
"""Plan a manual shared-datasets publish without writing remote objects."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field as dataclass_field
from pathlib import Path
from typing import Any, Callable, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import catalog_csv
from scripts.admission_check import parse_footprint_gb
from scripts.concierge_profiling import (
    CuratorFieldOptions,
    recommend_curator_field_options,
)

DEFAULT_BUCKET = "skytruth-shared-datasets-1"
PREVIEW_BUCKET = "skytruth-shared-datasets-1-preview"
WORKFLOW_SCHEMA_VERSION = 1
WORKFLOW_COMMANDS = {
    "start",
    "next",
    "confirm",
    "status",
    "render-pr",
    "render-report",
    "validate",
    "refresh-retry-plan",
}
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROPOSAL_RE = re.compile(r"^[A-Za-z0-9._-]+$")
PREVIEW_REF_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
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
STANDARD_WORK_ROOT_SHELL = "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
SUPPORTED_CANONICAL_FORMATS = {"fgb", "cog", "zarr", "pmtiles", "geojson", "ndgeojson", "csv"}
LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:_[a-z0-9]{2,8})*$")


class ConciergeError(ValueError):
    """Raised when a publish plan cannot be built."""


class WorkflowError(ValueError):
    """Raised when a stateful publish workflow cannot advance."""



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
    if access_tier not in {"public", "private", "internal"}:
        raise ConciergeError("access tier must be public, private, or internal")
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
    work_dir = f"{STANDARD_WORK_ROOT_SHELL}/vector-assets/{slug}"
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
        "Curator must choose source field ID candidates or approve generated monotonic feature IDs before publishing.",
    ]
    if resolved_format == "csv":
        notes.append("CSV must remain geometry-free under shared-datasets standards.")
    if include_pmtiles:
        notes.append("FGB vector assets require a PMTiles companion for catalog map preview.")
        notes.append("PMTiles feature properties must contain feature_id only for metadata lookup.")
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
    if release_metadata_contract_required({"plan": asdict(plan)}):
        metadata_paths = expected_latest_feature_metadata_paths(plan.asset_slug)
        files.extend(
            [
                {
                    "path": metadata_paths["sidecar_file"],
                    "format": "ndjson_gzip",
                    "role": "metadata",
                    "purpose": "Canonical feature metadata sidecar keyed by feature_id",
                },
                {
                    "path": metadata_paths["schema_file"],
                    "format": "json",
                    "role": "metadata",
                    "purpose": "Feature metadata schema",
                },
                {
                    "path": metadata_paths["manifest_file"],
                    "format": "json",
                    "role": "metadata",
                    "purpose": "Feature metadata release manifest",
                },
            ]
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
    if release_metadata_contract_required({"plan": asdict(plan)}):
        metadata["feature_metadata"] = {
            "storage": "metadata_sidecar_v1",
            "index_backend": "firestore",
            "feature_id_column": "feature_id",
            "geometry_hash_column": "geometry_hash",
            "properties_hash_column": "properties_hash",
            **expected_latest_feature_metadata_paths(plan.asset_slug),
            "provenance_default": True,
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


def preview_firestore_load_enabled(state: dict[str, Any]) -> bool:
    plan = plan_from_state(state)
    for value in (
        state.get("preview_firestore_load"),
        state.get("preview_firestore_load_required"),
        plan.get("preview_firestore_load"),
        plan.get("preview_firestore_load_required"),
    ):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "enabled", "required"}:
            return True
    return False


def preview_load_required(state: dict[str, Any]) -> bool:
    plan = plan_from_state(state)
    return (
        is_preview_workflow(state)
        and preview_firestore_load_enabled(state)
        and plan.get("canonical_format") == "fgb"
        and bool(plan.get("release_date"))
    )


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


def release_metadata_contract_required(state: dict[str, Any]) -> bool:
    plan = plan_from_state(state)
    return plan.get("canonical_format") == "fgb" and bool(plan.get("release_date"))


def expected_latest_feature_metadata_paths(asset_slug: str) -> dict[str, str]:
    return {
        "sidecar_file": f"latest/{asset_slug}.metadata.ndjson.gz",
        "schema_file": f"latest/{asset_slug}.schema.json",
        "manifest_file": f"latest/{asset_slug}.manifest.json",
    }


def expected_latest_feature_metadata_uris(state: dict[str, Any]) -> dict[str, str]:
    plan = plan_from_state(state)
    bucket = state.get("bucket") or DEFAULT_BUCKET
    prefix = f"gs://{bucket}/{plan['asset_root']}/"
    return {
        key: f"{prefix}{path}"
        for key, path in expected_latest_feature_metadata_paths(str(plan["asset_slug"])).items()
    }


def validate_feature_metadata_frontmatter(path: Path, metadata: dict[str, Any], state: dict[str, Any]) -> None:
    if not release_metadata_contract_required(state):
        return
    plan = plan_from_state(state)
    feature_metadata = metadata.get("feature_metadata")
    if not isinstance(feature_metadata, dict):
        raise WorkflowError(f"{path}: release-oriented FGB assets must define feature_metadata")
    if feature_metadata.get("storage") != "metadata_sidecar_v1":
        raise WorkflowError(f"{path}: feature_metadata.storage must be 'metadata_sidecar_v1'")
    for key in ("feature_id_column", "geometry_hash_column", "properties_hash_column"):
        if not str(feature_metadata.get(key, "")).strip():
            raise WorkflowError(f"{path}: feature_metadata.{key} must be populated")
    expected_paths = expected_latest_feature_metadata_paths(str(plan["asset_slug"]))
    for key, expected in expected_paths.items():
        if feature_metadata.get(key) != expected:
            raise WorkflowError(f"{path}: feature_metadata.{key} must be {expected!r}")

    files = metadata.get("files")
    if not isinstance(files, list):
        raise WorkflowError(f"{path}: frontmatter files must list feature metadata artifacts")
    by_path: dict[str, list[dict[str, Any]]] = {}
    for file_obj in files:
        if isinstance(file_obj, dict) and isinstance(file_obj.get("path"), str):
            by_path.setdefault(file_obj["path"], []).append(file_obj)
    expected_formats = {
        expected_paths["sidecar_file"]: "ndjson_gzip",
        expected_paths["schema_file"]: "json",
        expected_paths["manifest_file"]: "json",
    }
    for file_path, expected_format in expected_formats.items():
        matches = by_path.get(file_path, [])
        if len(matches) != 1:
            raise WorkflowError(f"{path}: files must include {file_path!r} exactly once")
        file_obj = matches[0]
        if file_obj.get("format") != expected_format:
            raise WorkflowError(f"{path}: files entry {file_path!r} format must be {expected_format!r}")
        if not str(file_obj.get("role", "")).strip():
            raise WorkflowError(f"{path}: files entry {file_path!r} role must be populated")


def catalog_asset_for_slug(catalog_payload: dict[str, Any], asset_slug: str) -> dict[str, Any]:
    assets = catalog_payload.get("assets")
    if not isinstance(assets, list):
        raise WorkflowError("catalog.json must contain an assets list")
    for asset in assets:
        if isinstance(asset, dict) and asset.get("slug") == asset_slug:
            return asset
    raise WorkflowError(f"catalog.json is missing asset slug {asset_slug!r}")


def runtime_catalog_file_paths(asset: dict[str, Any], release_date: str | None) -> set[str]:
    file_sets: list[Any] = []
    file_sets.append(asset.get("files"))
    latest_release = asset.get("latest_release")
    if isinstance(latest_release, dict):
        file_sets.append(latest_release.get("files"))
    for version in asset.get("versions") if isinstance(asset.get("versions"), list) else []:
        if not isinstance(version, dict):
            continue
        if release_date and version.get("date") != release_date:
            continue
        file_sets.append(version.get("files"))
    paths: set[str] = set()
    for files in file_sets:
        if not isinstance(files, list):
            continue
        for file_obj in files:
            if isinstance(file_obj, dict) and isinstance(file_obj.get("path"), str):
                paths.add(file_obj["path"])
    return paths


def validate_catalog_runtime_feature_metadata(catalog_json: Path, state: dict[str, Any]) -> dict[str, Any]:
    if not release_metadata_contract_required(state):
        return {}
    plan = plan_from_state(state)
    try:
        payload = json.loads(catalog_json.read_text())
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"{catalog_json}: catalog.json is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise WorkflowError(f"{catalog_json}: catalog.json must contain a JSON object")
    asset = catalog_asset_for_slug(payload, str(plan["asset_slug"]))
    feature_metadata = asset.get("feature_metadata")
    if not isinstance(feature_metadata, dict):
        raise WorkflowError(f"{catalog_json}: catalog asset is missing feature_metadata")
    if feature_metadata.get("storage") != "metadata_sidecar_v1":
        raise WorkflowError(f"{catalog_json}: catalog asset feature_metadata.storage must be metadata_sidecar_v1")
    colorizer_metadata = asset.get("colorizer_metadata")
    if not isinstance(colorizer_metadata, dict):
        raise WorkflowError(f"{catalog_json}: catalog asset is missing colorizer_metadata")
    if colorizer_metadata.get("source") != "metadata_sidecar_schema":
        raise WorkflowError(f"{catalog_json}: catalog asset colorizer_metadata.source must be metadata_sidecar_schema")
    if colorizer_metadata.get("field_source") != "feature_metadata.schema_file":
        raise WorkflowError(f"{catalog_json}: catalog asset colorizer_metadata.field_source must be feature_metadata.schema_file")
    if colorizer_metadata.get("feature_id_property") != "feature_id":
        raise WorkflowError(f"{catalog_json}: catalog asset colorizer_metadata.feature_id_property must be feature_id")
    runtime_paths = runtime_catalog_file_paths(asset, str(plan.get("release_date") or ""))
    expected_uris = expected_latest_feature_metadata_uris(state)
    missing = sorted(uri for uri in expected_uris.values() if uri not in runtime_paths)
    if missing:
        raise WorkflowError("catalog.json asset is missing runtime feature metadata file URI(s): " + ", ".join(missing))
    return {
        "feature_metadata_runtime_files_verified": True,
        "feature_metadata_runtime_files": sorted(expected_uris.values()),
    }


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
                "The production catalog-web-deploy.yml automation runs from the reviewed "
                "post-merge mutation/localization chain, but preview-only bucket uploads "
                "do not trigger it."
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


def detect_existing_asset(plan: ConciergePlan, *, catalog_path: Path = Path("catalog/shared-datasets-catalog.csv")) -> list[str]:
    matches = []
    doc_path = Path(plan.asset_doc_path)
    if doc_path.exists():
        matches.append(str(doc_path))
    if catalog_path.exists():
        try:
            if catalog_csv.catalog_row(plan.asset_slug, catalog_path) is not None:
                matches.append(str(catalog_path))
        except (csv.Error, catalog_csv.CatalogCsvError) as exc:
            raise WorkflowError(f"could not inspect catalog for duplicate asset slug: {exc}") from exc
    return matches


def render_catalog_web_command() -> str:
    return (
        f'WORK_ROOT="{STANDARD_WORK_ROOT_SHELL}"\n'
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
        "Choose whether feature_id comes from a URL-safe source field or a generated numeric sequence.",
        "If planning-time profile was unavailable, profile the canonical artifact after conversion before confirming.",
    ]


def commands_for_translation_decision(_state: dict[str, Any]) -> list[str]:
    return [
        "Review `plan.curator_field_options.translation_field_candidates` in `status --json` output before asking for final translation choices.",
        "Ask the maintainer which locales and metadata fields should be autogenerated, or record an explicit no-translation decision.",
        "When autogeneration is selected, the build step will include generic feature metadata translation commands for the recorded fields and locales.",
    ]


def autogenerated_translation_fields(state: dict[str, Any]) -> list[str]:
    evidence = step_record(state, "translation-decision").get("evidence", {})
    if evidence.get("decision") != "autogenerate":
        return []
    return [str(field) for field in evidence.get("fields") or []]


def shell_option_flags(option: str, values: Sequence[str]) -> str:
    return " ".join(f"{option} {shlex.quote(str(value))}" for value in values)


def commands_for_translation_artifacts(state: dict[str, Any]) -> list[str]:
    locales = autogenerated_locales(state)
    fields = autogenerated_translation_fields(state)
    if not locales or not fields:
        return []
    plan = plan_from_state(state)
    asset_slug = str(plan["asset_slug"])
    release_date = str(plan.get("release_date") or "YYYY-MM-DD")
    publish_dir = str(plan.get("publish_dir") or f"{STANDARD_WORK_ROOT_SHELL}/vector-assets/{asset_slug}/publish")
    locale_flags = shell_option_flags("--locale", locales)
    field_flags = shell_option_flags("--field", fields)
    canonical_sidecar = f"{publish_dir}/{asset_slug}.metadata.ndjson.gz"
    translation_source = f"{publish_dir}/{asset_slug}.metadata-translations.csv"
    schema = f"{publish_dir}/{asset_slug}.schema.json"
    return [
        "Generate machine translation rows for the exact locales and fields recorded in translation-decision evidence.",
        (
            "UV_CACHE_DIR=.uv-cache uv run --with deep-translator --with tqdm "
            "python scripts/feature_metadata_machine_translate.py "
            f"--canonical-sidecar {canonical_sidecar} "
            f"--translation-source {translation_source} "
            f"--schema {schema} "
            f"{locale_flags} {field_flags} "
            f"--asset-slug {asset_slug} --release {release_date} "
            f"--report {publish_dir}/{asset_slug}.metadata-translations.report.json "
            "--progress"
        ),
        (
            "UV_CACHE_DIR=.uv-cache uv run python scripts/feature_metadata_localization.py "
            f"--canonical-sidecar {canonical_sidecar} "
            f"--translation-source {translation_source} "
            f"--schema {schema} "
            "--all-locales "
            f"--output-dir {publish_dir} "
            f"--report-dir {publish_dir}/localization-reports "
            f"--asset-slug {asset_slug} --release {release_date} "
            f"--report {publish_dir}/{asset_slug}.metadata-localization-report.json"
        ),
    ]


def commands_for_build_artifacts(state: dict[str, Any]) -> list[str]:
    commands = list(plan_from_state(state).get("suggested_commands") or [])
    commands.extend(commands_for_translation_artifacts(state))
    return commands


def autogenerated_locales(state: dict[str, Any]) -> list[str]:
    evidence = step_record(state, "translation-decision").get("evidence", {})
    if evidence.get("decision") != "autogenerate":
        return []
    return [str(locale) for locale in evidence.get("locales") or []]



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
            "Set object metadata at upload time; the evidence validator enforces it per role: "
            "pmtiles requires application/vnd.pmtiles, feature-metadata-sidecar and localized sidecars require "
            "application/x-ndjson, metadata-translations requires text/csv, and schema/manifest/release-index/run-record "
            f"require application/json; every one of those roles also requires cache_control {no_cache_control()!r}. "
            "Only the canonical artifact has no fixed metadata requirement."
        ),
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
            "via `.github/workflows/catalog-web-deploy.yml` after the reviewed post-merge "
            "mutation/localization chain, but preview-only GCS uploads do not trigger that workflow."
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
    commands = [
        f"Create or update {plan['asset_doc_path']} with source/license/citation, admission evidence, files, schema, row count, and data profile.",
        "UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate",
    ]
    if release_metadata_contract_required(state):
        commands.insert(
            1,
            "Verify feature_metadata sidecar_file/schema_file/manifest_file and matching files[] entries are present before confirming.",
        )
    return commands


def commands_for_catalog_outputs(_state: dict[str, Any]) -> list[str]:
    return [
        "UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate",
        "UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py check",
        f'WORK_ROOT="{STANDARD_WORK_ROOT_SHELL}"\n'
        'UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py export-readmes --output-dir "$WORK_ROOT/readmes"',
    ]


def commands_for_catalog_web(_state: dict[str, Any]) -> list[str]:
    return [
        render_catalog_web_command(),
        "For release-oriented vector assets, inspect catalog.json and confirm the asset exposes the latest metadata sidecar, schema, and manifest file URIs.",
    ]


def commands_for_stage_scratch(state: dict[str, Any]) -> list[str]:
    plan = plan_from_state(state)
    prefix = pending_publish_prefix(state)
    commands = [
        f"Stage every publish candidate under {prefix} with no-clobber uploads.",
        "Use `UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload LOCAL_PATH SCRATCH_URI --content-type TYPE --cache-control CACHE_CONTROL` where metadata is required.",
        (
            "Set object metadata at upload time; the evidence validator enforces it per destination: "
            "`.pmtiles` destinations require content_type application/vnd.pmtiles and cache_control "
            f"{no_cache_control()!r}; the `_catalog/web/catalog.json` destination requires content_type "
            f"application/json and cache_control {no_cache_control()!r}."
        ),
        "Record each staged source URI and generation in evidence JSON.",
    ]
    if release_metadata_contract_required(state):
        commands.append(
            f"The staged destinations must include latest/{plan['asset_slug']}.metadata.ndjson.gz, "
            f"latest/{plan['asset_slug']}.schema.json, and latest/{plan['asset_slug']}.manifest.json."
        )
    return commands


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


# Temporary bridge for workflow states and evidence recorded before the
# resolve-metadata contract adopted the canonical admission field names used
# by templates/ and catalog_docs.py. Remove once no pre-rename states remain.
LEGACY_RESOLVE_METADATA_KEYS = {
    "shared_datasets_rationale": "shared_rationale",
    "deprecation_exit_policy": "deprecation_policy",
    "estimated_published_footprint": "estimated_published_size_gb",
}


def canonical_resolve_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {LEGACY_RESOLVE_METADATA_KEYS.get(key, key): value for key, value in record.items()}


def validate_resolve_metadata(_state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    evidence = canonical_resolve_metadata(evidence)
    normalized = {
        "source_name": require_non_empty_string(evidence, "source_name"),
        "license": require_non_empty_string(evidence, "license"),
        "citation": require_non_empty_string(evidence, "citation"),
        "steward": require_non_empty_string(evidence, "steward"),
        "source_version_date": require_non_empty_string(evidence, "source_version_date"),
        "update_cadence": require_non_empty_string(evidence, "update_cadence"),
        "intended_consumers": require_string_list(evidence, "intended_consumers"),
        "shared_rationale": require_non_empty_string(evidence, "shared_rationale"),
        "alternatives_considered": require_non_empty_string(evidence, "alternatives_considered"),
        "deprecation_policy": require_non_empty_string(evidence, "deprecation_policy"),
        "estimated_published_size_gb": require_non_empty_string(evidence, "estimated_published_size_gb"),
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
    if access_tier not in {"public", "private", "internal"}:
        raise WorkflowError("evidence.access_tier must be public, private, or internal")
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
    metadata = canonical_resolve_metadata(step_record(state, "resolve-metadata").get("evidence", {}))
    footprint_gb = parse_footprint_gb(str(metadata.get("estimated_published_size_gb", "")))
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
    source = require_non_empty_string(evidence, "source_field_id_decision")
    if source not in {"use-source-field", "none-suitable"}:
        raise WorkflowError("evidence.source_field_id_decision must be use-source-field or none-suitable")
    generated = require_non_empty_string(evidence, "generated_feature_id_decision")
    if generated not in {"not-needed", "approved"}:
        raise WorkflowError("evidence.generated_feature_id_decision must be not-needed or approved")
    feature_id_decision = require_non_empty_string(evidence, "feature_id_decision")
    if feature_id_decision not in {"source-field", "generated-sequence"}:
        raise WorkflowError("evidence.feature_id_decision must be source-field or generated-sequence")
    assignment_key_field_name = "assignment_" + "key_fields"
    normalized = {
        "decision_table_present": True,
        "profile_scope": profile_scope,
        "source_field_id_decision": source,
        "source_field_id_fields": require_string_list(evidence, "source_field_id_fields", allow_empty=source != "use-source-field"),
        "generated_feature_id_decision": generated,
        assignment_key_field_name: require_string_list(
            evidence,
            assignment_key_field_name,
            allow_empty=generated != "approved",
        ),
        "feature_id_decision": feature_id_decision,
        "feature_id_fields": require_string_list(evidence, "feature_id_fields", allow_empty=feature_id_decision == "generated-sequence"),
        "search_fields": require_string_list(evidence, "search_fields", allow_empty=True),
        "notes": str(evidence.get("notes", "")).strip(),
    }
    if source == "use-source-field" and generated == "approved":
        raise WorkflowError("do not approve generated IDs when a source field ID is selected")
    if feature_id_decision == "source-field" and source != "use-source-field":
        raise WorkflowError("evidence.feature_id_decision=source-field requires source_field_id_decision=use-source-field")
    if feature_id_decision == "generated-sequence" and generated != "approved":
        raise WorkflowError("evidence.feature_id_decision=generated-sequence requires generated_feature_id_decision=approved")
    if feature_id_decision == "generated-sequence" and normalized["feature_id_fields"]:
        raise WorkflowError("evidence.feature_id_fields must be empty when feature_id_decision=generated-sequence")
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
    locales = autogenerated_locales(state)
    if locales:
        required.add("metadata_translations_csv_v1")
        required.update(f"localized_metadata_sidecar_v1:{locale}" for locale in locales)
    return required


def localized_sidecar_locale(asset_slug: str, path: Path) -> str | None:
    prefix = f"{asset_slug}.metadata."
    suffix = ".ndjson.gz"
    if not path.name.startswith(prefix) or not path.name.endswith(suffix):
        return None
    locale = path.name[len(prefix) : -len(suffix)]
    if not LOCALE_RE.fullmatch(locale):
        return None
    return locale


def validate_build_artifacts(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    plan = plan_from_state(state)
    artifacts = evidence.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise WorkflowError("evidence.artifacts must be a non-empty list")
    seen_formats: set[str] = set()
    normalized = []
    for index, artifact in enumerate(artifacts):
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
        normalized_artifact = {
            "path": str(path),
            "format": fmt,
            "role": role,
            "destination_uri": str(artifact.get("destination_uri", "")).strip(),
        }
        if fmt == "metadata_translations_csv_v1" or role == "metadata-translations":
            expected_name = f"{plan['asset_slug']}.metadata-translations.csv"
            if path.name != expected_name:
                raise WorkflowError(f"evidence.artifacts[{index}].path must be named {expected_name!r}")
            seen_formats.add("metadata_translations_csv_v1")
        elif fmt == "localized_metadata_sidecar_v1" or role == "localized-metadata-sidecar":
            locale = localized_sidecar_locale(str(plan["asset_slug"]), path)
            if locale is None:
                raise WorkflowError(
                    f"evidence.artifacts[{index}].path must be named "
                    f"{plan['asset_slug']}.metadata.{{locale}}.ndjson.gz with a field-safe locale"
                )
            seen_formats.add(f"localized_metadata_sidecar_v1:{locale}")
            normalized_artifact["locale"] = locale
        else:
            seen_formats.add(fmt)
        normalized.append(normalized_artifact)
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


def normalize_preview_locale(value: Any, *, label: str) -> str:
    locale = str(value or "").strip().lower().replace("-", "_")
    if not LOCALE_RE.fullmatch(locale):
        raise WorkflowError(f"{label} must be a lower-case field-safe locale such as es, fr, or pt_br")
    return locale


def normalize_preview_role(role: str, locale: Any = None) -> str:
    aliases = {
        "companion": "pmtiles",
        "metadata_sidecar_v1": "feature-metadata-sidecar",
        "release_schema_v1": "schema",
        "release_manifest_v1": "manifest",
        "release_index_v1": "release-index",
        "run_record_v1": "run-record",
        "metadata_translations_csv_v1": "metadata-translations",
    }
    normalized = aliases.get(role, role)
    if normalized == "localized-metadata-sidecar":
        return f"localized-metadata-sidecar:{normalize_preview_locale(locale, label='evidence.uploaded_objects[].locale')}"
    if normalized.startswith("localized-metadata-sidecar:"):
        raw_locale = normalized.split(":", 1)[1]
        return f"localized-metadata-sidecar:{normalize_preview_locale(raw_locale, label='localized preview role locale')}"
    return normalized


def required_preview_upload_roles(state: dict[str, Any]) -> set[str]:
    plan = plan_from_state(state)
    roles = {"canonical", "release-index", "run-record"}
    if "pmtiles" in plan.get("available_formats", []):
        roles.add("pmtiles")
    if plan.get("canonical_format") == "fgb" and plan.get("release_date"):
        roles.update({"feature-metadata-sidecar", "schema", "manifest"})
    locales = autogenerated_locales(state)
    if locales:
        roles.add("metadata-translations")
        roles.update(f"localized-metadata-sidecar:{locale}" for locale in locales)
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
    if role == "metadata-translations":
        return f"{release_prefix}{asset_slug}.metadata-translations.csv"
    if role.startswith("localized-metadata-sidecar:"):
        locale = role.split(":", 1)[1]
        return f"{release_prefix}{asset_slug}.metadata.{locale}.ndjson.gz"
    if role == "schema":
        return f"{release_prefix}{asset_slug}.schema.json"
    if role == "manifest":
        return f"{release_prefix}{asset_slug}.manifest.json"
    if role == "release-index":
        return f"gs://{PREVIEW_BUCKET}/_catalog/releases/{asset_slug}.json"
    if role == "run-record":
        return f"gs://{PREVIEW_BUCKET}/{plan['asset_root']}/runs/{release_date}.json"
    raise WorkflowError(f"unsupported preview upload role: {role}")


def validate_preview_upload_metadata(index: int, role: str, content_type: str, cache_control: str, *, uri: str = "") -> None:
    where = f"evidence.uploaded_objects[{index}]" + (f" ({uri})" if uri else "")
    if role == "pmtiles":
        if content_type != "application/vnd.pmtiles":
            raise WorkflowError(f"{where}.content_type must be application/vnd.pmtiles")
        if cache_control != no_cache_control():
            raise WorkflowError(f"{where}.cache_control must be {no_cache_control()!r}")
    if role == "feature-metadata-sidecar" or role.startswith("localized-metadata-sidecar:"):
        if content_type != "application/x-ndjson":
            raise WorkflowError(f"{where}.content_type must be application/x-ndjson")
        if cache_control != no_cache_control():
            raise WorkflowError(f"{where}.cache_control must be {no_cache_control()!r}")
    if role == "metadata-translations":
        if content_type != "text/csv":
            raise WorkflowError(f"{where}.content_type must be text/csv")
        if cache_control != no_cache_control():
            raise WorkflowError(f"{where}.cache_control must be {no_cache_control()!r}")
    if role in {"schema", "manifest", "release-index", "run-record"}:
        if content_type != "application/json":
            raise WorkflowError(f"{where}.content_type must be application/json")
        if cache_control != no_cache_control():
            raise WorkflowError(f"{where}.cache_control must be {no_cache_control()!r}")


def validate_preview_upload(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    objects = evidence.get("uploaded_objects")
    if not isinstance(objects, list) or not objects:
        raise WorkflowError("evidence.uploaded_objects must be a non-empty list")
    prefix = f"gs://{PREVIEW_BUCKET}/"
    allowed_roles = required_preview_upload_roles(state)
    normalized = []
    seen_uris = set()
    seen_roles = set()
    for index, obj in enumerate(objects):
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
        role = normalize_preview_role(require_non_empty_string(obj, "role"), obj.get("locale"))
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
        validate_preview_upload_metadata(index, role, content_type, cache_control, uri=uri)
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
    validate_feature_metadata_frontmatter(path, metadata, state)
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


def validate_catalog_web(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
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
    normalized = {
        "built": True,
        "catalog_json_path": str(catalog_json),
        "content_type": content_type,
        "cache_control": cache_control,
    }
    normalized.update(validate_catalog_runtime_feature_metadata(catalog_json, state))
    return normalized


def validate_stage_scratch(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    prefix = pending_publish_prefix(state)
    objects = evidence.get("staged_objects")
    if not isinstance(objects, list) or not objects:
        raise WorkflowError("evidence.staged_objects must be a non-empty list")
    normalized = []
    destinations = set()
    for index, obj in enumerate(objects):
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
        where = f"evidence.staged_objects[{index}] ({destination_uri})"
        if destination_uri.endswith(".pmtiles"):
            if content_type != "application/vnd.pmtiles":
                raise WorkflowError(f"{where}.content_type must be application/vnd.pmtiles")
            if cache_control != no_cache_control():
                raise WorkflowError(f"{where}.cache_control must be {no_cache_control()!r}")
        if destination_uri.endswith("_catalog/web/catalog.json") or destination_uri.endswith("/_catalog/web/catalog.json"):
            if content_type != "application/json":
                raise WorkflowError(f"{where}.content_type must be application/json")
            if cache_control != no_cache_control():
                raise WorkflowError(f"{where}.cache_control must be {no_cache_control()!r}")
        normalized_item = {
            "source_uri": source_uri,
            "source_generation": source_generation,
            "destination_uri": destination_uri,
            "content_type": content_type,
            "cache_control": cache_control,
        }
        compatibility_waiver = obj.get("compatibility_waiver")
        if compatibility_waiver is not None:
            if not isinstance(compatibility_waiver, dict):
                raise WorkflowError(f"evidence.staged_objects[{index}].compatibility_waiver must be an object")
            normalized_item["compatibility_waiver"] = compatibility_waiver
        normalized.append(normalized_item)
    if release_metadata_contract_required(state):
        required_destinations = set(expected_latest_feature_metadata_uris(state).values())
        missing = sorted(required_destinations - destinations)
        if missing:
            raise WorkflowError("missing staged feature metadata destination URI(s): " + ", ".join(missing))
    return {"staged_objects": normalized}


def validate_stat_destinations(state: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    staged = step_record(state, "stage-scratch").get("evidence", {}).get("staged_objects", [])
    staged_destinations = {obj["destination_uri"] for obj in staged if isinstance(obj, dict) and obj.get("destination_uri")}
    destinations = evidence.get("destinations")
    if not isinstance(destinations, list) or not destinations:
        raise WorkflowError("evidence.destinations must be a non-empty list")
    normalized = []
    seen = set()
    for index, destination in enumerate(destinations):
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
        promotion = {
            "source_uri": item["source_uri"],
            "source_generation": item["source_generation"],
            "destination_uri": destination_uri,
            "destination_generation": destinations.get(destination_uri, ""),
            "content_type": item.get("content_type", ""),
            "cache_control": item.get("cache_control", ""),
        }
        if item.get("compatibility_waiver"):
            promotion["compatibility_waiver"] = item["compatibility_waiver"]
        promotions.append(promotion)
    publish_plan = {
        "asset_slug": plan["asset_slug"],
        "proposal_id": state["proposal_id"],
        "promotions": promotions,
    }
    return reviewed_dataset_plan.normalize_publish_plan(publish_plan, bucket=state["bucket"])


def _string_generation(value: Any) -> str:
    return "" if value is None else str(value)


def _stat_blob_payload(uri: str, *, generation: str = "") -> dict[str, Any]:
    from google.api_core.exceptions import NotFound
    from scripts import gcs_asset

    client = gcs_asset.get_client()
    bucket_name, name = gcs_asset.parse_gs_uri(uri)
    blob = client.bucket(bucket_name).blob(name, generation=int(generation) if generation else None)
    try:
        blob.reload()
    except NotFound:
        return {"uri": uri, "requested_generation": generation, "exists": False}
    return {
        "uri": uri,
        "requested_generation": generation,
        "exists": True,
        "generation": str(blob.generation),
        "size": blob.size,
        "content_type": blob.content_type,
        "cache_control": blob.cache_control or "",
        "crc32c": blob.crc32c,
        "md5_hash": blob.md5_hash,
        "updated": blob.updated.isoformat() if blob.updated else None,
    }


def stat_publish_plan_objects(plan: dict[str, Any]) -> dict[str, Any]:
    rows = []
    for index, promotion in enumerate(plan.get("promotions", []), start=1):
        source_generation = _string_generation(promotion.get("source_generation"))
        source = _stat_blob_payload(promotion["source_uri"], generation=source_generation)
        destination = _stat_blob_payload(promotion["destination_uri"])
        rows.append(
            {
                "index": index,
                "source_uri": promotion["source_uri"],
                "destination_uri": promotion["destination_uri"],
                "planned_source_generation": source_generation,
                "planned_destination_generation": _string_generation(promotion.get("destination_generation")),
                "source": source,
                "destination": destination,
                "source_generation_ok": source.get("exists") and source.get("generation") == source_generation,
                "destination_generation_changed": destination.get("generation", "")
                != _string_generation(promotion.get("destination_generation")),
            }
        )
    return {"asset_slug": plan.get("asset_slug"), "proposal_id": plan.get("proposal_id", ""), "rows": rows}


def refresh_retry_plan(
    plan: dict[str, Any],
    stat_report: dict[str, Any],
    *,
    remove_waivers: bool = False,
    allow_crc_mismatch: bool = False,
    bucket: str = DEFAULT_BUCKET,
) -> tuple[dict[str, Any], dict[str, Any]]:
    from scripts import reviewed_dataset_plan

    promotions = plan.get("promotions")
    rows = stat_report.get("rows")
    if not isinstance(promotions, list) or not promotions:
        raise WorkflowError("publish plan must contain a non-empty promotions list")
    if not isinstance(rows, list) or not rows:
        raise WorkflowError("stat report must contain a non-empty rows list")
    rows_by_destination = {
        str(row.get("destination_uri")): row for row in rows if isinstance(row, dict) and row.get("destination_uri")
    }
    missing_stats = []
    missing_sources = []
    source_generation_mismatches = []
    crc_mismatches = []
    refreshed_destinations = []
    destinations_still_absent = []
    refreshed_plan = json.loads(json.dumps(plan))
    for index, promotion in enumerate(refreshed_plan["promotions"], start=1):
        destination_uri = promotion["destination_uri"]
        row = rows_by_destination.get(destination_uri)
        if not row:
            missing_stats.append(destination_uri)
            continue
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        destination = row.get("destination") if isinstance(row.get("destination"), dict) else {}
        planned_source_generation = _string_generation(promotion.get("source_generation"))
        if not source.get("exists"):
            missing_sources.append(destination_uri)
            continue
        if _string_generation(source.get("generation")) != planned_source_generation:
            source_generation_mismatches.append(
                {
                    "destination_uri": destination_uri,
                    "planned_source_generation": planned_source_generation,
                    "current_source_generation": _string_generation(source.get("generation")),
                }
            )
            continue
        if destination.get("exists"):
            source_crc = source.get("crc32c")
            destination_crc = destination.get("crc32c")
            if not source_crc or not destination_crc:
                crc_mismatches.append(
                    {
                        "destination_uri": destination_uri,
                        "source_crc32c": source_crc or "",
                        "destination_crc32c": destination_crc or "",
                        "reason": "missing CRC32C evidence",
                    }
                )
                if not allow_crc_mismatch:
                    continue
            elif source_crc != destination_crc:
                crc_mismatches.append(
                    {
                        "destination_uri": destination_uri,
                        "source_crc32c": source_crc,
                        "destination_crc32c": destination_crc,
                    }
                )
                if not allow_crc_mismatch:
                    continue
            current_generation = _string_generation(destination.get("generation"))
            if _string_generation(promotion.get("destination_generation")) != current_generation:
                refreshed_destinations.append(destination_uri)
            promotion["destination_generation"] = current_generation
        else:
            if promotion.get("destination_generation"):
                refreshed_destinations.append(destination_uri)
            promotion["destination_generation"] = ""
            destinations_still_absent.append(destination_uri)
        if remove_waivers and promotion.get("compatibility_waiver") is not None:
            promotion["compatibility_waiver"] = None

    if missing_stats:
        raise WorkflowError("missing stat report rows for: " + ", ".join(sorted(missing_stats)))
    errors = []
    if missing_sources:
        errors.append("missing staged sources for: " + ", ".join(missing_sources))
    if source_generation_mismatches:
        errors.append("source generation mismatches: " + json.dumps(source_generation_mismatches, sort_keys=True))
    if crc_mismatches and not allow_crc_mismatch:
        errors.append("source/destination CRC32C mismatches: " + json.dumps(crc_mismatches, sort_keys=True))
    if errors:
        raise WorkflowError("; ".join(errors))

    normalized = reviewed_dataset_plan.normalize_publish_plan(refreshed_plan, bucket=bucket)
    summary = {
        "asset_slug": normalized.get("asset_slug"),
        "proposal_id": normalized.get("proposal_id", ""),
        "promotion_count": len(normalized.get("promotions", [])),
        "refreshed_destination_count": len(refreshed_destinations),
        "destinations_still_absent": destinations_still_absent,
        "crc_mismatch_count": len(crc_mismatches),
        "compatibility_waiver_count": sum(1 for item in normalized["promotions"] if item.get("compatibility_waiver")),
    }
    return normalized, summary


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
            "shared_rationale": "string",
            "alternatives_considered": "string",
            "deprecation_policy": "string",
            "estimated_published_size_gb": "string (total GB, e.g. '3.2' or '3.2 GB')",
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
            "access_tier": "public|private|internal",
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
        "Profile source field IDs and grouping/search fields",
        "Record the decision table and explicit generated-ID decisions before any generated IDs are added.",
        {
            "decision_table_present": True,
            "profile_scope": "full|random_sample|schema_only|canonical-artifact|other",
            "source_field_id_decision": "use-source-field|none-suitable",
            "source_field_id_fields": ["string"],
            "generated_feature_id_decision": "not-needed|approved",
            "assignment_" + "key_fields": ["string"],
            "feature_id_decision": "source-field|generated-sequence",
            "feature_id_fields": ["string"],
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
            "tool_versions": {"tool-name": "resolved path and version, or explicit not-applicable note"},
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
                    "content_type": "required per role: pmtiles=application/vnd.pmtiles, sidecars=application/x-ndjson, metadata-translations=text/csv, schema/manifest/release-index/run-record=application/json; canonical has no fixed requirement",
                    "cache_control": "required 'no-cache, max-age=0, must-revalidate' for every role except canonical",
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
                    "content_type": "required application/vnd.pmtiles for .pmtiles destinations and application/json for _catalog/web/catalog.json; otherwise optional",
                    "cache_control": "required 'no-cache, max-age=0, must-revalidate' for .pmtiles and _catalog/web/catalog.json destinations; otherwise optional",
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
    metadata = canonical_resolve_metadata(step_record(state, "resolve-metadata").get("evidence", {}))
    validation = step_record(state, "validate-artifacts").get("evidence", {})
    catalog = step_record(state, "catalog-outputs").get("evidence", {})
    publish_plan = publish_plan or build_publish_plan_from_state(state)
    existing_asset_destination = any(
        promotion.get("destination_generation")
        and "/_catalog/" not in str(promotion.get("destination_uri", ""))
        for promotion in publish_plan.get("promotions", [])
        if isinstance(promotion, dict)
    )
    summary_action = "Publish shared dataset revision" if existing_asset_destination else "Publish new shared dataset"
    body = f"""## Summary

{summary_action} `{plan['asset_slug']}`: {plan['title']}.

## Validation

- Artifact validation: {validation.get('validation_summary', 'Recorded in concierge workflow state.')}
- Commands run: {', '.join(validation.get('commands_run', [])) or 'Recorded in concierge workflow state.'}
- Catalog docs check passed: {catalog.get('check_passed', False)}
- Remote object generations are encoded in the publish plan below.

## Dataset Admission

- Intended consumer(s): {', '.join(metadata.get('intended_consumers', []))}
- Why this belongs in shared-datasets instead of project storage, scratch storage, or direct upstream access: {metadata.get('shared_rationale', '')}
- Source, license, and citation status: Source: {metadata.get('source_name', '')}; license/terms: {metadata.get('license', '')}; citation: {metadata.get('citation', '')}
- Named steward: {metadata.get('steward', '')}
- Update expectations: {metadata.get('update_cadence', '')}
- Estimated published size in GB, one total across canonical files, companion artifacts, and expected release copies: {metadata.get('estimated_published_size_gb', '')}
- Large-data exception, required when the proposed published footprint is >= 10 GB: See contract exception flags in concierge workflow state.
- Alternatives considered: {metadata.get('alternatives_considered', '')}
- Deprecation or exit policy: {metadata.get('deprecation_policy', '')}

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
        preview_load_status = preview_load.get("status", "not recorded")
        if not preview_load_required(state):
            preview_load_status = "skipped (Firestore preview serving inactive)"
        followup_state = f"""## Preview Load State

- Workflow: {preview_load.get('workflow_name', 'not recorded')}
- Workflow run: {preview_load.get('workflow_run_url', 'not recorded')}
- Dispatch ref: {preview_load.get('dispatched_ref', 'not recorded')}
- Status: {preview_load_status}
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
    parser.add_argument("--access-tier", default="public", choices=["public", "private", "internal"])
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


def command_refresh_retry_plan(args: argparse.Namespace) -> int:
    plan = read_json_file(args.plan, label="publish plan")
    if args.stat_gcs:
        stat_report = stat_publish_plan_objects(plan)
        if args.stats_output:
            write_json_file(args.stats_output, stat_report, overwrite=True)
    else:
        stat_report = read_json_file(args.stat_report, label="stat report")
    refreshed_plan, summary = refresh_retry_plan(
        plan,
        stat_report,
        remove_waivers=args.remove_waivers,
        allow_crc_mismatch=args.allow_crc_mismatch,
        bucket=args.bucket,
    )
    write_json_file(args.output, refreshed_plan, overwrite=True)
    if args.summary_output:
        write_json_file(args.summary_output, summary, overwrite=True)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


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

    retry = subparsers.add_parser(
        "refresh-retry-plan",
        help="Refresh destination generations in a fenced publish plan after a partial approved mutation failure.",
    )
    retry.add_argument("--plan", type=Path, required=True, help="Normalized publish plan JSON from a PR body.")
    stat_group = retry.add_mutually_exclusive_group(required=True)
    stat_group.add_argument(
        "--stat-report",
        type=Path,
        help="Previously captured JSON report with source and destination object stats.",
    )
    stat_group.add_argument(
        "--stat-gcs",
        action="store_true",
        help="Stat every planned source and destination in GCS now. Requires network access and credentials.",
    )
    retry.add_argument("--output", type=Path, required=True, help="Path for the refreshed publish plan JSON.")
    retry.add_argument("--stats-output", type=Path, help="Optional path for GCS stats when using --stat-gcs.")
    retry.add_argument("--summary-output", type=Path, help="Optional path for the compact refresh summary JSON.")
    retry.add_argument("--bucket", default=DEFAULT_BUCKET, help="Expected shared datasets bucket for plan validation.")
    retry.add_argument(
        "--remove-waivers",
        action="store_true",
        help="Remove compatibility waivers when a partial run already advanced the schema snapshot.",
    )
    retry.add_argument(
        "--allow-crc-mismatch",
        action="store_true",
        help="Refresh generations even when current destination CRC32C differs from the staged source. Use only after review.",
    )
    retry.set_defaults(func=command_refresh_retry_plan)

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
