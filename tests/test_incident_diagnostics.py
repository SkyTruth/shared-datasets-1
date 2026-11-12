"""Exact workflow errors survive observation, persisted claims and Slack delivery."""
import copy
import json
import subprocess
from unittest.mock import Mock

import pytest

from scripts import slack_incidents as incidents
from test_slack_incidents import FakeSlack, IDENTITY, MemoryLedger, SHA


ERROR = "The version '3.12.12' with architecture 'x64' was not found for Ubuntu 26.04."


def setup_failure():
    run = {"id": 43291191693, "run_attempt": 1, "name": "Alerts / Workflow health and Slack reconciliation",
           "event": "workflow_dispatch", "head_sha": SHA, "conclusion": "failure",
           "updated_at": "2026-11-12T19:32:41Z"}
    job = {"id": 131400000001, "name": "Reconcile Slack incident status", "status": "completed",
           "conclusion": "failure", "completed_at": run["updated_at"], "steps": [
               {"name": "Run actions/setup-python@v5", "number": 3, "status": "completed", "conclusion": "failure",
                "started_at": "2026-11-12T19:32:30Z", "completed_at": "2026-11-12T19:32:35Z"},
               {"name": "Prepare incident changes and claim posts", "number": 7, "status": "completed", "conclusion": "skipped"},
               {"name": "Deliver or update incident messages", "number": 9, "status": "completed", "conclusion": "skipped"},
           ]}
    return run, job


def test_python_setup_error_survives_wire_claim_and_original_parent_recovery(monkeypatch):
    run, job = setup_failure()
    api = Mock()
    api.pages.return_value = [job]
    error_reader = Mock(return_value=ERROR)
    monkeypatch.setattr(incidents, "job_error", error_reader)
    monkeypatch.setattr(incidents.alerts, "verify_source_event", lambda *_: True)
    observed = incidents.source_observations(api, {"workflow_run": run, "repository": {"full_name": incidents.REPOSITORY}}, {})
    ledger, slack = MemoryLedger(), FakeSlack()
    records = incidents.prepare(ledger, IDENTITY, json.loads(json.dumps(observed)), success=lambda *_: (None, None))
    incidents.deliver(ledger, json.loads(json.dumps(records)), slack)
    opened = slack.calls[0][1]["text"]
    assert ERROR in opened
    assert "Reconciliation never started" in opened
    assert "Repair Python setup" in opened
    assert f"/job/{job['id']}#step:3:1" in opened
    for clutter in ("Impact:", "Mutation state:", "operation owner unknown", "Failed attempts:* 1", "SD-"):
        assert clutter not in opened
    assert len(opened.splitlines()) <= 5
    assert error_reader.call_args.args == (job["id"],)

    recovered = copy.deepcopy(observed[0])
    recovered.update(run_id=43303919987, job_id=131482048150, conclusion="success", time="2026-11-12T20:55:08Z")
    recovered.pop("diagnostic", None)
    ledger.states = json.loads(json.dumps(ledger.states))
    records = incidents.prepare(ledger, IDENTITY, [recovered], ancestor=lambda *_: True, success=lambda *_: (None, None))
    incidents.deliver(ledger, json.loads(json.dumps(records)), slack)
    assert [method for method, *_ in slack.calls if method in {"post", "update"}] == ["post", "update"]
    closed = slack.calls[-1][1]["text"]
    assert "Resolved" in closed and "Job completed successfully" in closed and "None" in closed
    assert "published" not in closed.lower() and "unknown" not in closed.lower()
    assert ERROR in closed  # Retain the original error after recovery.
    assert slack.calls[-1][2]["ts"] == ledger.states[observed[0]["scope"]]["incident"]["posts"]["parent"]["ts"]


@pytest.mark.parametrize("conclusion", ["success", "failure", None])
def test_reconciliation_effect_requires_both_write_steps_proven_skipped(monkeypatch, conclusion):
    run, job = setup_failure()
    job["steps"][-1]["conclusion"] = conclusion
    api = Mock()
    api.pages.return_value = [job]
    error_reader = Mock(return_value=ERROR)
    monkeypatch.setattr(incidents, "job_error", error_reader)
    monkeypatch.setattr(incidents.alerts, "verify_source_event", lambda *_: True)
    observed = incidents.source_observations(api, {"workflow_run": run, "repository": {"full_name": incidents.REPOSITORY}}, {})
    value = incidents.fold(None, observed[0], IDENTITY)
    assert "Reconciliation never started" not in incidents.render(value)["text"]


def test_cloud_run_closure_stays_warning_and_keeps_error():
    from services.monitoring_slack import executions
    value = {"source": {"project": "shared-datasets-1", "job": "sea-ice-daily", "execution": "sea-ice-daily-abcde"},
             "reviews": {}, "decisions": {}, "failures": {"0.failure": {"closed": True, "cause": "RuntimeError: release incomplete"}}}
    text = executions.render(value)["text"]
    assert text.startswith(":warning:")
    assert "RuntimeError: release incomplete" in text
    assert "Verify publication before retrying" in text
    assert "white_check_mark" not in text and "operation owner unknown" not in text


