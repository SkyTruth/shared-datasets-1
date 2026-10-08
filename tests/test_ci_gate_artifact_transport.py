"""Gate transport excludes retained payloads without weakening result verification."""
from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import sys
import zipfile

import pytest

from scripts import ci_preflight as preflight
from scripts import ci_source_proof as proof
from scripts.ci_contract import SUITES, expected_tools
from ci_result_fixtures import production_images
from test_ci_preflight import fixture_repo
from workflow_helpers import load_workflow


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"
REPOSITORY = "SkyTruth/shared-datasets-1"


def expand(value, runner, suite, attempt):
    return (value.replace("${{ runner.temp }}", str(runner))
            .replace("${{ matrix.node }}", suite.removeprefix("sdk-node"))
            .replace("${{ github.run_attempt }}", str(attempt)))


def producer_steps(workflow, suite):
    job = "sdk-validation" if suite.startswith("sdk-node") else suite
    return workflow["jobs"][job]["steps"]


def package_upload(step, runner, suite, attempt, directory):
    """Package actual configured files, using upload-artifact's relative paths."""
    source = Path(expand(step["with"]["path"], runner, suite, attempt))
    assert source.exists(), f"missing configured upload source: {source}"
    paths = sorted(source.rglob("*")) if source.is_dir() else [source]
    base = source if source.is_dir() else source.parent
    name = expand(step["with"]["name"], runner, suite, attempt)
    archive = directory / (name + ".zip")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        for path in paths:
            if path.is_file():
                bundle.write(path, path.relative_to(base).as_posix())
    return name, archive


@pytest.fixture
def transport(tmp_path):
    workflow = load_workflow(WORKFLOW)
    source, git = fixture_repo(tmp_path)
    base = git("rev-parse", "HEAD")
    (source / ".github/workflows/ci.yml").write_text(WORKFLOW.read_text())
    git("add", ".github/workflows/ci.yml")
    git("commit", "-m", "fixture workflow contract")
    plan = preflight.make_plan(source, base, "HEAD")
    assert plan["suites"] == list(SUITES)
    plan["source"] = {"run_id": "123", "run_attempt": 1}
    runner, archives, downloaded = [tmp_path / name for name in ("runner", "archives", "downloaded")]
    archives.mkdir()
    downloaded.mkdir()
    uploaded = {}
    for suite in SUITES:
        output = runner / "ci-results" / suite
        output.mkdir(parents=True)
        result = {
            **{key: plan[key] for key in ("base", "head", "tested_sha", "tree", "contract_digest")},
            "suite": suite, "source": {"run_id": "123", "run_attempt": 1},
            "status": "success", "tools": expected_tools(suite), "commands": [{"exit_code": 0}],
        }
        if suite == "production-images":
            result["production_images"] = production_images(plan["tested_sha"])
            images = output / "images"
            images.mkdir()
            (images / "manifest.json").write_text(json.dumps(result["production_images"]))
            for image in result["production_images"]["images"].values():
                (images / image["archive"]).write_bytes(b"fixture-image-bytes" * 1024)
        if suite.startswith("sdk-node"):
            package = output / "package"
            package.mkdir()
            (package / "candidate.json").write_text('{"fixture":true}')
            (package / "skytruth-shared-datasets.tgz").write_bytes(b"fixture-package-bytes" * 1024)
        (output / "runner.log").write_text("retained diagnostics\n")
        (output / "result.json").write_text(json.dumps(result))
        for step in producer_steps(workflow, suite):
            if step.get("uses", "").startswith("actions/upload-artifact@"):
                name, archive = package_upload(step, runner, suite, 1, archives)
                assert name not in uploaded
                uploaded[name] = archive
    gate = workflow["jobs"]["ci-ready"]
    download = next(step for step in gate["steps"] if step.get("uses", "").startswith("actions/download-artifact@")
                    and step["with"]["path"].endswith("/ci-results"))
    selected = {name: archive for name, archive in uploaded.items()
                if fnmatch.fnmatchcase(name, download["with"]["pattern"])}
    for name, archive in selected.items():
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(downloaded / name)
    return {"plan": plan, "workflow": workflow, "runner": runner, "uploaded": uploaded,
            "selected": selected, "downloaded": downloaded, "source": source}


