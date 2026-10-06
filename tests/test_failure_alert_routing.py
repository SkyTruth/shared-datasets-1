"""Keep supervised failures in GitHub without losing unattended coverage."""

import json
import os
from pathlib import Path
import subprocess
from unittest import mock

import pytest

from scripts import workflow_failure_alert as alerts
from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
MARKER = "SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL"


def event(**updates):
    run = {
        "id": 123, "name": "Bucket hygiene audit", "event": "schedule",
        "status": "completed", "conclusion": "failure", "head_branch": "main",
        "head_repository": {"full_name": alerts.REPOSITORY},
    }
    run.update(updates)
    return {"repository": {"full_name": alerts.REPOSITORY}, "workflow_run": run}


@pytest.mark.parametrize("trigger", ["schedule", "workflow_run"])
@pytest.mark.parametrize("conclusion", sorted(alerts.FAILED_CONCLUSIONS))
def test_unattended_failures_link_the_failed_run(trigger, conclusion):
    alert = alerts.alert_for_run(event(event=trigger, conclusion=conclusion))
    assert "actions/runs/123|" in alert["body"]
    assert conclusion in alert["body"]
    assert alert["status"] == "error"


@pytest.mark.parametrize("update", [
    {"event": "pull_request"}, {"event": "pull_request_target"},
    {"event": "push"}, {"event": "workflow_dispatch"},
    {"conclusion": "success"}, {"conclusion": "skipped"}, {"conclusion": "neutral"},
    {"status": "in_progress"}, {"head_branch": "feature"},
    {"head_repository": {"full_name": "someone/fork"}},
])
def test_supervised_or_nonfailure_runs_are_quiet(update):
    assert alerts.alert_for_run(event(**update)) is None


def test_event_data_cannot_override_destination_or_inject_slack_mentions():
    payload = event(name="<@U123> & <https://evil.test|click>", html_url="https://evil.test")
    alert = alerts.alert_for_run(payload)
    assert "<@U123>" not in alert["body"]
    assert "&lt;@U123&gt;" in alert["body"]
    assert "<https://github.com/SkyTruth/shared-datasets-1/actions/runs/123|" in alert["body"]
    payload["repository"]["full_name"] = "someone/fork"
    assert alerts.alert_for_run(payload) is None
    for run_id in (True, 0, "123|@channel"):
        with pytest.raises(ValueError):
            alerts.alert_for_run(event(id=run_id))


def test_cli_dry_run_and_delivery_failure(tmp_path, monkeypatch, capsys):
    path = tmp_path / "event.json"
    path.write_text(json.dumps(event()))
    monkeypatch.setattr("sys.argv", ["alert", "--event-path", str(path), "--dry-run"])
    with mock.patch.object(alerts, "notify") as notify, mock.patch.object(alerts, "verify_source_event", return_value=True):
        alerts.main()
        notify.assert_not_called()
    assert "actions/runs/123" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["alert", "--event-path", str(path)])
    with mock.patch.object(alerts, "notify", side_effect=RuntimeError("delivery failed")) as notify, mock.patch.object(alerts, "verify_source_event", return_value=True):
        with pytest.raises(RuntimeError, match="delivery failed"):
            alerts.main()
        assert notify.call_args.kwargs["strict"] is True
    path.write_text(json.dumps(event(event="pull_request")))
    with mock.patch.object(alerts, "notify") as notify:
        alerts.main()
        notify.assert_not_called()


def test_every_scheduled_workflow_has_failure_coverage_without_self_recursion():
    workflow = load_workflow(WORKFLOWS / "unattended-workflow-alert.yml")
    subscriptions = workflow_triggers(workflow)["workflow_run"]["workflows"]
    for path in WORKFLOWS.glob("*.yml"):
        candidate = load_workflow(path)
        if "schedule" in workflow_triggers(candidate):
            assert candidate["name"] in subscriptions, path.name
    assert workflow["name"] not in subscriptions
    assert {"Catalog web deploy", "Catalog viewer deploy", "PMTiles CDN sync"} <= set(subscriptions)
    assert "CI" in subscriptions
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    worker_filter = workflow["jobs"]["notify"]["if"]
    assert "github.event.workflow_run.status == 'completed'" in worker_filter
    assert "contains(fromJSON(" in worker_filter
    conclusions = json.loads(worker_filter.split("fromJSON('", 1)[1].split("')", 1)[0])
    assert set(conclusions) == alerts.FAILED_CONCLUSIONS
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in worker_filter
    steps = workflow["jobs"]["notify"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"ref": "main", "persist-credentials": False}
    assert all("download-artifact" not in s.get("uses", "") for s in steps)


