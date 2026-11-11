"""The release lifecycle: one order of operations for every updating dataset.

`run` is the rulebook. A job supplies the stages; their order and the rules
between them are defined here and nowhere else.

    resume    A completed or interrupted publication for this attempt ends the
              run before any source read. A rerun never replaces a release.
    baseline  The latest completed, verified publication. A skip, a failed
              attempt or a check-in is never a baseline.
    acquire   Fetch the source, or report that upstream has not published it.
    describe  Normalize and fingerprint. An unchanged fingerprint publishes
              nothing: the run records a check-in against the baseline.
    build     Only changed, non-empty content is built. Built rows equal source
              rows.
    gate      An empty source, more lost objects than the asset's limit allows,
              a job-specific reason or an unresolved earlier hold stops
              publication for a human decision; the built candidate is kept.
              A lost object is a baseline feature absent from the new release.
              Additions never offset losses, so an equal-sized swap counts in
              full.
    publish   Changed content never replaces an existing release. The record
              is complete before the publisher claims or writes anything.

Every record names two dates. `run_date` is the attempt. `release_date` is the
release the record is about: the new release on success, the baseline that
stays current on an unchanged check-in, and the release upstream has not
produced when the source is unavailable.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

SCHEMA_VERSION = 1
UNCHANGED = "source fingerprint unchanged"
EMPTY_SOURCE = "empty_source"
OBJECTS_LOST = "objects_lost"
DEFAULT_LOSS_LIMIT = 5
POLICY_FILE = Path(__file__).resolve().parents[2] / "catalog/ingestion-quality.json"
_SHA256 = re.compile(r"[0-9a-f]{64}")


class LifecycleError(RuntimeError):
    """A lifecycle rule was broken; nothing further may be written."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise LifecycleError(message)


# Records ---------------------------------------------------------------------


