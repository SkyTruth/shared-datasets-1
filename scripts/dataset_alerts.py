#!/usr/bin/env python3
"""Dataset upload summaries and schema-change warnings."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import typer
import yaml
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import catalog_docs
from scripts.gcs_asset import ALLOW_CANONICAL_MUTATION_ENV, parse_gs_uri, require_mutation_allowed
from scripts.slack_notify import notify


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
SCHEMA_SNAPSHOT_PREFIX = "_catalog/schema-snapshots"
SCHEMA_ALERT_LOG_NAME = "shared-datasets-alerts"
SAMPLE_COLUMN_LIMIT = 5
SCHEMA_COMPATIBILITY_BLOCKING_KINDS = {"removed", "renamed", "type_changed"}
SCHEMA_COMPATIBILITY_WARNING_KINDS = {"reordered"}
WAIVER_REQUIRED_TEXT_FIELDS = ("rationale", "consumer_impact", "reviewer", "pr_reference", "migration_path")
BREAKING_CHANGE_CATEGORIES = {
    "path",
    "format",
    "artifact_set",
    "schema",
    "feature_identity",
    "pmtiles_lookup",
    "metadata_sidecar",
    "access",
    "catalog",
    "lifecycle_delete",
    "other",
}
BREAKING_SCHEMA_KINDS = SCHEMA_COMPATIBILITY_BLOCKING_KINDS | SCHEMA_COMPATIBILITY_WARNING_KINDS
BREAKING_ALERT_MARKER_PREFIX = "shared-datasets-breaking-alert"
MAX_BREAKING_ALERT_BULLETS = 5

app = typer.Typer(no_args_is_help=True)


class SchemaCompatibilityError(RuntimeError):
    """Raised when a proposed canonical schema is incompatible."""


@dataclass(frozen=True)
class SchemaCompatibilityResult:
    asset_slug: str
    snapshot_uri: str
    snapshot_generation: int | None
    source_path: str
    fields: list[dict[str, str]]
    diffs: list[dict[str, str]]
    blocked_diffs: list[dict[str, str]]
    warning_diffs: list[dict[str, str]]
    waiver: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["compatible"] = not self.blocked_diffs or self.waiver is not None
        return payload


def load_catalog(path: Path = Path("catalog/shared-datasets-catalog.csv")) -> dict[str, dict[str, str]]:
    with path.open(newline="") as file_obj:
        return {row["asset_slug"]: row for row in csv.DictReader(file_obj)}


def asset_root_from_canonical(canonical_path: str) -> str:
    if "/latest/" not in canonical_path:
        return canonical_path
    return canonical_path.split("/latest/", 1)[0]


def dataset_description(asset_slug: str, row: dict[str, str] | None) -> str:
    if not row:
        return asset_slug
    title = row.get("title") or asset_slug
    source = row.get("source", "").strip()
    if source and source != title:
        return f"{title}. Source: {source}."
    return title


def format_sample_columns(sample_columns: list[str] | None, *, limit: int = SAMPLE_COLUMN_LIMIT) -> str:
    if not sample_columns:
        return "`unknown`"
    visible = sample_columns[:limit]
    formatted = ", ".join(f"`{column}`" for column in visible)
    remaining = len(sample_columns) - len(visible)
    if remaining > 0:
        formatted = f"{formatted}, +{remaining} more"
    return formatted


def sample_columns_from_schema(fields: list[dict[str, str]]) -> list[str]:
    return [field["name"] for field in fields if field.get("name")]


def row_count_from_asset_doc(asset_slug: str, docs_dir: Path = Path("docs/assets")) -> int | None:
    path = docs_dir / f"{asset_slug}.md"
    try:
        metadata, _body = catalog_docs.split_frontmatter(path.read_text(), path)
    except (OSError, UnicodeError, catalog_docs.CatalogDocsError, yaml.YAMLError):
        return None
    value = metadata.get("row_count")
    if value in (None, ""):
        return None
    try:
        row_count = int(value)
    except (TypeError, ValueError):
        return None
    if row_count < 0:
        return None
    return row_count


def upload_summary_title(*, new_dataset: bool) -> str:
    return "New dataset added!" if new_dataset else "Dataset updated"


def upload_summary_status(*, new_dataset: bool) -> str:
    return "new" if new_dataset else "success"


def build_upload_summary(
    *,
    asset_slug: str,
    row: dict[str, str] | None,
    changed_paths: list[str],
    release_path: str | None = None,
    row_count: int | None = None,
    sample_columns: list[str] | None = None,
    new_dataset: bool = False,
) -> tuple[str, str, dict[str, str]]:
    title = upload_summary_title(new_dataset=new_dataset)
    canonical_path = row.get("canonical_path", "") if row else ""
    asset_root = asset_root_from_canonical(canonical_path) if canonical_path else "unknown"
    body_lines = [
        f"*Asset:* {dataset_description(asset_slug, row)}",
        f"*Rows:* `{row_count}`" if row_count is not None else "*Rows:* `unknown`",
        f"*Sample columns:* {format_sample_columns(sample_columns)}",
        f"*Asset root:* `{asset_root}`",
    ]
    return title, "\n".join(body_lines), {}


def infer_scalar_type(value: str) -> str:
    text = value.strip()
    if text == "":
        return "String"
    for type_name, caster in (
        ("Integer", int),
        ("Real", float),
    ):
        try:
            caster(text)
            return type_name
        except ValueError:
            continue
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return "Boolean"
    return "String"


def merge_types(current: str, new: str) -> str:
    if not current:
        return new
    if current == new:
        return current
    if {current, new} <= {"Integer", "Real"}:
        return "Real"
    return "String"


def schema_from_csv(path: Path, *, sample_rows: int = 200) -> list[dict[str, str]]:
    with path.open(newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        if not reader.fieldnames:
            return []
        types = {field: "" for field in reader.fieldnames}
        for index, row in enumerate(reader):
            if index >= sample_rows:
                break
            for field in reader.fieldnames:
                value = row.get(field, "")
                if value.strip():
                    types[field] = merge_types(types[field], infer_scalar_type(value))
    return [{"name": field, "type": types[field] or "String"} for field in reader.fieldnames]


def schema_from_ogr(
    path: Path,
    *,
    runner: Any = subprocess.run,
) -> list[dict[str, str]]:
    result = runner(
        ["ogrinfo", "-ro", "-al", "-so", "-json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    result.check_returncode()
    payload = json.loads(result.stdout)
    layers = payload.get("layers") or []
    if not layers:
        raise RuntimeError(f"No layers found in {path}")
    fields = layers[0].get("fields") or []
    return [
        {
            "name": str(field.get("name", "")),
            "type": str(field.get("type", field.get("typeName", "")) or "Unknown"),
        }
        for field in fields
    ]

def schema_for_path(path: Path) -> list[dict[str, str]]:
    if path.suffix.lower() == ".csv":
        return schema_from_csv(path)
    return schema_from_ogr(path)


def diff_schemas(
    old_fields: list[dict[str, str]],
    new_fields: list[dict[str, str]],
) -> list[dict[str, str]]:
    old_by_name = {field["name"]: field for field in old_fields}
    new_by_name = {field["name"]: field for field in new_fields}
    old_names = [field["name"] for field in old_fields]
    new_names = [field["name"] for field in new_fields]
    diffs: list[dict[str, str]] = []

    removed = [name for name in old_names if name not in new_by_name]
    added = [name for name in new_names if name not in old_by_name]
    if len(removed) == 1 and len(added) == 1 and len(old_names) == len(new_names):
        old_index = old_names.index(removed[0])
        new_index = new_names.index(added[0])
        if old_index == new_index:
            diffs.append(
                {
                    "kind": "renamed",
                    "field": f"{removed[0]} -> {added[0]}",
                    "old": old_by_name[removed[0]].get("type", ""),
                    "new": new_by_name[added[0]].get("type", ""),
                }
            )
            removed = []
            added = []

    for name in removed:
        diffs.append({"kind": "removed", "field": name, "old": old_by_name[name].get("type", ""), "new": ""})
    for name in added:
        diffs.append({"kind": "added", "field": name, "old": "", "new": new_by_name[name].get("type", "")})

    for name in old_names:
        if name in new_by_name and old_by_name[name].get("type") != new_by_name[name].get("type"):
            diffs.append(
                {
                    "kind": "type_changed",
                    "field": name,
                    "old": old_by_name[name].get("type", ""),
                    "new": new_by_name[name].get("type", ""),
                }
            )

    if not removed and not added and old_names != new_names and set(old_names) == set(new_names):
        diffs.append({"kind": "reordered", "field": "*", "old": ", ".join(old_names), "new": ", ".join(new_names)})

    return diffs


def blocking_schema_diffs(diffs: list[dict[str, str]]) -> list[dict[str, str]]:
    return [diff for diff in diffs if diff.get("kind") in SCHEMA_COMPATIBILITY_BLOCKING_KINDS]


def warning_schema_diffs(diffs: list[dict[str, str]]) -> list[dict[str, str]]:
    return [diff for diff in diffs if diff.get("kind") in SCHEMA_COMPATIBILITY_WARNING_KINDS]


def compact_diff(diff: dict[str, str]) -> dict[str, str]:
    return {"kind": str(diff.get("kind", "")), "field": str(diff.get("field", ""))}


def load_compatibility_waiver(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SchemaCompatibilityError(f"schema compatibility waiver JSON is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchemaCompatibilityError("schema compatibility waiver must be a JSON object")
    return payload


def validate_compatibility_waiver(
    waiver: dict[str, Any],
    *,
    asset_slug: str,
    blocked_diffs: list[dict[str, str]],
) -> dict[str, Any]:
    if not blocked_diffs:
        raise SchemaCompatibilityError("schema compatibility waiver was supplied, but no blocked schema changes exist")
    if not isinstance(waiver, dict):
        raise SchemaCompatibilityError("schema compatibility waiver must be a JSON object")
    if waiver.get("asset_slug") != asset_slug:
        raise SchemaCompatibilityError("schema compatibility waiver asset_slug does not match the published asset")

    normalized: dict[str, Any] = {"asset_slug": asset_slug}
    for key in WAIVER_REQUIRED_TEXT_FIELDS:
        value = str(waiver.get(key, "")).strip()
        if not value:
            raise SchemaCompatibilityError(f"schema compatibility waiver is missing required field {key!r}")
        normalized[key] = value

    raw_changes = waiver.get("blocked_changes")
    if not isinstance(raw_changes, list) or not raw_changes:
        raise SchemaCompatibilityError("schema compatibility waiver must include nonempty blocked_changes")

    normalized_changes: list[dict[str, str]] = []
    for index, raw_change in enumerate(raw_changes, start=1):
        if not isinstance(raw_change, dict):
            raise SchemaCompatibilityError(f"schema compatibility waiver blocked_changes[{index}] must be an object")
        kind = str(raw_change.get("kind", "")).strip()
        field = str(raw_change.get("field", "")).strip()
        if kind not in SCHEMA_COMPATIBILITY_BLOCKING_KINDS:
            raise SchemaCompatibilityError(
                f"schema compatibility waiver blocked_changes[{index}].kind is not a blocking kind"
            )
        if not field:
            raise SchemaCompatibilityError(f"schema compatibility waiver blocked_changes[{index}].field is required")
        normalized_changes.append({"kind": kind, "field": field})

    expected = {(compact_diff(diff)["kind"], compact_diff(diff)["field"]) for diff in blocked_diffs}
    approved = {(change["kind"], change["field"]) for change in normalized_changes}
    if expected != approved:
        raise SchemaCompatibilityError(
            "schema compatibility waiver blocked_changes must exactly match blocked schema changes: "
            + format_schema_diffs(blocked_diffs)
        )

    normalized["blocked_changes"] = normalized_changes
    return normalized


def check_schema_compatibility(
    *,
    asset_slug: str,
    dataset_path: Path,
    snapshot_uri: str | None = None,
    fields: list[dict[str, str]] | None = None,
    compatibility_waiver: dict[str, Any] | None = None,
    compatibility_waiver_path: Path | None = None,
    snapshot_loader: Any = None,
    schema_reader: Any = None,
) -> SchemaCompatibilityResult:
    """Enforce append-compatible canonical vector/table schemas before publish."""

    schema_probe = schema_reader or schema_for_path
    proposed_fields = fields if fields is not None else schema_probe(dataset_path)
    uri = snapshot_uri or snapshot_uri_for(asset_slug, os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET))
    load_snapshot_fn = snapshot_loader or load_snapshot
    previous, generation = load_snapshot_fn(uri)
    diffs = diff_schemas(previous.get("fields", []), proposed_fields) if previous else []
    blocked = blocking_schema_diffs(diffs)
    warnings = warning_schema_diffs(diffs)

    waiver_payload = compatibility_waiver
    if compatibility_waiver_path is not None:
        if compatibility_waiver is not None:
            raise SchemaCompatibilityError("provide either compatibility_waiver or compatibility_waiver_path, not both")
        waiver_payload = load_compatibility_waiver(compatibility_waiver_path)

    normalized_waiver = None
    if waiver_payload is not None:
        normalized_waiver = validate_compatibility_waiver(
            waiver_payload,
            asset_slug=asset_slug,
            blocked_diffs=blocked,
        )
    elif blocked:
        raise SchemaCompatibilityError(
            "blocked incompatible schema change(s): " + format_schema_diffs(blocked)
        )

    return SchemaCompatibilityResult(
        asset_slug=asset_slug,
        snapshot_uri=uri,
        snapshot_generation=generation,
        source_path=str(dataset_path),
        fields=proposed_fields,
        diffs=diffs,
        blocked_diffs=blocked,
        warning_diffs=warnings,
        waiver=normalized_waiver,
    )


def format_schema_diffs(diffs: list[dict[str, str]]) -> str:
    return "\n".join(
        f"- `{diff['kind']}` `{diff['field']}`: `{diff.get('old', '')}` -> `{diff.get('new', '')}`"
        for diff in diffs
    )


def normalize_breaking_surfaces(value: Any) -> list[str]:
    if isinstance(value, str):
        surfaces = [value.strip()] if value.strip() else []
    elif isinstance(value, list):
        surfaces = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise SchemaCompatibilityError("breaking change affected_surfaces must contain non-empty strings")
            surfaces.append(item.strip())
    else:
        raise SchemaCompatibilityError("breaking change affected_surfaces must be a non-empty string or list")
    if not surfaces:
        raise SchemaCompatibilityError("breaking change affected_surfaces must not be empty")
    return surfaces


def normalize_breaking_change(change: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(change, dict):
        raise SchemaCompatibilityError("breaking change must be an object")
    category = str(change.get("category", "")).strip()
    if category not in BREAKING_CHANGE_CATEGORIES:
        raise SchemaCompatibilityError(f"unsupported breaking change category: {category!r}")
    summary = str(change.get("summary", "")).strip()
    if not summary:
        raise SchemaCompatibilityError("breaking change summary is required")
    consumer_action = str(change.get("consumer_action", "")).strip()
    if not consumer_action:
        raise SchemaCompatibilityError("breaking change consumer_action is required")
    return {
        "category": category,
        "summary": summary,
        "consumer_action": consumer_action,
        "affected_surfaces": normalize_breaking_surfaces(change.get("affected_surfaces")),
    }


def normalize_breaking_changes(changes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    normalized = [normalize_breaking_change(change) for change in (changes or [])]
    deduped: list[dict[str, Any]] = []
    seen = set()
    for change in sorted(
        normalized,
        key=lambda item: (
            item["category"],
            item["summary"],
            item["consumer_action"],
            tuple(sorted(item["affected_surfaces"])),
        ),
    ):
        key = (change["category"], change["summary"], tuple(sorted(change["affected_surfaces"])))
        if key in seen:
            continue
        seen.add(key)
        change = dict(change)
        change["affected_surfaces"] = sorted(change["affected_surfaces"])
        deduped.append(change)
    return deduped


def semicolon_values(value: Any) -> set[str]:
    if isinstance(value, list):
        raw_values = value
    else:
        raw_values = str(value or "").split(";")
    return {str(item).strip().lower() for item in raw_values if str(item).strip()}


def catalog_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return None


def metadata_contract_roles(row: dict[str, Any]) -> set[str]:
    roles: set[str] = set()
    for path in semicolon_values(row.get("metadata_paths")):
        if ".metadata." in path or path.endswith(".metadata.ndjson.gz"):
            roles.add("metadata sidecar")
        if path.endswith(".schema.json"):
            roles.add("schema sidecar")
        if path.endswith(".manifest.json") or path.endswith("/manifest.json"):
            roles.add("release manifest")
    return roles


def row_value(row: dict[str, Any], key: str) -> str:
    return str(row.get(key) or "").strip()


def format_values(values: set[str]) -> str:
    return ", ".join(f"`{value}`" for value in sorted(values)) if values else "`none`"


def catalog_contract_breaking_changes(
    *,
    asset_slug: str,
    current_row: dict[str, Any] | None,
    proposed_row: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not current_row or not proposed_row:
        return []

    changes: list[dict[str, Any]] = []
    current_path = row_value(current_row, "canonical_path")
    proposed_path = row_value(proposed_row, "canonical_path")
    if current_path and proposed_path and current_path != proposed_path:
        changes.append(
            {
                "category": "path",
                "summary": f"Catalog canonical path changed from `{current_path}` to `{proposed_path}`.",
                "consumer_action": "Update latest object paths before the next workflow run.",
                "affected_surfaces": ["catalog canonical_path", current_path, proposed_path],
            }
        )

    current_format = row_value(current_row, "canonical_format").lower()
    proposed_format = row_value(proposed_row, "canonical_format").lower()
    if current_format and proposed_format and current_format != proposed_format:
        changes.append(
            {
                "category": "format",
                "summary": f"Catalog canonical format changed from `{current_format}` to `{proposed_format}`.",
                "consumer_action": "Update latest readers before consuming the new canonical format.",
                "affected_surfaces": ["catalog canonical_format", current_format, proposed_format],
            }
        )

    current_formats = semicolon_values(current_row.get("available_formats"))
    proposed_formats = semicolon_values(proposed_row.get("available_formats"))
    removed_formats = current_formats - proposed_formats
    if removed_formats:
        added_formats = proposed_formats - current_formats
        suffix = f"; added {format_values(added_formats)}" if added_formats else ""
        changes.append(
            {
                "category": "artifact_set",
                "summary": f"Available formats removed: {format_values(removed_formats)}{suffix}.",
                "consumer_action": "Verify required latest artifacts still exist before consuming latest.",
                "affected_surfaces": ["catalog available_formats", *sorted(removed_formats)],
            }
        )

    current_access = row_value(current_row, "access_tier").lower()
    proposed_access = row_value(proposed_row, "access_tier").lower()
    if current_access and proposed_access and current_access != proposed_access and proposed_access != "public":
        changes.append(
            {
                "category": "access",
                "summary": f"Access tier changed from `{current_access}` to `{proposed_access}`.",
                "consumer_action": "Confirm credentials and access policy before consuming latest.",
                "affected_surfaces": ["catalog access_tier", proposed_access],
            }
        )

    current_pmtiles = catalog_bool(current_row.get("has_pmtiles"))
    proposed_pmtiles = catalog_bool(proposed_row.get("has_pmtiles"))
    if current_pmtiles is True and proposed_pmtiles is False:
        changes.append(
            {
                "category": "pmtiles_lookup",
                "summary": "PMTiles availability changed from `true` to `false`.",
                "consumer_action": "Stop using latest PMTiles for this asset or pin a dated release.",
                "affected_surfaces": ["catalog has_pmtiles", f"{asset_slug}.pmtiles"],
            }
        )

    current_roles = metadata_contract_roles(current_row)
    proposed_roles = metadata_contract_roles(proposed_row)
    removed_roles = current_roles - proposed_roles
    for role in sorted(removed_roles):
        changes.append(
            {
                "category": "metadata_sidecar",
                "summary": f"{role.title()} removed from the catalog metadata contract.",
                "consumer_action": "Update metadata lookup or release-manifest readers before consuming latest.",
                "affected_surfaces": ["catalog metadata_paths", role],
            }
        )

    return normalize_breaking_changes(changes)


def schema_result_to_dict(result: SchemaCompatibilityResult | dict[str, Any]) -> dict[str, Any]:
    return result.to_dict() if isinstance(result, SchemaCompatibilityResult) else dict(result)


def breaking_changes_from_schema_results(results: list[SchemaCompatibilityResult | dict[str, Any]]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for raw_result in results:
        result = schema_result_to_dict(raw_result)
        waiver = result.get("waiver") if isinstance(result.get("waiver"), dict) else {}
        action = str(waiver.get("migration_path") or "").strip()
        if not action:
            action = "Review the schema diff and update parsers before consuming the new latest release."
        for diff in result.get("diffs") or []:
            if not isinstance(diff, dict):
                continue
            kind = str(diff.get("kind") or "")
            if kind not in BREAKING_SCHEMA_KINDS:
                continue
            field = str(diff.get("field") or "*")
            if kind == "type_changed":
                summary = f"Schema field `{field}` type changed from `{diff.get('old', '')}` to `{diff.get('new', '')}`."
            elif kind == "renamed":
                summary = f"Schema field renamed: `{field}`."
            elif kind == "removed":
                summary = f"Schema field removed: `{field}`."
            elif kind == "reordered":
                summary = "Schema field order changed."
            else:
                summary = f"Schema changed: `{kind}` `{field}`."
            changes.append(
                {
                    "category": "schema",
                    "summary": summary,
                    "consumer_action": action,
                    "affected_surfaces": ["latest canonical schema", field],
                }
            )
    return normalize_breaking_changes(changes)


def breaking_changes_from_waivers(plan: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for promotion in plan.get("promotions") or []:
        if not isinstance(promotion, dict):
            continue
        waiver = promotion.get("compatibility_waiver")
        if not isinstance(waiver, dict):
            continue
        action = str(waiver.get("migration_path") or "").strip() or "Review the PR migration guidance before consuming latest."
        for blocked in waiver.get("blocked_changes") or []:
            if not isinstance(blocked, dict):
                continue
            kind = str(blocked.get("kind") or "")
            field = str(blocked.get("field") or "")
            if kind not in SCHEMA_COMPATIBILITY_BLOCKING_KINDS or not field:
                continue
            changes.append(
                {
                    "category": "schema",
                    "summary": f"Schema compatibility waiver approved `{kind}` `{field}`.",
                    "consumer_action": action,
                    "affected_surfaces": [str(promotion.get("destination_uri") or "latest canonical schema"), field],
                }
            )
    return normalize_breaking_changes(changes)


def breaking_changes_from_delete_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    asset_slug = str(plan.get("asset_slug") or "asset")
    for deletion in plan.get("deletions") or []:
        if not isinstance(deletion, dict):
            continue
        uri = str(deletion.get("uri") or "")
        if "/latest/" in uri:
            changes.append(
                {
                    "category": "lifecycle_delete",
                    "summary": f"`latest` object deleted: `{uri}`.",
                    "consumer_action": f"Stop reading `{asset_slug}@latest` until a replacement is published, or pin a dated release.",
                    "affected_surfaces": [uri],
                }
            )
        elif "/_catalog/" in uri or uri.startswith(f"gs://{DEFAULT_BUCKET}/_catalog/"):
            changes.append(
                {
                    "category": "catalog",
                    "summary": f"Catalog contract object deleted: `{uri}`.",
                    "consumer_action": f"Refresh catalog configuration and verify `{asset_slug}@latest` resolution before the next run.",
                    "affected_surfaces": [uri],
                }
            )
    return normalize_breaking_changes(changes)


def collect_breaking_changes(
    *,
    plan: dict[str, Any],
    plan_type: str,
    schema_results: list[SchemaCompatibilityResult | dict[str, Any]] | None = None,
    current_row: dict[str, Any] | None = None,
    proposed_row: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    changes.extend(normalize_breaking_changes(plan.get("breaking_changes") or []))
    changes.extend(
        catalog_contract_breaking_changes(
            asset_slug=str(plan.get("asset_slug") or "asset"),
            current_row=current_row,
            proposed_row=proposed_row,
        )
    )
    schema_result_changes = breaking_changes_from_schema_results(schema_results or [])
    changes.extend(schema_result_changes)
    if plan_type == "publish" and not schema_result_changes:
        changes.extend(breaking_changes_from_waivers(plan))
    if plan_type == "delete":
        changes.extend(breaking_changes_from_delete_plan(plan))
    return normalize_breaking_changes(changes)


def breaking_change_fingerprint(asset_slug: str, changes: list[dict[str, Any]]) -> str:
    payload = {
        "asset_slug": asset_slug,
        "breaking_changes": normalize_breaking_changes(changes),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]


def build_breaking_alert(
    *,
    asset_slug: str,
    changes: list[dict[str, Any]],
    phase: str,
    row: dict[str, str] | None = None,
    plan_type: str = "publish",
    pr_number: str | None = None,
    pr_url: str | None = None,
    run_url: str | None = None,
) -> dict[str, Any] | None:
    normalized = normalize_breaking_changes(changes)
    if not normalized:
        return None
    if phase not in {"planned", "live"}:
        raise SchemaCompatibilityError("breaking alert phase must be planned or live")
    if plan_type not in {"publish", "delete"}:
        raise SchemaCompatibilityError("breaking alert plan_type must be publish or delete")
    title = f"BREAKING {phase}: {asset_slug} latest contract"
    asset_title = row.get("title") if row else ""
    status_line = f"Planned in PR #{pr_number}" if phase == "planned" and pr_number else "Live after approved promotion"
    if phase == "live" and plan_type == "delete":
        status_line = "Live after approved deletion"
    visible = normalized[:MAX_BREAKING_ALERT_BULLETS]
    bullets = []
    for change in visible:
        surfaces = ", ".join(change["affected_surfaces"][:2])
        if len(change["affected_surfaces"]) > 2:
            surfaces += f", +{len(change['affected_surfaces']) - 2} more"
        bullets.append(f"- *{change['category']}*: {change['summary']} ({surfaces})")
    if len(normalized) > MAX_BREAKING_ALERT_BULLETS:
        bullets.append(f"- +{len(normalized) - MAX_BREAKING_ALERT_BULLETS} more in PR")
    actions = []
    for change in normalized:
        if change["consumer_action"] not in actions:
            actions.append(change["consumer_action"])
    action = actions[0]
    if len(actions) > 1:
        action += " See PR for additional actions."
    link = pr_url or run_url or "not recorded"
    body = "\n".join(
        [
            f"*Asset:* {asset_title or asset_slug} (`{asset_slug}`)",
            f"*Status:* {status_line}",
            "*Changed:*",
            *bullets,
            f"*Action:* {action}; ignore if you do not consume `{asset_slug}@latest`.",
            f"*PR/Run:* {link}",
        ]
    )
    fingerprint = breaking_change_fingerprint(asset_slug, normalized)
    return {
        "title": title,
        "body": body,
        "status": "warning",
        "fingerprint": fingerprint,
        "marker": f"{BREAKING_ALERT_MARKER_PREFIX}:{phase}:{plan_type}:{asset_slug}:{fingerprint}",
        "changes": normalized,
    }


def load_schema_result_files(paths: list[Path]) -> list[dict[str, Any]]:
    results = []
    for path in paths:
        payload = json.loads(path.read_text())
        if isinstance(payload, dict):
            results.append(payload)
        elif isinstance(payload, list):
            results.extend(item for item in payload if isinstance(item, dict))
    return results


def load_catalog_row_file(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict):
        raise typer.BadParameter(f"catalog row file must contain an object: {path}")
    return payload


def format_field_list(fields: list[dict[str, str]]) -> str:
    if not fields:
        return "- none"
    return "\n".join(f"- `{field['name']}`: `{field.get('type', 'Unknown')}`" for field in fields)


def truncate_label_text(text: str, *, limit: int = 900) -> str:
    if len(text) <= limit:
        return text
    return f"{text[: limit - 20]}\n... truncated ..."


def emit_cloud_logging_warning(
    payload: dict[str, Any],
    *,
    project_id: str = "shared-datasets-1",
    dry_run: bool = False,
    runner: Any = subprocess.run,
) -> None:
    """Write a structured warning log entry for Cloud Monitoring log-match alerts."""

    if dry_run:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    command = [
        "gcloud",
        "logging",
        "write",
        SCHEMA_ALERT_LOG_NAME,
        json.dumps(payload, sort_keys=True),
        "--payload-type=json",
        "--severity=WARNING",
        "--project",
        project_id,
    ]
    try:
        runner(command, check=True)
    except subprocess.CalledProcessError as exc:
        print(
            "schema warning logging failed; continuing schema snapshot update: "
            f"{exc}",
            file=sys.stderr,
        )


def snapshot_uri_for(asset_slug: str, bucket: str = DEFAULT_BUCKET) -> str:
    return f"gs://{bucket}/{SCHEMA_SNAPSHOT_PREFIX}/{asset_slug}.json"


def load_snapshot(uri: str) -> tuple[dict[str, Any] | None, int | None]:
    bucket_name, name = parse_gs_uri(uri)
    blob = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT")).bucket(bucket_name).blob(name)
    try:
        blob.reload()
    except NotFound:
        return None, None
    return json.loads(blob.download_as_text()), int(blob.generation)


def write_snapshot(uri: str, payload: dict[str, Any], *, generation: int | None) -> None:
    bucket_name, name = parse_gs_uri(uri)
    blob = storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT")).bucket(bucket_name).blob(name)
    kwargs = {"if_generation_match": generation if generation is not None else 0}
    try:
        blob.upload_from_string(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            content_type="application/json",
            **kwargs,
        )
    except PreconditionFailed as exc:
        raise RuntimeError(f"Schema snapshot changed before update: {uri}") from exc


@app.command("upload-summary")
def upload_summary(
    asset_slug: str = typer.Option(..., help="Catalog asset slug."),
    changed_path: list[str] = typer.Option([], "--changed-path", help="Remote path changed by this upload."),
    release_path: Optional[str] = typer.Option(None, help="Release path, if one was published."),
    row_count: Optional[int] = typer.Option(None, help="Published row count, if known."),
    dataset_path: Optional[Path] = typer.Option(
        None,
        exists=True,
        dir_okay=False,
        help="Local canonical file for sample columns.",
    ),
    sample_column: list[str] = typer.Option(
        [],
        "--sample-column",
        help="Sample column name to include in the Slack summary.",
    ),
    new_dataset: bool = typer.Option(
        False,
        "--new-dataset/--updated-dataset",
        help="Use the new-dataset alert title only when the latest canonical object did not previously exist.",
    ),
    dry_run: bool = typer.Option(False, help="Print Slack payload instead of posting."),
) -> None:
    """Post a lightweight dataset upload summary."""

    resolved_new_dataset = new_dataset if isinstance(new_dataset, bool) else False
    row = load_catalog().get(asset_slug)
    resolved_row_count = row_count if row_count is not None else row_count_from_asset_doc(asset_slug)
    sample_columns = sample_column or (
        sample_columns_from_schema(schema_for_path(dataset_path)) if dataset_path else None
    )
    title, body, fields = build_upload_summary(
        asset_slug=asset_slug,
        row=row,
        changed_paths=changed_path,
        release_path=release_path,
        row_count=resolved_row_count,
        sample_columns=sample_columns,
        new_dataset=resolved_new_dataset,
    )
    notify(
        title=title,
        body=body,
        status=upload_summary_status(new_dataset=resolved_new_dataset),
        fields=fields,
        dry_run=dry_run,
    )


@app.command("breaking-alert")
def breaking_alert(
    phase: str = typer.Option(..., help="Alert phase: planned or live."),
    plan_type: str = typer.Option(..., help="Reviewed plan type: publish or delete."),
    plan_json: Path = typer.Option(..., exists=True, dir_okay=False, help="Normalized publish/delete plan JSON."),
    schema_result: list[Path] = typer.Option([], "--schema-result", exists=True, dir_okay=False, help="Schema compatibility result JSON. May be repeated."),
    current_catalog_row_json: Optional[Path] = typer.Option(None, exists=True, dir_okay=False, help="Current catalog row JSON used for catalog contract comparison."),
    proposed_catalog_row_json: Optional[Path] = typer.Option(None, exists=True, dir_okay=False, help="Proposed catalog row JSON used for catalog contract comparison."),
    pr_number: Optional[str] = typer.Option(None, help="Pull request number for planned/live context."),
    pr_url: Optional[str] = typer.Option(None, help="Pull request URL for alert context."),
    run_url: Optional[str] = typer.Option(None, help="GitHub Actions run URL for alert context."),
    summary_json: Optional[Path] = typer.Option(None, help="Write normalized alert summary JSON for workflow deduplication."),
    dry_run: bool = typer.Option(False, help="Print Slack payload instead of posting."),
) -> None:
    """Post a concise Slack warning for reviewed latest-contract breaking changes."""

    plan = json.loads(plan_json.read_text())
    if not isinstance(plan, dict):
        raise typer.BadParameter("plan JSON must contain an object")
    asset_slug = str(plan.get("asset_slug") or "")
    if not asset_slug:
        raise typer.BadParameter("plan JSON must include asset_slug")
    schema_results = load_schema_result_files(schema_result)
    catalog_row = load_catalog().get(asset_slug)
    current_row = load_catalog_row_file(current_catalog_row_json) or catalog_row
    proposed_row = load_catalog_row_file(proposed_catalog_row_json)
    changes = collect_breaking_changes(
        plan=plan,
        plan_type=plan_type,
        schema_results=schema_results,
        current_row=current_row,
        proposed_row=proposed_row,
    )
    row = proposed_row or current_row or catalog_row
    alert = build_breaking_alert(
        asset_slug=asset_slug,
        changes=changes,
        phase=phase,
        row=row,
        plan_type=plan_type,
        pr_number=pr_number,
        pr_url=pr_url,
        run_url=run_url,
    )
    summary = {
        "asset_slug": asset_slug,
        "plan_type": plan_type,
        "phase": phase,
        "has_breaking_changes": alert is not None,
        "breaking_change_count": len(changes),
        "changes": changes,
    }
    if alert:
        summary.update(
            {
                "title": alert["title"],
                "body": alert["body"],
                "fingerprint": alert["fingerprint"],
                "marker": alert["marker"],
            }
        )
    if summary_json is not None:
        summary_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if not alert:
        print("No breaking changes detected; skipping Slack alert.")
        return
    notify(
        title=alert["title"],
        body=alert["body"],
        status=alert["status"],
        dry_run=dry_run,
    )


@app.command("check-schema")
def check_schema(
    asset_slug: str = typer.Option(..., help="Catalog asset slug."),
    dataset_path: Path = typer.Option(..., exists=True, dir_okay=False, help="Local canonical dataset file."),
    snapshot_uri: Optional[str] = typer.Option(None, help="Override remote schema snapshot URI."),
    dry_run: bool = typer.Option(False, help="Print Slack payload instead of posting."),
    upload_snapshot: bool = typer.Option(
        False,
        "--upload-snapshot",
        help=(
            "Write the schema-change log and remote snapshot. Requires "
            f"{ALLOW_CANONICAL_MUTATION_ENV}=1."
        ),
    ),
    skip_snapshot_upload: bool = typer.Option(
        False,
        "--skip-snapshot-upload",
        help="Deprecated compatibility flag. Snapshot upload is skipped unless --upload-snapshot is set.",
    ),
) -> None:
    """Compare a canonical file schema with its last snapshot and warn on any delta."""

    resolved_dry_run = dry_run if isinstance(dry_run, bool) else False
    resolved_upload_snapshot = upload_snapshot if isinstance(upload_snapshot, bool) else False
    resolved_skip_snapshot_upload = skip_snapshot_upload if isinstance(skip_snapshot_upload, bool) else False

    if resolved_dry_run and resolved_upload_snapshot:
        raise typer.BadParameter("--dry-run cannot be combined with --upload-snapshot")
    if resolved_skip_snapshot_upload and resolved_upload_snapshot:
        raise typer.BadParameter("--skip-snapshot-upload cannot be combined with --upload-snapshot")

    fields = schema_for_path(dataset_path)
    uri = snapshot_uri or snapshot_uri_for(asset_slug, os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET))
    previous, generation = load_snapshot(uri)
    write_remote_snapshot = (
        resolved_upload_snapshot
        and not resolved_dry_run
        and not resolved_skip_snapshot_upload
    )
    if write_remote_snapshot:
        require_mutation_allowed(uri, operation="schema snapshot update")
    payload = {
        "asset_slug": asset_slug,
        "source_path": str(dataset_path),
        "updated": dt.datetime.now(dt.UTC).isoformat(),
        "fields": fields,
    }
    if previous:
        diffs = diff_schemas(previous.get("fields", []), fields)
        if diffs:
            old_fields = previous.get("fields", [])
            emit_cloud_logging_warning(
                {
                    "alert_type": "dataset_schema_changed",
                    "asset_slug": asset_slug,
                    "snapshot_uri": uri,
                    "source_path": str(dataset_path),
                    "timestamp": payload["updated"],
                    "change_count": len(diffs),
                    "field_count": len(fields),
                    "changes": diffs,
                    "old_fields": old_fields,
                    "new_fields": fields,
                    "changes_text": truncate_label_text(format_schema_diffs(diffs)),
                    "old_fields_text": truncate_label_text(format_field_list(old_fields)),
                    "new_fields_text": truncate_label_text(format_field_list(fields)),
                },
                dry_run=not write_remote_snapshot,
            )
    if write_remote_snapshot:
        write_snapshot(uri, payload, generation=generation)
    elif not resolved_dry_run:
        print(
            "schema snapshot upload skipped; pass --upload-snapshot from an approved "
            f"{ALLOW_CANONICAL_MUTATION_ENV}=1 runtime to write "
            f"{uri}",
            file=sys.stderr,
        )


@app.command("check-schema-compatibility")
def check_schema_compatibility_cli(
    asset_slug: str = typer.Option(..., help="Catalog asset slug."),
    dataset_path: Path = typer.Option(..., exists=True, dir_okay=False, help="Local canonical dataset file."),
    snapshot_uri: Optional[str] = typer.Option(None, help="Override remote schema snapshot URI."),
    compatibility_waiver: Optional[Path] = typer.Option(
        None,
        "--compatibility-waiver",
        exists=True,
        dir_okay=False,
        help="Reviewed waiver JSON for otherwise blocked schema changes.",
    ),
) -> None:
    """Fail unless a proposed canonical schema is append-compatible or waived."""

    try:
        result = check_schema_compatibility(
            asset_slug=asset_slug,
            dataset_path=dataset_path,
            snapshot_uri=snapshot_uri,
            compatibility_waiver_path=compatibility_waiver,
        )
    except SchemaCompatibilityError as exc:
        print(f"[red]schema compatibility failed:[/red] {exc}", file=sys.stderr)
        raise typer.Exit(2) from exc
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