def test_safety_discovery_replays_timestamped_setup_failure_without_notification_client(monkeypatch, tmp_path):
    from scripts import operational_safety_check as safety
    from test_operational_safety_check import OperationalAPI, START, NOW
    api = OperationalAPI()  # Same get/pages protocol as the production safety client; no job_error.
    source = api.source(100, "unattended-workflow-alert.yml")
    source["event"] = "workflow_dispatch"
    api.add_source(source)
    _, job = setup_failure()
    job["completed_at"] = source["updated_at"]
    step = job["steps"][0]
    step["started_at"] = step["completed_at"] = source["updated_at"]
    api.jobs[100] = [job]
    reads = []
    def read(path):
        reads.append(path)
        return f"{source['updated_at']} ##[error]{ERROR}\n".encode()
    monkeypatch.setattr(incidents, "github_read", read)
    settled, claims = safety.dry_reconciliation(api, api.current, IDENTITY, [source], START, NOW, tmp_path)
    assert settled is False and claims == [] and api.posts == []
    assert reads == [f"repos/{incidents.REPOSITORY}/actions/jobs/{job['id']}/logs"]


def test_job_log_excerpt_is_bound_to_the_failed_job_and_step_interval(monkeypatch):
    _, job = setup_failure()
    raw = ("2026-11-12T19:32:29.000Z ##[error]Earlier unrelated step\n"
           f"2026-11-12T19:32:33.1234567Z ##[error]{ERROR}\n"
           "2026-11-12T19:32:35Z ##[error]Process completed with exit code 1.\n"
           "2026-11-12T19:32:40Z ValueError: later cleanup failed\n").encode()
    reads = []
    def read(path):
        reads.append(path)
        return raw
    monkeypatch.setattr(incidents, "github_read", read)
    assert incidents.job_error(job["id"], step=job["steps"][0]) == ERROR
    assert reads == [f"repos/{incidents.REPOSITORY}/actions/jobs/{job['id']}/logs"]


@pytest.mark.parametrize("problem", [subprocess.CalledProcessError(1, "gh"), subprocess.TimeoutExpired("gh", 60)])
def test_missing_error_logs_still_deliver_the_failed_step(monkeypatch, problem):
    run, job = setup_failure()
    def unavailable(_):
        raise problem
    monkeypatch.setattr(incidents, "github_read", unavailable)
    observed = incidents.from_job(run, job, {}, error_reader=incidents.job_error)
    payload = incidents.render(incidents.fold(None, observed, IDENTITY))["text"]
    assert "Failed step: Run actions/setup-python@v5" in payload
    assert "Reconciliation never started" in payload and "Repair Python setup" in payload


def test_error_excerpt_redacts_credentials_escapes_mentions_and_stays_one_line():
    from services.monitoring_slack import messages
    secret = "xoxb-this-is-a-fixture-token"
    raw = f"\x1b[31mRuntimeError: {secret}\nAuthorization: Bearer fixture-bearer <@U123> https://example.test/log?signature=fixture-signature\x1b[0m"
    safe = messages.plain(raw)
    assert secret not in safe and "fixture-bearer" not in safe and "fixture-signature" not in safe
    assert "\n" not in safe and "\x1b" not in safe
    payload = messages.card("failure", details=[safe, "a" * 900], action="Inspect logs.",
                            links=[("https://example.test", "Evidence")], status="error")
    assert "<@U123>" not in payload["text"] and "&lt;@U123&gt;" in payload["text"]
    assert "a" * 501 not in payload["text"]


def test_verified_v4_checkpoint_retains_its_original_parent_and_history(monkeypatch, tmp_path):
    from test_slack_incidents import signed_record
    record = signed_record()
    record["payload"]["schema"] = incidents.DISPLAY_SCHEMA
    before = copy.deepcopy(record["payload"]["state"])
    api = Mock()
    api.pages.return_value = [{"id": 12, "state": "success", "description": "applied"}]
    verified = []
    def verify(*_, **__):
        assert record["payload"]["state"] == before
        verified.append(True)
    monkeypatch.setattr(incidents.emissions, "verify", verify)
    current = incidents.Ledger(api, {}, tmp_path).verify(record)
    assert verified == [True]
    assert current["incident"] == before["incident"] and current["identity"] == before["identity"]
    assert "diagnostic" not in current["incident"]["failures"][0]


@pytest.mark.parametrize("change", [{"effect": "publication_verified"}, {"number": True}, {"error": {"message": "bad"}}])
def test_display_diagnostics_cannot_introduce_unvalidated_outcomes(change):
    run, job = setup_failure()
    observed = incidents.from_job(run, job, {})
    observed["diagnostic"].update(change)
    with pytest.raises(incidents.IncidentError, match="invalid failure diagnostic"):
        incidents.validate_observation(observed)


@pytest.mark.parametrize("location", [None, "failure", "deferred"])
def test_checkpoint_version_matches_its_persisted_diagnostic_fields(tmp_path, location):
    from test_deployment_checkpoint_revert import CheckpointAPI
    run, job = setup_failure()
    diagnostic = incidents.from_job(run, job, {})
    original = {key: value for key, value in diagnostic.items() if key != "diagnostic"}
    value = incidents.fold(None, diagnostic if location == "failure" else original, IDENTITY)
    if location == "deferred":
        value["deferred"] = [{**diagnostic, "run_id": 43291191694}]
    api = CheckpointAPI()
    record = incidents.Ledger(api, api.runs[14], tmp_path).save(value, "claims")
    assert record["payload"]["schema"] == (incidents.SCHEMA if location else incidents.DISPLAY_SCHEMA)
    assert record["payload"]["state"] == value


def test_v4_terminal_validation_cache_survives_the_compatible_reader():
    from test_slack_incidents import state, success
    value = incidents.fold(state(), success(), IDENTITY, ancestor=lambda *_: True)
    value["terminal_validation"] = {"evidence_sha256": "a" * 64, "contract_sha256": "b" * 64}
    assert incidents.migrate_verified_state(value, incidents.DISPLAY_SCHEMA) == value
