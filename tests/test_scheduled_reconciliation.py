"""Scheduled GitHub recovery survives optional cloud failure and signed phases."""
from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
import json
from pathlib import Path
import shlex
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from scripts import cloud_observation_ledger as cycles, deployment_emission as emissions
from scripts import operational_reconcile as worker, slack_incidents as incidents
from scripts.ci_contract import select_deployments
from scripts.deployment_receipt_contracts import notification_boundary
from scripts.operational_delivery_activation import PROTOCOL_FILES
from scripts.workflow_failure_alert import OBSERVER_WORKFLOWS
from test_cloud_observation_ledger import API, RUN
from test_slack_incidents import FakeSlack, IDENTITY

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/unattended-workflow-alert.yml"
NOW = datetime(2026, 10, 21, 12, 12, tzinfo=timezone.utc)
SHADOW = {"mode": "shadow", "policy_deployment_id": None, "policy_status_id": None, "config_sha256": "d" * 64}


class PhaseAPI(API):
    """Real record/receipt JSON boundary; only external signatures are offline."""
    def __init__(self):
        super().__init__()
        self.repository = {"id": 17, "full_name": incidents.REPOSITORY}
        self.run = {**deepcopy(RUN), "workflow_id": 7, "repository": self.repository, "head_repository": self.repository}
        self.runs[100, 1] = deepcopy(self.run)
        self.sources, self.jobs, self.replay_reads = [], {}, 0

    def get(self, path):
        if path == f"repos/{incidents.REPOSITORY}":
            return deepcopy(self.repository)
        if "/actions/workflows/" in path:
            notification = path.endswith("unattended-workflow-alert.yml")
            return {"id": 7 if notification else 8, "path": incidents.WORKFLOW if notification else ".github/workflows/ci.yml"}
        if "/actions/runs/" in path and "/attempts/" not in path:
            return next(deepcopy(run) for run in self.sources if run["id"] == int(path.rsplit("/", 1)[-1]))
        return super().get(path)

    def pages(self, path, field=None):
        if "/statuses?" in path:
            return super().pages(path)
        if "/actions/runs?" in path:
            assert field == "workflow_runs"
            self.replay_reads += 1
            return deepcopy(self.sources)
        if "/jobs?" in path:
            assert field == "jobs"
            return deepcopy(self.jobs[int(path.split("/actions/runs/")[1].split("/")[0])])
        query = parse_qs(urlsplit(path).query)
        return [deepcopy(record) for record in self.records.values()
                if all(record.get(key) == query[key][0] for key in ("environment", "task") if key in query)]

    def post(self, path, payload):
        record = super().post(path, payload)
        if path.endswith("/deployments"):
            record["payload"] = json.loads(record["payload"])
            self.records[record["id"]] = deepcopy(record)
        return record


@pytest.fixture
def phase(tmp_path, monkeypatch):
    api, slack = PhaseAPI(), FakeSlack()
    monkeypatch.setattr(emissions, "verify", api.verify_signature)
    monkeypatch.setattr(emissions.subprocess, "check_output", api.signature_output)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setattr(worker.deployments, "GitHub", lambda: api)
    monkeypatch.setattr(worker, "Slack", lambda *_: slack)
    monkeypatch.setattr(worker, "utc_now", lambda: NOW)
    # Ancestry is an external read; retain the actual fold/save/load/delivery.
    monkeypatch.setattr(incidents, "prepare", partial(incidents.prepare, ancestor=lambda *_: True, success=lambda *_: None))
    for key, value in {"GITHUB_REPOSITORY": incidents.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                       "GITHUB_WORKFLOW_REF": incidents.REPOSITORY + "/" + incidents.WORKFLOW + "@refs/heads/main",
                       "GITHUB_RUN_ID": "100", "GITHUB_RUN_ATTEMPT": "1", "GITHUB_EVENT_NAME": "schedule",
                       "SHARED_DATASETS_SLACK_INCIDENTS_SINCE": "2026-10-01T00:00:00Z"}.items():
        monkeypatch.setenv(key, value)
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"]["incidents"]["steps"]

    def command(identifier):
        step = steps[-1] if identifier is None else next(step for step in steps if step.get("id") == identifier)
        argv = shlex.split(step["run"].replace("$RUNNER_TEMP", str(tmp_path)))
        assert argv[:4] == ["uv", "run", "--no-sync", "python"]
        monkeypatch.setattr(worker.sys, "argv", argv[4:])
        output = tmp_path / ((identifier or "heartbeat") + ".output")
        output.write_text("")
        monkeypatch.setenv("GITHUB_OUTPUT", str(output))
        worker.main()
        return dict(line.split("=", 1) for line in output.read_text().splitlines())

    return SimpleNamespace(api=api, slack=slack, command=command, root=tmp_path, steps=steps)


