"""Keep supervised failures in GitHub without losing unattended coverage."""

import io
import json
from pathlib import Path
from unittest import mock

import pytest

from scripts import workflow_failure_alert as alerts
from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]
# The legacy usage job names below belong to this immutable main workflow graph.
LEGACY_USAGE_REVISION = "7bd972e6150a01ca3be538d1ca33bc543d6aa6f8"
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


def test_removed_webhook_cli_cannot_post(monkeypatch):
    monkeypatch.setattr("sys.argv", ["alert", "--event-path", "event.json"])
    with pytest.raises(SystemExit):
        alerts.main()


def test_every_scheduled_workflow_has_failure_coverage_without_self_recursion():
    workflow = load_workflow(WORKFLOWS / "unattended-workflow-alert.yml")
    subscriptions = workflow_triggers(workflow)["workflow_run"]["workflows"]
    for path in WORKFLOWS.glob("*.yml"):
        candidate = load_workflow(path)
        if "schedule" in workflow_triggers(candidate) and path.name != "operational-safety-check.yml":
            assert candidate["name"] in subscriptions, path.name
    assert workflow["name"] not in subscriptions
    assert {"Catalog web deploy", "Catalog viewer deploy", "PMTiles CDN sync"} <= set(subscriptions)
    assert set(alerts.WORKFLOW_NAMES["ci.yml"] + alerts.WORKFLOW_NAMES["publish-typescript-sdk.yml"]) <= set(subscriptions)
    assert set(workflow["jobs"]) == {"incidents"}
    assert workflow["permissions"] == {"contents": "read", "actions": "read"}
    worker_filter = workflow["jobs"]["incidents"]["if"]
    assert "github.event.workflow_run.status == 'completed'" in worker_filter
    assert "contains(fromJSON(" in worker_filter
    conclusions = json.loads(worker_filter.split("fromJSON('", 1)[1].split("')", 1)[0])
    assert set(conclusions) == alerts.FAILED_CONCLUSIONS
    assert "github.event.workflow_run.head_repository.full_name == github.repository" in worker_filter
    steps = workflow["jobs"]["incidents"]["steps"]
    checkout = next(s for s in steps if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"ref": "${{ github.workflow_sha }}", "fetch-depth": 0, "persist-credentials": False}
    assert all("download-artifact" not in s.get("uses", "") for s in steps)


@pytest.mark.parametrize("name", alerts.WORKFLOW_NAMES["ci.yml"])
def test_ci_publication_failure_alerts_without_alerting_validation_only(name):
    payload = event(name=name, event="push")
    publication = {"name": "publish (208) / Apply approved PR mutation plans (PR #208)",
                   "status": "completed", "conclusion": "failure"}
    validation = {"name": "tests", "status": "completed", "conclusion": "failure"}
    assert alerts.alert_for_run(payload, jobs=[validation]) is None
    assert alerts.alert_for_run(payload, jobs=[publication]) is not None
    assert alerts.alert_for_run(event(name=name, event="pull_request"), jobs=[publication]) is None
    assert alerts.alert_for_run(payload, jobs=[{**publication, "conclusion": "skipped"}]) is None


def source_api(run, **updates):
    api = mock.Mock()
    repository = {"id": 9, "full_name": alerts.REPOSITORY}
    authoritative = {**run, "repository": repository, "head_repository": repository,
                     "workflow_id": 7, "path": ".github/workflows/" + alerts.WORKFLOW_PATHS[run["name"]], **updates}
    api.get.side_effect = [authoritative, {"id": 7, "path": authoritative["path"]}, repository]
    return api


@pytest.mark.parametrize("name", sorted(alerts.WORKFLOW_PATHS))
def test_display_aliases_still_require_the_registered_workflow_path(name):
    run = event(name=name, head_sha="a" * 40, run_attempt=1)["workflow_run"]
    assert alerts.verify_source_event(source_api(run), run)
    with pytest.raises(ValueError, match="trusted main source"):
        alerts.verify_source_event(source_api(run, path=".github/workflows/untrusted.yml"), run)


