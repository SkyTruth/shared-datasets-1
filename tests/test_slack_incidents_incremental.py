"""Signed batch reuse retains exact records and live provenance."""
import copy
import hashlib
import json
from unittest import mock

import pytest

from scripts import deployment_emission as e
from scripts import slack_incidents as incidents
from test_slack_incidents import signed_record
from test_deployment_emission import run, verified


def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    records = [signed_record(), signed_record()]
    records[1]["id"] = 20
    state = records[1]["payload"]["state"]
    state.update(scope="deployment:dataset-usage", label="dataset-usage")
    for failure in state["incident"]["failures"]:
        failure.update(scope=state["scope"], label=state["label"])
    records = json.loads(json.dumps(records))
    records[1]["payload"] = json.dumps(records[1]["payload"])
    emitter = run(identifier=11, attempt=1, path=incidents.WORKFLOW, event="workflow_run")
    statuses = {r["id"]: {"id": r["id"] + 1, "state": "success", "description": "applied",
        "environment_url": "", "log_url": e.invocation(incidents.REPOSITORY, 11, 1)} for r in records}
    api = mock.Mock()
    def pages(path, **_):
        if "deployments?" in path:
            return copy.deepcopy(records)
        return [copy.deepcopy(statuses[int(path.split("/deployments/")[1].split("/")[0])])]
    def get(path):
        if path == "repos/" + incidents.REPOSITORY:
            return {"id": 17, "full_name": incidents.REPOSITORY}
        if "/actions/workflows/" in path:
            return {"id": emitter["workflow_id"], "path": emitter["path"]}
        return copy.deepcopy(emitter)
    api.pages.side_effect = pages
    api.get.side_effect = get
    values = [e.receipt(incidents.REPOSITORY, r, statuses[r["id"]]) for r in records]
    result = verified(values[0], emitter=emitter, signer=incidents.WORKFLOW)
    result[0]["verificationResult"]["statement"]["subject"] = [
        {"name": e.name(v), "digest": {"sha256": hashlib.sha256(e.canonical(v)).hexdigest()}} for v in values]
    return incidents.Ledger(api, {}, tmp_path), api, records, statuses, result


def test_serialized_registry_reuses_batch_and_rechecks_each_live_emitter(tmp_path, monkeypatch):
    ledger, api, _, _, result = registry(tmp_path, monkeypatch)
    with mock.patch.object(e, "verified_output", return_value=json.dumps(result)) as verifier:
        states = ledger.load()
    assert set(states) == {"deployment:pmtiles-cdn", "deployment:dataset-usage"}
    assert verifier.call_count == 1
    assert sum("/attempts/1" in c.args[0] for c in api.get.call_args_list) == 2
    assert sum("/statuses?" in c.args[0] for c in api.pages.call_args_list) == 2


