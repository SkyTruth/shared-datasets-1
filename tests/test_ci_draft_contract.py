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
    assert workflow["run-name"].startswith("${{ github.event_name == 'pull_request' && github.event.pull_request.draft && 'Draft CI'")
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
            event = {"event_name": "pull_request", "event": {"pull_request": {"draft": True}}}
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