def check(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a run record before any publisher sees it."""
    require(record.get("schema_version") == SCHEMA_VERSION, "unsupported run record schema_version")
    require(isinstance(record.get("asset_slug"), str) and bool(record["asset_slug"]), "run record names no asset")
    for name in ("run_date", "release_date"):
        try:
            dt.date.fromisoformat(record.get(name))
        except (TypeError, ValueError):
            raise LifecycleError(f"run record {name} is not an ISO date") from None
    status = record.get("status")
    if status == "skipped":
        require(isinstance(record.get("reason"), str) and bool(record["reason"]), "skipped run record gives no reason")
        if record["reason"] == UNCHANGED:
            require(_is_sha256(record.get("source_fingerprint_hash")), "unchanged check-in names no fingerprint")
        return dict(record)
    require(status == "success", "run record status must be success or skipped")
    for name in ("source", "source_version", "release_path"):
        require(isinstance(record.get(name), str) and bool(record[name]), f"successful run record is missing {name}")
    require(type(record.get("row_count")) is int and record["row_count"] > 0, "successful run record needs a positive row_count")
    require(_is_sha256(record.get("source_fingerprint_hash")), "successful run record names no fingerprint")
    tools = record.get("tools")
    require(isinstance(tools, Mapping) and bool(tools) and all(
        isinstance(tool, Mapping) and tool.get("path") and tool.get("version") for tool in tools.values()
    ), "successful run record must name each native tool's path and version")
    return dict(record)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256.fullmatch(value))


def _record(slug: str, status: str, attempt: dt.date, release: dt.date, **fields: Any) -> dict[str, Any]:
    return check({"schema_version": SCHEMA_VERSION, "asset_slug": slug, "status": status,
                  "run_date": attempt.isoformat(), "release_date": release.isoformat(), **fields})


def published(slug: str, *, attempt: dt.date, source: Source, content: Content, built: Built,
              lost: int | None = None) -> dict[str, Any]:
    counted = {} if lost is None else {"lost_objects": lost}
    return _record(slug, "success", attempt, source.release, source=source.uri, source_version=source.version,
                   row_count=built.rows, tools=dict(built.tools), release_path=built.release_path,
                   **counted, **source.extra, **content.fingerprint)


def unchanged(slug: str, *, attempt: dt.date, baseline: Baseline, source: Source, content: Content) -> dict[str, Any]:
    return _record(slug, "skipped", attempt, baseline.release, reason=UNCHANGED, source=source.uri,
                   source_version=source.version, row_count=baseline.rows, **source.extra, **content.fingerprint)


def unavailable(slug: str, *, attempt: dt.date, missing: Unavailable) -> dict[str, Any]:
    return _record(slug, "skipped", attempt, missing.release, reason=missing.reason)


# Gate ------------------------------------------------------------------------


def loss_limit(slug: str) -> int | None:
    """Largest tolerated share of baseline objects lost by one release, in percent.

    An asset absent from the policy file gets the default. An asset listed with
    `null` has no percentage rule: its objects legitimately turn over.
    """
    policy = json.loads(POLICY_FILE.read_text())
    require(policy["schema_version"] == 1, "unsupported ingestion quality policy")
    limit = policy["assets"].get(slug, {}).get("maximum_lost_object_percent", DEFAULT_LOSS_LIMIT)
    require(limit is None or type(limit) is int and 0 <= limit < 100, "invalid lost-object limit")
    return limit


def exceeds(lost: int, baseline_rows: int, limit: int) -> bool:
    return lost * 100 > baseline_rows * limit


# Stages ----------------------------------------------------------------------
# A job may subclass these to carry its own paths and handles between stages.


@dataclass(frozen=True)
class Baseline:
    release: dt.date
    fingerprint: str | None  # None: published before fingerprints; equal to nothing
    rows: int
    hold: str | None = None  # unresolved reason from an earlier attempt


@dataclass(frozen=True)
class Unavailable:
    release: dt.date  # the release upstream has not produced
    reason: str


@dataclass(frozen=True)
class Source:
    release: dt.date  # the release these bytes would become
    uri: str
    version: str
    extra: Mapping[str, Any] = field(default_factory=dict)  # job-specific record fields


@dataclass(frozen=True)
class Content:
    fingerprint: Mapping[str, Any]  # Fingerprint.record_fields(), or the hash alone
    rows: int | None  # None when rows exist only after the build (raster sources)
    lost: int | None = None  # baseline objects absent from this source, when its keys are the identity
    hold: str | None = None  # job-specific gate reason


@dataclass(frozen=True)
class Built:
    rows: int
    tools: Mapping[str, Mapping[str, str]]
    release_path: str
    lost: int | None = None  # baseline feature IDs retired, when identity is assigned during the build


class Job(Protocol):
    slug: str
    loss_limit: int | None

    def resume(self, attempt: dt.date) -> dict[str, Any] | None:
        """Return the finished result for this attempt; raise on a partial publication."""

    def baseline(self) -> Baseline | None:
        """Return the verified latest publication, or None before the first one."""

    def acquire(self, attempt: dt.date) -> Source | Unavailable: ...

    def describe(self, source: Source, baseline: Baseline | None) -> Content: ...

    def build(self, source: Source, content: Content, baseline: Baseline | None) -> Built: ...

    def publish(self, built: Built, record: dict[str, Any]) -> dict[str, Any]: ...

    def check_in(self, record: dict[str, Any]) -> dict[str, Any]:
        """Record an attempt that published nothing."""

    def hold(self, reason: str, source: Source, content: Content, built: Built | None) -> dict[str, Any]:
        """Persist the hold, retain the candidate and ask for a decision."""


def run(job: Job, attempt: dt.date) -> dict[str, Any]:
    done = job.resume(attempt)
    if done is not None:
        return done

    baseline = job.baseline()
    source = job.acquire(attempt)
    if isinstance(source, Unavailable):
        return job.check_in(unavailable(job.slug, attempt=attempt, missing=source))

    content = job.describe(source, baseline)
    held = baseline.hold if baseline else None
    if baseline and not held and content.fingerprint["source_fingerprint_hash"] == baseline.fingerprint:
        return job.check_in(unchanged(job.slug, attempt=attempt, baseline=baseline, source=source, content=content))
    require(baseline is None or source.release > baseline.release,
            "changed source cannot replace or predate a published release")

    if content.rows == 0:
        return job.hold(EMPTY_SOURCE, source, content, None)
    built = job.build(source, content, baseline)
    require(content.rows is None or built.rows == content.rows, "built row count differs from the source row count")

    lost = content.lost if content.lost is not None else built.lost
    limited = baseline is not None and job.loss_limit is not None
    require(not limited or lost is not None, "an asset with a loss limit must count the baseline objects it lost")
    reason = held or content.hold or (OBJECTS_LOST if limited and exceeds(lost, baseline.rows, job.loss_limit) else None)
    if reason:
        return job.hold(reason, source, content, built)
    return job.publish(built, published(job.slug, attempt=attempt, source=source, content=content, built=built, lost=lost))