def invoke_verifier(transport, tmp_path, monkeypatch, *, defect=None):
    plan = transport["plan"]
    selected = transport["downloaded"]
    report = next(path for path in selected.rglob("result.json") if json.loads(path.read_text())["suite"] == "tests")
    result = json.loads(report.read_text())
    if defect == "missing-result":
        report.unlink()
    elif defect in {"failure", "cancelled", "skipped"}:
        result["status"] = defect
    elif defect == "stale-sha":
        result["tested_sha"] = "f" * 40
    elif defect == "other-run":
        result["source"]["run_id"] = "456"
    if defect != "missing-result":
        report.write_text(json.dumps(result))
    plan_path, jobs_path, evidence = [tmp_path / name for name in ("plan.json", "jobs.json", "evidence.json")]
    plan_path.write_text(json.dumps(plan))
    jobs = {job: {"result": "success"} for job in (
        "geospatial-changes", "lint", "tests", "geospatial-integration", "production-images", "sdk-validation", "browser",
    )}
    jobs_path.write_text(json.dumps(jobs))
    requests = []
    def api(path):
        requests.append(path)
        assert "/runs/123/attempts/1" in path  # Retry gate must prove the original producers.
        if "/jobs?" not in path:
            return {"id": 123, "run_attempt": 1, "path": ".github/workflows/ci.yml",
                    "event": "push", "head_sha": "f" * 40 if defect == "wrong-source-sha" else plan["tested_sha"],
                    "repository": {"full_name": REPOSITORY}}
        source_jobs = [{"id": 1, "name": "geospatial-changes", "status": "completed", "conclusion": "success"}]
        source_jobs.extend({"id": index + 2, "name": proof.job_name(suite), "status": "completed",
                            "conclusion": defect.removeprefix("producer-") if suite == "tests" and defect in {
                                "producer-failure", "producer-cancelled", "producer-skipped",
                            } else "success"} for index, suite in enumerate(SUITES))
        return {"total_count": len(source_jobs), "jobs": source_jobs}
    monkeypatch.setattr(proof, "api", api)
    monkeypatch.chdir(transport["source"])
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    monkeypatch.setenv("GITHUB_REPOSITORY", REPOSITORY)
    monkeypatch.setattr(sys, "argv", ["ci_preflight.py", "verify", "--plan", str(plan_path),
                                      "--results", str(selected), "--jobs", str(jobs_path), "--output", str(evidence)])
    outcome = preflight.main()
    return outcome, evidence, requests


def test_gate_packages_only_results_and_verifier_retains_original_bundle_identity(transport, tmp_path, monkeypatch):
    assert len(transport["selected"]) == len(SUITES)
    gate_members = {}
    for archive in transport["selected"].values():
        with zipfile.ZipFile(archive) as bundle:
            suite = json.loads(bundle.read("result.json"))["suite"]
            gate_members[suite] = bundle.namelist()
    assert gate_members["production-images"] == ["result.json"], "ci-ready downloaded unused retained image bytes"
    assert set(gate_members) == set(SUITES)
    assert all(members == ["result.json"] for members in gate_members.values())
    retained = transport["uploaded"]["ci-result-production-images-attempt1"]
    with zipfile.ZipFile(retained) as bundle:
        assert "images/manifest.json" in bundle.namelist()
        assert len([name for name in bundle.namelist() if name.endswith(".docker.tar")]) == 4
        assert "runner.log" in bundle.namelist()
    for node in (22, 24):
        with zipfile.ZipFile(transport["uploaded"][f"ci-result-sdk-node{node}-attempt1"]) as bundle:
            assert "package/candidate.json" in bundle.namelist()
            assert bundle.read("package/skytruth-shared-datasets.tgz") == b"fixture-package-bytes" * 1024
    assert sum(path.stat().st_size for path in transport["selected"].values()) < retained.stat().st_size
    outcome, evidence, requests = invoke_verifier(transport, tmp_path, monkeypatch)
    assert outcome == 0
    result = json.loads(evidence.read_text())
    assert result["source"] == {"run_id": "123", "run_attempt": 2}
    assert result["plan_artifact"] == "ci-validation-plan-attempt1"
    assert result["suite_artifacts"] == {suite: f"ci-result-{suite}-attempt1" for suite in SUITES}
    assert result["tested_sha"] == transport["plan"]["tested_sha"]
    assert len(requests) == 2 and requests[-1].endswith("/jobs?per_page=100&page=1")


@pytest.mark.parametrize("suite", SUITES)
def test_gate_receipt_follows_successful_retained_upload_and_has_no_bulk_path(suite):
    steps = producer_steps(load_workflow(WORKFLOW), suite)
    retained = next(step for step in steps if step.get("with", {}).get("name", "").startswith("ci-result-"))
    receipt = next(step for step in steps if step.get("with", {}).get("name", "").startswith("ci-gate-result-"))
    assert retained["id"] == "retained-results"
    assert steps.index(retained) < steps.index(receipt)
    assert receipt["if"] == "${{ always() && steps.retained-results.outcome == 'success' }}"
    assert receipt["with"]["path"] == retained["with"]["path"] + "/result.json"
    assert receipt["with"]["if-no-files-found"] == "error"
    assert receipt["with"]["retention-days"] == retained["with"]["retention-days"] == 14


def test_missing_result_cannot_form_a_compact_gate_archive(tmp_path):
    receipt = next(step for step in producer_steps(load_workflow(WORKFLOW), "production-images")
                   if step.get("with", {}).get("name", "").startswith("ci-gate-result-"))
    output = tmp_path / "ci-results/production-images"
    output.mkdir(parents=True)
    (output / "runner.log").write_text("runner stopped before producing a result\n")
    with pytest.raises(AssertionError, match="missing configured upload source"):
        package_upload(receipt, tmp_path, "production-images", 1, tmp_path)


@pytest.mark.parametrize("defect", [
    "missing-result", "failure", "cancelled", "skipped", "stale-sha", "other-run", "wrong-source-sha",
    "producer-failure", "producer-cancelled", "producer-skipped",
])
def test_compact_result_loader_still_rejects_invalid_or_unproven_producers(transport, tmp_path, monkeypatch, defect):
    outcome, evidence, _ = invoke_verifier(transport, tmp_path, monkeypatch, defect=defect)
    assert outcome == 1
    assert not evidence.exists()