@pytest.mark.parametrize("conclusion", ["success", "failure", "timed_out", "cancelled"])
@pytest.mark.parametrize("name", alerts.WORKFLOW_NAMES["catalog-web-deploy.yml"])
def test_scheduled_catalog_probe_with_skipped_publisher_stays_in_github(conclusion, name):
    payload = event(name=name)
    jobs = [
        {"name": "Check for catalog input changes", "status": "completed", "conclusion": conclusion},
        {"name": "Build and publish catalog web", "status": "completed", "conclusion": "skipped"},
    ]
    assert alerts.alert_for_run(payload, jobs=jobs) is None
    # Actual publisher failures and historical jobs without the activity gate
    # still alert. The exception never applies to an explicit action's failure.
    jobs[1]["conclusion"] = "failure"
    assert alerts.alert_for_run(payload, jobs=jobs) is not None
    assert alerts.alert_for_run(payload, jobs=jobs[1:]) is not None
    jobs[1]["conclusion"] = "skipped"
    assert alerts.alert_for_run(event(name=name, event="workflow_run"), jobs=jobs) is not None


def test_catalog_probe_uses_verified_job_evidence_before_suppressing():
    from scripts import slack_incidents as incidents
    payload = event(name="Catalog web deploy", head_sha="a" * 40, run_attempt=1)
    api = source_api(payload["workflow_run"])
    api.pages.return_value = [
        {"name": "Check for catalog input changes", "status": "completed", "conclusion": "failure"},
        {"name": "Build and publish catalog web", "status": "completed", "conclusion": "skipped"},
    ]
    assert incidents.source_observations(api, payload, {}) == []
    api.pages.assert_called_once_with(
        "repos/SkyTruth/shared-datasets-1/actions/runs/123/attempts/1/jobs?per_page=100", field="jobs")
    api = source_api(payload["workflow_run"], run_attempt=2)
    assert incidents.source_observations(api, payload, {}) == []
    api.pages.assert_not_called()


def test_catalog_probe_does_not_hide_missing_ambiguous_or_other_failed_jobs():
    payload = event(name="Catalog web deploy")
    probe = {"name": "Check for catalog input changes", "status": "completed", "conclusion": "failure"}
    publisher = {"name": "Build and publish catalog web", "status": "completed", "conclusion": "skipped"}
    other = {"name": "another job", "status": "completed", "conclusion": "failure"}
    for jobs in ([], [probe], [publisher], [probe, probe, publisher], [probe, publisher, publisher], [probe, publisher, other]):
        assert alerts.alert_for_run(payload, jobs=jobs) is not None


def test_ci_alert_checks_source_identity_and_suppresses_obsolete_attempts():
    run = event(name="CI", event="push", head_sha="a" * 40, run_attempt=1)["workflow_run"]
    api = source_api(run, run_attempt=2, status="in_progress", conclusion=None)
    assert alerts.jobs_for_event(api, run) is None
    api.pages.assert_not_called()
    api = source_api(run, path=".github/workflows/untrusted.yml")
    with pytest.raises(ValueError, match="trusted main source"):
        alerts.jobs_for_event(api, run)
    api = source_api(run)
    alerts.jobs_for_event(api, run)
    api.pages.assert_called_once_with(
        "repos/SkyTruth/shared-datasets-1/actions/runs/123/attempts/1/jobs?per_page=100", field="jobs")


@pytest.mark.parametrize("name", sorted(alerts.OBSERVER_WORKFLOWS))
def test_failed_terminal_observation_and_explicit_reconciliation_remain_visible(name):
    payload = event(name=name, event="workflow_dispatch", head_sha="a" * 40, run_attempt=1)
    assert alerts.alert_for_run(payload) is not None
    assert alerts.verify_source_event(source_api(payload["workflow_run"]), payload["workflow_run"])
    assert not alerts.verify_source_event(source_api(payload["workflow_run"], run_attempt=2), payload["workflow_run"])


