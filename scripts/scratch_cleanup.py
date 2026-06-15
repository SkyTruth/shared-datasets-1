#!/usr/bin/env python3
"""Audit and clean stale shared-datasets scratch publish prefixes."""

from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Keep direct invocation (`python scripts/scratch_cleanup.py ...`) equivalent to
# module/imported use by making repo-root packages importable.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import typer
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage
from rich import print

from scripts.gcs_asset import APPROVED_DATA_EXTENSIONS, get_client


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
PENDING_PREFIX = "_scratch/pending-publishes/"
WARNING_PREFIX = "_scratch/cleanup-audit/pending-publishes/"
DEFAULT_WARN_AGE_DAYS = 60
DEFAULT_DELETE_AGE_DAYS = 90

app = typer.Typer(no_args_is_help=True)


@dataclass(frozen=True)
class BlobRecord:
    name: str
    size: int
    updated: dt.datetime
    generation: str
    crc32c: str


@dataclass(frozen=True)
class WarningMarker:
    name: str
    generation: str
    newest_object_name: str
    newest_generation: str
    newest_updated: str


@dataclass(frozen=True)
class PendingProposal:
    asset_slug: str
    proposal_id: str
    prefix: str
    blobs: tuple[BlobRecord, ...]

    @property
    def newest_blob(self) -> BlobRecord:
        return max(self.blobs, key=lambda blob: blob.updated)

    @property
    def total_size(self) -> int:
        return sum(blob.size for blob in self.blobs)

    @property
    def marker_name(self) -> str:
        return f"{WARNING_PREFIX}{self.asset_slug}/{self.proposal_id}.json"


def ensure_aware_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def blob_record(blob: storage.Blob) -> BlobRecord:
    updated = blob.updated
    if updated is None:
        updated = dt.datetime.fromtimestamp(0, tz=dt.UTC)
    return BlobRecord(
        name=blob.name,
        size=int(blob.size or 0),
        updated=ensure_aware_utc(updated),
        generation=str(blob.generation),
        crc32c=str(blob.crc32c or ""),
    )


def parse_pending_name(name: str) -> tuple[str, str] | None:
    if not name.startswith(PENDING_PREFIX):
        return None
    rest = name.removeprefix(PENDING_PREFIX)
    parts = rest.split("/", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1]


def group_pending_blobs(blobs: list[BlobRecord]) -> list[PendingProposal]:
    grouped: dict[tuple[str, str], list[BlobRecord]] = defaultdict(list)
    for blob in blobs:
        parsed = parse_pending_name(blob.name)
        if parsed is None:
            continue
        grouped[parsed].append(blob)

    proposals: list[PendingProposal] = []
    for (asset_slug, proposal_id), proposal_blobs in sorted(grouped.items()):
        prefix = f"{PENDING_PREFIX}{asset_slug}/{proposal_id}/"
        proposals.append(
            PendingProposal(
                asset_slug=asset_slug,
                proposal_id=proposal_id,
                prefix=prefix,
                blobs=tuple(sorted(proposal_blobs, key=lambda blob: blob.name)),
            )
        )
    return proposals


def asset_roots_by_slug(catalog_path: Path, *, bucket: str) -> dict[str, str]:
    roots: dict[str, str] = {}
    with catalog_path.open(newline="") as file_obj:
        for row in csv.DictReader(file_obj):
            slug = (row.get("asset_slug") or "").strip()
            canonical_path = (row.get("canonical_path") or "").strip()
            prefix = f"gs://{bucket}/"
            if not slug or not canonical_path.startswith(prefix):
                continue
            object_name = canonical_path.removeprefix(prefix)
            if "/latest/" in object_name:
                roots[slug] = object_name.split("/latest/", 1)[0]
            elif "/releases/" in object_name:
                roots[slug] = object_name.split("/releases/", 1)[0]
    return roots


def is_release_data_match_candidate(blob: BlobRecord) -> bool:
    return Path(blob.name).suffix.lower() in APPROVED_DATA_EXTENSIONS and bool(blob.crc32c)


def proposal_has_matching_release(
    proposal: PendingProposal,
    release_blobs: list[BlobRecord],
) -> bool:
    releases_by_name: dict[str, list[BlobRecord]] = defaultdict(list)
    for release_blob in release_blobs:
        releases_by_name[Path(release_blob.name).name].append(release_blob)

    for scratch_blob in proposal.blobs:
        if not is_release_data_match_candidate(scratch_blob):
            continue
        for release_blob in releases_by_name.get(Path(scratch_blob.name).name, []):
            if (
                scratch_blob.size == release_blob.size
                and scratch_blob.crc32c
                and scratch_blob.crc32c == release_blob.crc32c
            ):
                return True
    return False


def marker_matches_current_state(marker: WarningMarker | None, proposal: PendingProposal) -> bool:
    if marker is None:
        return False
    newest = proposal.newest_blob
    return (
        marker.newest_object_name == newest.name
        and marker.newest_generation == newest.generation
        and marker.newest_updated == newest.updated.isoformat()
    )


