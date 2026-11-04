"""The real readiness-only incident recovers without a build or a new parent."""
import copy
import json
from unittest import mock

import pytest

from scripts import deployment_emission as emissions
from scripts import slack_incidents as incidents
from test_slack_incidents import FakeSlack, IDENTITY, SHA, observation, signed_record

WORKFLOW = "wdpa-monthly-build.yml"
STEP = "Report readiness for the next scheduler window"
OLD_SCOPE = "deployment:wdpa-monthly-build"
SCOPE = incidents.scope_for_job("readiness", {}, WORKFLOW)[0]
RUN = 41024926489
RECOVERY = 41074737108


@pytest.fixture
def history(tmp_path, monkeypatch):
    failure = observation(scope=OLD_SCOPE, label="wdpa-monthly-build", job_name="readiness",
                          run_id=RUN, job_id=123960658417, time="2026-10-30T08:13:03Z")
    state = incidents.fold(None, failure, IDENTITY)
    state["incident"].update(id="SD-A55407E3CD78", posts={"parent": {"state": "delivered", "ts": "1793348125.668069"}},
                             permalink=f"https://example.slack.com/archives/{IDENTITY['channel']}/p1793348125668069")
    state["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(state)
    original = signed_record()
    original["payload"]["state"] = state
    records = [original]
    repo = {"id": 9, "full_name": incidents.REPOSITORY}
    workflow = {"id": 7, "path": ".github/workflows/" + WORKFLOW}
    run = {"id": RUN, "name": "Ingestion / WDPA monthly build", "path": workflow["path"],
           "workflow_id": 7, "head_sha": SHA, "head_branch": "main", "event": "schedule",
           "run_attempt": 1, "status": "completed", "conclusion": "failure", "updated_at": failure["time"],
           "repository": repo, "head_repository": repo}
    runs = {RUN: run, RECOVERY: {**run, "id": RECOVERY, "event": "workflow_dispatch", "conclusion": "success"}}
    jobs = {RUN: [{"name": "readiness", "id": failure["job_id"], "status": "completed",
                   "conclusion": "failure", "completed_at": failure["time"]},
                  {"name": "build", "id": 123960659786, "status": "completed", "conclusion": "skipped"}],
            RECOVERY: [{"name": "readiness", "id": 124127795242, "status": "completed", "conclusion": "success",
                        "completed_at": "2026-10-30T14:47:44Z", "steps": [{"name": STEP, "status": "completed", "conclusion": "success"}]},
                       {"name": "build", "id": 124127797441, "status": "completed", "conclusion": "skipped"}]}
    api = mock.Mock()
    def get(path):
        if path == f"repos/{incidents.REPOSITORY}":
            return repo
        if "/workflows/" in path:
            return workflow
        return runs[int(path.split("/runs/")[1].split("/")[0])]
    api.get.side_effect = get
    def pages(path, field=None):
        if "/runs/" in path:
            return jobs[int(path.split("/runs/")[1].split("/")[0])]
        if path.endswith("/statuses?per_page=100"):
            return [{"id": 12, "state": "success", "description": "applied"}]
        return records
    api.pages.side_effect = pages
    def post(path, payload):
        if path.endswith("/deployments"):
            created = {**copy.deepcopy(payload), "id": max(item["id"] for item in records) + 1,
                       "sha": payload["ref"], "creator": {"login": "github-actions[bot]"}}
            records.append(created)
            return created
        return {**payload, "id": 12}
    api.post.side_effect = post
    verifier = mock.Mock()
    monkeypatch.setattr(emissions, "verify", verifier)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    ledger = incidents.Ledger(api, {"id": 500, "run_attempt": 1, "head_sha": "b" * 40}, tmp_path)
    return ledger, original, records, jobs, runs, verifier


def test_readiness_recovery_updates_original_parent_and_keeps_build_receipts_required(history):
    ledger, original, records, _, runs, verifier = history
    untouched = copy.deepcopy(original)
    targets = incidents.job_targets()
    assert incidents.scope_for_job("build", targets, WORKFLOW)[0] == OLD_SCOPE
    observed = incidents.source_observations(ledger.api, {"workflow_run": runs[RECOVERY]}, targets)
    assert any(item["scope"] == SCOPE for item in observed)
    prepared = incidents.prepare(ledger, IDENTITY, observed, ancestor=lambda *_: True, success=lambda *_: (None, None))
    assert verifier.called
    assert original == untouched
    slack = FakeSlack()
    incidents.deliver(ledger, prepared, slack)
    assert [call[0] for call in slack.calls] == ["update"]
    assert slack.calls[0][2]["ts"] == "1793348125.668069"
    assert "Resolved" in slack.calls[0][1]["text"]
    assert ledger.load()[SCOPE]["incident"]["id"] == "SD-A55407E3CD78"
    original["payload"] = json.dumps(original["payload"])
    assert incidents.prepare(ledger, IDENTITY, observed, ancestor=lambda *_: True, success=lambda *_: (None, None)) == []
    assert len(records) == 3


@pytest.mark.parametrize("defect", ["build-ran", "build-missing", "wrong-job", "wrong-sha", "mixed", "unsigned"])
def test_unverified_or_mutating_history_cannot_migrate(history, defect):
    ledger, original, _, jobs, runs, verifier = history
    if defect == "build-ran":
        jobs[RUN][1]["conclusion"] = "failure"
    elif defect == "build-missing":
        jobs[RUN].pop()
    elif defect == "wrong-job":
        jobs[RUN][0]["id"] += 1
    elif defect == "wrong-sha":
        runs[RUN]["head_sha"] = "f" * 40
    elif defect == "mixed":
        original["payload"]["state"]["deferred"] = [observation(scope=OLD_SCOPE, label="wdpa-monthly-build", job_name="build")]
    else:
        verifier.side_effect = emissions.EmissionError("forged")
    with pytest.raises((incidents.IncidentError, emissions.EmissionError)):
        ledger.load()
    ledger.api.post.assert_not_called()


@pytest.mark.parametrize("conclusion", ["success", "skipped", "failure", None])
def test_only_an_executed_successful_readiness_step_supplies_recovery(history, conclusion):
    ledger, _, _, jobs, runs, _ = history
    job = jobs[RECOVERY][0]
    job["steps"] = [] if conclusion is None else [{"name": STEP, "status": "completed", "conclusion": conclusion}]
    result = incidents.source_observations(ledger.api, {"workflow_run": runs[RECOVERY]}, incidents.job_targets())
    assert any(item["scope"] == SCOPE for item in result) == (conclusion == "success")


def test_readiness_success_cannot_close_a_real_build_failure(history):
    ledger, original, _, _, runs, _ = history
    original["payload"]["state"]["incident"]["failures"][0]["job_name"] = "build"
    observed = incidents.source_observations(ledger.api, {"workflow_run": runs[RECOVERY]}, incidents.job_targets())
    incidents.prepare(ledger, IDENTITY, observed, ancestor=lambda *_: True, success=lambda *_: (None, None))
    assert ledger.load()[OLD_SCOPE]["incident"]["resolution"] is None


@pytest.fixture
def observer_history(tmp_path, monkeypatch):
    """Four signed historical observer incidents plus one actual WDPA recovery."""
    import re
    import test_deployment_revision as provenance
    from scripts import deployment_revision as deployments

    fixture = provenance.DeploymentProvenanceTests()
    fixture.setUp()
    api, repo = fixture.api, {"id": 17, "full_name": incidents.REPOSITORY}
    runs, jobs, production, statuses, records = {}, {}, {}, {}, []
    for index in range(4):
        identifier, run_id = index + 1, index + 100
        run = {"id": run_id, "run_attempt": 1, "workflow_id": 77,
               "name": "Deployments / Completion check", "path": ".github/workflows/deployment-verification.yml",
               "event": "workflow_dispatch", "head_branch": "main", "head_sha": "f" * 40,
               "status": "completed", "conclusion": "failure", "updated_at": "2026-11-03T12:01:00Z",
               "repository": repo, "head_repository": repo}
        job = {"id": 200 + index, "name": f"verify ({identifier})", "status": "completed",
               "conclusion": "failure", "completed_at": run["updated_at"],
               "steps": [{"name": "Attest verified execution outcome", "status": "completed", "conclusion": "success"}]}
        runs[run_id], jobs[run_id] = run, [job]
        production[identifier] = provenance.ReplayTests.record(provenance.CURRENT, identifier)
        statuses[identifier] = [{"id": 2000 + identifier, "state": "failure", "description": "failed",
            "created_at": "2026-11-03T12:00:00Z", "log_url": emissions.invocation(incidents.REPOSITORY, run_id, 1),
            "environment_url": "https://console.cloud.google.com/run/jobs/executions/details/us-central1/wdpa-monthly-abcde?project=shared-datasets-1"},
            {"id": identifier, "description": "started"}]
        value = incidents.fold(None, incidents.from_job(run, job, {}), IDENTITY)
        value["incident"].update(posts={"parent": {"state": "delivered", "ts": f"1791270000.{identifier:06d}"}},
                                 permalink=f"https://example.slack.com/archives/{IDENTITY['channel']}/p1791270000{identifier:06d}")
        value["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(value)
        record = signed_record()
        record.update(id=900 + index)
        record["payload"]["state"] = value
        records.append(record)
    production[5] = provenance.ReplayTests.record(provenance.CURRENT, 5)
    statuses[5] = [{"id": 2010, "state": "success", "description": "verified",
                    "created_at": "2026-11-03T13:00:00Z", "log_url": emissions.invocation(incidents.REPOSITORY, 500, 1)},
                   {"id": 5, "description": "started"}]

    def get(path):
        if path.endswith("/actions/workflows/77"):
            return {"id": 77, "path": ".github/workflows/deployment-verification.yml"}
        if match := re.search(r"/actions/runs/(10[0-3])$", path):
            return runs[int(match[1])]
        if match := re.search(r"/deployments/([0-9]+)$", path):
            identifier = int(match[1])
            return production[identifier] if identifier < 900 else next(item for item in records if item["id"] == identifier)
        return fixture.route(path)

    def pages(path, field=None):
        if "environment=slack-incidents" in path:
            return records
        if "environment=production-wdpa-monthly" in path:
            return [production[5]]
        if match := re.search(r"/deployments/([0-9]+)/statuses", path):
            identifier = int(match[1])
            return statuses[identifier] if identifier < 900 else [{"id": 9999, "state": "success", "description": "applied"}]
        if match := re.search(r"/actions/runs/(10[0-3])/attempts/1/jobs", path):
            return jobs[int(match[1])]
        assert "/actions/runs/13/attempts/2/jobs" in path, path
        return [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]

    def post(path, payload):
        if path.endswith("/deployments"):
            created = {**copy.deepcopy(payload), "id": max(item["id"] for item in records) + 1,
                       "sha": payload["ref"], "creator": {"login": "github-actions[bot]"}}
            records.append(created)
            return created
        return {**payload, "id": 9999}

    api.get.side_effect, api.pages.side_effect, api.post.side_effect = get, pages, post
    monkeypatch.setattr(emissions, "verify", mock.Mock())
    monkeypatch.setattr(incidents, "terminal_contract_sha256", lambda: "c" * 64)
    monkeypatch.setattr(deployments, "git_ancestor", lambda older, newer: older == newer)
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    ledger = incidents.Ledger(api, {"id": 600, "run_attempt": 1, "head_sha": "e" * 40}, tmp_path)
    return ledger, records, runs, jobs, production, statuses


@pytest.mark.parametrize("count", [0, 1, 4])
def test_signed_runtime_recovery_closes_original_observer_parents(observer_history, count):
    ledger, records, *_ = observer_history
    del records[count:]
    original = copy.deepcopy(records)
    prepared = incidents.prepare(ledger, IDENTITY, [], ancestor=lambda older, newer: older == newer)
    slack = FakeSlack()
    incidents.deliver(ledger, json.loads(json.dumps(prepared)), slack)
    assert len(slack.calls) == count
    assert all(call[0] == "update" and "Resolved" in call[1]["text"] for call in slack.calls)
    assert {call[2]["ts"] for call in slack.calls} == {f"1791270000.{i + 1:06d}" for i in range(count)}
    assert records[:count] == original
    states = ledger.load()
    for record in original:
        old = record["payload"]["state"]
        state = states[old["scope"]]
        assert state["kind"] == old["kind"] == "job"
        assert state["incident"]["id"] == old["incident"]["id"]
        assert state["incident"]["failures"] == old["incident"]["failures"]
        assert state["incident"]["resolution"]["proof"]["record_id"] == 5
        assert state["incident"]["resolution"]["scope"] == old["scope"]
    for record in records:
        record["payload"] = json.dumps(record["payload"])
    assert incidents.prepare(ledger, IDENTITY, []) == []


def test_legacy_resolver_cli_accepts_signed_observer_runtime_recovery(observer_history, monkeypatch):
    from scripts import workflow_failure_alert as alerts
    from scripts import deployment_revision as deployments
    ledger, *_ = observer_history
    slack = FakeSlack()
    monkeypatch.setattr(deployments, "GitHub", lambda: ledger.api)
    monkeypatch.setattr("scripts.slack_incident_api.Slack", lambda *_: slack)
    monkeypatch.setattr("sys.argv", ["alert", "--resolve-run-id", "100", "--message-ts", "1791270000.000001"])
    alerts.main()
    assert len(slack.calls) == 1 and slack.calls[0][0] == "update"
    assert slack.calls[0][2]["ts"] == "1791270000.000001"
    assert "actions/runs/500/attempts/1" in slack.calls[0][1]["text"]


@pytest.mark.parametrize("defect", [
    "attestation-missing", "attestation-failed", "wrong-attempt", "missing-outcome", "unknown-outcome",
    "foreign-execution", "foreign-target", "source-sha", "source-job", "source-time", "unsigned-failure",
    "unsigned-recovery", "pending", "failed", "applied-only", "older", "divergent", "mixed",
])
def test_observer_recovery_requires_every_exact_signed_failure_and_new_runtime_success(observer_history, monkeypatch, defect):
    ledger, records, runs, jobs, production, statuses = observer_history
    del records[1:]
    if defect == "attestation-missing":
        jobs[100][0]["steps"] = []
    elif defect == "attestation-failed":
        jobs[100][0]["steps"][0]["conclusion"] = "failure"
    elif defect == "wrong-attempt":
        statuses[1][0]["log_url"] = emissions.invocation(incidents.REPOSITORY, 100, 2)
    elif defect == "missing-outcome":
        statuses[1].pop(0)
    elif defect == "unknown-outcome":
        statuses[1][0].update(state="error", description="unknown")
    elif defect == "foreign-execution":
        statuses[1][0]["environment_url"] = statuses[1][0]["environment_url"].replace("wdpa-monthly", "another-job")
    elif defect == "foreign-target":
        production[1]["environment"] = "production-sea-ice-daily"
        production[1]["payload"]["target"] = "sea-ice-daily"
    elif defect == "source-sha":
        runs[100]["head_sha"] = "c" * 40
    elif defect == "source-job":
        jobs[100][0]["id"] += 1
    elif defect == "source-time":
        jobs[100][0]["completed_at"] = "2026-11-03T12:02:00Z"
    elif defect.startswith("unsigned-"):
        def verify(_api, _repo, record, status, **_):
            if record["id"] == (1 if defect == "unsigned-failure" else 5) and status["description"] in {"failed", "verified"}:
                raise emissions.EmissionError("invalid signature")
        monkeypatch.setattr(emissions, "verify", verify)
    elif defect in {"pending", "failed", "applied-only"}:
        statuses[5][0].update({"pending": {"state": "in_progress", "description": "verification_pending"},
                               "failed": {"state": "failure", "description": "failed"},
                               "applied-only": {"state": "success", "description": "applied"}}[defect])
    elif defect == "older":
        statuses[5][0]["created_at"] = "2026-11-03T11:59:00Z"
    elif defect == "mixed":
        jobs[101][0]["name"] = "verify (1)"
        jobs[101][0]["steps"][0]["conclusion"] = "failure"
        records[0]["payload"]["state"]["incident"]["failures"].append(incidents.from_job(runs[101], jobs[101][0], {}))
        state = records[0]["payload"]["state"]
        state["incident"]["parent_view_sha256"] = incidents.parent_view_sha256(state)
    slack = FakeSlack()
    try:
        prepared = incidents.prepare(ledger, IDENTITY, [], ancestor=lambda older, newer: older == newer and defect != "divergent")
    except incidents.deployments.DeploymentError:
        prepared = []  # Failed signature verification remains a visible worker failure.
    incidents.deliver(ledger, prepared, slack)
    assert slack.calls == []
    assert records[0]["payload"]["state"]["incident"]["resolution"] is None
    ledger.api.post.assert_not_called()


def test_observer_recovery_never_adopts_an_uncertain_original_parent(observer_history):
    ledger, records, *_ = observer_history
    del records[1:]
    state = records[0]["payload"]["state"]
    state["incident"].update(posts={"parent": {"state": "claimed", "ts": None}}, permalink="", parent_view_sha256=None)
    slack = FakeSlack()
    incidents.deliver(ledger, incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True), slack)
    assert slack.calls == []
    assert ledger.load()[state["scope"]]["incident"]["resolution"] is None


def test_observer_recovery_retains_uncertain_historical_reply_claim(observer_history):
    ledger, records, runs, jobs, _, statuses = observer_history
    del records[1:]
    jobs[101][0]["name"] = "verify (1)"
    retry = incidents.from_job(runs[101], jobs[101][0], {})
    state = records[0]["payload"]["state"]
    state["incident"]["failures"].append(retry)
    key = "attempt:" + incidents.observation_key(retry)
    state["incident"]["posts"][key] = {"state": "claimed", "ts": None}
    statuses[1].insert(0, {**statuses[1][0], "id": 2101, "log_url": emissions.invocation(incidents.REPOSITORY, 101, 1)})
    slack = FakeSlack()
    incidents.deliver(ledger, incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True), slack)
    assert len(slack.calls) == 1 and slack.calls[0][0] == "update"
    current = ledger.load()[state["scope"]]
    assert current["incident"]["resolution"]["proof"]["record_id"] == 5
    assert current["incident"]["posts"][key] == {"state": "claimed", "ts": None}