def test_ci_publication_failure_alerts_without_alerting_validation_only():
    payload = event(name="CI", event="push")
    publication = {"name": "publish (208) / Apply approved PR mutation plans (PR #208)",
                   "status": "completed", "conclusion": "failure"}
    validation = {"name": "tests", "status": "completed", "conclusion": "failure"}
    assert alerts.alert_for_run(payload, jobs=[validation]) is None
    assert alerts.alert_for_run(payload, jobs=[publication]) is not None
    assert alerts.alert_for_run(event(name="CI", event="pull_request"), jobs=[publication]) is None
    assert alerts.alert_for_run(payload, jobs=[{**publication, "conclusion": "skipped"}]) is None


def source_api(run, **updates):
    api = mock.Mock()
    repository = {"id": 9, "full_name": alerts.REPOSITORY}
    authoritative = {**run, "repository": repository, "head_repository": repository,
                     "workflow_id": 7, "path": ".github/workflows/" + alerts.WORKFLOW_PATHS[run["name"]], **updates}
    api.get.side_effect = [authoritative, {"id": 7, "path": authoritative["path"]}, repository]
    return api


def test_ci_alert_checks_source_identity_and_suppresses_obsolete_attempts():
    run = event(name="CI", event="push", head_sha="a" * 40, run_attempt=1)["workflow_run"]
    api = source_api(run, run_attempt=2, status="in_progress", conclusion=None)
    assert alerts.ci_jobs_for_event(api, run) == []
    api.pages.assert_not_called()
    api = source_api(run, path=".github/workflows/untrusted.yml")
    with pytest.raises(ValueError, match="trusted main source"):
        alerts.ci_jobs_for_event(api, run)
    api = source_api(run)
    alerts.ci_jobs_for_event(api, run)
    api.pages.assert_called_once_with(
        "repos/SkyTruth/shared-datasets-1/actions/runs/123/attempts/1/jobs?per_page=100", field="jobs")


@pytest.mark.parametrize("name", sorted(alerts.OBSERVER_WORKFLOWS))
def test_failed_terminal_observation_and_explicit_reconciliation_remain_visible(name):
    payload = event(name=name, event="workflow_dispatch", head_sha="a" * 40, run_attempt=1)
    assert alerts.alert_for_run(payload) is not None
    assert alerts.verify_source_event(source_api(payload["workflow_run"]), payload["workflow_run"])
    assert not alerts.verify_source_event(source_api(payload["workflow_run"], run_attempt=2), payload["workflow_run"])


@pytest.mark.parametrize("updates", [{"head_sha": "b" * 40}, {"head_branch": "feature"}, {"conclusion": "success"}, {"head_repository": {"id": 10, "full_name": alerts.REPOSITORY}}])
def test_observer_events_must_match_actual_current_main_source(updates):
    run = event(name="Deployment terminal verification", event="schedule", head_sha="a" * 40, run_attempt=1)["workflow_run"]
    with pytest.raises(ValueError):
        alerts.verify_source_event(source_api(run, **updates), run)


def test_ci_covers_any_real_post_validation_deployment_failure():
    ready = {"name": "ci-ready", "status": "completed", "conclusion": "success"}
    deploy = {"name": "wdpa-validation / deploy", "status": "completed", "conclusion": "failure"}
    payload = event(name="CI", event="push")
    assert alerts.alert_for_run(payload, jobs=[ready, deploy]) is not None
    assert alerts.alert_for_run(payload, jobs=[{**ready, "conclusion": "failure"}, deploy]) is None
    assert alerts.alert_for_run(payload, jobs=[ready, {**deploy, "name": "sdk-validation (Node 24)"}]) is None
    assert alerts.alert_for_run(event(name="Cron alert delivery test", event="workflow_dispatch")) is None


