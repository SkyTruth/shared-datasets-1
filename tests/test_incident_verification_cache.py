"""Persisted verification saves crypto work only for exact fresh inputs."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from unittest import mock
import zipfile

import pytest

from scripts import deployment_emission as emissions
from scripts import incident_verification_cache as caches
from scripts import install_deployment_verifier as installer
from scripts import slack_incidents as incidents
from test_deployment_emission import run, verified
from test_slack_incidents import FakeSlack, IDENTITY, MemoryLedger, SHA, observation, signed_record, success


GH_VERSION = f"gh version {installer.VERSION} (2026-10-28)\nhttps://github.com/cli/cli/releases\n"
PREFIX = "repos/" + incidents.REPOSITORY


def serialized(value):
    return json.loads(json.dumps(value))


def timestamp(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class NativeArtifacts:
    """GitHub transport fixture; cache ZIP and JSON boundaries stay real."""

    def __init__(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.producer = {
            **run(identifier=200, attempt=1, path=incidents.WORKFLOW, event="workflow_run"),
            "status": "completed", "conclusion": "success", "name": "Unattended workflow alert",
            "created_at": timestamp(now - timedelta(minutes=5)),
            "updated_at": timestamp(now - timedelta(minutes=1)),
        }
        self.current = {**self.producer, "id": 300, "status": "in_progress", "conclusion": None}
        self.emitter = run(identifier=11, attempt=1, path=incidents.WORKFLOW, event="workflow_run")
        self.repo = {"id": 17, "full_name": incidents.REPOSITORY}
        self.workflow = {"id": 12, "path": incidents.WORKFLOW}
        self.record = serialized(signed_record())
        self.statuses = [{"id": 12, "state": "success", "description": "applied", "environment_url": "",
                          "log_url": emissions.invocation(incidents.REPOSITORY, 11, 1)}]
        self.job = {
            "id": 201, "name": caches.JOB, "run_id": 200, "run_attempt": 1,
            "status": "completed", "conclusion": "success", "head_sha": SHA,
            "started_at": self.producer["created_at"], "completed_at": self.producer["updated_at"],
            "steps": [{"name": name, "status": "completed", "conclusion": "success"}
                      for name in (caches.PREPARE_STEP, caches.UPLOAD_STEP)],
        }
        self.artifact = {
            "id": 202, "name": caches.artifact_name(self.producer), "expired": False,
            "created_at": timestamp(now - timedelta(minutes=3)),
            "updated_at": timestamp(now - timedelta(minutes=3)),
            "expires_at": timestamp(now + timedelta(days=1)),
            "workflow_run": {"id": 200, "head_sha": SHA, "head_branch": "main"},
        }
        self.raw = None
        self.reads = []
        self.archive_reads = []
        self.missing = False
        self.read_error = None
        self.candidates = None
        self.no_artifact_runs = set()

    def get(self, path):
        self.reads.append(path)
        if self.read_error and "/actions/runs/11/" in path:
            raise self.read_error
        if path == PREFIX:
            value = self.repo
        elif "/actions/workflows/" in path:
            value = self.workflow
        elif "/actions/runs/11/" in path:
            value = self.emitter
        elif f"/actions/runs/{self.producer['id']}" in path:
            value = self.producer
        elif f"/actions/runs/{self.current['id']}" in path:
            value = self.current
        else:
            raise AssertionError("unexpected GET: " + path)
        return serialized(value)

    def pages(self, path, field=None):
        self.reads.append(path)
        if "/statuses?" in path:
            value = self.statuses
        elif "/jobs?" in path:
            assert "/attempts/1/" in path
            value = [self.job]
        elif "/artifacts?" in path:
            missing_run = any(f"/actions/runs/{run_id}/" in path for run_id in self.no_artifact_runs)
            value = [] if self.missing or missing_run else [self.artifact]
        elif "/runs?" in path:
            value = self.candidates if self.candidates is not None else [self.producer]
        elif "/deployments?" in path:
            value = [self.record]
        else:
            raise AssertionError("unexpected pagination: " + path)
        return serialized(value)

    def archive(self, repository, artifact_id):
        assert (repository, artifact_id) == (incidents.REPOSITORY, self.artifact["id"])
        self.archive_reads.append(artifact_id)
        return self.raw

    def retain(self, cache_path, *, members=None):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for name, raw in (members or {caches.MEMBER: cache_path.read_bytes()}).items():
                archive.writestr(name, raw)
        self.raw = output.getvalue()
        self.artifact.update(size_in_bytes=len(self.raw), digest="sha256:" + hashlib.sha256(self.raw).hexdigest())

    def next_producer(self, cache_path):
        self.producer.update(json.loads(cache_path.read_bytes())["producer"])
        self.current = {**self.producer, "id": self.producer["id"] + 100, "status": "in_progress", "conclusion": None}
        self.artifact.update(name=caches.artifact_name(self.producer), workflow_run={
            "id": self.producer["id"], "head_sha": SHA, "head_branch": "main"})
        self.job["run_id"] = self.producer["id"]
        self.retain(cache_path)


@pytest.fixture
def retained(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    api = NativeArtifacts()
    cache = caches.VerificationCache(api.producer, IDENTITY, gh_version=GH_VERSION)
    calls = []

    def verify(api_view):
        calls.append("full")
        emissions.verify(api_view, incidents.REPOSITORY, api.record, api.statuses[0],
                         original_signer=incidents.WORKFLOW, batch=True, verified_statements=[])

    value = emissions.receipt(incidents.REPOSITORY, api.record, api.statuses[0])
    result = verified(value, emitter=api.emitter, signer=incidents.WORKFLOW)
    with mock.patch.object(emissions, "verified_output", return_value=json.dumps(result)) as crypto:
        assert cache.verify(api, api.record, api.statuses, IDENTITY, verify) is False
    assert crypto.call_count == 1 and calls == ["full"]
    path = cache.save(tmp_path / "producer")
    api.retain(path)
    api.reads.clear()
    return api, path, verify


def restored(api, *, identity=IDENTITY, run=None, gh_version=GH_VERSION):
    return caches.VerificationCache.restore(api, run or api.current, identity, gh_version=gh_version)


def unavailable(_):
    raise RuntimeError("original attestation unavailable")


def test_new_immutable_receipt_is_verified_once_per_batch_but_changed_bytes_are_rechecked(retained):
    api, _, verify = retained
    cache = caches.VerificationCache(api.current, IDENTITY, gh_version=GH_VERSION)
    receipt = emissions.receipt(incidents.REPOSITORY, api.record, api.statuses[0])
    result = verified(receipt, emitter=api.emitter, signer=incidents.WORKFLOW)
    with mock.patch.object(emissions, "verified_output", return_value=json.dumps(result)) as crypto:
        assert cache.verify(api, api.record, api.statuses, IDENTITY, verify) is False
        assert cache.verify(api, serialized(api.record), serialized(api.statuses), IDENTITY, unavailable) is True
        assert crypto.call_count == 1
    api.statuses[0]["environment_url"] = "https://example.test/changed"
    with pytest.raises(RuntimeError, match="original attestation unavailable"):
        cache.verify(api, api.record, api.statuses, IDENTITY, unavailable)


@pytest.mark.parametrize("event", ["workflow_run", "workflow_dispatch"])
def test_native_artifact_roundtrip_reuses_success_but_rereads_every_live_input(retained, tmp_path, event):
    api, _, _ = retained
    api.producer["event"] = event
    cache = restored(api)
    api.reads.clear()
    with mock.patch.object(emissions, "verified_output", side_effect=RuntimeError("old attestation unavailable")) as crypto:
        assert cache.verify(api, serialized(api.record), serialized(api.statuses), serialized(IDENTITY), unavailable) is True
    crypto.assert_not_called()
    assert sorted(api.reads) == sorted([PREFIX + "/actions/runs/11/attempts/1", PREFIX,
                                       PREFIX + "/actions/workflows/unattended-workflow-alert.yml"])
    assert api.archive_reads == [202]
    # A hit may itself be retained, but only after this invocation checked it.
    api.next_producer(cache.save(tmp_path / "consumer"))
    assert restored(api).verify(api, api.record, api.statuses, IDENTITY, unavailable) is True


@pytest.mark.parametrize("change", ["record", "payload", "status", "status-history", "emitter", "repository", "workflow"])
def test_any_changed_checkpoint_or_fresh_provenance_requires_full_verification(retained, change):
    api, _, _ = retained
    cache = restored(api)
    if change == "record":
        api.record["updated_at"] = "2026-10-30T01:00:00Z"
    elif change == "payload":
        api.record["payload"]["state"]["identity"]["channel"] = "C987654321"
    elif change == "status":
        api.statuses[0]["environment_url"] = "https://changed.example"
    elif change == "status-history":
        api.statuses.append(copy.deepcopy(api.statuses[0]))
    elif change == "emitter":
        api.emitter["updated_at"] = "2026-10-30T01:00:00Z"
    elif change == "repository":
        api.repo["description"] = "changed repository metadata"
    else:
        api.workflow["state"] = "disabled_manually"
    if change == "status-history":
        with pytest.raises(caches.CacheError, match="complete status history"):
            cache.verify(api, api.record, api.statuses, IDENTITY, unavailable)
        return
    with pytest.raises(RuntimeError, match="original attestation unavailable"):
        cache.verify(api, api.record, api.statuses, IDENTITY, unavailable)


@pytest.mark.parametrize("change", ["code", "verifier", "team", "user", "channel"])
def test_changed_code_verifier_or_slack_identity_never_reuses_prior_success(retained, change):
    api, _, _ = retained
    identity, current, version = dict(IDENTITY), dict(api.current), GH_VERSION
    if change == "code":
        current["head_sha"] = "b" * 40
    elif change == "verifier":
        version += "changed binary version output"
    else:
        identity[change] = {"team": "T987654321", "user": "U987654321", "channel": "C987654321"}[change]
    cache = restored(api, identity=identity, run=current, gh_version=version)
    with pytest.raises(RuntimeError, match="original attestation unavailable"):
        cache.verify(api, api.record, api.statuses, identity, unavailable)


def test_live_read_failure_is_fatal_and_cannot_fall_back_to_cached_api_responses(retained):
    api, _, _ = retained
    cache = restored(api)
    api.read_error = RuntimeError("live emitter API unavailable")
    with pytest.raises(RuntimeError, match="live emitter API unavailable"):
        cache.verify(api, api.record, api.statuses, IDENTITY, mock.Mock())


@pytest.mark.parametrize("reason", ["missing", "expired", "expiry-time"])
def test_missing_or_expired_retention_is_a_normal_cold_path(retained, reason):
    api, _, _ = retained
    if reason == "missing":
        api.missing = True
    elif reason == "expired":
        api.artifact["expired"] = True
    else:
        api.artifact["expires_at"] = "2020-01-01T00:00:00Z"
    cache = restored(api)
    assert api.archive_reads == []
    with pytest.raises(RuntimeError, match="original attestation unavailable"):
        cache.verify(api, api.record, api.statuses, IDENTITY, unavailable)


@pytest.mark.parametrize("change", ["digest", "json", "member", "extra-member", "producer-payload", "artifact-run",
                                     "job-failed", "job-skipped", "job-run", "job-attempt", "job-sha",
                                     "prepare-skipped", "upload-skipped", "producer-failed", "producer-skipped",
                                     "producer-branch", "producer-event", "producer-untrusted-event", "producer-workflow", "producer-repository"])
def test_selected_artifact_requires_native_digest_exact_producer_and_successful_export(retained, change):
    api, path, _ = retained
    if change == "digest":
        api.raw += b"forged archive bytes"
    elif change == "json":
        api.retain(path, members={caches.MEMBER: b'{"forged":true}\n'})
    elif change == "member":
        api.retain(path, members={"../cache.json": path.read_bytes()})
    elif change == "extra-member":
        api.retain(path, members={caches.MEMBER: path.read_bytes(), "extra.json": b"{}"})
    elif change == "producer-payload":
        data = json.loads(path.read_bytes())
        data["producer"]["run_attempt"] += 1
        api.retain(path, members={caches.MEMBER: caches.canonical(data)})
    elif change == "artifact-run":
        api.artifact["workflow_run"]["id"] += 1
    elif change == "producer-failed":
        api.producer["conclusion"] = "failure"
    elif change == "producer-skipped":
        api.producer["conclusion"] = "skipped"
    elif change == "producer-branch":
        api.producer["head_branch"] = "feature/untrusted"
    elif change == "producer-event":
        api.producer["event"] = "pull_request"
    elif change == "producer-untrusted-event":
        api.producer["event"] = "pull_request"
    elif change == "producer-workflow":
        api.producer["path"] = ".github/workflows/ci.yml"
    elif change == "producer-repository":
        api.producer["head_repository"]["full_name"] = "Other/repository"
    elif change == "job-run":
        api.job["run_id"] += 1
    elif change == "job-attempt":
        api.job["run_attempt"] += 1
    elif change == "job-sha":
        api.job["head_sha"] = "b" * 40
    elif change.startswith("job-"):
        api.job["conclusion"] = change.removeprefix("job-")
    else:
        step = caches.PREPARE_STEP if change == "prepare-skipped" else caches.UPLOAD_STEP
        next(item for item in api.job["steps"] if item["name"] == step)["conclusion"] = "skipped"
    with pytest.raises(caches.CacheError):
        restored(api)


@pytest.mark.parametrize("change", ["policy", "identity", "nonfinite"])
def test_malformed_selected_context_cannot_be_mistaken_for_a_normal_cache_miss(retained, change):
    api, path, _ = retained
    data = json.loads(path.read_bytes())
    if change == "nonfinite":
        data["policy"]["gh_version"] = float("nan")
    else:
        data[change] = None
    raw = (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode()
    api.retain(path, members={caches.MEMBER: raw})
    with pytest.raises(caches.CacheError):
        restored(api)


def test_newer_catalog_short_worker_without_artifact_does_not_hide_retained_full_verification(retained):
    api, _, _ = retained
    newer = {**api.producer, "id": 250}
    api.candidates = [newer, api.producer]
    api.no_artifact_runs.add(250)
    cache = restored(api)
    assert cache.verify(api, api.record, api.statuses, IDENTITY, unavailable) is True
    assert any("/actions/runs/250/artifacts?" in path for path in api.reads)
    assert any("/actions/runs/200/artifacts?" in path for path in api.reads)


def test_failed_verification_is_not_serialized_as_reusable_success(tmp_path):
    api = NativeArtifacts()
    cache = caches.VerificationCache(api.producer, IDENTITY, gh_version=GH_VERSION)
    with pytest.raises(RuntimeError, match="original attestation unavailable"):
        cache.verify(api, api.record, api.statuses, IDENTITY, unavailable)
    api.retain(cache.save(tmp_path / "failed"))
    with pytest.raises(RuntimeError, match="original attestation unavailable"):
        restored(api).verify(api, api.record, api.statuses, IDENTITY, unavailable)


def test_unused_prior_entries_are_not_carried_forward_without_current_validation(retained, tmp_path):
    api, _, _ = retained
    cache = restored(api)
    api.next_producer(cache.save(tmp_path / "unused"))
    with pytest.raises(RuntimeError, match="original attestation unavailable"):
        restored(api).verify(api, api.record, api.statuses, IDENTITY, unavailable)


def test_real_ledger_reads_current_records_statuses_and_keeps_uncached_default(retained, tmp_path):
    api, _, _ = retained
    cache = restored(api)
    api.reads.clear()
    with mock.patch.object(emissions, "verified_output", side_effect=RuntimeError("old attestation unavailable")) as crypto:
        states = incidents.Ledger(api, api.current, tmp_path, verification_cache=cache).load()
        assert states == {api.record["payload"]["state"]["scope"]: api.record["payload"]["state"]}
        with pytest.raises(RuntimeError, match="old attestation unavailable"):
            incidents.Ledger(api, api.current, tmp_path).load()
    assert crypto.call_count == 1
    assert sum("/deployments?environment=slack-incidents" in path for path in api.reads) == 2
    assert sum("/deployments/10/statuses?" in path for path in api.reads) == 2
    assert sum("/actions/runs/11/attempts/1" in path for path in api.reads) == 2


def test_cached_ledger_preserves_parent_delivery_deferred_failure_and_recovery(retained, tmp_path):
    api, _, _ = retained
    value = emissions.receipt(incidents.REPOSITORY, api.record, api.statuses[0])
    result = verified(value, emitter=api.emitter, signer=incidents.WORKFLOW)
    with mock.patch.object(emissions, "verified_output", return_value=json.dumps(result)):
        cold = incidents.Ledger(api, api.current, tmp_path).load()
    with mock.patch.object(emissions, "verified_output", side_effect=RuntimeError("old attestation unavailable")):
        warm = incidents.Ledger(api, api.current, tmp_path, verification_cache=restored(api)).load()

    traces = []
    retry = observation(run_id=102, time="2026-10-06T09:00:00Z")
    for states in (cold, warm):
        ledger, slack = MemoryLedger(states), FakeSlack()
        # An unconfirmed parent remains blocked and retains new observations.
        assert incidents.prepare(ledger, IDENTITY, [retry], success=lambda *_: (None, None)) == []
        assert ledger.states[retry["scope"]]["deferred"] == [retry]
        assert slack.calls == []
        # Confirmed delivery and later recovery preserve the original parent.
        incidents.deliver(ledger, [{"payload": {"state": ledger.states[retry["scope"]]}}], slack)
        records = incidents.prepare(ledger, IDENTITY, [success()], ancestor=lambda *_: True,
                                    success=lambda *_: (None, None))
        incidents.deliver(ledger, records, slack)
        traces.append((copy.deepcopy(ledger.states), copy.deepcopy(slack.calls)))
    assert traces[0] == traces[1]
    assert len(traces[1][0][retry["scope"]]["incident"]["failures"]) == 2
    assert [call[0] for call in traces[1][1]] == ["post", "update"]
    assert "Resolved" in traces[1][1][-1][1]["text"]


def test_real_signature_failure_cannot_be_exported_as_success(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    api = NativeArtifacts()
    cache = caches.VerificationCache(api.producer, IDENTITY, gh_version=GH_VERSION)
    value = emissions.receipt(incidents.REPOSITORY, api.record, api.statuses[0])
    result = verified(value, emitter=api.emitter, signer=incidents.WORKFLOW)
    result[0]["verificationResult"]["statement"]["subject"][0]["digest"]["sha256"] = "f" * 64

    def verify(api_view):
        emissions.verify(api_view, incidents.REPOSITORY, api.record, api.statuses[0],
                         original_signer=incidents.WORKFLOW, batch=True)

    with mock.patch.object(emissions, "verified_output", return_value=json.dumps(result)):
        with pytest.raises(emissions.EmissionError, match="exact record, phase and protected emitter"):
            cache.verify(api, api.record, api.statuses, IDENTITY, verify)
    api.retain(cache.save(tmp_path / "failed-signature"))
    with pytest.raises(RuntimeError, match="original attestation unavailable"):
        restored(api).verify(api, api.record, api.statuses, IDENTITY, unavailable)