@pytest.mark.parametrize("updates", [{"head_sha": "b" * 40}, {"head_branch": "feature"}, {"conclusion": "success"}, {"head_repository": {"id": 10, "full_name": alerts.REPOSITORY}}])
@pytest.mark.parametrize("name", ["Deployment terminal verification", "Deployment completion check"])
def test_observer_events_must_match_actual_current_main_source(updates, name):
    run = event(name=name, event="schedule", head_sha="a" * 40, run_attempt=1)["workflow_run"]
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


@pytest.mark.parametrize("name", [
    "geospatial-changes", "lint", "tests", "geospatial-integration", "production-images",
    "sdk-validation (Node 22)", "sdk-validation (Node 24)", "browser", "ci-ready",
])
def test_all_validation_workers_remain_validation_when_routing_a_failed_ci_attempt(name):
    failed_validation = {"name": name, "status": "completed", "conclusion": "failure"}
    payload = event(name="CI", event="push")
    assert alerts.alert_for_run(payload, jobs=[failed_validation]) is None
    # Routing must still classify validation explicitly when a successful gate
    # appears in the supplied job evidence; it cannot infer deployment by exclusion.
    ready = {"name": "ci-ready", "status": "completed", "conclusion": "success"}
    assert alerts.alert_for_run(payload, jobs=[ready, failed_validation]) is None
    failed_delivery = {"name": "eamlis / Build image and apply", "status": "completed", "conclusion": "failure"}
    assert alerts.alert_for_run(payload, jobs=[ready, failed_validation, failed_delivery]) is not None


