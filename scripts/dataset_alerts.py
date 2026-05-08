#!/usr/bin/env python3
"""Dataset upload summaries and schema-change warnings."""

from __future__ import annotations

import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional

import typer
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.gcs_asset import parse_gs_uri
from scripts.slack_notify import notify


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
SCHEMA_SNAPSHOT_PREFIX = "_catalog/schema-snapshots"
SCHEMA_ALERT_LOG_NAME = "shared-datasets-alerts"
SAMPLE_COLUMN_LIMIT = 5
SCHEMA_COMPATIBILITY_BLOCKING_KINDS = {"removed", "renamed", "type_changed"}
SCHEMA_COMPATIBILITY_WARNING_KINDS = {"reordered"}
WAIVER_REQUIRED_TEXT_FIELDS = ("rationale", "consumer_impact", "reviewer", "pr_reference", "migration_path")
OGRINFO_FIELD_RE = re.compile(r"^([^:]+):\s+([A-Za-z][A-Za-z0-9_]*)\b")
OGRINFO_NON_FIELD_NAMES = {
    "INFO",
    "Layer name",
    "Metadata",
    "Geometry",
    "Feature Count",
    "Extent",
    "Layer SRS WKT",
    "Data axis to CRS axis mapping",
}

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


def build_upload_summary(
    *,
    asset_slug: str,
    row: dict[str, str] | None,
    changed_paths: list[str],
    release_path: str | None = None,
    row_count: int | None = None,
    sample_columns: list[str] | None = None,
) -> tuple[str, str, dict[str, str]]:
    title = "New dataset added!"
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
    if result.returncode != 0 and "Unknown option name '-json'" in result.stderr:
        fallback = runner(
            ["ogrinfo", "-ro", "-al", "-so", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        return schema_from_ogrinfo_text(fallback.stdout)
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


def schema_from_ogrinfo_text(text: str) -> list[dict[str, str]]:
    """Parse field names/types from older GDAL ogrinfo -so text output."""
    fields: list[dict[str, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = OGRINFO_FIELD_RE.match(line)
        if not match:
            continue
        name, type_name = match.groups()
        if name in OGRINFO_NON_FIELD_NAMES:
            continue
        fields.append({"name": name, "type": type_name})
    return fields


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
    runner(command, check=True)


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
    dry_run: bool = typer.Option(False, help="Print Slack payload instead of posting."),
) -> None:
    """Post a lightweight dataset upload summary."""

    row = load_catalog().get(asset_slug)
    sample_columns = sample_column or (
        sample_columns_from_schema(schema_for_path(dataset_path)) if dataset_path else None
    )
    title, body, fields = build_upload_summary(
        asset_slug=asset_slug,
        row=row,
        changed_paths=changed_path,
        release_path=release_path,
        row_count=row_count,
        sample_columns=sample_columns,
    )
    notify(title=title, body=body, status="new", fields=fields, dry_run=dry_run)


@app.command("check-schema")
def check_schema(
    asset_slug: str = typer.Option(..., help="Catalog asset slug."),
    dataset_path: Path = typer.Option(..., exists=True, dir_okay=False, help="Local canonical dataset file."),
    snapshot_uri: Optional[str] = typer.Option(None, help="Override remote schema snapshot URI."),
    dry_run: bool = typer.Option(False, help="Print Slack payload instead of posting."),
    skip_snapshot_upload: bool = typer.Option(False, help="Do not write the new snapshot."),
) -> None:
    """Compare a canonical file schema with its last snapshot and warn on any delta."""

    fields = schema_for_path(dataset_path)
    uri = snapshot_uri or snapshot_uri_for(asset_slug, os.environ.get("SHARED_DATASETS_BUCKET", DEFAULT_BUCKET))
    previous, generation = load_snapshot(uri)
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
                dry_run=dry_run,
            )
    if not skip_snapshot_upload and not dry_run:
        write_snapshot(uri, payload, generation=generation)


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