def seed_github_failure(phase):
    api = phase.api
    source = {"id": 200, "run_attempt": 1, "head_sha": RUN["head_sha"], "workflow_id": 8,
              "name": "CI", "path": ".github/workflows/ci.yml", "head_branch": "main", "event": "push",
              "repository": api.repository, "head_repository": api.repository, "status": "completed", "conclusion": "success"}
    job = {"id": 201, "name": "New operational receipt", "status": "completed", "conclusion": "success",
           "completed_at": "2026-10-21T12:03:00Z"}
    api.sources, api.jobs[200] = [source], [job]
    failure = incidents.from_job({**source, "id": 199}, {**job, "id": 198, "conclusion": "failure", "completed_at": "2026-10-21T12:00:00Z"}, {})
    state = incidents.fold(None, failure, IDENTITY)
    state["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "1791270000.000001"}
    state["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(state)
    state["incident"]["permalink"] = phase.slack.permalink("1791270000.000001")
    old_run = {**api.run, "id": 90}
    api.runs[90, 1] = old_run
    incidents.Ledger(api, old_run, phase.root / "old").save(state, "claims")
    api.sign()
    return state


def finish_github(phase):
    assert phase.command("github-prepare")["claims"] == "true"
    with pytest.raises(emissions.EmissionError, match="signature"):
        phase.command("github-deliver")
    phase.api.sign()
    assert phase.command("github-deliver")["outcomes"] == "true"
    with pytest.raises(emissions.EmissionError, match="signature"):
        phase.command("github-verify")
    phase.api.sign()
    phase.command("github-verify")


@pytest.mark.parametrize("cloud_enabled", ["true", "false"])
@pytest.mark.parametrize("failure", ["auth", "scan"])
def test_actual_scheduled_commands_resolve_github_before_optional_cloud_failure(phase, monkeypatch, cloud_enabled, failure):
    old = seed_github_failure(phase)
    monkeypatch.setenv("SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED", cloud_enabled)
    forbidden = Mock(side_effect=AssertionError("GitHub phase has no observer capability"))
    monkeypatch.setattr(worker, "reader", forbidden)
    monkeypatch.setattr(worker, "routing", forbidden)
    finish_github(phase)
    assert phase.api.replay_reads == 1
    assert [call[0] for call in phase.slack.calls] == ["update"]
    if cloud_enabled == "true" and failure == "scan":
        monkeypatch.setattr(worker, "routing", lambda *_: deepcopy(SHADOW))
        source = SimpleNamespace(entries=Mock(side_effect=RuntimeError("complete cloud scan failed")), executions=lambda job: iter(()))
        monkeypatch.setattr(worker, "reader", lambda: source)
        with pytest.raises(RuntimeError, match="complete cloud scan failed"):
            phase.command("prepare")
    else:
        # A failing auth action cannot undo the already completed GitHub chain.
        auth = next(step for step in phase.steps if step.get("uses") == "google-github-actions/auth@v2")
        assert "success()" in auth["if"] and "steps.github-verify.outcome == 'success'" in auth["if"]
    fresh = incidents.Ledger(phase.api, phase.api.run, phase.root / "fresh").load()[old["scope"]]
    assert fresh["incident"]["id"] == old["incident"]["id"]
    assert fresh["incident"]["posts"]["parent"] == old["incident"]["posts"]["parent"]
    assert fresh["incident"]["resolution"]["run_id"] == 200
    assert not incidents.needs_parent_delivery(fresh)
    assert not any(record["task"] == cycles.TASK for record in phase.api.records.values())


def test_normal_two_phase_cli_cycle_replays_github_once_and_signs_before_cloud_heartbeat(phase, monkeypatch):
    seed_github_failure(phase)
    monkeypatch.setenv("SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED", "true")
    finish_github(phase)
    cloud_reader = SimpleNamespace(entries=lambda *_args, **_kwargs: iter([]), executions=lambda job: iter(()), heartbeat=Mock(), translation_manifest=Mock())
    monkeypatch.setattr(worker, "routing", lambda *_: deepcopy(SHADOW))
    monkeypatch.setattr(worker, "reader", lambda: cloud_reader)
    assert phase.command("prepare")["cycle_proposed"] == "true"
    assert phase.api.replay_reads == 1
    assert not (phase.root / "github-incidents" / worker.PROPOSAL).exists()
    assert (phase.root / "slack-incidents" / worker.PROPOSAL).exists()
    phase.api.sign()
    phase.command("verify")
    assert phase.command("finalize-cycle")["cycle_saved"] == "true"
    with pytest.raises(emissions.EmissionError, match="signature"):
        phase.command(None)  # Last workflow command is the heartbeat verifier.
    phase.api.sign()
    assert phase.command(None)["heartbeat"] == "true"
    cloud_reader.heartbeat.assert_called_once_with(NOW)
    durable = cycles.CycleLedger(phase.api, None, phase.root / "cold").load()
    assert durable["evaluation"]["public_transport_count"] == 0
    assert [call[0] for call in phase.slack.calls] == ["update"]


def seed_state(phase, state):
    old_run = {**phase.api.run, "id": 90}
    phase.api.runs[90, 1] = old_run
    record = incidents.Ledger(phase.api, old_run, phase.root / "old").save(state, "claims")
    phase.api.sign()
    return record


@pytest.mark.parametrize("acknowledged", [False, True])
def test_mixed_parent_new_github_failure_remains_actionable_without_cloud_credentials(phase, monkeypatch, acknowledged):
    from scripts import cloud_incident_adapter as adapter
    from test_cloud_incident_adapter import source, correlation, at, JOB
    from test_slack_incidents import observation
    proof = correlation()
    failed = observation(scope="deployment:" + JOB, label=JOB, sha=proof["revision"], run_id=3, job_id=4,
                         job_name=proof["job_name"], time=at(1))
    mixed = incidents.fold(None, failed, IDENTITY)
    mixed = incidents.fold(mixed, adapter.normalize(source(), correlation=proof, promote=True), IDENTITY)
    if acknowledged:
        mixed["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "1791270000.000001"}
        mixed["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(mixed)
        mixed["incident"]["permalink"] = phase.slack.permalink("1791270000.000001")
    seed_state(phase, mixed)
    source_run = {**deepcopy(phase.api.run), "id": 200, "workflow_id": 8, "name": "CI", "event": "push",
                  "path": ".github/workflows/ci.yml", "head_sha": proof["revision"], "status": "completed", "conclusion": "failure"}
    phase.api.sources = [source_run]
    target_name = next(name for (workflow, name), target in incidents.job_targets().items()
                       if workflow == "ci.yml" and target == JOB)
    phase.api.jobs[200] = [
        {"id": 201, "name": target_name, "status": "completed", "conclusion": "failure", "completed_at": at(3)},
        {"id": 202, "name": "ci-ready", "status": "completed", "conclusion": "success", "completed_at": at(2)},
    ]
    monkeypatch.setenv("SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED", "true")
    forbidden = Mock(side_effect=AssertionError("mixed GitHub action has no cloud capability"))
    monkeypatch.setattr(worker, "reader", forbidden)
    monkeypatch.setattr(worker, "routing", forbidden)
    finish_github(phase)
    forbidden.assert_not_called()
    ledger = incidents.Ledger(phase.api, phase.api.run, phase.root / "cold")
    states = ledger.load()
    latest = states[mixed["scope"]]
    if acknowledged:
        assert latest["incident"]["posts"]["parent"] == mixed["incident"]["posts"]["parent"]
    assert latest["first_failure_at"] == mixed["first_failure_at"]
    assert any(item.get("run_id") == 200 for item in latest["incident"]["failures"])
    assert [call[0] for call in phase.slack.calls] == ["update" if acknowledged else "post"]
    counts = worker.public_delivery_counts(ledger, states, shadow=True)
    assert counts["public_transport_count"] == 1
    field = "historical_parent_update_count" if acknowledged else "independent_github_delivery_count"
    assert counts[field] == 1
    # Both phases share a worker identity: cloud audit must count the earlier
    # GitHub transport exactly once, despite the distinct local work directories.
    cloud_reader = SimpleNamespace(entries=lambda *_args, **_kwargs: iter([]), executions=lambda job: iter(()), heartbeat=Mock(), translation_manifest=Mock())
    monkeypatch.setattr(worker, "routing", lambda *_: deepcopy(SHADOW))
    monkeypatch.setattr(worker, "reader", lambda: cloud_reader)
    assert phase.command("prepare")["cycle_proposed"] == "true"
    assert phase.api.replay_reads == 1
    phase.api.sign()
    phase.command("verify")
    phase.command("finalize-cycle")
    phase.api.sign()
    phase.command(None)
    durable = cycles.CycleLedger(phase.api, None, phase.root / "cold-cycle").load()
    assert durable["evaluation"]["public_transport_count"] == 1
    assert durable["evaluation"][field] == 1
    assert durable["evaluation"]["public_delivery_count"] == 0
    assert [call[0] for call in phase.slack.calls] == ["update" if acknowledged else "post"]
    cloud_reader.heartbeat.assert_called_once_with(NOW)


def test_mixed_parent_github_recovery_edits_known_parent_without_cloud_credentials(phase, monkeypatch):
    from scripts import cloud_incident_adapter as adapter
    from test_cloud_incident_adapter import source, published, correlation, at, SUBJECT
    api = phase.api
    current_source = {**deepcopy(api.run), "id": 200, "workflow_id": 8, "name": "CI", "event": "push",
                      "path": ".github/workflows/ci.yml", "head_sha": "b" * 40, "status": "completed", "conclusion": "success"}
    current_job = {"id": 201, "name": "New operational receipt", "status": "completed", "conclusion": "success", "completed_at": at(3)}
    api.sources, api.jobs[200] = [current_source], [current_job]
    failed = incidents.from_job({**current_source, "id": 3},
                               {**current_job, "id": 5, "conclusion": "failure", "completed_at": at(2)}, {})
    mixed = incidents.fold(None, failed, IDENTITY)
    cloud_failure = adapter.normalize(source(kind="identity_decision", status="required", subject=SUBJECT,
                                             details={"ambiguity_count": 1}), correlation=correlation(), promote=True)
    mixed = incidents.fold(mixed, cloud_failure, IDENTITY, correlate=True)
    cloud_recovery = next(value for value in incidents.cloud_variants(published(), correlation(), shadow=False)
                          if value["scope"] == cloud_failure["scope"])
    mixed = incidents.fold(mixed, cloud_recovery, IDENTITY, correlate=True)
    assert cloud_failure["scope"] in mixed["recoveries"] and mixed["incident"]["resolution"] is None
    mixed["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "1791270000.000001"}
    mixed["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(mixed)
    mixed["incident"]["permalink"] = phase.slack.permalink("1791270000.000001")
    seed_state(phase, mixed)
    forbidden = Mock(side_effect=AssertionError("existing noncanonical recovery needs no observer credentials"))
    monkeypatch.setattr(worker, "reader", forbidden)
    monkeypatch.setattr(worker, "routing", forbidden)
    finish_github(phase)
    fresh = incidents.Ledger(api, api.run, phase.root / "cold").load()[mixed["scope"]]
    assert fresh["incident"]["resolution"] is not None
    assert fresh["incident"]["posts"]["parent"] == mixed["incident"]["posts"]["parent"]
    assert [call[0] for call in phase.slack.calls] == ["update"]
    forbidden.assert_not_called()


@pytest.mark.parametrize("canonical_green", [False, True])
def test_github_phase_defers_cloud_only_parent_and_current_canonical_green(phase, monkeypatch, canonical_green):
    from scripts import cloud_incident_adapter as adapter
    from test_cloud_incident_adapter import source
    if canonical_green:
        from test_translation_incident_recovery import posted, ingest, proof
        local, _, scope = posted()
        ingest(local, proof())
        state = local.states[scope]
        assert incidents.canonical_translation_recovery(state)
    else:
        state = incidents.fold(None, adapter.normalize(source()), IDENTITY)
    seed_state(phase, state)
    monkeypatch.setenv("SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED", "true")
    forbidden = Mock(side_effect=AssertionError("GitHub phase cannot read canonical objects"))
    monkeypatch.setattr(worker, "reader", forbidden)
    monkeypatch.setattr(worker, "routing", forbidden)
    phase.command("github-prepare")
    assert json.loads((phase.root / "github-incidents/prepared.json").read_text()) == []
    phase.api.sign()
    phase.command("github-verify")  # Signature-only verification permits queued cloud work.
    if canonical_green:
        ledger = incidents.Ledger(phase.api, phase.api.run, phase.root / "github-incidents")
        record = ledger.save(state, "claims")
        phase.api.sign()
        (ledger.directory / "prepared.json").write_text(json.dumps([record]))
        with pytest.raises(incidents.IncidentError, match="canonical translation green"):
            phase.command("github-deliver")
    assert phase.slack.calls == []
    forbidden.assert_not_called()


def test_same_worker_unposted_github_checkpoint_can_be_adopted_before_cloud_transport(phase, monkeypatch):
    from scripts import cloud_incident_adapter as adapter
    from test_cloud_incident_adapter import source, correlation, at, supervised_deployment
    raw = source()
    shadow = incidents.fold(None, adapter.normalize(raw), IDENTITY)
    promoted = adapter.normalize(raw, correlation=correlation(), promote=True)
    prospective = supervised_deployment(active=False)
    seed_state(phase, shadow)
    seed_state(phase, prospective)
    phase.command("github-prepare")
    phase.api.sign()
    phase.command("github-verify")
    github_records = [record for record in phase.api.records.values()
                      if record["payload"]["execution_run_id"] == 100]
    assert github_records and all(not record["payload"]["state"]["incident"]["posts"] for record in github_records)
    directory = phase.root / "slack-incidents"
    directory.mkdir()
    ledger = incidents.Ledger(phase.api, phase.api.run, directory)
    states = ledger.load()
    before = deepcopy(states)
    incidents.adopt_cloud_shadow(states, [promoted])
    states[promoted["scope"]] = incidents.fold(states[promoted["scope"]], promoted, IDENTITY)
    records = incidents.prepare(ledger, IDENTITY, [], initial_states=states, original_states=before,
                                cloud_delivery_enabled=True, now=at(12))
    (directory / "prepared.json").write_text(json.dumps(records))
    selected = {**SHADOW, "mode": "unified", "policy_deployment_id": 77, "policy_status_id": 88}
    monkeypatch.setattr(worker, "routing", lambda *_: deepcopy(selected))
    cycle = cycles.CycleLedger(phase.api, phase.api.run, directory)
    worker.write_context(ledger, cycle)
    phase.api.sign()
    worker.deliver_messages(ledger, cycle, phase.slack)
    phase.api.sign()
    fresh = incidents.Ledger(phase.api, phase.api.run, phase.root / "cold").load()
    assert list(fresh) == [promoted["scope"]]
    assert shadow["scope"] in fresh[promoted["scope"]]["aliases"]
    assert worker.public_delivery_counts(ledger, fresh)["public_transport_count"] == 1
    assert [call[0] for call in phase.slack.calls] == ["post"]


@pytest.mark.parametrize("path", ["scripts/operational_reconcile.py", "scripts/operational_delivery_activation.py",
                                  ".github/workflows/unattended-workflow-alert.yml"])
def test_fenced_notification_protocol_changes_select_only_policy_sync(path):
    assert select_deployments([path]) == ["cron_alert_policy"]
    assert ".github/workflows/unattended-workflow-alert.yml" in PROTOCOL_FILES


def test_workflow_retains_observer_aliases_and_requires_exact_phase_order():
    value = yaml.safe_load(WORKFLOW.read_text())
    job = value["jobs"]["incidents"]
    assert notification_boundary(WORKFLOW.name, "incidents", value, job)
    for name in OBSERVER_WORKFLOWS:
        assert f'"{name}"' in job["if"]
    steps = job["steps"]
    assert next(i for i, step in enumerate(steps) if step.get("id") == "github-verify") < next(
        i for i, step in enumerate(steps) if step.get("uses") == "google-github-actions/auth@v2")


@pytest.mark.parametrize("change", ["literal_space", "ordinary_success", "observer_alias", "auth_first", "unsigned_github", "shared_dir", "bypass_barrier"])
def test_serialized_workflow_phase_or_admission_weakening_is_rejected(change):
    value = yaml.safe_load(WORKFLOW.read_text())
    job = value["jobs"]["incidents"]
    by_id = {step.get("id"): step for step in job["steps"] if step.get("id")}
    if change == "literal_space":
        job["if"] = job["if"].replace('"Deployment completion check"', '"Deployment  completion check"')
    elif change == "ordinary_success":
        job["if"] = job["if"].replace("github.event.workflow_run.conclusion != 'success'", "true")
    elif change == "observer_alias":
        job["if"] = job["if"].replace('"Deployment terminal verification"', '"Not the historical verifier"')
    elif change == "auth_first":
        auth = next(step for step in job["steps"] if step.get("uses") == "google-github-actions/auth@v2")
        job["steps"].remove(auth)
        job["steps"].insert(6, auth)
    elif change == "unsigned_github":
        by_id["github-deliver"]["if"] = "${{ always() }}"
    elif change == "shared_dir":
        by_id["github-prepare"]["run"] = by_id["github-prepare"]["run"].replace("github-incidents", "slack-incidents")
    else:
        by_id["prepare"]["if"] = "${{ always() }}"
    serialized = yaml.safe_load(yaml.safe_dump(value))
    assert not notification_boundary(WORKFLOW.name, "incidents", serialized, serialized["jobs"]["incidents"])
