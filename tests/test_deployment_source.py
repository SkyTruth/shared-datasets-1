"""Replay catalog CI selection through the actual CLI and GitHub JSON reader."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "SkyTruth/shared-datasets-1"
SHA = "a" * 40


def source_cli(tmp_path, *, status="in_progress", gates=None, source_sha=SHA,
               caller=".github/workflows/catalog-web-deploy.yml", older=False):
    repository = {"id": 9, "full_name": REPOSITORY}
    source = {"id": 123, "run_attempt": 2, "workflow_id": 7, "path": ".github/workflows/ci.yml",
              "event": "push", "head_branch": "main", "head_sha": source_sha,
              "repository": repository, "head_repository": repository, "status": status}
    current = {**source, "id": 456, "run_attempt": 1, "workflow_id": 8, "path": caller,
               "event": "schedule", "head_sha": SHA}
    runs = [{**source, "head_sha": SHA}]
    if older:
        runs.append({**source, "id": 122, "status": "completed"})
    responses = {
        f"repos/{REPOSITORY}": repository,
        f"repos/{REPOSITORY}/actions/workflows/ci.yml": {"id": 7, "path": ".github/workflows/ci.yml"},
        f"repos/{REPOSITORY}/actions/workflows/catalog-web-deploy.yml": {"id": 8, "path": ".github/workflows/catalog-web-deploy.yml"},
        f"repos/{REPOSITORY}/actions/runs/123/attempts/2": source,
        f"repos/{REPOSITORY}/actions/runs/456": current,
        f"repos/{REPOSITORY}/actions/workflows/ci.yml/runs?head_sha={SHA}&event=push&branch=main&per_page=100":
            [{"total_count": len(runs), "workflow_runs": runs}],
        f"repos/{REPOSITORY}/actions/runs/123/attempts/2/jobs?per_page=100":
            [{"total_count": len(gates or []), "jobs": gates or []}],
    }
    data = tmp_path / "responses.json"
    data.write_text(json.dumps(responses))
    commands = tmp_path / "commands.jsonl"
    bin_path = tmp_path / "bin"
    bin_path.mkdir()
    gh = bin_path / "gh"
    gh.write_text(f"#!{sys.executable}\n" + """import json, os, sys
with open(os.environ['SOURCE_COMMANDS'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
assert sys.argv[1:4] == ['api', '--method', 'GET']
path = next(argument for argument in sys.argv if argument.startswith('repos/'))
print(json.dumps(json.load(open(os.environ['SOURCE_RESPONSES']))[path]))
""")
    gh.chmod(0o755)
    git = bin_path / "git"
    git.write_text(f"#!/bin/sh\ncase \"$1\" in\nrev-parse) echo {SHA};;\ncat-file|merge-base) exit 0;;\n*) exit 99;;\nesac\n")
    git.chmod(0o755)
    event = tmp_path / "event.json"
    event.write_text("{}")
    output = tmp_path / "output"
    env = {**os.environ, "PATH": str(bin_path) + os.pathsep + os.environ["PATH"],
           "SOURCE_RESPONSES": str(data), "SOURCE_COMMANDS": str(commands),
           "GITHUB_EVENT_PATH": str(event), "GITHUB_OUTPUT": str(output),
           "GITHUB_REPOSITORY": REPOSITORY, "GITHUB_RUN_ID": "456", "GITHUB_RUN_ATTEMPT": "1",
           "GITHUB_REF": "refs/heads/main", "GITHUB_EVENT_NAME": "schedule", "BOOTSTRAP_WORKFLOW_SHA": SHA,
           "GITHUB_WORKFLOW_REF": REPOSITORY + "/.github/workflows/catalog-web-deploy.yml@refs/heads/main"}
    result = subprocess.run([sys.executable, str(ROOT / "scripts/deployment_source.py"), "--bootstrap",
                             "--workflow", "catalog-web-deploy.yml", "--executor-sha", SHA, "--defer-pending"],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    return result, output.read_text() if output.exists() else "", commands.read_text()


def test_passing_exact_ci_ready_during_running_ci_is_authoritative(tmp_path):
    result, output, _ = source_cli(tmp_path, gates=[{"name": "ci-ready", "status": "completed", "conclusion": "success"}])
    assert result.returncode == 0, result.stderr
    assert output == "ready=true\nsource_run_id=123\nsource_run_attempt=2\n"


@pytest.mark.parametrize("gates", [[], [{"name": "ci-ready", "status": "queued", "conclusion": None}],
                                   [{"name": "ci-ready", "status": "in_progress", "conclusion": None}]])
def test_exact_pending_gate_defers_without_substituting_older_evidence(tmp_path, gates):
    result, output, commands = source_cli(tmp_path, gates=gates, older=True)
    assert result.returncode == 0, result.stderr
    assert output == "ready=false\n"
    assert "deferred" in result.stdout
    assert "/runs/122/" not in commands


@pytest.mark.parametrize("status,gates", [
    ("completed", []),
    *[("in_progress", [{"name": "ci-ready", "status": "completed", "conclusion": conclusion}])
      for conclusion in ("failure", "cancelled", "skipped", None)],
    ("in_progress", [{"name": "ci-ready", "status": "queued", "conclusion": None}] * 2),
])
def test_missing_terminal_unsuccessful_or_ambiguous_gates_remain_failures(tmp_path, status, gates):
    result, output, commands = source_cli(tmp_path, status=status, gates=gates, older=True)
    assert result.returncode != 0
    assert output == ""
    assert "/runs/122/" not in commands


@pytest.mark.parametrize("updates", [{"source_sha": "b" * 40}, {"caller": ".github/workflows/evil.yml"}])
def test_wrong_revision_or_caller_cannot_claim_a_quiet_deferral(tmp_path, updates):
    result, output, _ = source_cli(tmp_path, **updates)
    assert result.returncode != 0
    assert output == ""
