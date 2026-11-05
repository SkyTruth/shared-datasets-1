"""Workflow labels may change without changing trusted identities or routing."""

import json
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import deployment_revision, deployment_source, workflow_failure_alert as alerts
from scripts.deployment_revision import DeploymentError
from scripts.workflow_identity import WORKFLOW_NAMES, RETIRED_WORKFLOWS
from workflow_helpers import load_workflow, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"


def test_workflow_names_have_unique_current_and_historical_identities():
    actual = {path.name: load_workflow(path)["name"] for path in WORKFLOWS.glob("*.yml")}
    assert actual == {filename: names[0] for filename, names in WORKFLOW_NAMES.items() if filename not in RETIRED_WORKFLOWS}
    names = [name for aliases in WORKFLOW_NAMES.values() for name in aliases]
    assert len(names) == len(set(names))


def test_shared_ingestion_leaf_adds_no_top_level_or_notification_authority():
    workflow = load_workflow(WORKFLOWS / "ingestion-deploy.yml")
    assert set(workflow_triggers(workflow)) == {"workflow_call"}
    assert "ingestion-deploy.yml" not in alerts.WORKFLOW_EVENTS
    assert "ingestion-deploy.yml" not in deployment_revision.TARGET_WORKFLOWS.values()
    assert set(deployment_revision.TARGET_WORKFLOWS[target] for target in deployment_revision.INGESTION_TARGETS) == {
        "dataset-usage-deploy.yml", "wdpa-monthly-deploy.yml", "mpatlas-monthly-deploy.yml", "eamlis-monthly-deploy.yml", "sea-ice-daily-deploy.yml",
    }


def test_subscriptions_and_worker_filters_keep_current_and_historical_runs():
    observer = load_workflow(WORKFLOWS / "unattended-workflow-alert.yml")
    subscriptions = workflow_triggers(observer)["workflow_run"]["workflows"]
    assert len(subscriptions) == len(set(subscriptions))
    polled_only = {name for filename in ("operational-safety-check.yml", "unattended-workflow-alert.yml") for name in WORKFLOW_NAMES[filename]}
    assert set(subscriptions) == set(alerts.WORKFLOW_PATHS) - polled_only
    matched = re.findall(r"contains\(fromJSON\('([^']+)'\), github.event.workflow_run.name\)",
                         observer["jobs"]["incidents"]["if"])
    assert {name for group in matched for name in json.loads(group)} == set(
        WORKFLOW_NAMES["deployment-verification.yml"] + WORKFLOW_NAMES["ci.yml"])
    for filename, upstream in (("catalog-web-deploy.yml", ("release-index-rebuild.yml",)),):
        trigger = workflow_triggers(load_workflow(WORKFLOWS / filename))["workflow_run"]
        assert set(trigger["workflows"]) == {name for file in upstream for name in WORKFLOW_NAMES[file]}


def test_readonly_workflow_files_match_the_oidc_policy_independently_of_labels():
    policy = (ROOT / "terraform/envs/prod/github_readonly_iam.tf").read_text()
    assert 'assertion.workflow ==' not in policy
    allowed = set(json.loads((ROOT / 'terraform/envs/prod/github-workflow-identities.json').read_text())['bucket_reader'])
    assert allowed == set((
        "bucket-hygiene-audit.yml", "catalog-drift-guard.yml", "dataset-breaking-change-alert.yml",
    ))


@pytest.mark.parametrize("name", WORKFLOW_NAMES["ci.yml"])
def test_deployment_source_resolves_the_exact_verified_ci_attempt_for_either_label(name):
    repository = {"id": 9, "full_name": alerts.REPOSITORY}
    sha = "a" * 40
    workflow = {"id": 7, "path": ".github/workflows/ci.yml"}
    run = {"id": 123, "run_attempt": 2, "name": name, "workflow_id": 7, "path": workflow["path"],
           "event": "push", "head_branch": "main", "head_sha": sha,
           "repository": repository, "head_repository": repository}
    api = Mock()
    responses = {
        f"repos/{alerts.REPOSITORY}": repository,
        f"repos/{alerts.REPOSITORY}/actions/workflows/ci.yml": workflow,
        f"repos/{alerts.REPOSITORY}/actions/runs/123/attempts/2": run,
    }
    api.get.side_effect = responses.__getitem__
    api.pages.return_value = [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
    event = json.loads(json.dumps({"workflow_run": run}))
    assert deployment_source.resolve(api, alerts.REPOSITORY, sha, event, 999, 1) == (123, 2)
    api.pages.assert_called_once_with(f"repos/{alerts.REPOSITORY}/actions/runs/123/attempts/2/jobs?per_page=100", "jobs")
    run["path"] = ".github/workflows/untrusted.yml"
    with pytest.raises(DeploymentError, match="repository CI workflow"):
        deployment_source.resolve(api, alerts.REPOSITORY, sha, event, 999, 1)
    api.post.assert_not_called()


@pytest.mark.parametrize("name", WORKFLOW_NAMES["release-index-rebuild.yml"])
def test_catalog_followup_verifies_current_and_historical_index_completions(tmp_path, monkeypatch, name):
    repository = {"id": 9, "full_name": alerts.REPOSITORY}
    sha = "a" * 40
    common = {"head_sha": sha, "head_branch": "main", "repository": repository, "head_repository": repository}
    ci = {**common, "id": 123, "run_attempt": 2, "workflow_id": 7, "path": ".github/workflows/ci.yml", "event": "push"}
    index = {**common, "id": 456, "run_attempt": 1, "workflow_id": 8, "path": ".github/workflows/release-index-rebuild.yml",
             "name": name, "conclusion": "success"}
    catalog = {**common, "id": 789, "run_attempt": 1, "workflow_id": 10, "path": ".github/workflows/catalog-web-deploy.yml", "event": "workflow_run"}
    responses = {f"repos/{alerts.REPOSITORY}": repository}
    for run in (ci, index, catalog):
        responses[f"repos/{alerts.REPOSITORY}/actions/runs/{run['id']}"] = run
        responses[f"repos/{alerts.REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}"] = run
        responses[f"repos/{alerts.REPOSITORY}/actions/workflows/{run['path'].rsplit('/', 1)[-1]}"] = {"id": run["workflow_id"], "path": run["path"]}
    api = Mock()
    api.get.side_effect = responses.__getitem__
    api.pages.return_value = [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"workflow_run": index}))
    for key, value in {"GITHUB_REPOSITORY": alerts.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                       "GITHUB_RUN_ID": "789", "GITHUB_EVENT_PATH": str(event_path)}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(deployment_revision.subprocess, "check_output", lambda *_args, **_kwargs: sha)
    monkeypatch.setattr(deployment_revision, "git_ancestor", lambda *_: True)
    args = SimpleNamespace(workflow="catalog-web-deploy.yml", executor_sha=sha, source_run_id=123, source_run_attempt=2)
    assert deployment_revision.verify_context(args, api) == alerts.REPOSITORY
    index["path"] = ".github/workflows/untrusted.yml"
    with pytest.raises(DeploymentError, match="untrusted release-index completion"):
        deployment_revision.verify_context(args, api)
    api.post.assert_not_called()
