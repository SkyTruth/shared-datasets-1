"""Each release-lifecycle rule, exercised through `lifecycle.run` with an in-memory job."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from ingestion.common import lifecycle as lc

ATTEMPT = dt.date(2026, 11, 3)
OCTOBER = dt.date(2026, 10, 2)
PUBLISHED_HASH = "a" * 64
CHANGED_HASH = "b" * 64
TOOLS = {"ogr2ogr": {"path": "/usr/bin/ogr2ogr", "version": "GDAL 3.6.2"}}
BASELINE = lc.Baseline(OCTOBER, PUBLISHED_HASH, 1000)
SOURCE = lc.Source(ATTEMPT, "https://example.test/data", "v2", {"source_stats": {"rows": 1000}})


def content(rows=1000, *, lost=0, fingerprint=CHANGED_HASH, hold=None):
    return lc.Content({"source_fingerprint_hash": fingerprint}, rows, lost, hold)


class FakeJob:
    slug = "example-asset"

    def __init__(self, *, baseline=BASELINE, source=SOURCE, content=content(), built_rows=None, built_lost=None,
                 done=None, loss_limit=5):
        self.loss_limit = loss_limit
        self.calls: list[str] = []
        self._baseline = baseline
        self._done = done
        self._source = source
        self._content = content
        self._built_rows = built_rows
        self._built_lost = built_lost

    def resume(self, attempt):
        self.calls.append("resume")
        return self._done

    def baseline(self):
        self.calls.append("baseline")
        if isinstance(self._baseline, Exception):
            raise self._baseline
        return self._baseline

    def acquire(self, attempt):
        self.calls.append("acquire")
        return self._source

    def describe(self, source, baseline):
        self.calls.append("describe")
        return self._content

    def build(self, source, content, baseline):
        self.calls.append("build")
        rows = self._built_rows if self._built_rows is not None else content.rows
        return lc.Built(rows, TOOLS, "gs://bucket/root/releases/2026-11-03/", self._built_lost)

    def publish(self, built, record):
        self.calls.append("publish")
        return record

    def check_in(self, record):
        self.calls.append("check_in")
        return record

    def hold(self, reason, source, content, built):
        self.calls.append("hold")
        return {"status": "held", "reason": reason, "candidate": built is not None}


def test_changed_source_runs_every_stage_in_order_and_publishes_a_complete_record():
    job = FakeJob(content=content(lost=7))
    record = lc.run(job, ATTEMPT)
    assert job.calls == ["resume", "baseline", "acquire", "describe", "build", "publish"]
    assert record == {
        "schema_version": 1, "asset_slug": "example-asset", "status": "success",
        "run_date": "2026-11-03", "release_date": "2026-11-03",
        "source": "https://example.test/data", "source_version": "v2", "source_stats": {"rows": 1000},
        "source_fingerprint_hash": CHANGED_HASH, "row_count": 1000, "lost_objects": 7, "tools": TOOLS,
        "release_path": "gs://bucket/root/releases/2026-11-03/",
    }


def test_completed_attempt_returns_before_any_source_read():
    job = FakeJob(done={"status": "success"})
    assert lc.run(job, ATTEMPT) == {"status": "success"}
    assert job.calls == ["resume"]


def test_broken_baseline_stops_before_any_source_read():
    job = FakeJob(baseline=RuntimeError("published manifest failed its contract"))
    with pytest.raises(RuntimeError, match="failed its contract"):
        lc.run(job, ATTEMPT)
    assert job.calls == ["resume", "baseline"]


def test_unavailable_source_checks_in_against_the_missing_release():
    job = FakeJob(source=lc.Unavailable(dt.date(2026, 11, 1), "November file not published yet"))
    record = lc.run(job, ATTEMPT)
    assert job.calls == ["resume", "baseline", "acquire", "check_in"]
    assert (record["status"], record["run_date"], record["release_date"]) == ("skipped", "2026-11-03", "2026-11-01")
    assert record["reason"] == "November file not published yet"


def test_unchanged_fingerprint_checks_in_against_the_baseline_and_builds_nothing():
    job = FakeJob(content=content(fingerprint=PUBLISHED_HASH))
    record = lc.run(job, ATTEMPT)
    assert job.calls == ["resume", "baseline", "acquire", "describe", "check_in"]
    assert record["reason"] == lc.UNCHANGED
    assert (record["run_date"], record["release_date"]) == ("2026-11-03", "2026-10-02")
    assert record["row_count"] == 1000


def test_release_published_before_fingerprints_never_counts_as_unchanged():
    job = FakeJob(baseline=lc.Baseline(OCTOBER, None, 1000))
    assert lc.run(job, ATTEMPT)["status"] == "success"


def test_first_publication_has_no_baseline_to_compare_or_gate_against():
    job = FakeJob(baseline=None, content=content(lost=None))
    record = lc.run(job, ATTEMPT)
    assert record["status"] == "success" and "lost_objects" not in record
    assert job.calls[-1] == "publish"


@pytest.mark.parametrize("release", [OCTOBER, dt.date(2026, 9, 2)])
def test_changed_source_never_replaces_or_predates_a_published_release(release):
    job = FakeJob(source=lc.Source(release, "https://example.test/data", "v2"))
    with pytest.raises(lc.LifecycleError, match="cannot replace or predate"):
        lc.run(job, ATTEMPT)
    assert "build" not in job.calls


def test_empty_source_holds_without_building():
    job = FakeJob(content=content(0))
    assert lc.run(job, ATTEMPT) == {"status": "held", "reason": lc.EMPTY_SOURCE, "candidate": False}
    assert job.calls == ["resume", "baseline", "acquire", "describe", "hold"]


@pytest.mark.parametrize(("lost", "held"), [(50, False), (51, True)])
def test_lost_objects_beyond_the_limit_hold_the_built_candidate(lost, held):
    job = FakeJob(content=content(1000 - lost, lost=lost))
    result = lc.run(job, ATTEMPT)
    assert job.calls[-2:] == (["build", "hold"] if held else ["build", "publish"])
    if held:
        assert result == {"status": "held", "reason": lc.OBJECTS_LOST, "candidate": True}


@pytest.mark.parametrize("rows", [1000, 1500])
def test_additions_never_offset_lost_objects(rows):
    job = FakeJob(content=content(rows, lost=200))
    assert lc.run(job, ATTEMPT)["reason"] == lc.OBJECTS_LOST
    assert "publish" not in job.calls


@pytest.mark.parametrize(("built_lost", "outcome"), [(3, "publish"), (51, "hold")])
def test_objects_lost_can_be_counted_during_the_build(built_lost, outcome):
    job = FakeJob(content=content(lost=None), built_lost=built_lost)
    result = lc.run(job, ATTEMPT)
    assert job.calls[-1] == outcome
    assert result.get("lost_objects", built_lost) == built_lost


def test_asset_with_a_loss_limit_must_count_its_lost_objects():
    job = FakeJob(content=content(lost=None))
    with pytest.raises(lc.LifecycleError, match="must count"):
        lc.run(job, ATTEMPT)
    assert "publish" not in job.calls and "hold" not in job.calls


def test_asset_without_a_percentage_rule_publishes_through_heavy_turnover():
    job = FakeJob(content=content(100, lost=None), loss_limit=None)
    record = lc.run(job, ATTEMPT)
    assert record["status"] == "success" and "lost_objects" not in record


def test_rows_known_only_after_the_build_are_not_compared_with_the_source():
    job = FakeJob(content=content(None, lost=None), built_rows=12, loss_limit=None)
    assert lc.run(job, ATTEMPT)["row_count"] == 12


def test_unresolved_hold_blocks_both_the_unchanged_skip_and_publication():
    job = FakeJob(baseline=lc.Baseline(OCTOBER, PUBLISHED_HASH, 1000, hold=lc.OBJECTS_LOST),
                  content=content(fingerprint=PUBLISHED_HASH))
    assert lc.run(job, ATTEMPT)["reason"] == lc.OBJECTS_LOST
    assert job.calls[-2:] == ["build", "hold"]


def test_job_specific_reason_holds():
    job = FakeJob(content=content(hold="source_selection_changed"))
    assert lc.run(job, ATTEMPT)["reason"] == "source_selection_changed"
    assert "publish" not in job.calls


def test_built_rows_must_equal_source_rows():
    job = FakeJob(built_rows=999)
    with pytest.raises(lc.LifecycleError, match="built row count"):
        lc.run(job, ATTEMPT)
    assert "publish" not in job.calls and "hold" not in job.calls


@pytest.mark.parametrize(("change", "message"), [
    ({"release_date": None}, "release_date"),
    ({"run_date": None}, "run_date"),
    ({"status": "failed"}, "status"),
    ({"row_count": True}, "row_count"),
    ({"row_count": 0}, "row_count"),
    ({"tools": {}}, "native tool"),
    ({"tools": {"ogr2ogr": {"path": "/usr/bin/ogr2ogr"}}}, "native tool"),
    ({"source_fingerprint_hash": "abc"}, "fingerprint"),
    ({"schema_version": 2}, "schema_version"),
    ({"asset_slug": ""}, "asset"),
])
def test_incomplete_success_record_is_rejected_before_publication(change, message):
    record = lc.run(FakeJob(), ATTEMPT)
    with pytest.raises(lc.LifecycleError, match=message):
        lc.check({**record, **change})


def test_skipped_record_needs_a_reason_and_an_unchanged_one_needs_its_fingerprint():
    skipped = {"schema_version": 1, "asset_slug": "example-asset", "status": "skipped",
               "run_date": "2026-11-03", "release_date": "2026-10-02"}
    with pytest.raises(lc.LifecycleError, match="reason"):
        lc.check(skipped)
    with pytest.raises(lc.LifecycleError, match="fingerprint"):
        lc.check({**skipped, "reason": lc.UNCHANGED})
    assert lc.check({**skipped, "reason": "upstream not ready"})["status"] == "skipped"


def test_loss_limit_reads_the_per_asset_policy_with_a_default(tmp_path, monkeypatch):
    assert lc.loss_limit("mpatlas-zone-assessments") == lc.DEFAULT_LOSS_LIMIT == 5
    policy = tmp_path / "ingestion-quality.json"
    monkeypatch.setattr(lc, "POLICY_FILE", policy)
    policy.write_text(json.dumps({"schema_version": 1, "assets": {
        "seasonal": {"maximum_lost_object_percent": None},
        "strict": {"maximum_lost_object_percent": 1},
        "broken": {"maximum_lost_object_percent": 100}}}))
    assert lc.loss_limit("seasonal") is None
    assert lc.loss_limit("strict") == 1
    assert lc.loss_limit("unlisted") == lc.DEFAULT_LOSS_LIMIT
    with pytest.raises(lc.LifecycleError, match="lost-object limit"):
        lc.loss_limit("broken")