@pytest.mark.parametrize("changed_proof", [False, True])
def test_checkpoint_sealing_reverifies_observer_failure_and_recovery(observer_history, changed_proof):
    import hashlib
    ledger, records, _, jobs, *_ = observer_history
    del records[1:]
    claim = incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True)[0]
    ledger.run.update(event="workflow_dispatch", actor={"login": "jonaraphael"})
    digest = hashlib.sha256(emissions.canonical(claim["payload"])).hexdigest()
    count = ledger.api.post.call_count
    if changed_proof:
        jobs[100][0]["steps"][0]["conclusion"] = "failure"
        with pytest.raises(incidents.IncidentError, match="original signed worker failures"):
            incidents.seal_checkpoint(ledger, IDENTITY, claim["payload"]["state"]["incident"]["id"], f"checkpoint:{claim['id']}:{digest}")
        assert ledger.api.post.call_count == count
    else:
        assert incidents.seal_checkpoint(ledger, IDENTITY, claim["payload"]["state"]["incident"]["id"], f"checkpoint:{claim['id']}:{digest}") == []
        current = ledger.load()[claim["payload"]["state"]["scope"]]
        assert current["incident"]["resolution"]["proof"] == claim["payload"]["state"]["incident"]["resolution"]["proof"]


def test_runtime_recovery_proof_cannot_be_attached_to_an_unrelated_job(observer_history):
    ledger, records, *_ = observer_history
    del records[1:]
    claim = incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True)[0]
    value = copy.deepcopy(claim["payload"]["state"])
    value["incident"]["failures"][0]["job_name"] = "unregistered signing job"
    with pytest.raises(incidents.IncidentError, match="completion-job runtime recovery"):
        incidents.validate_state(value)
