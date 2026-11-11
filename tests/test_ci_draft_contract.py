"""Draft results cannot satisfy the strict required-check set after marking ready."""
import json
import os
from pathlib import Path
import subprocess

from workflow_helpers import load_workflow, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = {"geospatial-changes", "lint", "tests", "ci-ready"}


def test_draft_failure_is_sticky_for_the_original_event_and_allocates_only_two_jobs(tmp_path):
    workflow = load_workflow(ROOT / ".github/workflows/ci.yml")
    assert "ready_for_review" in workflow_triggers(workflow)["pull_request"]["types"]
    jobs = workflow["jobs"]
    assert all(jobs[name].get("name", name) == name for name in REQUIRED)
    # Planner fails before any checkout, setup, artifact or selection output.
    # The always-running required gate independently fails before setup too.
    allocated = {"geospatial-changes", "ci-ready"}
    outcomes = dict.fromkeys(jobs, "skipped")
    for name in allocated:
        job = jobs[name]
        assert "if" not in job or job["if"] == "always()"
        step = job["steps"][0]
        assert step["name"] == "Require a ready PR"
        assert step["if"] == "${{ github.event_name == 'pull_request' && github.event.pull_request.draft }}"
        assert "continue-on-error" not in job and "continue-on-error" not in step
        # Re-running an old draft run retains its original payload even after
        # the live PR is ready. It must fail again rather than turn green.
        for live_draft in (True, False):
            event = {"event_name": "pull_request", "event": {
                "pull_request": {"draft": True}, "live_pull_request": {"draft": live_draft},
            }}
            expression = step["if"].removeprefix("${{").removesuffix("}}").strip()
            evaluated = subprocess.run(["node", "-e", "console.log(Function('github', 'return ' + process.argv[1])(JSON.parse(process.argv[2])))",
                                        expression, json.dumps(event)], check=True, capture_output=True, text=True)
            assert evaluated.stdout.strip() == "true", live_draft
            result = subprocess.run(["bash", "-euo", "pipefail", "-c", step["run"]], cwd=tmp_path,
                                    env={**os.environ}, capture_output=True, text=True)
            assert result.returncode == 1
        outcomes[name] = "failure"
    assert jobs["ci-ready"]["if"] == "always()"
    assert not all(outcomes[name] in {"success", "skipped"} for name in REQUIRED)
    for name, job in jobs.items():
        if name in allocated:
            continue
        needs = job["needs"] if isinstance(job["needs"], list) else [job["needs"]]
        if "geospatial-changes" in needs:
            # No always()/!failure() override may allocate another runner on a
            # failed planner. Normal needs success semantics suppress these.
            assert not any(token in job.get("if", "") for token in ("always()", "failure()", "cancelled()"))
        else:
            assert "ci-ready" in needs  # Delivery remains blocked by the failed gate.
    assert len(allocated) == 2  # Two shell-only jobs, each below one billed minute.


def test_draft_failures_do_not_allocate_workflow_health_reconciliation():
    from scripts import workflow_failure_alert as alerts
    workflow = load_workflow(ROOT / ".github/workflows/unattended-workflow-alert.yml")
    assert workflow_triggers(workflow)["workflow_run"]["branches"] == ["main"]
    event = {"repository": {"full_name": alerts.REPOSITORY}, "workflow_run": {
        "id": 1, "name": "CI / Validate and deliver", "event": "pull_request", "status": "completed",
        "conclusion": "failure", "head_branch": "feature", "head_repository": {"full_name": alerts.REPOSITORY}}}
    assert alerts.alert_for_run(event) is None


def _actions(expression, github, **functions):
    """Evaluate an Actions expression with the functions it uses."""
    source = {"fromJSON": "JSON.parse",
              "contains": "(a, v) => Array.isArray(a) ? a.includes(v) : String(a).includes(v)",
              "startsWith": "(v, p) => String(v).startsWith(p)",
              "format": "(f, ...a) => f.replace(/\\{(\\d+)\\}/g, (_, i) => a[i])"}
    names = ["github", "vars", *source]
    script = ("const [expression, github, vars] = process.argv.slice(1);"
              f"const fns = [{', '.join(source.values())}];"
              f"process.stdout.write(JSON.stringify(Function({', '.join(map(repr, names))}, 'return (' + expression + ')')"
              "(JSON.parse(github), JSON.parse(vars), ...fns)))")
    expression = expression.strip().removeprefix("${{").removesuffix("}}").strip()
    result = subprocess.run(["node", "-e", script, expression, json.dumps(github), json.dumps(functions.get("vars", {}))],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def test_only_draft_runs_leave_the_run_name_that_alerting_and_incidents_key_on():
    from scripts.workflow_identity import WORKFLOW_NAMES
    ci = load_workflow(ROOT / ".github/workflows/ci.yml")
    alert = load_workflow(ROOT / ".github/workflows/unattended-workflow-alert.yml")
    refresh = load_workflow(ROOT / ".github/workflows/owner-acceptance-refresh.yml")
    events = {
        "push": {"event_name": "push", "event": {"head_commit": {"message": "Merge pull request #1"}}},
        "dispatch": {"event_name": "workflow_dispatch", "event": {}},
        "ready": {"event_name": "pull_request", "event": {"pull_request": {"draft": False, "title": "ready PR"}}},
        "draft": {"event_name": "pull_request", "event": {"pull_request": {"draft": True, "title": "draft PR"}}},
    }
    names = {key: _actions(ci["run-name"], github) for key, github in events.items()}
    # GitHub reports run-name as workflow_run.name and run.name; consumers match it exactly.
    assert names["push"] == names["dispatch"] == names["ready"] == ci["name"] == WORKFLOW_NAMES["ci.yml"][0]
    assert names["draft"] == "Draft CI / draft PR"
    run = {"status": "completed", "conclusion": "failure", "event": "push", "name": names["push"],
           "head_branch": "main", "head_repository": {"full_name": "SkyTruth/shared-datasets-1"}}
    github = {"ref": "refs/heads/main", "repository": "SkyTruth/shared-datasets-1",
              "event_name": "workflow_run", "event": {"workflow_run": run}}
    vars_ = {"SHARED_DATASETS_SLACK_INCIDENTS_ENABLED": "true"}
    assert _actions(alert["jobs"]["incidents"]["if"], github, vars=vars_) is True
    # The pre-fix run name (commit message) dropped every main CI failure.
    stale = {**github, "event": {"workflow_run": {**run, "name": "CI / Merge pull request #1"}}}
    assert _actions(alert["jobs"]["incidents"]["if"], stale, vars=vars_) is False
    for key, skipped in (("ready", False), ("draft", True)):
        pr_run = {"event": "pull_request", "conclusion": "failure", "display_title": names[key]}
        refresh_event = {"event_name": "workflow_run", "event": {"workflow_run": pr_run}}
        assert _actions(refresh["jobs"]["refresh"]["if"], refresh_event) is (not skipped), key
