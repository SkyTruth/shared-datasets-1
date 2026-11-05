"""Settled safety artifacts retain only catalog proof and native receipt verification."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from unittest import mock
import zipfile

import pytest

from scripts import deployment_emission as emissions
from scripts import incident_verification_cache as native
from scripts import operational_safety_check as snapshots
from test_incident_verification_cache import GH_VERSION, NativeArtifacts, PREFIX, unavailable
from test_deployment_emission import verified
from test_slack_incidents import IDENTITY, SHA

POLICY = {"head_sha": SHA, "gh_version": GH_VERSION, "since": "2026-10-01T00:00:00Z"}
INVENTORY = {"asset.json": "1", "catalog.csv": "2"}


def capture(run, policy, identity, inventory, cache):
    return snapshots.Snapshot({"schema": snapshots.SCHEMA, "producer": native.execution(run), "policy": policy,
        "identity": identity, "inventory_sha256": native.digest(inventory), "entries": cache.used})


def write_snapshot(directory, snapshot):
    path = directory / native.MEMBER
    path.write_bytes(native.canonical(snapshot.data))
    return path


class SafetyArtifacts(NativeArtifacts):
    def __init__(self):
        super().__init__()
        self.producer.update(path=snapshots.WORKFLOW, workflow_id=13, event="schedule")
        self.current.update(path=snapshots.WORKFLOW, workflow_id=13, event="schedule")
        self.workflow.update(id=13, path=snapshots.WORKFLOW)
        self.job["name"] = snapshots.JOB
        self.job["steps"] = [{"name": name, "status": "completed", "conclusion": "success"}
                             for name in (snapshots.PROBE_STEP, snapshots.UPLOAD_STEP)]
        self.artifact["name"] = 'safety-snapshot-200-1'

    def get(self, path):
        if path.endswith('/actions/workflows/unattended-workflow-alert.yml'):
            self.reads.append(path)
            return {'id': 12, 'path': '.github/workflows/unattended-workflow-alert.yml'}
        return super().get(path)


@pytest.fixture
def retained(tmp_path, monkeypatch):
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    api = SafetyArtifacts()
    cache = native.VerificationCache(api.producer, IDENTITY, gh_version=GH_VERSION)
    def verify(view):
        emissions.verify(view, native.REPOSITORY, api.record, api.statuses[0],
                         original_signer=native.WORKFLOW, batch=True, verified_statements=[])
    receipt = emissions.receipt(native.REPOSITORY, api.record, api.statuses[0])
    with mock.patch.object(emissions, 'verified_output', return_value=json.dumps(
            verified(receipt, emitter=api.emitter, signer=native.WORKFLOW))) as crypto:
        cache.verify(api, api.record, api.statuses, IDENTITY, verify)
    assert crypto.call_count == 1
    snapshot = capture(api.producer, POLICY, IDENTITY, INVENTORY, cache)
    api.retain(write_snapshot(tmp_path, snapshot))
    api.reads.clear()
    return api, snapshot


def restore(api, **changes):
    values = {"run": api.current, "policy": POLICY, "identity": IDENTITY, "inventory": INVENTORY}
    return snapshots.Snapshot.restore(api, **{**values, **changes})


def test_native_roundtrip_reuses_verified_receipt_only_after_fresh_provenance_reads(retained):
    api, _ = retained
    snapshot = restore(api)
    api.reads.clear()
    assert snapshot.cache.verify(api, api.record, api.statuses, IDENTITY, unavailable)
    assert sorted(api.reads) == sorted([PREFIX, PREFIX + '/actions/runs/11/attempts/1',
                                       PREFIX + '/actions/workflows/unattended-workflow-alert.yml'])
    assert api.archive_reads == [api.artifact['id']]
    assert 'requests' not in snapshot.data  # No second persisted API-response transcript.


@pytest.mark.parametrize('change', ['record', 'status', 'emitter', 'repository'])
def test_changed_live_evidence_requires_original_signature_verification(retained, change):
    api, _ = retained
    snapshot = restore(api)
    target = {'record': api.record, 'status': api.statuses[0], 'emitter': api.emitter, 'repository': api.repo}[change]
    target['changed'] = True
    with pytest.raises(RuntimeError, match='original attestation unavailable'):
        snapshot.cache.verify(api, api.record, api.statuses, IDENTITY, unavailable)


def test_failed_live_read_cannot_be_hidden_by_saved_verification(retained):
    api, _ = retained
    snapshot = restore(api)
    api.read_error = RuntimeError('live emitter unavailable')
    with pytest.raises(RuntimeError, match='live emitter unavailable'):
        snapshot.cache.verify(api, api.record, api.statuses, IDENTITY, unavailable)


@pytest.mark.parametrize("change", ["code", "verifier", "activation", "identity", "catalog"])
def test_changed_context_requires_a_full_catalog_comparison(retained, change):
    api, _ = retained
    policy, identity, inventory, run = copy.deepcopy(POLICY), copy.deepcopy(IDENTITY), dict(INVENTORY), dict(api.current)
    if change == "code":
        policy["head_sha"] = run["head_sha"] = "b" * 40
        api.candidates = []
    elif change == "verifier":
        policy["gh_version"] += "different build"
    elif change == "activation":
        policy["since"] = "2026-10-02T00:00:00Z"
    elif change == "identity":
        identity["channel"] = "C987654321"
    else:
        inventory['catalog.csv'] = '3'
    assert restore(api, run=run, policy=policy, identity=identity, inventory=inventory) is None


@pytest.mark.parametrize("reason", ["missing", "expired", "expiry-time"])
def test_missing_or_expired_retention_requires_full_verification(retained, reason):
    api, _ = retained
    if reason == "missing":
        api.missing = True
    elif reason == "expired":
        api.artifact["expired"] = True
    else:
        api.artifact["expires_at"] = "2020-01-01T00:00:00Z"
    assert restore(api) is None
    assert api.archive_reads == []


@pytest.mark.parametrize("change", ["digest", "producer", "job-attempt", "job-skipped", "probe-skipped", "upload-skipped", "member", "extra-member", "schema", "entries", "foreign-path"])
def test_selected_proof_requires_exact_native_producer_and_bounded_verified_entries(retained, tmp_path, change):
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
        api.retain(write_snapshot(tmp_path, snapshot), members=members)
    else:
        if change == "producer":
            data["producer"]["run_attempt"] += 1
        elif change == "schema":
            data["schema"] = "unrecognized"
        elif change == "entries":
            data['entries'] = {'invalid': {}}
        else:
            reads = next(iter(data['entries'].values()))
            reads['repos/SkyTruth/other'] = reads.pop(PREFIX)
        api.retain(write_snapshot(tmp_path, snapshot), members={native.MEMBER: native.canonical(data)})
    with pytest.raises((RuntimeError, native.CacheError)):
        restore(api)


@pytest.mark.parametrize("change", ["metadata", "archive", "member"])
def test_archive_limits_apply_before_restore(retained, monkeypatch, change):
    api, _ = retained
    limit = 4096
    monkeypatch.setattr(native, "MAX_BYTES", limit)
    if change == "metadata":
        api.artifact["size_in_bytes"] = limit + 1
    elif change == "archive":
        api.raw = b"x" * (limit + 1)
        api.artifact.update(size_in_bytes=1, digest="sha256:" + hashlib.sha256(api.raw).hexdigest())
    else:
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(native.MEMBER, b"x" * (limit + 1))
        api.raw = output.getvalue()
        api.artifact.update(size_in_bytes=len(api.raw), digest="sha256:" + hashlib.sha256(api.raw).hexdigest())
    with pytest.raises(native.CacheError):
        restore(api)


def test_only_checked_receipts_can_be_renewed_and_revision_cannot_change(retained, tmp_path):
    api, _ = retained
    snapshot = restore(api)
    assert snapshot.cache.verify(api, api.record, api.statuses, IDENTITY, unavailable)
    newer = {**api.current, 'id': 400}
    renewed = capture(newer, POLICY, IDENTITY, INVENTORY, snapshot.cache)
    assert json.loads(write_snapshot(tmp_path, renewed).read_bytes())['entries'] == snapshot.cache.used
    with pytest.raises(RuntimeError, match='policy differs'):
        capture({**newer, 'head_sha': 'b' * 40}, POLICY, IDENTITY, INVENTORY, snapshot.cache)


def test_probe_transport_has_no_mutation_method():
    assert not hasattr(snapshots.GitHub(), "post")


def test_snapshot_producer_accepts_manual_safety_but_rejects_push(retained):
    api, _ = retained
    api.producer['event'] = 'workflow_dispatch'
    assert restore(api)
    api.producer['event'] = 'push'
    with pytest.raises(native.CacheError, match='untrusted'):
        restore(api)