def classify_proposal(
    proposal: PendingProposal,
    *,
    now: dt.datetime,
    warn_age_days: int,
    delete_age_days: int,
    has_matching_release: bool,
    warning_marker: WarningMarker | None,
) -> dict[str, object]:
    newest = proposal.newest_blob
    age_days = (ensure_aware_utc(now) - newest.updated).total_seconds() / 86400
    warned_for_current_state = marker_matches_current_state(warning_marker, proposal)
    base = {
        "asset_slug": proposal.asset_slug,
        "proposal_id": proposal.proposal_id,
        "prefix": proposal.prefix,
        "object_count": len(proposal.blobs),
        "total_size": proposal.total_size,
        "newest_object": newest.name,
        "newest_generation": newest.generation,
        "newest_updated": newest.updated.isoformat(),
        "age_days": round(age_days, 2),
    }

    if has_matching_release:
        return {**base, "action": "delete", "reason": "matching-release"}
    if age_days >= delete_age_days and warned_for_current_state:
        return {**base, "action": "delete", "reason": "stale-after-warning"}
    if age_days >= warn_age_days and not warned_for_current_state:
        return {
            **base,
            "action": "warn",
            "reason": "stale-warning",
            "delete_after": (newest.updated + dt.timedelta(days=delete_age_days)).isoformat(),
        }
    return {**base, "action": "keep", "reason": "not-eligible"}


def list_records(client: storage.Client, *, bucket: str, prefix: str) -> list[BlobRecord]:
    return [blob_record(blob) for blob in client.list_blobs(bucket, prefix=prefix)]


def load_warning_markers(client: storage.Client, *, bucket: str) -> dict[tuple[str, str], WarningMarker]:
    markers: dict[tuple[str, str], WarningMarker] = {}
    for blob in client.list_blobs(bucket, prefix=WARNING_PREFIX):
        if not blob.name.endswith(".json"):
            continue
        rest = blob.name.removeprefix(WARNING_PREFIX)
        parts = rest.split("/", 1)
        if len(parts) != 2 or not parts[1].endswith(".json"):
            continue
        asset_slug = parts[0]
        proposal_id = parts[1].removesuffix(".json")
        try:
            payload = json.loads(blob.download_as_text())
        except json.JSONDecodeError:
            continue
        markers[(asset_slug, proposal_id)] = WarningMarker(
            name=blob.name,
            generation=str(blob.generation),
            newest_object_name=str(payload.get("newest_object_name", "")),
            newest_generation=str(payload.get("newest_generation", "")),
            newest_updated=str(payload.get("newest_updated", "")),
        )
    return markers


def release_records_for_proposals(
    client: storage.Client,
    *,
    bucket: str,
    catalog_path: Path,
    proposals: list[PendingProposal],
) -> dict[str, list[BlobRecord]]:
    asset_roots = asset_roots_by_slug(catalog_path, bucket=bucket)
    needed_slugs = {proposal.asset_slug for proposal in proposals}
    records: dict[str, list[BlobRecord]] = {}
    for asset_slug in sorted(needed_slugs):
        asset_root = asset_roots.get(asset_slug)
        if not asset_root:
            records[asset_slug] = []
            continue
        records[asset_slug] = list_records(client, bucket=bucket, prefix=f"{asset_root}/releases/")
    return records


def write_warning_marker(
    client: storage.Client,
    *,
    bucket: str,
    proposal: PendingProposal,
    now: dt.datetime,
    existing_marker: WarningMarker | None,
) -> None:
    newest = proposal.newest_blob
    payload = {
        "asset_slug": proposal.asset_slug,
        "proposal_id": proposal.proposal_id,
        "prefix": proposal.prefix,
        "warned_at": ensure_aware_utc(now).isoformat(),
        "newest_object_name": newest.name,
        "newest_generation": newest.generation,
        "newest_updated": newest.updated.isoformat(),
    }
    blob = client.bucket(bucket).blob(proposal.marker_name)
    kwargs = {"if_generation_match": 0}
    if existing_marker is not None:
        kwargs = {"if_generation_match": int(existing_marker.generation)}
    blob.upload_from_string(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        content_type="application/json",
        **kwargs,
    )


def delete_blob_record(client: storage.Client, *, bucket: str, blob: BlobRecord) -> None:
    client.bucket(bucket).blob(blob.name).delete(if_generation_match=int(blob.generation))


def delete_warning_marker(
    client: storage.Client,
    *,
    bucket: str,
    marker: WarningMarker | None,
) -> None:
    if marker is None:
        return
    try:
        client.bucket(bucket).blob(marker.name).delete(if_generation_match=int(marker.generation))
    except NotFound:
        return


