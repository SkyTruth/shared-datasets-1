"""CLI delivery admits the same signed claim after GitHub updates its timestamp."""
from __future__ import annotations

import copy
import json
import sys
from types import SimpleNamespace
from unittest import mock

import pytest

from scripts import deployment_emission as emissions
from scripts import deployment_revision as deployments
from scripts import slack_incidents as incidents


@pytest.fixture
def deliver_cli(tmp_path, monkeypatch):
    identity = {"team": "T123456789", "user": "U123456789", "channel": "C123456789"}
    sha = "a" * 40
    observation = {
        "scope": "deployment:pmtiles-cdn", "kind": "deployment", "label": "pmtiles-cdn",
        "run_id": 100, "attempt": 1, "sha": sha, "time": "2026-10-07T22:00:00Z",
        "job_id": 123, "job_name": "pmtiles-cdn / Apply PMTiles CDN route sync",
        "conclusion": "failure", "proof": None,
    }
    state = incidents.fold(None, observation, identity)
    state["incident"]["posts"]["parent"] = {"state": "claimed", "ts": None}
    prepared = {
        "id": 6921953605, "sha": sha, "ref": sha, "task": "slack-incident",
        "environment": incidents.ENVIRONMENT, "production_environment": False,
        "creator": {"login": "github-actions[bot]", "id": 41898282, "avatar_url": "https://example.invalid/bot.png"},
        "created_at": "2026-10-07T22:02:22Z", "updated_at": "2026-10-07T22:02:22Z",
        "statuses_url": f"https://api.github.com/repos/{incidents.REPOSITORY}/deployments/6921953605/statuses",
        "payload": {"schema": incidents.SCHEMA, "state": state,
                    "execution_run_id": 11, "execution_run_attempt": 1},
    }
    current = copy.deepcopy(prepared)
    current["updated_at"] = "2026-10-07T22:02:23Z"
    repository = {"id": 1, "full_name": incidents.REPOSITORY}
    workflow = {"id": 2, "path": incidents.WORKFLOW}
    run = {"id": 11, "run_attempt": 1, "path": incidents.WORKFLOW, "workflow_id": 2,
           "head_branch": "main", "head_sha": sha, "event": "workflow_run",
           "repository": copy.deepcopy(repository), "head_repository": copy.deepcopy(repository)}
    status = {"id": 12, "description": "applied", "state": "success",
              "log_url": emissions.invocation(incidents.REPOSITORY, 11, 1)}
    api = mock.Mock()
    claim_path = f"repos/{incidents.REPOSITORY}/deployments/{prepared['id']}"
    answers = {
        f"repos/{incidents.REPOSITORY}/actions/runs/11/attempts/1": run,
        f"repos/{incidents.REPOSITORY}": repository,
        f"repos/{incidents.REPOSITORY}/actions/workflows/{incidents.WORKFLOW.rsplit('/', 1)[-1]}": workflow,
        claim_path: current,
    }
    fetched = []
    def get(path):
        answer = copy.deepcopy(answers[path])
        if path == claim_path:
            fetched.append(answer)
        return answer
    api.get.side_effect = get
    api.pages.return_value = [status]
    slack = mock.Mock()
    slack.identity.return_value = identity
    slack.post.return_value = "1791400000.000001"
    slack.permalink.return_value = "https://example.slack.com/archives/C123456789/p1791400000000001"
    signature = mock.Mock()
    save = mock.Mock()
    calls = mock.Mock()
    calls.attach_mock(signature, "signature")
    calls.attach_mock(slack, "slack")
    monkeypatch.setattr(deployments, "GitHub", lambda: api)
    monkeypatch.setattr(incidents, "Slack", lambda *_: slack)
    monkeypatch.setattr(emissions, "verify", signature)
    monkeypatch.setattr(incidents.Ledger, "save", save)
    monkeypatch.setattr(sys, "argv", ["slack_incidents.py", "deliver", "--work-dir", str(tmp_path)])
    monkeypatch.setenv("GITHUB_REPOSITORY", incidents.REPOSITORY)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_WORKFLOW_REF", incidents.REPOSITORY + "/" + incidents.WORKFLOW + "@refs/heads/main")
    monkeypatch.setenv("GITHUB_RUN_ID", "11")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    def invoke():
        (tmp_path / "prepared.json").write_text(json.dumps([prepared]))
        incidents.main()
    return SimpleNamespace(prepared=prepared, current=current, run=run, status=status,
                           api=api, slack=slack, signature=signature, save=save,
                           calls=calls, fetched=fetched, invoke=invoke)


def assert_no_slack_writes(context):
    context.slack.post.assert_not_called()
    context.slack.update.assert_not_called()
    context.slack.permalink.assert_not_called()
    context.save.assert_not_called()
    context.api.post.assert_not_called()