@pytest.mark.parametrize("change", ["payload", "status", "emitter"])
def test_cached_batch_rejects_changed_bytes_or_live_policy(change, tmp_path, monkeypatch):
    ledger, api, records, statuses, result = registry(tmp_path, monkeypatch)
    with mock.patch.object(e, "verified_output", return_value=json.dumps(result)) as verifier:
        ledger.verify(records[0])
        altered = copy.deepcopy(records[1])
        if change == "payload":
            payload = json.loads(altered["payload"])
            payload["state"]["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "1791270000.000001"}
            altered["payload"] = json.dumps(payload)
        elif change == "status":
            statuses[20]["id"] += 100
        else:
            original_get = api.get.side_effect
            def changed_get(path):
                value = original_get(path)
                if "/attempts/" in path:
                    value["head_sha"] = "b" * 40
                return value
            api.get.side_effect = changed_get
        with pytest.raises(e.EmissionError, match="exact record, phase and protected emitter"):
            ledger.verify(altered)
    assert verifier.call_count == 2


def test_verified_results_do_not_survive_another_ledger_invocation(tmp_path, monkeypatch):
    ledger, api, _, _, result = registry(tmp_path, monkeypatch)
    with mock.patch.object(e, "verified_output", return_value=json.dumps(result)) as verifier:
        ledger.load()
        incidents.Ledger(api, {}, tmp_path).load()
    assert verifier.call_count == 2


def test_separate_signed_batches_each_require_their_own_verification(tmp_path, monkeypatch):
    ledger, _, _, _, result = registry(tmp_path, monkeypatch)
    subjects = result[0]["verificationResult"]["statement"]["subject"]
    batches = []
    for subject in subjects:
        batch = copy.deepcopy(result)
        batch[0]["verificationResult"]["statement"]["subject"] = [subject]
        batches.append(json.dumps(batch))
    with mock.patch.object(e, "verified_output", side_effect=batches) as verifier:
        assert len(ledger.load()) == 2
    assert verifier.call_count == 2



def test_failed_crypto_verification_is_never_cached(tmp_path, monkeypatch):
    ledger, _, records, _, result = registry(tmp_path, monkeypatch)
    with mock.patch.object(e, "verified_output", side_effect=[RuntimeError("verification failed"), json.dumps(result)]) as verifier:
        with pytest.raises(RuntimeError, match="verification failed"):
            ledger.verify(records[0])
        assert ledger.verified_statements == []
        ledger.verify(records[0])
    assert verifier.call_count == 2



class ProductionAPI:
    """Serialize genuine API objects through the real production validator."""
    def __init__(self):
        from test_deployment_emission import record, status
        self.record = record()
        self.records = [self.record]
        self.statuses = [status("verified", identifier=83), status("started", identifier=81)]
        for phase in self.statuses:
            phase["created_at"] = "2026-10-06T10:18:44Z"
        self.emitter = run()
        self.reads = []
        self.error = None
        self.provenance_unavailable = False

    def get(self, path):
        self.reads.append(path)
        if self.provenance_unavailable:
            raise RuntimeError("old provenance unavailable")
        if path == "repos/" + incidents.REPOSITORY:
            return {"id": 17, "full_name": incidents.REPOSITORY}
        if "/actions/workflows/" in path:
            return {"id": 12, "path": ".github/workflows/ci.yml"}
        return copy.deepcopy(self.emitter)

    def pages(self, path, field=None):
        self.reads.append(path)
        if self.error:
            raise self.error
        if "/statuses?" in path:
            return json.loads(json.dumps(self.statuses))
        if "/jobs?" in path:
            return [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
        assert "deployments?environment=production-wdpa-monthly" in path
        return json.loads(json.dumps(self.records))


def production(tmp_path, monkeypatch):
    from pathlib import Path
    from test_deployment_emission import SIGNER
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(incidents, "terminal_contract_sha256", lambda: "c" * 64)
    api = ProductionAPI()
    def crypto(command, *_):
        value = json.loads(Path(command[3]).read_text())
        return json.dumps(verified(value, emitter=api.emitter, signer=SIGNER))
    return api, mock.patch.object(e, "verified_output", side_effect=crypto)


def test_unchanged_terminal_fact_survives_old_provenance_outage_but_reads_latest(tmp_path, monkeypatch):
    api, crypto = production(tmp_path, monkeypatch)
    with crypto as verifier:
        healthy, memo = incidents.terminal_success(api, "deployment:wdpa-monthly")
        assert verifier.call_count == 2
        api.reads.clear()
        healthy, memo = json.loads(json.dumps([healthy, memo]))
        api.provenance_unavailable = True
        assert incidents.terminal_success(api, "deployment:wdpa-monthly", healthy, memo) == (healthy, memo)
        assert verifier.call_count == 2
    assert len(api.reads) == 2
    assert "deployments?environment=production-wdpa-monthly" in api.reads[0]
    assert "/statuses?" in api.reads[1]


@pytest.mark.parametrize("change", ["artifact", "creator", "ref", "history", "healthy", "contract", "legacy"])
def test_changed_evidence_requires_fresh_validation_and_cannot_hide_outage(change, tmp_path, monkeypatch):
    api, crypto = production(tmp_path, monkeypatch)
    with crypto:
        healthy, memo = incidents.terminal_success(api, "deployment:wdpa-monthly")
        if change == "artifact":
            api.record["payload"]["artifact"] = "image@sha256:" + "d" * 64
        elif change == "creator":
            api.record["creator"]["login"] = "attacker"
        elif change == "ref":
            api.record["ref"] = "refs/heads/main"
        elif change == "history":
            api.statuses[1]["environment_url"] = "https://changed.example"
        elif change == "healthy":
            healthy = {**healthy, "time": "2026-10-06T10:18:45Z"}
        elif change == "contract":
            monkeypatch.setattr(incidents, "terminal_contract_sha256", lambda: "d" * 64)
        else:
            memo = None
        api.provenance_unavailable = True
        with pytest.raises((RuntimeError, incidents.deployments.DeploymentError)):
            incidents.terminal_success(api, "deployment:wdpa-monthly", healthy, memo)


@pytest.mark.parametrize("history_change", ["duplicate-started", "missing-started"])
def test_unchanged_terminal_receipt_cannot_hide_invalid_initial_history(history_change, tmp_path, monkeypatch):
    api, crypto = production(tmp_path, monkeypatch)
    with crypto:
        healthy, memo = incidents.terminal_success(api, "deployment:wdpa-monthly")
        if history_change == "duplicate-started":
            api.statuses.append(copy.deepcopy(api.statuses[1]))
        else:
            api.statuses.pop()
        with pytest.raises(incidents.deployments.DeploymentError, match="unambiguous protected initial"):
            incidents.terminal_success(api, "deployment:wdpa-monthly", healthy, memo)


@pytest.mark.parametrize("phase,state", [("started", "in_progress"), ("failed", "failure")])
def test_new_pending_or_failed_attempt_cannot_reuse_old_health(phase, state, tmp_path, monkeypatch):
    api, crypto = production(tmp_path, monkeypatch)
    with crypto:
        healthy, memo = incidents.terminal_success(api, "deployment:wdpa-monthly")
        newest = copy.deepcopy(api.record)
        newest["id"] += 1
        api.records.append(newest)
        api.statuses[0].update(description=phase, state=state)
        if phase == "started":
            api.statuses = [api.statuses[0]]
        assert incidents.terminal_success(api, "deployment:wdpa-monthly", healthy, memo) == (None, None)


def test_memo_never_interprets_failed_enumeration_as_empty(tmp_path, monkeypatch):
    api, crypto = production(tmp_path, monkeypatch)
    with crypto:
        healthy, memo = incidents.terminal_success(api, "deployment:wdpa-monthly")
        api.error = incidents.deployments.DeploymentError("incomplete API enumeration")
        with pytest.raises(incidents.deployments.DeploymentError, match="incomplete API enumeration"):
            incidents.terminal_success(api, "deployment:wdpa-monthly", healthy, memo)


@pytest.mark.parametrize("schema", ["v1", "v2", "v3"])
def test_historical_signed_checkpoint_migrates_without_inventing_validation(schema, tmp_path):
    from test_slack_incidents import retired_record
    record = retired_record() if schema == "v3" else signed_record()
    state = record["payload"]["state"]
    if schema != "v3":
        del state["terminal_validation"]
        record["payload"]["schema"] = incidents.LEGACY_SCHEMA if schema == "v1" else incidents.PREVIOUS_SCHEMA
        if schema == "v1":
            del state["incident"]["parent_view_sha256"]
    api = mock.Mock()
    api.pages.return_value = [{"id": 12, "state": "success", "description": "applied"}]
    original = copy.deepcopy(record)
    with mock.patch.object(e, "verify", side_effect=e.EmissionError("unsigned")):
        with pytest.raises(e.EmissionError, match="unsigned"):
            incidents.Ledger(api, {}, tmp_path).verify(json.loads(json.dumps(record)))
    assert record == original
    with mock.patch.object(e, "verify") as signature:
        migrated = incidents.Ledger(api, {}, tmp_path).verify(json.loads(json.dumps(record)))
    signature.assert_called_once()
    assert migrated["terminal_validation"] is None
    assert migrated["incident"]["id"] == state["incident"]["id"]
    assert migrated["incident"]["posts"] == state["incident"]["posts"]


def test_unsigned_notification_memo_cannot_suppress_fresh_validation(tmp_path):
    from test_slack_incidents import IDENTITY, success
    record = signed_record()
    state = incidents.fold(record["payload"]["state"], success(), IDENTITY, ancestor=lambda *_: True)
    state["terminal_validation"] = {"evidence_sha256": "a" * 64, "contract_sha256": "b" * 64}
    record["payload"]["state"] = state
    api = mock.Mock()
    api.pages.return_value = [{"id": 12, "state": "success", "description": "applied"}]
    with mock.patch.object(e, "verify", side_effect=e.EmissionError("forged memo")):
        with pytest.raises(e.EmissionError, match="forged memo"):
            incidents.Ledger(api, {}, tmp_path).verify(json.loads(json.dumps(record)))


def test_failed_fresh_validation_does_not_create_a_reusable_memo(tmp_path, monkeypatch):
    api, crypto = production(tmp_path, monkeypatch)
    with mock.patch.object(e, "verified_output", side_effect=RuntimeError("verification failed")):
        with pytest.raises(RuntimeError, match="verification failed"):
            incidents.terminal_success(api, "deployment:wdpa-monthly")
    with crypto as verifier:
        healthy, memo = incidents.terminal_success(api, "deployment:wdpa-monthly")
    assert healthy is not None and memo is not None and verifier.call_count == 2


def test_serialized_memo_preserves_parent_and_cannot_resolve_later_failure(tmp_path, monkeypatch):
    from test_slack_incidents import FakeSlack, IDENTITY, MemoryLedger, observation
    first = observation(scope="deployment:wdpa-monthly", label="wdpa-monthly")
    ledger, slack = MemoryLedger(), FakeSlack()
    incidents.deliver(ledger, incidents.prepare(ledger, IDENTITY, [first], success=lambda *_: (None, None)), slack)
    parent = ledger.states[first["scope"]]["incident"]["posts"]["parent"]["ts"]
    api, crypto = production(tmp_path, monkeypatch)
    ledger.api = api
    with crypto as verifier:
        prepared = incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True)
        incidents.deliver(ledger, prepared, slack)
        saved = json.loads(json.dumps(ledger.states))
        assert saved[first["scope"]]["terminal_validation"] is not None
        assert saved[first["scope"]]["incident"]["posts"]["parent"]["ts"] == parent
        restored = MemoryLedger(saved)
        restored.api = api
        api.provenance_unavailable = True
        assert incidents.prepare(restored, IDENTITY, [], ancestor=lambda *_: True) == []
        assert verifier.call_count == 2
        later = {**first, "run_id": 105, "time": "2026-10-06T11:00:00Z"}
        next_claim = incidents.prepare(restored, IDENTITY, [later], ancestor=lambda *_: True)
        current = restored.states[first["scope"]]
        assert current["incident"]["resolution"] is None
        assert current["incident"]["failures"] == [later]
        assert len(next_claim) == 1
        # Historical parent stays audit evidence; its green acknowledgement preceded a new claim.
        assert saved[first["scope"]]["incident"]["posts"]["parent"]["ts"] == parent


@pytest.mark.parametrize("validation", [True, {}, {"evidence_sha256": "a" * 64},
    {"evidence_sha256": "bad", "contract_sha256": "b" * 64},
    {"evidence_sha256": "a" * 64, "contract_sha256": "b" * 64, "optional": True}])
def test_validation_checkpoint_has_one_strict_shape(validation):
    from test_slack_incidents import IDENTITY, success
    state = incidents.fold(signed_record()["payload"]["state"], success(), IDENTITY, ancestor=lambda *_: True)
    state["terminal_validation"] = validation
    with pytest.raises(incidents.IncidentError, match="terminal validation"):
        incidents.validate_state(state)


def test_validation_contract_changes_with_source_and_requires_pinned_verifier(tmp_path, monkeypatch):
    sources = ("slack_incidents.py", "deployment_revision.py", "deployment_emission.py",
               "workflow_failure_alert.py", "workflow_identity.py", "github_api_read.py", "install_deployment_verifier.py")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in sources:
        (scripts / name).write_text(name)
    monkeypatch.setattr(incidents, "ROOT", tmp_path)
    with mock.patch.object(incidents.subprocess, "check_output", return_value=f"gh version {incidents.verifier.VERSION} (test)\n"):
        original = incidents.terminal_contract_sha256.__wrapped__()
        (scripts / "deployment_revision.py").write_text("changed validator")
        assert incidents.terminal_contract_sha256.__wrapped__() != original
    with mock.patch.object(incidents.subprocess, "check_output", return_value="gh version 0.0.0 (wrong)\n"):
        with pytest.raises(incidents.IncidentError, match="pinned receipt verifier"):
            incidents.terminal_contract_sha256.__wrapped__()


@pytest.mark.parametrize("during_verification", [1, 2])
@pytest.mark.parametrize("phase,state", [("started", "in_progress"), ("failed", "failure")])
def test_status_appended_during_verification_cannot_produce_recovery_or_memo(during_verification, phase, state, tmp_path, monkeypatch):
    api, crypto = production(tmp_path, monkeypatch)
    with crypto as verifier:
        original = verifier.side_effect
        count = 0
        def changing_crypto(command, *args):
            nonlocal count
            result = original(command, *args)
            count += 1
            if count == during_verification:
                latest = copy.deepcopy(api.statuses[0])
                latest.update(id=99, description=phase, state=state)
                api.statuses.insert(0, latest)
            return result
        verifier.side_effect = changing_crypto
        with pytest.raises(incidents.IncidentError, match="status history changed"):
            incidents.terminal_success(api, "deployment:wdpa-monthly")
    assert verifier.call_count == 2


@pytest.mark.parametrize("schema", [incidents.LEGACY_SCHEMA, incidents.PREVIOUS_SCHEMA])
@pytest.mark.parametrize("change", ["memo", "episode-field", "missing-episode-field"])
def test_historical_normalization_rejects_fields_before_rewriting(schema, change, tmp_path):
    record = signed_record()
    record["payload"]["schema"] = schema
    state = record["payload"]["state"]
    del state["terminal_validation"]
    if schema == incidents.LEGACY_SCHEMA:
        del state["incident"]["parent_view_sha256"]
    if change == "memo":
        state["terminal_validation"] = None
    elif change == "episode-field":
        state["incident"]["unexpected"] = None
    else:
        del state["incident"]["posts"]
    api = mock.Mock()
    api.pages.return_value = [{"id": 12, "state": "success", "description": "applied"}]
    with mock.patch.object(e, "verify") as signature:
        with pytest.raises(incidents.IncidentError, match="schema|episode"):
            incidents.Ledger(api, {}, tmp_path).verify(json.loads(json.dumps(record)))
    signature.assert_called_once()
