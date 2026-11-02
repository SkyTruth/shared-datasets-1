"""Exercise real transcript JSON/ZIP boundaries with native API transport doubles."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import zipfile

import pytest

from scripts import incident_verification_cache as native
from scripts import operational_snapshot as snapshots
from test_incident_verification_cache import GH_VERSION, NativeArtifacts, PREFIX
from test_slack_incidents import IDENTITY, SHA


POLICY = {"head_sha": SHA, "gh_version": GH_VERSION, "since": "2026-10-01T00:00:00Z"}
INVENTORY = {"sources": [{"id": 11, "run_attempt": 1}], "catalog": [{"name": "asset.json", "generation": "1"}]}
DEPLOYMENTS = PREFIX + "/deployments?environment=slack-incidents&task=slack-incident&per_page=100"
STATUSES = PREFIX + "/deployments/10/statuses?per_page=100"
JOBS = PREFIX + "/actions/runs/11/attempts/1/jobs?per_page=100"


class SafetyArtifacts(NativeArtifacts):
    def __init__(self):
        super().__init__()
        self.producer.update(path=snapshots.WORKFLOW, event="schedule")
        self.current.update(path=snapshots.WORKFLOW, event="schedule")
        self.workflow["path"] = snapshots.WORKFLOW
        self.job["name"] = snapshots.JOB
        self.job["steps"] = [{"name": name, "status": "completed", "conclusion": "success"}
                             for name in (snapshots.PROBE_STEP, snapshots.UPLOAD_STEP)]
        self.artifact["name"] = snapshots.artifact_name(self.producer)


def capture(api):
    recording = snapshots.RecordingAPI(api)
    recording.get(PREFIX)
    recording.get(PREFIX + "/actions/runs/11/attempts/1")
    recording.pages(DEPLOYMENTS)
    recording.pages(STATUSES)
    recording.pages(JOBS, field="jobs")
    return recording


@pytest.fixture
def retained(tmp_path):
    api = SafetyArtifacts()
    recording = capture(api)
    snapshot = snapshots.Snapshot.capture(api.producer, POLICY, IDENTITY, INVENTORY, recording)
    api.retain(snapshot.save(tmp_path, api.producer))
    api.reads.clear()
    return api, snapshot


def restore(api, **changes):
    values = {"run": api.current, "policy": POLICY, "identity": IDENTITY, "inventory": INVENTORY}
    values.update(changes)
    return snapshots.Snapshot.restore(api, **values)


def test_native_roundtrip_matches_every_actual_get_and_pages_response(retained):
    api, original = retained
    snapshot = restore(api)
    api.reads.clear()
    assert snapshot.matches(api)
    assert api.reads == [item["path"] for item in original.data["requests"]]
    assert api.archive_reads == [api.artifact["id"]]
    assert {item["field"] for item in snapshot.data["requests"]} == {None, "jobs"}


@pytest.mark.parametrize("change", ["record", "status", "emitter", "repository", "job"])
def test_changed_input_prevents_idle_without_replaying_old_responses(retained, change):
    api, _ = retained
    snapshot = restore(api)
    target = {"record": api.record, "status": api.statuses[0], "emitter": api.emitter,
              "repository": api.repo, "job": api.job}[change]
    target["changed"] = True
    api.reads.clear()
    assert snapshot.matches(api) is False
    assert len(api.reads) == len(snapshot.data["requests"])


def test_live_failure_is_not_hidden_after_an_earlier_changed_response(retained):
    api, _ = retained
    snapshot = restore(api)
    api.repo["changed"] = True
    api.read_error = RuntimeError("current emitter unavailable")
    with pytest.raises(RuntimeError, match="current emitter unavailable"):
        snapshot.matches(api)


def test_source_job_history_is_fetched_and_unavailability_remains_fatal(retained, monkeypatch):
    api, _ = retained
    snapshot = restore(api)
    original = api.pages

    def unavailable_jobs(path, field=None):
        if path == JOBS:
            raise RuntimeError("current source jobs unavailable")
        return original(path, field=field)

    monkeypatch.setattr(api, "pages", unavailable_jobs)
    with pytest.raises(RuntimeError, match="current source jobs unavailable"):
        snapshot.matches(api)


def test_conflicting_duplicate_capture_is_ineligible_but_unchanged_duplicate_is_deduplicated():
    api = SafetyArtifacts()
    recording = snapshots.RecordingAPI(api)
    recording.get(PREFIX)
    recording.get(PREFIX)
    assert len(recording.requests) == 1
    assert snapshots.Snapshot.capture(api.producer, POLICY, IDENTITY, INVENTORY, recording)
    api.repo["changed"] = True
    recording.get(PREFIX)
    assert snapshots.Snapshot.capture(api.producer, POLICY, IDENTITY, INVENTORY, recording) is None


def test_mutations_are_rejected_before_transport_and_cannot_be_exported():
    class NoWrites:
        def post(self, *_):
            pytest.fail("mutation reached transport")
    recording = snapshots.RecordingAPI(NoWrites())
    with pytest.raises(snapshots.SnapshotWrite):
        recording.post(PREFIX + "/deployments", {})
    api = SafetyArtifacts()
    assert snapshots.Snapshot.capture(api.producer, POLICY, IDENTITY, INVENTORY, recording) is None


@pytest.mark.parametrize("method,path,field", [
    ("get", "https://evil.example/token", None),
    ("get", "repos/SkyTruth/other", None),
    ("get", PREFIX + "-other", None),
    ("get", PREFIX + "/../other", None),
    ("get", PREFIX + "/actions/runs/1\n", None),
    ("get", PREFIX + "/actions/runs/1?token=secret", None),
    ("post", PREFIX, None),
    ("get", PREFIX, "jobs"),
    ("pages", JOBS, None),
    ("pages", STATUSES, "jobs"),
    ("pages", DEPLOYMENTS + "&per_page=1", None),
    ("pages", DEPLOYMENTS + "&arbitrary=value", None),
    ("get", PREFIX + "/actions/secrets", None),
])
def test_only_known_same_repository_read_endpoints_are_accepted(method, path, field):
    with pytest.raises(snapshots.SnapshotError):
        snapshots.validate_request(method, path, field)


@pytest.mark.parametrize("change", ["code", "verifier", "activation", "identity", "sources", "catalog"])
def test_changed_context_is_a_normal_full_comparison_path(retained, change):
    api, _ = retained
    policy, identity, inventory, run = copy.deepcopy(POLICY), copy.deepcopy(IDENTITY), copy.deepcopy(INVENTORY), dict(api.current)
    if change == "code":
        policy["head_sha"] = run["head_sha"] = "b" * 40
    elif change == "verifier":
        policy["gh_version"] += "different build"
    elif change == "activation":
        policy["since"] = "2026-10-02T00:00:00Z"
    elif change == "identity":
        identity["channel"] = "C987654321"
    else:
        inventory[change].append({"new": True})
    if change == "code":
        # The server's exact-head filter excludes the previous code revision.
        api.candidates = []
    assert restore(api, run=run, policy=policy, identity=identity, inventory=inventory) is None


@pytest.mark.parametrize("reason", ["missing", "expired", "expiry-time"])
def test_missing_or_expired_retention_is_a_normal_full_comparison(retained, reason):
    api, _ = retained
    if reason == "missing":
        api.missing = True
    elif reason == "expired":
        api.artifact["expired"] = True
    else:
        api.artifact["expires_at"] = "2020-01-01T00:00:00Z"
    assert restore(api) is None
    assert api.archive_reads == []


@pytest.mark.parametrize("change", ["digest", "producer", "job-attempt", "job-skipped", "probe-skipped", "upload-skipped", "member", "extra-member", "schema", "duplicate", "foreign-path"])
def test_selected_proof_requires_exact_native_producer_and_strict_bounded_document(retained, tmp_path, change):
    api, snapshot = retained
    data = copy.deepcopy(snapshot.data)
    if change == "digest":
        api.raw += b"tampered"
    elif change == "job-attempt":
        api.job["run_attempt"] += 1
    elif change == "job-skipped":
        api.job["conclusion"] = "skipped"
    elif change in {"probe-skipped", "upload-skipped"}:
        name = snapshots.PROBE_STEP if change == "probe-skipped" else snapshots.UPLOAD_STEP
        next(step for step in api.job["steps"] if step["name"] == name)["conclusion"] = "skipped"
    elif change in {"member", "extra-member"}:
        members = {"../cache.json": native.canonical(data)} if change == "member" else {native.MEMBER: native.canonical(data), "extra": b"{}"}
        api.retain(snapshot.save(tmp_path, api.producer), members=members)
    else:
        if change == "producer":
            data["producer"]["run_attempt"] += 1
        elif change == "schema":
            data["schema"] = "unrecognized"
        elif change == "duplicate":
            data["requests"].append(copy.deepcopy(data["requests"][0]))
        else:
            data["requests"][0]["path"] = "repos/SkyTruth/other"
        api.retain(snapshot.save(tmp_path, api.producer), members={native.MEMBER: native.canonical(data)})
    with pytest.raises((snapshots.SnapshotError, native.CacheError)):
        restore(api)


def test_limits_apply_to_captured_and_loaded_requests(retained, monkeypatch):
    api, snapshot = retained
    monkeypatch.setattr(snapshots, "MAX_REQUESTS", 1)
    recording = snapshots.RecordingAPI(api)
    recording.get(PREFIX)
    with pytest.raises(snapshots.SnapshotError, match="too many"):
        recording.get(PREFIX + "/actions/runs/11/attempts/1")
    with pytest.raises(snapshots.SnapshotError, match="request transcript"):
        snapshots.Snapshot(snapshot.data)


def test_empty_transcript_cannot_certify_reconciliation(retained):
    _, snapshot = retained
    data = {**snapshot.data, "requests": []}
    with pytest.raises(snapshots.SnapshotError, match="request transcript"):
        snapshots.Snapshot(data)


@pytest.mark.parametrize("change", ["metadata", "archive", "member"])
def test_shared_archive_limits_also_apply_to_safety_proofs(retained, monkeypatch, change):
    api, _ = retained
    limit = 4096
    monkeypatch.setattr(native, "MAX_BYTES", limit)
    if change == "metadata":
        api.artifact["size_in_bytes"] = limit + 1
    elif change == "archive":
        api.raw = b"x" * (limit + 1)
        api.artifact["size_in_bytes"] = 1
        api.artifact["digest"] = "sha256:" + hashlib.sha256(api.raw).hexdigest()
    else:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(native.MEMBER, b"x" * (limit + 1))
        api.raw = output.getvalue()
        api.artifact.update(size_in_bytes=len(api.raw), digest="sha256:" + hashlib.sha256(api.raw).hexdigest())
    with pytest.raises(native.CacheError):
        restore(api)


def test_candidate_discovery_is_bounded_and_skips_newer_run_without_artifact(retained):
    api, _ = retained
    newer = {**api.producer, "id": api.producer["id"] + 1}
    api.candidates = [newer, api.producer]
    api.no_artifact_runs.add(newer["id"])
    assert restore(api).matches(api)
    query = next(path for path in api.reads if "/runs?" in path)
    assert "created=%3E%3D" in query
    assert "head_sha=" + SHA in query
    assert f"/actions/runs/{newer['id']}/artifacts?" in "\n".join(api.reads)


def test_snapshot_can_be_renewed_only_with_same_policy_revision(retained, tmp_path):
    api, _ = retained
    snapshot = restore(api)
    assert snapshot.matches(api)
    newer = {**api.current, "id": 400}
    path = snapshot.save(tmp_path, newer)
    assert json.loads(path.read_bytes())["producer"]["id"] == 400
    assert json.loads(path.read_bytes())["requests"] == snapshot.data["requests"]
    with pytest.raises(snapshots.SnapshotError, match="policy differs"):
        snapshot.save(tmp_path, {**newer, "head_sha": "b" * 40})


def test_restore_accepts_scheduled_and_manual_producers_but_not_other_events(retained):
    api, _ = retained
    api.producer["event"] = "workflow_dispatch"
    assert restore(api).matches(api)
    api.producer["event"] = "push"
    with pytest.raises(native.CacheError, match="untrusted"):
        restore(api)