@pytest.mark.parametrize("target", ["mpatlas-monthly", "sea-ice-daily", "eamlis-monthly", "wdpa-monthly"])
@pytest.mark.parametrize("date", ["", "2026-10-01", "invalid; exit 0"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_real_canary_launch_marks_only_short_executions_and_propagates_failure(tmp_path, target, date, exit_code):
    from test_ingestion_deploy_workflow import run_shell, shared_step
    launch = shared_step("verify", "Execute ingestion canary")
    recorder = f'''gcloud() {{
      printf "%s\\n" "$@" > "$COMMAND_LOG"
      echo test-execution
      return {exit_code}
    }}
'''
    log = tmp_path / "commands"
    result = run_shell(tmp_path, launch["run"], target=target, prefix=recorder, extra={
        "COMMAND_LOG": str(log), "CANARY_RUN_DATE": date,
        "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": alerts.REPOSITORY, "GITHUB_RUN_ID": "123",
    })
    if date.startswith("invalid"):
        assert result.returncode != 0
        assert not log.exists()
        return
    assert result.returncode == exit_code, result.stderr
    args = log.read_text().splitlines()
    assert args[:4] == ["run", "jobs", "execute", target]
    assert "--async" in args and "--wait" not in args
    overrides = [arg for arg in args if arg.startswith("--update-env-vars=")]
    marker = f"{MARKER}=https://github.com/{alerts.REPOSITORY}/actions/runs/123"
    assert (marker in " ".join(overrides)) == (target != "wdpa-monthly")
    assert ("RUN_DATE=2026-10-01" in " ".join(overrides)) == bool(date)
    assert not launch.get("continue-on-error", False)
    if not exit_code:
        assert (tmp_path / "environment").read_text() == "CANARY_EXECUTION=test-execution\n"


@pytest.mark.parametrize("target", ["mpatlas-monthly", "sea-ice-daily", "eamlis-monthly", "wdpa-monthly"])
@pytest.mark.parametrize("phase", ["verified", "failed", "wrong-image", "unknown"])
def test_shared_canary_watcher_requires_terminal_success_of_exact_image(tmp_path, target, phase):
    from test_ingestion_deploy_workflow import run_shell, shared_step
    image = "image@sha256:" + "a" * 64
    facts = {"spec": {"template": {"spec": {"containers": [{"image": image if phase != "wrong-image" else "wrong"}]}}},
             "status": {"completionTime": "now", "succeededCount": 1 if phase == "verified" else 0,
                        "failedCount": 1 if phase == "failed" else 0}}
    prefix = "gcloud() { printf '%s' '$FACTS'; }\n".replace("$FACTS", json.dumps(facts))
    step = shared_step("verify", "Watch ingestion canary")
    result = run_shell(tmp_path, step["run"], target=target, prefix=prefix,
                       extra={"IMAGE": image, "CANARY_EXECUTION": "test-execution"})
    assert (result.returncode == 0) == (phase == "verified"), result.stderr
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
                    assert "gcloud run jobs execute" in run
                    assert "set -euo pipefail" in run
                    assert not step.get("continue-on-error", False)
                    if path.name == "ingestion-deploy.yml":
                        assert step["name"] == "Execute ingestion canary"
                        assert step['env']['CANARY_RUN_URL'] == '${{ fromJSON(needs.prepare.outputs.config).canary_run_url }}'
                        from scripts.ingestion_targets import DEPLOY_TARGETS as TARGETS
                        assert {name for name, config in TARGETS.items() if not config['canary_run_url']} == {'wdpa-monthly'}
                        assert "Watch ingestion canary" in workflow_steps_by_name(workflow, "verify")
                    else:
                        assert "--wait" in run and "--async" not in run
    assert set(marked_steps) == {"Execute ingestion canary"}
    for path in (ROOT / "terraform").rglob("*.tf"):
        if path.name != "monitoring.tf":
            assert MARKER not in path.read_text(), path


def test_cloud_policy_covers_all_workers_and_suppresses_only_trusted_marked_executions():
    text = (ROOT / "terraform/envs/prod/monitoring.tf").read_text()
    failure = text.split('"scheduled_ingestion_cloud_run_failure"', 1)[1].split("\nresource ", 1)[0]
    filter_text = failure.split("filter = <<-EOT", 1)[1].split("EOT", 1)[0]
    assert 'protoPayload.status.code>0' in filter_text
    assert '''NOT (
  protoPayload.response.metadata.annotations."run.googleapis.com/creator"="${var.github_actions_terraform_service_account_email}"
  AND protoPayload.response.spec.template.spec.containers.env.name="SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL"
)''' in filter_text
    assert "resource.labels.job_name" not in filter_text
    scheduler = text.split('"scheduled_ingestion_scheduler_failure"', 1)[1].split("\nresource ", 1)[0]
    scheduler_filter = scheduler.split("filter = <<-EOT", 1)[1].split("EOT", 1)[0]
    assert "resource.labels.job_id" not in scheduler_filter
    assert "wdpa_execution_observer" not in text
    write = text.split('"dataset_object_written_by_unapproved_principal"', 1)[1]
    assert "wdpa_observer" not in write
    assert "_catalog/wdpa-monthly-execution.json" not in write
    iam = (ROOT / "terraform/envs/prod/canonical_mutation_iam.tf").read_text()
    assert "wdpa_observer" not in iam


@pytest.mark.parametrize("failure", [None, "missing", "applied", "older", "divergent", "unsigned", "unregistered"])
def test_legacy_resolution_cli_requires_every_failed_target_to_recover(monkeypatch, failure):
    from scripts import slack_incidents as incidents
    from scripts import deployment_revision as deployments
    from scripts.slack_incident_api import Slack

    run = event(name="CI", event="push", run_attempt=1, head_sha=LEGACY_USAGE_REVISION)["workflow_run"]
    repository = {"id": 9, "full_name": alerts.REPOSITORY}
    run.update(workflow_id=7, path=".github/workflows/ci.yml", repository=repository, head_repository=repository)
    job = {"id": 10, "name": "dataset-usage / Deploy tested dataset usage image and private telemetry resources",
           "status": "completed", "conclusion": "failure", "completed_at": "2026-10-23T22:05:00Z"}
    jobs = [{**job, "id": 11, "name": "ci-ready", "conclusion": "success"}, job]
    if failure == "unregistered":
        jobs.append({**job, "id": 12, "name": "Unknown failed job"})
    record = {"id": 20, "sha": "b" * 40}
    status = {"id": 21, "state": "success", "description": "applied" if failure == "applied" else "verified",
              "created_at": "2026-10-22T22:05:00Z" if failure == "older" else "2026-10-25T12:00:00Z",
              "log_url": incidents.emissions.invocation(alerts.REPOSITORY, 456, 1)}
    api = mock.Mock()
    api.get.side_effect = lambda path: ({"id": 7, "path": run["path"]} if path.endswith("/workflows/7")
                                      else repository if path == "repos/" + alerts.REPOSITORY else run)
    api.pages.side_effect = lambda path, **kwargs: (jobs if "/jobs?" in path else [status] if "/statuses?" in path else ([] if failure == "missing" else [record]))

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    opener = mock.Mock(return_value=Response(b'{"ok":true,"channel":"C0123456789","ts":"1792793158.506179"}'))
    slack = Slack("test-token", "C0123456789", opener=opener)
    monkeypatch.setattr("sys.argv", ["alert", "--resolve-run-id", "123", "--message-ts", "1792793158.506179"])
    with mock.patch.object(alerts, "GitHub", return_value=api), \
            mock.patch.object(deployments, "GitHub", return_value=api), \
            mock.patch("scripts.slack_incident_api.Slack", return_value=slack), \
            mock.patch.object(deployments, "verify_record", return_value={"target": "dataset-usage", "artifact": "image@sha256:" + "f" * 64},
                              side_effect=deployments.DeploymentError("unsigned") if failure == "unsigned" else None), \
            mock.patch.object(deployments, "record_payload", return_value={"target": "dataset-usage", "artifact": "image@sha256:" + "f" * 64}), \
            mock.patch.object(incidents, "terminal_contract_sha256", return_value="c" * 64), \
            mock.patch.object(deployments, "verify_receipt"), \
            mock.patch.object(deployments, "git_ancestor", return_value=failure != "divergent"):
        if failure:
            with pytest.raises((incidents.IncidentError, deployments.DeploymentError)):
                alerts.main()
            opener.assert_not_called()
        else:
            alerts.main()
            request = opener.call_args.args[0]
            assert request.full_url == "https://slack.com/api/chat.update"
            payload = json.loads(request.data)
            assert payload["channel"] == "C0123456789" and payload["ts"] == "1792793158.506179"
            assert payload["text"].startswith("✅ Unattended GitHub workflow resolved")
            assert "actions/runs/123" in payload["text"] and "actions/runs/456/attempts/1" in payload["text"]
            assert "thread_ts" not in payload and "reply_broadcast" not in payload
            assert opener.call_count == 1


def test_legacy_resolution_workflow_is_owner_only_main_and_read_only():
    workflow = load_workflow(WORKFLOWS / "resolve-workflow-alert.yml")
    assert set(workflow_triggers(workflow)) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read", "actions": "read", "deployments": "read", "attestations": "read"}
    job = workflow["jobs"]["resolve"]
    assert job["if"] == "github.actor == 'jonaraphael' && github.ref == 'refs/heads/main' && inputs.monitoring_incident_id == ''"
    checkout = job["steps"][0]
    assert checkout["with"] == {"ref": "${{ github.workflow_sha }}", "fetch-depth": 0, "persist-credentials": False}
    step = job["steps"][-1]
    assert '${{ inputs.' not in step["run"]
    assert step["env"]["ORIGINAL_MESSAGE_TS"] == "${{ inputs.message_ts }}"


def test_legacy_resolution_uses_real_deployment_client_through_ci_verification(monkeypatch):
    from scripts import dataset_mutation_authorization as authorization
    from scripts import deployment_revision as deployments
    from scripts import deployment_emission as emissions
    from scripts.slack_incident_api import Slack

    repository = {"id": 9, "full_name": alerts.REPOSITORY}
    original = event(name="CI", event="push", run_attempt=1, head_sha=LEGACY_USAGE_REVISION)["workflow_run"]
    original.update(workflow_id=7, path=".github/workflows/ci.yml", repository=repository, head_repository=repository)
    recovered = {**original, "id": 456, "head_sha": "b" * 40, "conclusion": "success"}
    job = {"id": 10, "name": "dataset-usage / Deploy tested dataset usage image and private telemetry resources",
           "status": "completed", "conclusion": "failure", "completed_at": "2026-10-23T22:05:00Z"}
    ready = {**job, "id": 11, "name": "ci-ready", "conclusion": "success"}
    record = {"id": 20, "sha": "b" * 40, "ref": "b" * 40, "environment": "production-dataset-usage",
              "creator": {"login": "github-actions[bot]", "type": "Bot"},
              "payload": {"schema": deployments.SCHEMA, "target": "dataset-usage", "artifact": "image@sha256:" + "f" * 64,
                          "ci_run_id": 456, "ci_run_attempt": 1, "execution_run_id": 456, "execution_run_attempt": 1}}
    status = {"id": 21, "state": "success", "description": "verified", "created_at": "2026-10-25T12:00:00Z",
              "log_url": emissions.invocation(alerts.REPOSITORY, 456, 1)}
    prefix = "repos/" + alerts.REPOSITORY
    responses = {
        prefix: repository,
        prefix + "/actions/workflows/7": {"id": 7, "path": original["path"]},
        prefix + "/actions/workflows/ci.yml": {"id": 7, "path": original["path"]},
        prefix + "/actions/runs/123": original,
        prefix + "/actions/runs/123/attempts/1/jobs?per_page=100": {"total_count": 2, "jobs": [ready, job]},
        prefix + "/actions/runs/456/attempts/1": recovered,
        prefix + "/actions/runs/456/attempts/1/jobs?per_page=100": {"total_count": 1, "jobs": [ready]},
        prefix + "/deployments?environment=production-dataset-usage&per_page=100": [record],
        prefix + "/deployments/20/statuses?per_page=100": [status, {**status, "id": 19, "state": "in_progress", "description": "started"}],
    }
    def read(path, *, paginate=False):
        value = responses[path]
        return json.dumps([value] if paginate else value)

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    opener = mock.Mock(return_value=Response(b'{"ok":true,"channel":"C0123456789","ts":"1792793158.506179"}'))
    slack = Slack("test-token", "C0123456789", opener=opener)
    monkeypatch.setattr("sys.argv", ["alert", "--resolve-run-id", "123", "--message-ts", "1792793158.506179"])
    with mock.patch.object(authorization, "read", side_effect=read), mock.patch.object(deployments, "read", side_effect=read), \
            mock.patch.object(emissions, "verify") as verify_receipt, \
            mock.patch("scripts.slack_incidents.terminal_contract_sha256", return_value="c" * 64), \
            mock.patch.object(deployments, "git_ancestor", return_value=True), \
            mock.patch("scripts.slack_incident_api.Slack", return_value=slack):
        alerts.main()
    assert [call.args[3]["description"] for call in verify_receipt.call_args_list] == ["started", "verified"]
    assert opener.call_count == 1
    assert opener.call_args.args[0].full_url == "https://slack.com/api/chat.update"


def test_native_safety_failure_needs_no_parent_handoff_or_timing_assumption():
    from scripts import slack_incidents as incidents
    payload = event(name="Operations / Safety check", event="schedule", head_sha="a" * 40, run_attempt=1)
    run = payload["workflow_run"]
    run["updated_at"] = "2026-11-04T18:19:00Z"
    api = source_api(run)
    api.pages.return_value = [{"id": 456, "name": "Check for operational work", "status": "completed",
                              "conclusion": "failure", "completed_at": "2026-11-04T18:00:00Z"}]
    observed = incidents.source_observations(api, payload, {})
    assert len(observed) == 1 and observed[0]["conclusion"] == "failure"
    assert observed[0]["job_id"] == 456
