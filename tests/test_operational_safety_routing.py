"""One periodic probe admits existing production and incident workflows on demand."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import slack_incidents as incidents
from scripts import workflow_failure_alert as alerts
from scripts.workflow_identity import WORKFLOW_NAMES
from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
SAFETY = "operational-safety-check.yml"


def test_one_hourly_entrypoint_has_no_unconditional_downstream_subscription():
    files = (SAFETY, "catalog-web-deploy.yml", "deployment-verification.yml")
    schedules = {name: workflow_triggers(load_workflow(WORKFLOWS / name)).get("schedule") for name in files}
    assert schedules == {SAFETY: [{"cron": "17 * * * *"}], "catalog-web-deploy.yml": None, "deployment-verification.yml": None}
    observer = load_workflow(WORKFLOWS / "unattended-workflow-alert.yml")
    subscriptions = workflow_triggers(observer)["workflow_run"]["workflows"]
    assert set(WORKFLOW_NAMES[SAFETY]).isdisjoint(subscriptions)
    for filename in files[1:]:
        assert set(WORKFLOW_NAMES[filename]) <= set(subscriptions)
    assert SAFETY in alerts.MONITORED_WORKFLOWS and SAFETY in alerts.REPLAY_ONLY_WORKFLOWS


def test_safety_probe_is_readonly_except_fixed_workflow_scheduling():
    workflow = load_workflow(WORKFLOWS / SAFETY)
    assert set(workflow["jobs"]) == {"safety"}
    assert workflow_triggers(workflow)["workflow_dispatch"] is None
    job = workflow["jobs"]["safety"]
    assert job["environment"] == "shared-datasets-production"
    assert job["permissions"] == {"contents": "read", "actions": "write", "deployments": "read", "id-token": "write"}
    assert job["env"]["SHARED_DATASETS_ALLOW_CANONICAL_MUTATION"] == "0"
    steps = workflow_steps_by_name(workflow, "safety")
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"] == {"ref": "${{ github.workflow_sha }}", "fetch-depth": 0, "persist-credentials": False}
    probe = steps["Check operational work"]
    assert "operational_safety_check.py --work-dir" in probe["run"]
    assert steps["Install pinned durable receipt verifier"]["run"] == "python scripts/install_deployment_verifier.py"
    assert list(steps).index("Install pinned durable receipt verifier") < list(steps).index("Check operational work")
    assert probe["env"]["SHARED_DATASETS_SLACK_BOT_TOKEN"] == "${{ secrets.SHARED_DATASETS_SLACK_BOT_TOKEN }}"
    assert steps["Request catalog refresh"]["if"] == "${{ steps.probe.outputs.catalog == 'true' }}"
    recovery_if = steps["Request operational reconciliation"]["if"]
    assert "always()" in recovery_if and "failure()" in recovery_if and "steps.trust.outcome == 'success'" in recovery_if
    assert "steps.probe.outputs.reconcile == 'true'" in recovery_if
    snapshot = steps["Retain operational safety snapshot"]
    assert snapshot["if"] == "${{ success() && steps.probe.outputs.snapshot == 'true' }}"
    assert snapshot["with"] == {"name": "safety-snapshot-${{ github.run_id }}-${{ github.run_attempt }}",
                                "path": "${{ runner.temp }}/operational-safety/cache.json",
                                "if-no-files-found": "error", "retention-days": 1}
    assert not any("attest" in step.get("uses", "") for step in job["steps"])


@pytest.mark.parametrize("missed", [[], [{"id": 99, "run_attempt": 1}, {"id": 100, "run_attempt": 2}]])
@pytest.mark.parametrize("step_name,expected", [
    ("Request catalog refresh", ["workflow", "run", "catalog-web-deploy.yml", "--ref", "main",
                                 "-f", "safety_run_id=123", "-f", "safety_run_attempt=2"]),
    ("Request operational reconciliation", ["workflow", "run", "deployment-verification.yml", "--ref", "main",
                                            "-f", "safety_run_id=123", "-f", "safety_run_attempt=2"]),
])
def test_dispatch_executes_only_literal_main_workflow_routes(tmp_path, step_name, expected, missed):
    workflow = load_workflow(WORKFLOWS / SAFETY)
    step = workflow_steps_by_name(workflow, "safety")[step_name]
    capture = tmp_path / "arguments.json"
    gh = tmp_path / "gh"
    gh.write_text("#!/usr/bin/env python3\nimport json,os,sys\nwith open(os.environ['CAPTURE'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n")
    gh.chmod(0o755)
    subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]], check=True,
                   env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "CAPTURE": str(capture),
                            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "MISSED_HANDOFFS": json.dumps(missed)})
    expected_calls = [expected]
    if step_name == "Request operational reconciliation":
        expected_calls.extend(["workflow", "run", "deployment-verification.yml", "--ref", "main",
                               "-f", f"safety_run_id={parent['id']}", "-f", f"safety_run_attempt={parent['run_attempt']}"]
                              for parent in missed)
    assert [json.loads(line) for line in capture.read_text().splitlines()] == expected_calls


def test_dispatched_catalog_rechecks_source_inputs_and_exact_ci_before_publication():
    workflow = load_workflow(WORKFLOWS / "catalog-web-deploy.yml")
    trigger = workflow_triggers(workflow)
    assert set(trigger["workflow_dispatch"]["inputs"]) == {"safety_run_id", "safety_run_attempt"}
    assert all(value["required"] and value["type"] == "string" for value in trigger["workflow_dispatch"]["inputs"].values())
    assert workflow["permissions"]["actions"] == "read"
    check = workflow["jobs"]["check-inputs"]
    assert check["if"] == "${{ github.event_name == 'workflow_dispatch' }}"
    steps = workflow_steps_by_name(workflow, "check-inputs")
    assert "--validate-catalog-dispatch" in steps["Verify operational safety request"]["run"]
    names = [step.get("name") for step in check["steps"]]
    assert names.index("Verify operational safety request") < names.index("Compare catalog inputs")
    assert "catalog_refresh_check.py" in steps["Compare catalog inputs"]["run"]
    assert "needs.check-inputs.outputs.changed == 'true'" in workflow["jobs"]["deploy"]["if"]
    assert workflow["jobs"]["deploy"]["concurrency"] == {"group": "catalog-web-deploy", "queue": "max", "cancel-in-progress": False}
    deploy = workflow_steps_by_name(workflow, "deploy")
    assert "deployment_source.py" in deploy["Verify exact passing CI revision"]["run"]
    assert "Verify live catalog after publication" in deploy


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_catalog_probe_only_does_not_masquerade_as_a_publisher_failure(event):
    run = {"name": WORKFLOW_NAMES["catalog-web-deploy.yml"][0], "event": event}
    jobs = [{"name": "Check for catalog input changes", "status": "completed", "conclusion": "failure"},
            {"name": "Build and publish catalog web", "status": "completed", "conclusion": "skipped"}]
    assert alerts.catalog_probe_only(run, jobs)
    jobs[1]["conclusion"] = "failure"
    assert not alerts.catalog_probe_only(run, jobs)


@pytest.mark.parametrize("filename", [SAFETY, "catalog-web-deploy.yml"])
def test_automatic_dispatch_failures_remain_unattended(filename):
    event = {"repository": {"full_name": alerts.REPOSITORY}, "workflow_run": {
        "id": 123, "name": WORKFLOW_NAMES[filename][0], "event": "workflow_dispatch", "status": "completed",
        "conclusion": "failure", "head_branch": "main", "head_repository": {"full_name": alerts.REPOSITORY}}}
    assert alerts.alert_for_run(event) is not None


def test_safety_job_failure_and_recovery_keep_the_same_nonproduction_scope():
    name = "Check for operational work"
    workflow = WORKFLOW_NAMES[SAFETY][0]
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    run = {"id": 123, "run_attempt": 1, "head_sha": sha, "name": workflow}
    job = {"id": 456, "name": name, "conclusion": "failure", "completed_at": "2026-11-02T01:00:00Z"}
    failure = incidents.from_job(run, job, incidents.job_targets())
    assert failure["kind"] == "job" and failure["scope"].startswith("job:")
    identity = {"team": "T123", "user": "U123", "channel": "C123"}
    state = incidents.fold(None, failure, identity)
    recovered = incidents.from_job({**run, "id": 124},
        {**job, "id": 457, "conclusion": "success", "completed_at": "2026-11-02T02:00:00Z"}, incidents.job_targets())
    state = incidents.fold(state, recovered, identity)
    assert state["incident"]["resolution"] == recovered
