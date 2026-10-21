"""Supervision requests are data, exact-run confirmation is not a green workflow guess."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import slack_supervision as cli


ROOT = Path(__file__).resolve().parents[1]
CHAT = "codex://threads/01a15cc7-3222-7610-acd6-19ec6f6a848a"


def request(command="claim"):
    return {"command": command, "binding": {"operation_id": "deployment:catalog-web", "target": "catalog-web", "revision": "a" * 40, "pr_number": 300},
            "responsible_human": "jonaraphael", "conversation_ref": CHAT,
            "current_action": "Inspecting exact catalog prerequisite", "question_ref": None, "handoff_ref": None}


def test_actual_cli_serializes_a_prospective_request_without_authority_or_failure_clocks():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/slack_supervision.py"), "claim", "--target", "catalog-web",
                             "--revision", "a" * 40, "--pr", "300", "--human", "jonaraphael", "--conversation", CHAT,
                             "--current-action", "Inspecting exact catalog prerequisite", "--dry-run"], text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == request()


def test_actual_dispatch_sends_json_stdin_and_cannot_execute_question_text(tmp_path, monkeypatch):
    gh = tmp_path / "gh"
    output = tmp_path / "inputs.json"
    gh.write_text("#!" + sys.executable + "\n" + '''
import json, os, pathlib, sys
assert sys.argv[1:] == ["workflow", "run", "unattended-workflow-alert.yml", "--repo", "SkyTruth/shared-datasets-1", "--ref", "main", "--json"]
pathlib.Path(os.environ["CAPTURE"]).write_text(sys.stdin.read())
''')
    gh.chmod(0o700)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("CAPTURE", str(output))
    data = request()
    data["current_action"] = "Clarifying $(touch NEVER_EXECUTE) `echo private`"
    result = cli.dispatch(data, wait=False)
    inputs = json.loads(output.read_text())
    assert inputs["action"] == "supervision"
    assert inputs["supervision_request_id"] == result["request_id"]
    assert json.loads(inputs["supervision_request"]) == data
    assert result["verified"] is False
    assert not (ROOT / "NEVER_EXECUTE").exists()


class API:
    def __init__(self, *, change=None, worker="success", verified=True):
        self.run = {"id": 50, "run_attempt": 2, "display_title": "Supervision request-123", "path": cli.WORKFLOW,
                    "head_repository": {"full_name": cli.REPOSITORY}, "head_branch": "main", "event": "workflow_dispatch",
                    "status": "completed", "conclusion": "success", "html_url": "https://github.com/SkyTruth/shared-datasets-1/actions/runs/50"}
        if change:
            self.run.update(change)
        self.jobs = [{"name": "Reconcile Slack incident status", "status": "completed", "conclusion": worker,
                      "steps": [{"name": "Verify durable incident checkpoints", "conclusion": "success" if verified else "skipped"}]}]
        self.paths = []

    def pages(self, path, key):
        self.paths.append(path)
        return [self.run] if key == "workflow_runs" else self.jobs


def wait(api):
    return cli.wait_for_request(api, "request-123", started="2026-10-21T00:00:00Z", clock=lambda: 0, timeout=1)


def test_exact_run_and_signed_checkpoint_verification_are_required():
    api = API()
    assert wait(api).endswith("/50")
    assert api.paths[-1].endswith("/runs/50/attempts/2/jobs?per_page=100")


@pytest.mark.parametrize("worker,verified", [("skipped", True), ("failure", True), ("success", False)])
def test_skipped_or_unsigned_worker_does_not_establish_supervision(worker, verified):
    with pytest.raises(ValueError, match="signed checkpoint"):
        wait(API(worker=worker, verified=verified))


@pytest.mark.parametrize("change", [{"head_branch": "feature"}, {"path": ".github/workflows/ci.yml"},
                                   {"event": "push"}, {"head_repository": {"full_name": "someone/fork"}}])
def test_other_run_provenance_cannot_confirm_ownership(change):
    with pytest.raises(ValueError, match="provenance"):
        wait(API(change=change))


def test_other_request_success_cannot_confirm_this_request():
    ticks = iter([0, 2])
    with pytest.raises(TimeoutError, match="still pending"):
        cli.wait_for_request(API(change={"display_title": "Supervision another-request"}), "request-123",
                             started="2026-10-21T00:00:00Z", timeout=1, clock=lambda: next(ticks))