@pytest.mark.parametrize("filename,step_name", [
    ("sea-ice-daily-deploy.yml", "Execute sea-ice-daily canary"),
    ("eamlis-monthly-deploy.yml", "Execute eamlis-monthly canary"),
])
@pytest.mark.parametrize("date", ["", "2026-10-01", "invalid; exit 0"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_real_synchronous_canary_shell_marks_only_execution_and_propagates_failure(filename, step_name, date, exit_code):
    step = workflow_steps_by_name(load_workflow(WORKFLOWS / filename), "deploy")[step_name]
    recorder = f'gcloud() {{ printf "%s\\n" "$@"; return {exit_code}; }}\n'
    env = {**os.environ, "JOB_NAME": "test-job", "REGION": "us-central1",
           "GOOGLE_CLOUD_PROJECT": "test-project", "CANARY_RUN_DATE": date,
           "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": alerts.REPOSITORY,
           "GITHUB_RUN_ID": "123", "GITHUB_ENV": os.devnull}
    result = subprocess.run(["bash", "-c", recorder + step["run"]], env=env, capture_output=True, text=True)
    if date.startswith("invalid"):
        assert result.returncode != 0
        assert result.stdout == ""
        return
    assert result.returncode == exit_code, result.stderr
    args = result.stdout.splitlines()
    assert args[:3] == ["run", "jobs", "execute"] or args[1:4] == ["run", "jobs", "execute"]
    assert "--wait" in args and "--async" not in args
    overrides = [a for a in args if a.startswith("--update-env-vars=")]
    assert len(overrides) == 1
    assert f"{MARKER}=https://github.com/{alerts.REPOSITORY}/actions/runs/123" in overrides[0]
    assert (",RUN_DATE=2026-10-01" in overrides[0]) == bool(date)
    assert not step.get("continue-on-error", False)


def test_marker_is_never_persistent_or_attached_to_detached_work():
    marked_steps = []
    for path in WORKFLOWS.glob("*.yml"):
        workflow = load_workflow(path)
        assert MARKER not in str(workflow.get("env", {}))
        for job in workflow["jobs"].values():
            assert MARKER not in str(job.get("env", {}))
            for step in job.get("steps", []):
                run = step.get("run", "")
                if MARKER in run:
                    marked_steps.append(step["name"])
                    assert "--wait" in run and "--async" not in run
                    assert "gcloud run jobs execute" in run
                    assert "set -euo pipefail" in run
                    assert not step.get("continue-on-error", False)
    assert set(marked_steps) == {"Execute sea-ice-daily canary", "Execute eamlis-monthly canary", "Verify a real upload before processing"}
    for path in (ROOT / "terraform").rglob("*.tf"):
        if path.name != "monitoring.tf":
            assert MARKER not in path.read_text(), path


def test_cloud_policy_suppresses_only_trusted_marked_executions_and_exact_observer_write():
    text = (ROOT / "terraform/envs/prod/monitoring.tf").read_text()
    failure = text.split('"scheduled_ingestion_cloud_run_failure"', 1)[1].split("\nresource ", 1)[0]
    filter_text = failure.split("filter = <<-EOT", 1)[1].split("EOT", 1)[0]
    assert 'protoPayload.status.code>0' in filter_text
    assert '''NOT (
  protoPayload.response.metadata.annotations."run.googleapis.com/creator"="${var.github_actions_terraform_service_account_email}"
  AND protoPayload.response.spec.template.spec.containers.env.name="SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL"
)''' in filter_text
    assert "resource.labels.job_name" not in filter_text  # Future jobs keep coverage.
    write = text.split('"dataset_object_written_by_unapproved_principal"', 1)[1]
    assert '''NOT (
  protoPayload.authenticationInfo.principalEmail="${module.wdpa_observer_service_account.email}"
  AND protoPayload.resourceName="${local.shared_bucket_object_resource_prefix}_catalog/wdpa-monthly-execution.json"
)''' in write
    iam = (ROOT / "terraform/envs/prod/canonical_mutation_iam.tf").read_text()
    approved = iam.split("canonical_write_allowed_principal_emails = [", 1)[1].split("]", 1)[0]
    assert "wdpa_observer" not in approved


def test_alert_probes_are_explicit_and_routine_wdpa_runs_keep_async_coverage():
    for filename,step_name in [
        ("wdpa-monthly-deploy.yml", "Execute wdpa-monthly canary"),
        ("wdpa-processing-validation-deploy.yml", "Start the unattended validation execution"),
    ]:
        workflow = load_workflow(WORKFLOWS / filename)
        assert "failure_alert_verified_execution" not in str(workflow)
        steps = workflow_steps_by_name(workflow, "deploy")
        assert "WDPA_FAIL_BEFORE" not in str(steps)
        assert "--async" in steps[step_name]["run"]
        assert MARKER not in steps[step_name]["run"]
    probe = load_workflow(WORKFLOWS / "cron-alert-delivery-test.yml")
    assert set(workflow_triggers(probe)) == {"workflow_dispatch"}
    assert probe["jobs"]["probe"]["environment"] == "shared-datasets-production"
    run = workflow_steps_by_name(probe, "probe")["Run a controlled failure without dataset writes"]["run"]
    assert MARKER not in run
    assert "WDPA_FAIL_BEFORE_WRITES=true" in run
    assert "WDPA_FAIL_BEFORE_DATASET_WRITES=1" in run
    assert run.index("completionTime") < run.index("gcloud run jobs execute")
