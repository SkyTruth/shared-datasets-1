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


def test_three_hour_entrypoint_and_direct_safety_failure_coverage():
    files = (SAFETY, "catalog-web-deploy.yml", "deployment-verification.yml")
    schedules = {name: workflow_triggers(load_workflow(WORKFLOWS / name)).get("schedule") for name in files}
    assert schedules == {SAFETY: [{"cron": "17 */3 * * *"}], "catalog-web-deploy.yml": None, "deployment-verification.yml": None}
    observer = load_workflow(WORKFLOWS / "unattended-workflow-alert.yml")
    subscriptions = workflow_triggers(observer)["workflow_run"]["workflows"]
    assert not (set(WORKFLOW_NAMES[SAFETY]) & set(subscriptions))
    assert "Report safety failure directly" in workflow_steps_by_name(load_workflow(WORKFLOWS / SAFETY), "safety")
    for filename in files[1:]:
        assert set(WORKFLOW_NAMES[filename]) <= set(subscriptions)
    assert SAFETY in alerts.MONITORED_WORKFLOWS


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
    assert "Install pinned durable receipt verifier" in steps
    assert steps["Retain operational safety snapshot"]["with"]["retention-days"] == 1
    assert steps["Retain safety health report"]["with"]["retention-days"] == 3
    assert not any("attest" in step.get("uses", "") for step in job["steps"])


@pytest.mark.parametrize("step_name,expected", [
    ("Request catalog refresh", ["workflow", "run", "catalog-web-deploy.yml", "--ref", "main",
                                 "-f", "safety_run_id=123", "-f", "safety_run_attempt=2"]),
    ("Request execution verification", ["workflow", "run", "deployment-verification.yml", "--ref", "main"]),
    ("Request operational reconciliation", ["workflow", "run", "unattended-workflow-alert.yml", "--ref", "main",
         "-f", "incident_id=operational-batch", "-f", "delivery_result=batch", "-f", "operation=safety:123/2"]),
])
def test_dispatch_executes_only_literal_main_workflow_routes(tmp_path, step_name, expected):
    workflow = load_workflow(WORKFLOWS / SAFETY)
    step = workflow_steps_by_name(workflow, "safety")[step_name]
    capture = tmp_path / "arguments.json"
    gh = tmp_path / "gh"
    gh.write_text("#!/usr/bin/env python3\nimport json,os,sys\nwith open(os.environ['CAPTURE'], 'a') as f: f.write(json.dumps(sys.argv[1:]) + '\\n')\n")
    gh.chmod(0o755)
    subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]], check=True,
                   env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"], "CAPTURE": str(capture),
                            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"})
    assert [json.loads(line) for line in capture.read_text().splitlines()] == [expected]


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


@pytest.mark.parametrize("filename", [SAFETY, "catalog-web-deploy.yml", "catalog-viewer-deploy.yml", "unattended-workflow-alert.yml"])
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


def test_workflow_batches_successes_before_allocating_runner_and_preserves_failures():
    workflow = load_workflow(WORKFLOWS / "unattended-workflow-alert.yml")
    cases = []
    for name in alerts.WORKFLOW_PATHS:
        for conclusion in alerts.FAILED_CONCLUSIONS | {"success", "skipped"}:
            run = {"name": name, "status": "completed", "conclusion": conclusion,
                   "head_branch": "main", "head_repository": {"full_name": alerts.REPOSITORY},
                   "event": "push" if name in WORKFLOW_NAMES["ci.yml"] else "schedule"}
            github = {"event_name": "workflow_run", "ref": "refs/heads/main",
                      "repository": alerts.REPOSITORY, "event": {"workflow_run": run}}
            expected = (conclusion in alerts.FAILED_CONCLUSIONS or
                        conclusion == "success" and name in WORKFLOW_NAMES["deployment-verification.yml"])
            cases.append({"github": github, "expected": expected})
    expression = workflow["jobs"]["incidents"]["if"]
    script = r"""
const {expression, cases} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const evaluate = new Function('github', 'vars', 'contains', 'fromJSON', `return (${expression});`);
for (const {github, expected} of cases) {
  const actual = evaluate(github, {SHARED_DATASETS_SLACK_INCIDENTS_ENABLED: 'true'},
    (values, value) => values.includes(value), JSON.parse);
  if (actual !== expected) throw new Error(JSON.stringify({github, expected, actual}));
}
"""
    subprocess.run(["node", "-e", script], input=json.dumps({"expression": expression, "cases": cases}),
                   text=True, check=True, capture_output=True)


@pytest.mark.parametrize('enabled', ['true', 'false'])
def test_health_step_executes_with_the_workflows_effective_incident_mode(tmp_path, enabled):
    workflow = load_workflow(WORKFLOWS / SAFETY)
    job = workflow['jobs']['safety']
    step = workflow_steps_by_name(workflow, 'safety')['Check freshness and stalled operations']
    runner = tmp_path / 'runner.py'
    runner.write_text('''import sys
from unittest.mock import Mock
from scripts import operational_safety_check as safety, catalog_drift_guard as catalog, slack_incidents as incidents
safety.context = lambda *_: {'id': 300}
incidents.GitHub = Mock
catalog.get_client = lambda: None
safety.freshness = lambda *a, **k: []
safety.policy_warnings = lambda *_: ([], [])
safety.previous_health = lambda *_: {'problems': [], 'warnings': [], 'notified_at': None}
def legacy(*a, **k):
    print('LEGACY_POLLED')
    return [], [], None, None
safety.source_inventory = legacy
sys.argv = sys.argv[4:]
safety.main()
''')
    uv = tmp_path / 'uv'
    uv.write_text(f'#!/bin/sh\nexec {os.sys.executable} {runner} "$@"\n')
    uv.chmod(0o755)
    directory = tmp_path / 'operational-safety'
    directory.mkdir()
    (directory / 'result.json').write_text('{"reconcile":false}')
    (directory / 'claims.json').write_text('[]')
    environment = {**workflow.get('env', {}), **job.get('env', {}), **step.get('env', {})}
    environment = {key: enabled if value == '${{ vars.SHARED_DATASETS_SLACK_INCIDENTS_ENABLED }}' else value
                   for key, value in environment.items()}
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', step['run']], cwd=ROOT, check=True, text=True,
        capture_output=True, env={**os.environ, **environment, 'PYTHONPATH': str(ROOT), 'RUNNER_TEMP': str(tmp_path),
                                  'PATH': str(tmp_path) + os.pathsep + os.environ['PATH']})
    assert ('LEGACY_POLLED' in result.stdout) is (enabled == 'false')


def test_infrastructure_fallback_executes_without_checkout_even_when_dispatch_fails(tmp_path):
    step = workflow_steps_by_name(load_workflow(WORKFLOWS / SAFETY), 'safety')['Report safety failure directly']
    for command, body in [('gh', 'exit 1'), ('curl', 'printf "%s\\n" "$@" > "$CAPTURE"')]:
        path = tmp_path / command
        path.write_text('#!/bin/sh\n' + body + '\n')
        path.chmod(0o755)
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', step['run']], cwd=tmp_path,
        env={**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'], 'GITHUB_REPOSITORY': alerts.REPOSITORY,
             'GITHUB_RUN_ID': '123', 'SHARED_DATASETS_SLACK_WEBHOOK_URL': 'https://example.invalid',
             'CAPTURE': str(tmp_path / 'capture')})
    assert result.returncode == 1
    assert 'Operational safety infrastructure failed' in (tmp_path / 'capture').read_text()
