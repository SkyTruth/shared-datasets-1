"""Monthly promotion verifies persisted terminal facts after campaign retirement."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from scripts import release_contracts as contracts

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repository(tmp_path):
    for directory in (".github", "catalog", "terraform", "docs/wdpa-processing-evidence", "scripts", "ingestion"):
        shutil.copytree(ROOT / directory, tmp_path / directory, ignore=shutil.ignore_patterns("__pycache__"))
    return tmp_path


def run_release_cli(root):
    return subprocess.run(
        [sys.executable, str(root / "scripts/release_contracts.py"), "--target", "wdpa"],
        cwd=root, capture_output=True, text=True, timeout=30,
    )


def test_monthly_release_cli_accepts_retained_terminal_facts_without_cloud_access(repository):
    result = run_release_cli(repository)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("mutation", [
    "missing-binding", "missing-file", "corrupt-bytes", "missing-execution", "wrong-execution",
    "wrong-image", "unfinished", "failed", "not-completed", "empty-containers", "multiple-containers",
])
def test_serialized_terminal_evidence_cannot_authorize_a_different_or_failed_build(repository, mutation):
    acceptance_path = repository / "catalog/wdpa-processing-acceptance.json"
    acceptance = json.loads(acceptance_path.read_text())
    reference = acceptance["evidence_files"]["cloud-facts.json"]
    facts_path = repository / reference["path"]
    facts = json.loads(facts_path.read_text())
    execution = facts["execution"]
    if mutation == "missing-binding":
        del acceptance["evidence_files"]["cloud-facts.json"]
    elif mutation == "missing-file":
        facts_path.unlink()
    elif mutation == "corrupt-bytes":
        facts_path.write_bytes(facts_path.read_bytes() + b" ")
    else:
        if mutation == "missing-execution":
            del facts["execution"]
        elif mutation == "wrong-execution":
            execution["name"] += "-other"
        elif mutation == "wrong-image":
            execution["template"]["containers"][0]["image"] = "registry/other@sha256:" + "a" * 64
        elif mutation == "unfinished":
            del execution["completionTime"]
        elif mutation == "failed":
            execution["failedCount"] = 1
        elif mutation == "not-completed":
            execution["conditions"] = []
        elif mutation == "empty-containers":
            execution["template"]["containers"] = []
        elif mutation == "multiple-containers":
            execution["template"]["containers"] *= 2
        # Re-pin bytes to prove semantic validation independently of hash checking.
        facts_path.write_text(json.dumps(facts))
        reference["sha256"] = hashlib.sha256(facts_path.read_bytes()).hexdigest()
    acceptance_path.write_text(json.dumps(acceptance))
    result = run_release_cli(repository)
    assert result.returncode != 0
    assert "RELEASE_CONTRACT_NOT_READY" in result.stderr
    assert "retained" in result.stderr


@pytest.mark.parametrize("mutation", ["removed", "disabled", "late", "live-query"])
def test_monthly_workflow_cannot_bypass_persisted_terminal_verification(repository, mutation):
    path = repository / ".github/workflows/wdpa-monthly-deploy.yml"
    value = yaml.safe_load(path.read_text())
    steps = value["jobs"]["deploy"]["steps"]
    terminal = next(step for step in steps if step.get("name") == "Verify accepted build terminal success")
    if mutation == "removed":
        steps.remove(terminal)
    elif mutation == "disabled":
        terminal["if"] = False
    elif mutation == "late":
        steps.remove(terminal)
        steps.append(terminal)
    else:
        terminal["run"] = "gcloud run jobs executions describe retired-execution"
    path.write_text(yaml.safe_dump(value))
    assert any("retained terminal" in error for error in contracts.deployment_contract(repository, "wdpa"))
