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