def test_deliver_cli_accepts_status_write_timestamp_change_and_verifies_current_claim(deliver_cli):
    context = deliver_cli
    context.invoke()
    assert len(context.fetched) == 1
    context.signature.assert_called_once_with(
        context.api, incidents.REPOSITORY, context.fetched[0], context.status,
        original_signer=incidents.WORKFLOW, batch=True,
    )
    assert context.signature.call_args.args[2] is context.fetched[0]
    assert context.signature.call_args.args[2]["updated_at"] == "2026-10-07T22:02:23Z"
    assert [call[0] for call in context.calls.mock_calls].index("signature") < [
        call[0] for call in context.calls.mock_calls
    ].index("slack.post")
    context.slack.post.assert_called_once()
    assert "Open" in context.slack.post.call_args.args[0]["text"]
    assert context.slack.post.call_args.kwargs == {}
    context.slack.update.assert_not_called()
    context.slack.permalink.assert_called_once_with("1791400000.000001")
    assert context.prepared["updated_at"] == "2026-10-07T22:02:22Z"


@pytest.mark.parametrize("field,value", [
    ("id", 6921953606), ("ref", "b" * 40), ("sha", "b" * 40),
    ("task", "deploy"), ("environment", "production-pmtiles-cdn"),
    ("production_environment", True), ("creator", {"login": "someone-else"}),
    ("created_at", "2026-10-07T22:02:21Z"), ("statuses_url", "https://example.invalid/other"),
    ("new_metadata", "different"),
])
def test_deliver_cli_rejects_other_changed_claim_fields_before_slack(deliver_cli, field, value):
    context = deliver_cli
    context.current[field] = value
    with pytest.raises(incidents.IncidentError, match="prepared claim changed"):
        context.invoke()
    context.signature.assert_not_called()
    assert_no_slack_writes(context)


@pytest.mark.parametrize("change", ["state", "worker-id", "worker-attempt", "json-string", "avatar"])
def test_deliver_cli_rejects_nested_or_representation_changes_before_slack(deliver_cli, change):
    context = deliver_cli
    payload = context.current["payload"]
    if change == "state":
        payload["state"]["incident"]["posts"]["parent"] = {"state": "delivered", "ts": "1791400000.000099"}
    elif change == "worker-id":
        payload["execution_run_id"] += 1
    elif change == "worker-attempt":
        payload["execution_run_attempt"] += 1
    elif change == "json-string":
        context.current["payload"] = json.dumps(payload)
    else:
        context.current["creator"]["avatar_url"] = "https://example.invalid/changed.png"
    with pytest.raises(incidents.IncidentError, match="prepared claim changed"):
        context.invoke()
    context.signature.assert_not_called()
    assert_no_slack_writes(context)


@pytest.mark.parametrize("field", ["execution_run_id", "execution_run_attempt"])
def test_deliver_cli_rejects_matching_claims_from_another_worker(deliver_cli, field):
    context = deliver_cli
    context.prepared["payload"][field] += 1
    context.current["payload"][field] += 1
    with pytest.raises(incidents.IncidentError, match="another worker"):
        context.invoke()
    context.signature.assert_not_called()
    assert context.fetched == []
    assert_no_slack_writes(context)


@pytest.mark.parametrize("field,value", [
    ("head_branch", "feature"), ("event", "pull_request"), ("path", ".github/workflows/ci.yml"),
    ("workflow_id", 3), ("repository", {"id": 4, "full_name": incidents.REPOSITORY}),
    ("head_repository", {"id": 1, "full_name": "someone-else/fork"}),
])
def test_deliver_cli_rejects_invalid_worker_provenance_before_slack(deliver_cli, field, value):
    context = deliver_cli
    context.run[field] = value
    with pytest.raises(incidents.IncidentError, match="invalid incident worker provenance"):
        context.invoke()
    context.signature.assert_not_called()
    assert context.fetched == []
    assert_no_slack_writes(context)


@pytest.mark.parametrize("forged", [False, True])
def test_deliver_cli_requires_signature_for_equal_claims_after_timestamp_change(deliver_cli, forged):
    context = deliver_cli
    if forged:
        for record in (context.prepared, context.current):
            record["payload"]["state"]["incident"]["posts"]["parent"] = {
                "state": "delivered", "ts": "1791400000.000099",
            }
    context.signature.side_effect = emissions.EmissionError("unsigned claim")
    with pytest.raises(emissions.EmissionError, match="unsigned claim"):
        context.invoke()
    context.signature.assert_called_once()
    assert_no_slack_writes(context)


@pytest.mark.parametrize("case", ["failed", "unrecognized-phase", "missing", "duplicate"])
def test_deliver_cli_requires_one_completed_signed_checkpoint_before_slack(deliver_cli, case):
    context = deliver_cli
    if case == "failed":
        context.status["state"] = "failure"
    elif case == "unrecognized-phase":
        context.status["description"] = "started"
    elif case == "missing":
        context.api.pages.return_value = []
    else:
        context.api.pages.return_value = [context.status, {**context.status, "id": 13}]
    with pytest.raises(incidents.IncidentError, match="checkpoint is incomplete"):
        context.invoke()
    context.signature.assert_not_called()
    assert_no_slack_writes(context)
