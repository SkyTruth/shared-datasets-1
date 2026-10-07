"""Failures remain visible; only exact verified recovery closes their incidents."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
from unittest import mock
import urllib.error
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import deployment_emission as emissions
from scripts import deployment_revision as deployments
from scripts import slack_incidents as incidents
from scripts.slack_incident_api import Slack, SlackError
from workflow_helpers import load_workflow, workflow_triggers

IDENTITY = {"team": "T123456789", "user": "U123456789", "channel": "C123456789"}
SHA = "a" * 40
SCOPE = "deployment:pmtiles-cdn"


def observation(**changes):
    value = {"scope": SCOPE, "kind": "deployment", "label": "pmtiles-cdn", "run_id": 100, "attempt": 1,
             "sha": SHA, "time": "2026-10-06T08:52:58Z", "job_id": 112187253799,
             "job_name": "pmtiles-cdn / Apply PMTiles CDN route sync", "conclusion": "failure", "proof": None}
    value.update(changes)
    return value


def success(**changes):
    value = observation(conclusion="success", run_id=101, job_id=0, job_name="pmtiles-cdn", sha="b" * 40,
                        time="2026-10-06T10:18:44Z", proof={"record_id": 6881366174, "status_id": 19335159323,
                                                         "artifact": "terraform@sha256:" + "f" * 64})
    value.update(changes)
    return value


def state():
    return incidents.fold(None, observation(), IDENTITY)


class MemoryLedger:
    def __init__(self, states=None):
        self.states = copy.deepcopy(states or {})
        self.writes = []
        self.api = mock.Mock()
        self.run = {"event": "workflow_dispatch", "actor": {"login": "jonaraphael"}}

    def load(self):
        return copy.deepcopy(self.states)

    def save(self, value, phase):
        incidents.validate_state(value)
        value = copy.deepcopy(value)
        self.states[value["scope"]] = value
        self.writes.append((phase, value))
        return {"payload": {"state": value}}

    def verify(self, record):
        return copy.deepcopy(record["payload"]["state"])


class FakeSlack:
    def __init__(self, *, crash=None):
        self.calls = []
        self.crash = crash

    def identity(self):
        return IDENTITY

    def post(self, payload, **routing):
        self.calls.append(("post", payload, routing))
        if len([item for item in self.calls if item[0] == "post"]) == self.crash:
            raise SlackError("delivery is unconfirmed")
        return f"1791270000.{len(self.calls):06d}"

    def update(self, ts, payload):
        self.calls.append(("update", payload, {"ts": ts}))

    def permalink(self, ts):
        return "https://example.slack.com/archives/" + IDENTITY["channel"] + "/p" + ts.replace(".", "")


def test_real_preview_sync_failures_are_distinct_from_bootstrap_and_other_jobs():
    targets = incidents.job_targets()
    scope, kind, label = incidents.scope_for_job(
        "preview-terraform-iam / Apply preview Terraform IAM sync / Apply Preview Terraform IAM sync", targets, "ci.yml")
    assert scope == "deployment:terraform-" + hashlib.sha256(b"Preview Terraform IAM sync").hexdigest()[:16]
    assert kind == "deployment"
    bootstrap = incidents.scope_for_job(
        "preview-terraform-iam / Bootstrap preview Terraform role authority / Apply Preview Terraform IAM bootstrap", targets, "ci.yml")
    assert bootstrap[0] != scope
    assert bootstrap[1] == "deployment"
    assert incidents.scope_for_job("pmtiles-cdn / Apply PMTiles CDN route sync", targets, "ci.yml")[0] == SCOPE
    assert incidents.scope_for_job("unregistered / Apply PMTiles CDN route sync", targets, "ci.yml")[1] == "job"
    assert incidents.scope_for_job("deploy", targets, "deployment-verification.yml")[1] == "job"
    assert label == "Preview Terraform IAM sync"


def test_publication_uses_original_pr_identity_and_exact_phase():
    targets = incidents.job_targets()
    first = incidents.scope_for_job("Publish PR #208 / Apply approved PR mutation plans (PR #208)", targets, "ci.yml")
    recovery = incidents.scope_for_job("Apply approved PR mutation plans (PR #208)", targets, "publish-dataset.yml")
    assert first == recovery
    assert first[0] != incidents.scope_for_job("Publish PR #209 / Apply approved PR mutation plans (PR #209)", targets, "ci.yml")[0]
    assert first[0] != incidents.scope_for_job("Publish PR #208 / Install reviewed feature-ID reset", targets, "ci.yml")[0]
    with pytest.raises(incidents.IncidentError, match="ambiguous PR"):
        incidents.scope_for_job("Publish PR #208 / Apply approved PR mutation plans (PR #209)", targets, "ci.yml")
    assert incidents.scope_for_job("Publish PR #208 / refresh-catalog / Build and publish catalog web", targets, "ci.yml")[0] == "deployment:catalog-web"


def test_retry_keeps_id_and_history_recovery_keeps_failures_and_new_failure_opens_episode():
    first = state()
    retry = observation(run_id=100, attempt=2, time="2026-10-06T09:00:00Z")
    second = incidents.fold(first, retry, IDENTITY)
    assert second["incident"]["id"] == first["incident"]["id"]
    assert len(second["incident"]["failures"]) == 2
    assert incidents.fold(second, retry, IDENTITY) == second
    recovered = incidents.fold(second, success(), IDENTITY, ancestor=lambda *_: True)
    assert recovered["incident"]["resolution"] == success()
    assert recovered["incident"]["failures"] == second["incident"]["failures"]
    assert incidents.fold(recovered, observation(), IDENTITY) == recovered
    recurrence = incidents.fold(recovered, observation(run_id=102, sha="b" * 40, time="2026-10-06T11:00:00Z"), IDENTITY)
    assert recurrence["incident"]["id"] != first["incident"]["id"]
    assert recurrence["incident"]["resolution"] is None


@pytest.mark.parametrize("update", [{"time": "2026-10-06T08:00:00Z"}, {"time": "2026-10-06T08:52:58Z"}])
def test_old_success_cannot_resolve_newer_failure(update):
    evidence = success()
    evidence.update(update)
    evidence["sha"] = SHA
    assert incidents.fold(state(), evidence, IDENTITY, ancestor=lambda *_: True) == state()


def test_divergent_revision_wrong_scope_unsigned_success_and_skips_never_resolve():
    assert incidents.fold(state(), success(), IDENTITY, ancestor=lambda *_: False) == state()
    with pytest.raises(incidents.IncidentError, match="scope changed"):
        incidents.fold(state(), success(scope="deployment:catalog-web"), IDENTITY)
    for conclusion in ("success", "skipped", "neutral"):
        with pytest.raises(incidents.IncidentError):
            incidents.fold(state(), observation(conclusion=conclusion), IDENTITY)
    with pytest.raises(incidents.IncidentError, match="another Slack"):
        incidents.fold(state(), observation(), {**IDENTITY, "channel": "C987654321"})


def test_unrelated_healthy_runs_do_not_create_messages_or_close_open_target():
    ledger = MemoryLedger({SCOPE: state()})
    observations = [observation(scope="job:unrelated", kind="job", conclusion="success", proof=None)]
    records = incidents.prepare(ledger, IDENTITY, observations, success=lambda *_: None)
    assert len(records) == 1  # Send only the original open incident.
    assert ledger.states[SCOPE]["incident"]["resolution"] is None
    assert incidents.prepare(MemoryLedger(), IDENTITY, observations, success=lambda *_: None) == []


def test_one_parent_retries_in_thread_and_one_broadcast_recovery_with_id_in_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    ledger, slack = MemoryLedger(), FakeSlack()
    records = incidents.prepare(ledger, IDENTITY, [observation()], success=lambda *_: None)
    assert records[0]["payload"]["state"]["incident"]["posts"]["parent"]["state"] == "claimed"
    incidents.deliver(ledger, records, slack)
    retry = observation(run_id=102, time="2026-10-06T09:00:00Z")
    records = incidents.prepare(ledger, IDENTITY, [retry], success=lambda *_: None)
    incidents.deliver(ledger, records, slack)
    records = incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True, success=lambda *_: success())
    incidents.deliver(ledger, records, slack)
    posts = [item for item in slack.calls if item[0] == "post"]
    assert len(posts) == 3
    assert not posts[0][2]
    assert posts[1][2]["broadcast"] is False
    assert posts[2][2]["broadcast"] is True
    assert posts[1][2]["thread_ts"] == posts[2][2]["thread_ts"]
    assert "Original failure" in slack.calls[-1][1]["text"]
    assert "Resolved" in slack.calls[-1][1]["text"]
    assert incidents.prepare(ledger, IDENTITY, [retry], success=lambda *_: success()) == []
    assert "SD-" in (tmp_path / "summary.md").read_text()


def test_permalink_failure_retains_acknowledgement_and_resumes_without_another_post():
    ledger, slack = MemoryLedger(), FakeSlack()
    records = incidents.prepare(ledger, IDENTITY, [observation()], success=lambda *_: None)
    with mock.patch.object(slack, "permalink", side_effect=SlackError("permalink rejected")):
        with pytest.raises(SlackError, match="permalink rejected"):
            incidents.deliver(ledger, records, slack)
    acknowledged = copy.deepcopy(ledger.states[SCOPE]["incident"]["posts"])
    assert acknowledged["parent"]["state"] == "delivered"
    assert ledger.writes[-1][0] == "outcomes"
    assert ledger.states[SCOPE]["incident"]["permalink"] == ""

    # A fresh worker loads the acknowledged state, not the original post claim.
    records = incidents.prepare(ledger, IDENTITY, [], success=lambda *_: None)
    assert len(records) == 1
    assert records[0]["payload"]["state"]["incident"]["posts"] == acknowledged
    incidents.deliver(ledger, records, slack)
    assert len([item for item in slack.calls if item[0] == "post"]) == 1
    assert ledger.states[SCOPE]["incident"]["posts"] == acknowledged
    assert ledger.states[SCOPE]["incident"]["permalink"]
    assert incidents.prepare(ledger, IDENTITY, [], success=lambda *_: None) == []


@pytest.mark.parametrize("crash", [1, 2])
def test_unconfirmed_parent_or_recovery_reply_is_never_automatically_repeated(crash):
    ledger, slack = MemoryLedger(), FakeSlack(crash=crash)
    records = incidents.prepare(ledger, IDENTITY, [observation()], success=lambda *_: None)
    if crash == 2:
        incidents.deliver(ledger, records, slack)
        records = incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True, success=lambda *_: success())
    with pytest.raises(SlackError, match="unconfirmed"):
        incidents.deliver(ledger, records, slack)
    call_count = len(slack.calls)
    assert incidents.prepare(ledger, IDENTITY, [], success=lambda *_: None) == []
    assert len(slack.calls) == call_count


def test_uncertain_target_does_not_block_other_alerts_and_retains_new_events():
    ledger = MemoryLedger()
    incidents.prepare(ledger, IDENTITY, [observation()], success=lambda *_: None)
    first_id = ledger.states[SCOPE]["incident"]["id"]
    newer = observation(run_id=102, time="2026-10-06T11:00:00Z")
    other = observation(scope="deployment:catalog-web", label="catalog-web", run_id=103)
    records = incidents.prepare(ledger, IDENTITY, [newer, other], success=lambda *_: None)
    assert len(records) == 1 and records[0]["payload"]["state"]["scope"] == other["scope"]
    assert ledger.states[SCOPE]["incident"]["id"] == first_id
    assert ledger.states[SCOPE]["deferred"] == [newer]


def test_uncertain_resolution_is_preserved_until_reconciled_before_new_episode():
    ledger, slack = MemoryLedger(), FakeSlack()
    records = incidents.prepare(ledger, IDENTITY, [observation()], success=lambda *_: None)
    incidents.deliver(ledger, records, slack)
    records = incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True, success=lambda *_: success())
    old_id = ledger.states[SCOPE]["incident"]["id"]
    new_failure = observation(sha="b" * 40, run_id=103, time="2026-10-06T11:00:00Z")
    assert incidents.prepare(ledger, IDENTITY, [new_failure], success=lambda *_: None) == []
    assert ledger.states[SCOPE]["incident"]["id"] == old_id
    operation = next(key for key in ledger.states[SCOPE]["incident"]["posts"] if key.startswith("resolved:"))
    records = incidents.reconcile(ledger, IDENTITY, old_id, operation, "delivered", "1791270000.000099")
    incidents.deliver(ledger, records, slack)
    records = incidents.prepare(ledger, IDENTITY, [], success=lambda *_: None)
    assert records[0]["payload"]["state"]["incident"]["id"] != old_id
    assert records[0]["payload"]["state"]["incident"]["failures"] == [new_failure]


def test_signed_healthy_watermark_advances_without_another_recovery_post():
    ledger, slack = MemoryLedger(), FakeSlack()
    records = incidents.prepare(ledger, IDENTITY, [observation()], success=lambda *_: None)
    incidents.deliver(ledger, records, slack)
    records = incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True, success=lambda *_: success())
    incidents.deliver(ledger, records, slack)
    count = len(slack.calls)
    newer = success(sha="c" * 40, run_id=104, time="2026-10-06T11:00:00Z")
    assert incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True, success=lambda *_: newer) == []
    assert ledger.states[SCOPE]["healthy"] == newer
    assert ledger.states[SCOPE]["incident"]["resolution"] == success()
    late = observation(sha="b" * 40, run_id=105, time="2026-10-06T11:30:00Z")
    assert incidents.prepare(ledger, IDENTITY, [late], ancestor=lambda *_: True, success=lambda *_: newer) == []
    assert len(slack.calls) == count


def test_checkpoint_sealing_requires_exact_owner_review_and_sends_nothing():
    ledger = MemoryLedger()
    record = signed_record()
    ledger.api.get.return_value = record
    ledger.api.pages.return_value = [record]
    digest = hashlib.sha256(emissions.canonical(record["payload"])).hexdigest()
    assert incidents.seal_checkpoint(ledger, IDENTITY, record["payload"]["state"]["incident"]["id"], f"checkpoint:10:{digest}") == []
    assert len(ledger.writes) == 1
    with pytest.raises(incidents.IncidentError, match="reviewed payload"):
        incidents.seal_checkpoint(ledger, IDENTITY, record["payload"]["state"]["incident"]["id"], "checkpoint:10:" + "0" * 64)


def test_checkpoint_sealing_reverifies_original_recovery_and_newer_health_separately():
    ledger = MemoryLedger()
    record = signed_record()
    recovered = incidents.fold(record["payload"]["state"], success(), IDENTITY, ancestor=lambda *_: True)
    newer = success(sha="c" * 40, run_id=104, time="2026-10-06T11:00:00Z",
                    proof={"record_id": 6881366175, "status_id": 19335159324, "artifact": "terraform@sha256:" + "e" * 64})
    record["payload"]["state"] = incidents.fold(recovered, newer, IDENTITY, ancestor=lambda *_: True)
    ledger.api.get.return_value = record
    ledger.api.pages.return_value = [record]
    digest = hashlib.sha256(emissions.canonical(record["payload"])).hexdigest()
    operation = f"checkpoint:10:{digest}"
    with mock.patch.object(incidents, "verify_saved_success") as verify, mock.patch.object(deployments, "git_ancestor", return_value=True):
        assert incidents.seal_checkpoint(ledger, IDENTITY, recovered["incident"]["id"], operation) == []
        assert [call.args[1] for call in verify.call_args_list] == [success(), newer]
    with mock.patch.object(incidents, "verify_saved_success"), mock.patch.object(deployments, "git_ancestor", return_value=False):
        with pytest.raises(incidents.IncidentError, match="ancestry"):
            incidents.seal_checkpoint(ledger, IDENTITY, recovered["incident"]["id"], operation)


def test_saved_recovery_requires_exact_signed_historical_status_not_latest_status():
    api = mock.Mock()
    evidence = success()
    proof = evidence["proof"]
    record = {"id": proof["record_id"], "sha": evidence["sha"]}
    status = {"id": proof["status_id"], "state": "success", "description": "applied", "created_at": evidence["time"],
              "log_url": emissions.invocation(incidents.REPOSITORY, evidence["run_id"], evidence["attempt"])}
    api.get.return_value = record
    api.pages.return_value = [{**status, "id": proof["status_id"] + 1, "state": "failure"}, status]
    payload = {"target": "pmtiles-cdn", "artifact": proof["artifact"]}
    with mock.patch.object(deployments, "verify_record", return_value=payload), mock.patch.object(deployments, "verify_receipt") as verify:
        incidents.verify_saved_success(api, evidence)
        verify.assert_called_once_with(api, incidents.REPOSITORY, record, status)
        assert api.get.call_args.args[0].endswith(f"/deployments/{proof['record_id']}")
        with pytest.raises(incidents.IncidentError, match="differs"):
            incidents.verify_saved_success(api, {**evidence, "time": "2026-10-06T10:18:45Z"})
    with mock.patch.object(deployments, "verify_record", return_value=payload), mock.patch.object(deployments, "verify_receipt", side_effect=deployments.DeploymentError("unsigned")):
        with pytest.raises(deployments.DeploymentError, match="unsigned"):
            incidents.verify_saved_success(api, evidence)


def test_unattended_startup_failure_with_no_jobs_retains_workflow_identity():
    api = mock.Mock()
    run = {"id": 100, "run_attempt": 1, "name": "Catalog viewer deploy", "event": "workflow_run", "head_sha": SHA,
           "conclusion": "startup_failure", "updated_at": "2026-10-06T10:18:44Z", "status": "completed",
           "head_branch": "main", "head_repository": {"full_name": incidents.REPOSITORY}}
    event = {"workflow_run": run, "repository": {"full_name": incidents.REPOSITORY}}
    api.pages.return_value = []
    with mock.patch.object(incidents.alerts, "verify_source_event", return_value=True):
        failed = incidents.source_observations(api, event, {})
        assert len(failed) == 1 and failed[0]["kind"] == "workflow"
        assert failed[0]["scope"] == "workflow:catalog-viewer-deploy.yml"
        run.update(conclusion="success", updated_at="2026-10-06T11:00:00Z")
        recovered = incidents.source_observations(api, event, {})[0]
        opened = incidents.fold(None, failed[0], IDENTITY)
        assert incidents.fold(opened, recovered, IDENTITY, ancestor=lambda *_: True)["incident"]["resolution"] == recovered
    with mock.patch.object(incidents.alerts, "verify_source_event", return_value=False):
        assert incidents.source_observations(api, event, {}) == []


def test_batch_signatures_bind_exact_checkpoint_and_keep_production_single_subject_default():
    record = signed_record()
    status = {"id": 12, "state": "success", "description": "applied", "log_url": emissions.invocation(incidents.REPOSITORY, 11, 1)}
    value = emissions.receipt(incidents.REPOSITORY, record, status)
    subject = {"name": emissions.name(value), "digest": {"sha256": hashlib.sha256(emissions.canonical(value)).hexdigest()}}
    expected = {"sourceRepositoryRef": "refs/heads/main"}
    result = [{"verificationResult": {"signature": {"certificate": expected}, "verifiedTimestamps": ["verified"],
                                      "statement": {"_type": "https://in-toto.io/Statement/v1", "predicateType": emissions.PREDICATE,
                                                    "subject": [subject, {"name": "other.json", "digest": {"sha256": "b" * 64}}]}}}]
    emissions.verify_result(result, value, expected, batch=True)
    with pytest.raises(emissions.EmissionError):
        emissions.verify_result(result, value, expected)
    changed = copy.deepcopy(value)
    changed["payload"]["state"]["identity"]["channel"] = "C987654321"
    with pytest.raises(emissions.EmissionError):
        emissions.verify_result(result, changed, expected, batch=True)


def test_same_named_jobs_from_other_workflows_cannot_close_incident():
    assert incidents.scope_for_job("pending", {}, "deployment-verification.yml")[0] != incidents.scope_for_job("pending", {}, "deployment-recovery.yml")[0]


def test_manual_publisher_reset_uses_complete_same_attempt_pr_identity():
    api = mock.Mock()
    run = {"id": 100, "run_attempt": 1, "name": "Approved dataset mutation", "event": "workflow_dispatch",
           "head_sha": SHA, "conclusion": "success", "updated_at": "2026-10-06T10:18:44Z"}
    job = {"id": 1, "name": "Install reviewed feature-ID reset", "status": "completed", "conclusion": "success", "completed_at": observation()["time"]}
    apply = {**job, "id": 2, "name": "Apply approved PR mutation plans (PR #208)"}
    api.pages.return_value = [job, apply]
    with mock.patch.object(incidents.alerts, "verify_source_event", return_value=True):
        result = incidents.source_observations(api, {"workflow_run": run, "repository": {"full_name": incidents.REPOSITORY}}, {})
        assert result[0]["scope"] == "publication:pr-208:reset"
        api.pages.return_value = [job]
        assert incidents.source_observations(api, {"workflow_run": run, "repository": {"full_name": incidents.REPOSITORY}}, {})[0]["kind"] == "job"


@pytest.mark.parametrize("result,ts", [("delivered", "1791270000.000001"), ("not-delivered", "")])
def test_owner_reconciliation_acknowledges_exact_uncertain_operation(result, ts):
    ledger = MemoryLedger()
    records = incidents.prepare(ledger, IDENTITY, [observation()], success=lambda *_: None)
    incident = records[0]["payload"]["state"]["incident"]
    result_records = incidents.reconcile(ledger, IDENTITY, incident["id"], "parent", result, ts)
    assert result_records[0]["payload"]["state"]["incident"]["posts"]["parent"]["state"] == ("delivered" if ts else "claimed")
    ledger.run["actor"]["login"] = "someone"
    with pytest.raises(incidents.IncidentError, match="repository owner"):
        incidents.reconcile(ledger, IDENTITY, incident["id"], "parent", result, ts)


def signed_record():
    value = state()
    value["incident"]["posts"]["parent"] = {"state": "claimed", "ts": None}
    return {"id": 10, "sha": SHA, "ref": SHA, "task": "slack-incident", "environment": "slack-incidents",
            "production_environment": False, "creator": {"login": "github-actions[bot]"},
            "payload": {"schema": incidents.SCHEMA, "state": value, "execution_run_id": 11, "execution_run_attempt": 1}}


def test_forged_actions_bot_ledger_claim_cannot_choose_parent_or_suppress_real_alert(tmp_path):
    api = mock.Mock()
    api.pages.return_value = [{"id": 12, "state": "success", "description": "applied"}]
    ledger = incidents.Ledger(api, {}, tmp_path)
    with mock.patch.object(emissions, "verify", side_effect=emissions.EmissionError("forged receipt")):
        with pytest.raises(emissions.EmissionError, match="forged"):
            ledger.verify(signed_record())
    with mock.patch.object(emissions, "verify") as verify:
        assert ledger.verify(signed_record())["scope"] == SCOPE
        assert verify.call_args.kwargs == {"original_signer": incidents.WORKFLOW, "batch": True}
    with pytest.raises(deployments.DeploymentError, match="unrecognized deployment record"):
        deployments.record_payload(signed_record())


def test_checkpoint_writes_disable_merges_production_and_auto_inactivation(tmp_path):
    api = mock.Mock()
    def post(path, payload):
        if path.endswith("/deployments"):
            return {"id": 10, "sha": SHA, **copy.deepcopy(payload)}
        return {"id": 12, **payload}
    api.post.side_effect = post
    ledger = incidents.Ledger(api, {"head_sha": SHA, "id": 11, "run_attempt": 1}, tmp_path)
    ledger.save(signed_record()["payload"]["state"], "claims")
    payload = api.post.call_args_list[0].args[1]
    assert payload["required_contexts"] == []
    assert payload["auto_merge"] is False and payload["production_environment"] is False
    assert payload["environment"] == "slack-incidents"
    assert api.post.call_args_list[1].args[1]["auto_inactive"] is False
    assert list((tmp_path / "claims").glob("record-*-status-*.json"))


def test_runtime_started_applied_or_missing_receipt_cannot_establish_terminal_success():
    api = mock.Mock()
    record = {"id": 10, "sha": SHA}
    api.pages.return_value = [record]
    payload = {"target": "wdpa-monthly", "artifact": "image@sha256:" + "f" * 64}
    status = {"id": 12, "state": "success", "description": "applied", "created_at": "2026-10-06T10:18:44Z",
              "log_url": emissions.invocation(incidents.REPOSITORY, 11, 1)}
    with mock.patch.object(deployments, "verify_record", return_value=payload), mock.patch.object(deployments, "verified_statuses", return_value=[status]):
        assert incidents.terminal_success(api, "deployment:wdpa-monthly") is None
        payload["target"] = "pmtiles-cdn"
        assert incidents.terminal_success(api, SCOPE)["proof"]["status_id"] == 12
    with mock.patch.object(deployments, "verify_record", side_effect=deployments.DeploymentError("unsigned")):
        with pytest.raises(deployments.DeploymentError, match="unsigned"):
            incidents.terminal_success(api, SCOPE)


def test_exact_completed_ci_jobs_and_publication_negative_controls():
    run = {"id": 100, "run_attempt": 1, "name": "CI", "event": "push", "head_sha": SHA, "conclusion": "failure"}
    event = {"workflow_run": run}
    api = mock.Mock()
    ready = {"id": 1, "name": "ci-ready", "status": "completed", "conclusion": "success", "completed_at": "2026-10-06T08:00:00Z"}
    failed = {"id": 2, "name": observation()["job_name"], "status": "completed", "conclusion": "failure", "completed_at": observation()["time"]}
    api.pages.return_value = [ready, failed]
    with mock.patch.object(incidents.alerts, "verify_source_event", return_value=True):
        result = incidents.source_observations(api, event, incidents.job_targets())
        assert len(result) == 1 and result[0]["scope"] == SCOPE
        api.pages.assert_called_with("repos/SkyTruth/shared-datasets-1/actions/runs/100/attempts/1/jobs?per_page=100", field="jobs")
        api.pages.return_value = [ready, {**failed, "conclusion": "skipped"}]
        assert incidents.source_observations(api, event, incidents.job_targets()) == []
    with mock.patch.object(incidents.alerts, "verify_source_event", return_value=False):
        assert incidents.source_observations(api, event, incidents.job_targets()) == []


def test_hourly_replay_is_complete_bounded_main_only_and_activation_limited():
    api = mock.Mock()
    api.pages.return_value = [{"status": "completed", "name": "CI", "head_branch": "main", "head_repository": {"full_name": incidents.REPOSITORY}}]
    with mock.patch.object(incidents, "source_observations", return_value=[observation()]):
        assert incidents.replay_observations(api, {}, "2026-10-06T08:00:00Z", now=datetime(2026, 10, 7, 12, tzinfo=timezone.utc)) == [observation()]
    path = api.pages.call_args.args[0]
    assert "2026-10-06T12%3A00%3A00Z" in path and "branch=main" in path
    assert api.pages.call_args.kwargs == {"field": "workflow_runs"}
    api.pages.side_effect = deployments.DeploymentError("incomplete API enumeration")
    with pytest.raises(deployments.DeploymentError, match="incomplete"):
        incidents.replay_observations(api, {}, "2026-10-06T08:00:00Z")


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def client(responses):
    calls = []
    def opener(request, timeout):
        calls.append((request.full_url, json.loads(request.data), timeout))
        return Response(json.dumps(responses.pop(0)).encode())
    return Slack("xoxb-test-secret", IDENTITY["channel"], opener=opener), calls


def test_slack_api_uses_fixed_channel_exact_parent_and_broadcast_only_for_recovery():
    slack, calls = client([{"ok": True, "channel": IDENTITY["channel"], "ts": "1791270000.000001"}])
    assert slack.post({"text": "Recovered"}, thread_ts="1791260000.000001", broadcast=True) == "1791270000.000001"
    assert calls[0][1]["reply_broadcast"] is True and calls[0][1]["channel"] == IDENTITY["channel"]
    with pytest.raises(SlackError, match="override routing"):
        slack.post({"text": "hello", "channel": "evil"})


def test_permalink_uses_documented_get_query_contract_without_timestamp_conversion():
    ts = "1791270000.000001"
    link = "https://example.slack.com/archives/" + IDENTITY["channel"] + "/p" + ts.replace(".", "")
    calls = []
    def opener(request, timeout):
        calls.append(request)
        assert request.get_method() == "GET"
        assert request.data is None
        parsed = urlsplit(request.full_url)
        assert parsed.scheme == "https" and parsed.netloc == "slack.com"
        assert parsed.path == "/api/chat.getPermalink"
        assert parse_qs(parsed.query) == {"channel": [IDENTITY["channel"]], "message_ts": [ts]}
        assert request.get_header("Authorization") == "Bearer xoxb-test-secret"
        assert request.get_header("Content-type") is None
        assert timeout == 30
        return Response(json.dumps({"ok": True, "permalink": link}).encode())
    slack = Slack("xoxb-test-secret", IDENTITY["channel"], opener=opener)
    assert slack.permalink(ts) == link
    assert len(calls) == 1


@pytest.mark.parametrize("response", [
    {"ok": False, "error": "invalid_arguments"}, {"ok": True}, {"ok": True, "permalink": None},
    {"ok": True, "permalink": "https://evil.example/archives/C123456789/p1791270000000001"},
    {"ok": True, "permalink": "https://example.slack.com/archives/C987654321/p1791270000000001"},
    {"ok": True, "permalink": "https://example.slack.com/archives/C123456789/p1791270000000002"},
    {"ok": True, "permalink": "https://example.slack.com/archives/C123456789/p1791270000000001?redirect=https://evil.example"},
])
def test_permalink_requires_acknowledged_success_and_exact_parent_without_retries(response):
    calls = []
    def opener(request, timeout):
        calls.append(request)
        return Response(json.dumps(response).encode())
    slack = Slack("xoxb-test-secret", IDENTITY["channel"], opener=opener)
    with pytest.raises(SlackError):
        slack.permalink("1791270000.000001")
    assert len(calls) == 1
    assert calls[0].get_method() == "GET"


@pytest.mark.parametrize("method", ["auth.test", "chat.postMessage", "chat.update"])
def test_other_slack_methods_keep_json_post_transport(method):
    def opener(request, timeout):
        assert request.get_method() == "POST"
        assert request.full_url == "https://slack.com/api/" + method
        assert request.get_header("Content-type") == "application/json; charset=utf-8"
        assert json.loads(request.data) == {"channel": IDENTITY["channel"]}
        return Response(b'{"ok":true}')
    Slack("xoxb-test-secret", IDENTITY["channel"], opener=opener).call(method, {"channel": IDENTITY["channel"]})


@pytest.mark.parametrize("response", [{"ok": False, "error": "internal_error"}, {"ok": True},
                                     {"ok": True, "channel": "evil", "ts": "1791270000.000001"},
                                     {"ok": True, "channel": IDENTITY["channel"], "ts": 1791270000.123456}])
def test_slack_http_200_is_insufficient_and_bad_responses_never_acknowledge(response):
    slack, _ = client([response])
    with pytest.raises(SlackError):
        slack.post({"text": "hello"})


def test_persisted_message_timestamp_cannot_be_a_float():
    value = state()
    value["incident"]["posts"]["parent"] = {"state": "delivered", "ts": 1791270000.123456}
    with pytest.raises(incidents.IncidentError, match="acknowledgement"):
        incidents.validate_state(value)


def test_slack_errors_do_not_echo_tokens_and_do_not_retry():
    calls = []
    def opener(request, timeout):
        calls.append(request)
        raise urllib.error.URLError("xoxb-test-secret")
    slack = Slack("xoxb-test-secret", IDENTITY["channel"], opener=opener)
    with pytest.raises(SlackError) as error:
        slack.post({"text": "hello"})
    assert "xoxb-test-secret" not in str(error.value)
    assert len(calls) == 1


def test_malicious_labels_cannot_insert_mentions_and_id_is_searchable():
    value = state()
    value["label"] = "<@U123> & <https://evil|click>"
    payload = incidents.render(value)
    assert "<@U123>" not in payload["text"] and "&lt;@U123&gt;" in payload["text"]
    assert value["incident"]["id"] in payload["text"]
    assert payload["metadata"]["event_payload"]["incident_id"] == value["incident"]["id"]


def test_workflow_has_safe_activation_exact_checkout_signed_claim_barrier_and_preserved_failure_filter():
    root = Path(__file__).resolve().parents[1]
    workflow = load_workflow(root / incidents.WORKFLOW)
    legacy = workflow["jobs"]["notify"]
    worker = workflow["jobs"]["incidents"]
    assert "INCIDENTS_ENABLED != 'true'" in legacy["if"]
    assert "INCIDENTS_ENABLED == 'true'" in worker["if"]
    assert worker["concurrency"]["queue"] == "max" and worker["concurrency"]["cancel-in-progress"] is False
    checkout = next(step for step in worker["steps"] if step.get("uses") == "actions/checkout@v4")
    assert checkout["with"] == {"ref": "${{ github.workflow_sha }}", "fetch-depth": 0, "persist-credentials": False}
    assert "download-artifact" not in str(worker)
    assert "id-token" in worker["permissions"] and "deployments" in worker["permissions"]
    assert "gcp" not in str(worker).lower()
    steps = {step.get("id"): step for step in worker["steps"] if step.get("id")}
    assert "steps.attest-claims.outcome == 'success'" in steps["deliver"]["if"]
    assert "always()" in steps["attest-outcomes"]["if"]
    assert "workflow_dispatch" in workflow_triggers(workflow)
    assert "Approved dataset mutation" in workflow_triggers(workflow)["workflow_run"]["workflows"]