def append_markdown_summary(path: Path, summary: dict[str, object]) -> None:
    lines = [
        "## Scratch Cleanup Audit",
        "",
        f"- Pending prefixes scanned: `{summary['proposal_count']}`",
        f"- Prefixes warned: `{len(summary['warnings'])}`",
        f"- Prefixes deleted: `{len(summary['deletions'])}`",
        f"- Prefixes kept: `{len(summary['kept'])}`",
        f"- Dry run: `{summary['dry_run']}`",
    ]
    for heading, key in (("Warnings", "warnings"), ("Deletions", "deletions")):
        items = summary[key]
        if not items:
            continue
        lines.extend(["", f"### {heading}"])
        for item in items[:20]:
            lines.append(f"- `{item['prefix']}`: `{item['reason']}`")
        remaining = len(items) - 20
        if remaining > 0:
            lines.append(f"- plus {remaining} more")
    with path.open("a") as file_obj:
        file_obj.write("\n".join(lines) + "\n")


def run_cleanup(
    *,
    client: storage.Client,
    bucket: str,
    catalog_path: Path,
    now: dt.datetime,
    warn_age_days: int,
    delete_age_days: int,
    apply_changes: bool,
    summary_path: Path | None = None,
) -> dict[str, object]:
    pending_blobs = list_records(client, bucket=bucket, prefix=PENDING_PREFIX)
    proposals = group_pending_blobs(pending_blobs)
    warning_markers = load_warning_markers(client, bucket=bucket)
    releases_by_slug = release_records_for_proposals(
        client,
        bucket=bucket,
        catalog_path=catalog_path,
        proposals=proposals,
    )

    decisions: list[dict[str, object]] = []
    proposals_by_key = {(proposal.asset_slug, proposal.proposal_id): proposal for proposal in proposals}
    for proposal in proposals:
        marker = warning_markers.get((proposal.asset_slug, proposal.proposal_id))
        has_match = proposal_has_matching_release(proposal, releases_by_slug.get(proposal.asset_slug, []))
        decisions.append(
            classify_proposal(
                proposal,
                now=now,
                warn_age_days=warn_age_days,
                delete_age_days=delete_age_days,
                has_matching_release=has_match,
                warning_marker=marker,
            )
        )

    warnings = [decision for decision in decisions if decision["action"] == "warn"]
    deletions = [decision for decision in decisions if decision["action"] == "delete"]
    kept = [decision for decision in decisions if decision["action"] == "keep"]

    if apply_changes:
        for warning in warnings:
            proposal = proposals_by_key[(str(warning["asset_slug"]), str(warning["proposal_id"]))]
            marker = warning_markers.get((proposal.asset_slug, proposal.proposal_id))
            write_warning_marker(
                client,
                bucket=bucket,
                proposal=proposal,
                now=now,
                existing_marker=marker,
            )

        deleted_object_count = 0
        for deletion in deletions:
            proposal = proposals_by_key[(str(deletion["asset_slug"]), str(deletion["proposal_id"]))]
            for blob in proposal.blobs:
                try:
                    delete_blob_record(client, bucket=bucket, blob=blob)
                    deleted_object_count += 1
                except PreconditionFailed:
                    print(f"[red]Skipped changed object during cleanup:[/red] gs://{bucket}/{blob.name}")
            delete_warning_marker(
                client,
                bucket=bucket,
                marker=warning_markers.get((proposal.asset_slug, proposal.proposal_id)),
            )
    else:
        deleted_object_count = 0

    summary = {
        "bucket": bucket,
        "pending_prefix": PENDING_PREFIX,
        "proposal_count": len(proposals),
        "warning_count": len(warnings),
        "deletion_count": len(deletions),
        "deleted_object_count": deleted_object_count,
        "dry_run": not apply_changes,
        "warnings": warnings,
        "deletions": deletions,
        "kept": kept,
    }
    if summary_path is not None:
        append_markdown_summary(summary_path, summary)
    return summary


@app.command("run")
def run_command(
    bucket: str = typer.Option(DEFAULT_BUCKET, help="Shared datasets bucket name."),
    catalog: Path = typer.Option(
        Path("catalog/shared-datasets-catalog.csv"),
        exists=True,
        dir_okay=False,
        help="Catalog CSV used to locate canonical release roots.",
    ),
    warn_age_days: int = typer.Option(DEFAULT_WARN_AGE_DAYS, min=1, help="Warn when newest prefix object is this old."),
    delete_age_days: int = typer.Option(
        DEFAULT_DELETE_AGE_DAYS,
        min=1,
        help="Delete stale prefixes after this age, but only after a matching warning marker.",
    ),
    apply_changes: bool = typer.Option(False, "--apply/--dry-run", help="Apply warning markers and deletions."),
    summary_path: Optional[Path] = typer.Option(None, help="Append a Markdown summary to this path."),
) -> None:
    """Audit pending-publish scratch prefixes and optionally clean eligible ones."""
    if warn_age_days >= delete_age_days:
        raise typer.BadParameter("warn-age-days must be less than delete-age-days")
    summary = run_cleanup(
        client=get_client(),
        bucket=bucket,
        catalog_path=catalog,
        now=dt.datetime.now(dt.UTC),
        warn_age_days=warn_age_days,
        delete_age_days=delete_age_days,
        apply_changes=apply_changes,
        summary_path=summary_path,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    app()
