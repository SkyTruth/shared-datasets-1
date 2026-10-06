"""Fail preflight if a deployment replaces CI image bytes or loses their proof."""

import copy
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from scripts.ci_contract import ALWAYS, expected_tools, verify_results
from ci_result_fixtures import production_images
from workflow_helpers import load_workflow

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ("eamlis-monthly", "sea-ice-daily", "catalog-viewer")


def deployment_steps(target):
    value = load_workflow(ROOT / ".github/workflows" / f"{target}-deploy.yml")
    return next(job["steps"] for job in value["jobs"].values()
                if any("tested_image_authorization.py" in step.get("run", "") for step in job.get("steps", [])))


def image_contract(steps, target):
    runs = [step.get("run", "").replace("\\\n", " ") for step in steps]
    assert not any(re.search(r"\bdocker\s+(?:(?:image|buildx)\s+)?build(?:\s|$)", run) for run in runs), "deployment must not rebuild tested bytes"
    loaders = [index for index, run in enumerate(runs) if "tested_image_authorization.py" in run]
    assert len(loaders) == 1, "one authenticated retained-image loader is required"
    loaded = loaders[0]
    assert steps[loaded].get("id") == "tested-image"
    assert not steps[loaded].get("continue-on-error") and "||" not in runs[loaded], "loader cannot have a fallback"
    for argument in (f"--workflow {target}-deploy.yml", f"--target {target}", '--executor-sha "$EXECUTOR_SHA"',
                     '--source-run-id "$SOURCE_RUN_ID"', '--source-run-attempt "$SOURCE_RUN_ATTEMPT"'):
        assert argument in runs[loaded], "retained image must bind exact workflow, target and tested source"
    assert steps[loaded].get("env", {}).get("GH_TOKEN") == "${{ github.token }}"
    assert any("deployment_revision.py verify --bootstrap" in run for run in runs[:loaded]), "trusted source proof must precede loading"
    assert any("deployment_revision.py check" in run for run in runs[:loaded]), "replay proof must precede loading"
    tagged = [index for index, run in enumerate(runs) if re.search(r"\bdocker\s+tag\s", run)]
    assert len(tagged) == 1 and tagged[0] > loaded
    tag_step = steps[tagged[0]]
    assert tag_step.get("env", {}).get("TESTED_IMAGE_ID") == "${{ steps.tested-image.outputs.image_id }}"
    assert 'docker tag "$TESTED_IMAGE_ID"' in runs[tagged[0]], "only the verified daemon image reference may be tagged"
    if target == "catalog-viewer":
        assert tag_step.get("env", {}).get("TESTED_IMAGE_CONFIG") == "${{ steps.tested-image.outputs.config_digest }}"
        assert "CATALOG_VIEWER_LOCAL_DIGEST=${TESTED_IMAGE_CONFIG}" in runs[tagged[0]], "viewer claim must bind canonical config bytes"
    pushed = [index for index, run in enumerate(runs) if re.search(r"\bdocker\s+push\s", run)]
    assert len(pushed) == 1 and pushed[0] > tagged[0]
    push = steps[pushed[0]]
    assert push.get("env", {}).get("TESTED_IMAGE_CONFIG") == "${{ steps.tested-image.outputs.config_digest }}"
    assert re.search(r"\bimagetools\s+inspect\s+--raw\s", runs[pushed[0]]), "registry config must be read from the actual pushed manifest"
    assert 'manifest.get("config", {}).get("digest")' in runs[pushed[0]] and "assert actual == expected" in runs[pushed[0]], "registry must retain the tested config"
    assert runs[pushed[0]].index("assert actual == expected") < runs[pushed[0]].index("image_ref="), "unverified registry bytes cannot become a deployment input"
    applied = [index for index, run in enumerate(runs) if re.search(r"(?:terraform_retry\.sh|\bterraform\b).*\sapply(?:\s|$)", run)]
    assert applied and all(index > pushed[0] for index in applied), "registry config proof must precede every Terraform mutation"
    return push


@pytest.mark.parametrize("target", TARGETS)
def test_deployments_load_authenticated_ci_bytes_and_verify_registry_config(target):
    image_contract(deployment_steps(target), target)


def test_viewer_claim_cannot_bind_a_daemon_reference_instead_of_config_bytes():
    steps = copy.deepcopy(deployment_steps("catalog-viewer"))
    tag = next(step for step in steps if "docker tag" in step.get("run", ""))
    tag["run"] = tag["run"].replace("CATALOG_VIEWER_LOCAL_DIGEST=${TESTED_IMAGE_CONFIG}", "CATALOG_VIEWER_LOCAL_DIGEST=${TESTED_IMAGE_ID}")
    with pytest.raises(AssertionError, match="canonical config bytes"):
        image_contract(steps, "catalog-viewer")


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("defect", ("rebuild", "missing-source", "loader-fallback", "mutable-tag", "no-registry-read", "daemon-id-as-config", "unverified-config", "early-apply"))
def test_image_contract_rejects_rebuild_fallback_and_missing_proof(target, defect):
    steps = copy.deepcopy(deployment_steps(target))
    loader = next(step for step in steps if step.get("id") == "tested-image")
    tag = next(step for step in steps if "docker tag" in step.get("run", ""))
    push = next(step for step in steps if "docker push" in step.get("run", ""))
    if defect == "rebuild":
        loader["run"] += "\ndocker build -t mutable .\n"
    elif defect == "missing-source":
        loader["run"] = loader["run"].replace('--source-run-attempt "$SOURCE_RUN_ATTEMPT"', "")
    elif defect == "loader-fallback":
        loader["run"] += " || docker pull mutable\n"
    elif defect == "mutable-tag":
        tag["env"]["TESTED_IMAGE_ID"] = "mutable:latest"
    elif defect == "no-registry-read":
        push["run"] = push["run"].replace("imagetools inspect --raw", "imagetools inspect")
    elif defect == "daemon-id-as-config":
        push["env"]["TESTED_IMAGE_CONFIG"] = "${{ steps.tested-image.outputs.image_id }}"
    elif defect == "unverified-config":
        push["run"] = push["run"].replace("assert actual == expected", "assert True")
    else:
        steps.insert(steps.index(push), {"run": "terraform apply saved.tfplan"})
    with pytest.raises(AssertionError):
        image_contract(steps, target)


@pytest.mark.parametrize("target", TARGETS)
def test_actual_registry_manifest_verifier_rejects_another_config(tmp_path, target):
    push = image_contract(deployment_steps(target), target)
    line = next(line.strip() for line in push["run"].splitlines() if line.strip().startswith("python -c "))
    source = shlex.split(line)[2]
    expected = "sha256:" + "1" * 64
    manifest = tmp_path / "manifest.json"
    for actual in (expected, "sha256:" + "2" * 64, None):
        manifest.write_text(json.dumps({"config": {"digest": actual}}))
        completed = subprocess.run([sys.executable, "-c", source, str(manifest)], env={"TESTED_IMAGE_CONFIG": expected, "TESTED_IMAGE_ID": "sha256:" + "3" * 64},
                                   capture_output=True, text=True)
        assert (completed.returncode == 0) == (actual == expected), completed.stderr


def image_results():
    selected = sorted(ALWAYS | {"production-images"})
    plan = {"schema_version": 1, "base": "a" * 40, "head": "b" * 40, "tested_sha": "c" * 40,
            "tree": "d" * 40, "contract_digest": "e" * 64, "suites": selected}
    results = [{**{key: plan[key] for key in ("base", "head", "tested_sha", "tree", "contract_digest")},
                "suite": suite, "status": "success", "tools": expected_tools(suite),
                "commands": [{"argv": ["actual-small-image-fixtures"], "exit_code": 0}]} for suite in selected]
    image_result = next(result for result in results if result["suite"] == "production-images")
    image_result["production_images"] = production_images(plan["tested_sha"])
    return plan, results, image_result


def test_ci_ready_requires_complete_image_retention_evidence():
    plan, results, _ = image_results()
    assert set(verify_results(plan, results)) == set(plan["suites"])


@pytest.mark.parametrize("defect", ("missing", "partial", "foreign-sha", "foreign-image-tag", "wrong-platform", "oversized", "duplicate-kind", "daemon-id-field"))
def test_ci_ready_rejects_success_without_complete_exact_images(defect):
    plan, results, image_result = image_results()
    manifest = image_result["production_images"]
    if defect == "missing":
        del image_result["production_images"]
    elif defect == "partial":
        del manifest["images"]["sea-ice-daily"]
    elif defect == "foreign-sha":
        manifest["tested_sha"] = "f" * 40
    elif defect == "foreign-image-tag":
        manifest["images"]["catalog-viewer"]["source_tag"] = "unverified:latest"
    elif defect == "wrong-platform":
        manifest["images"]["eamlis-monthly"]["platform"] = "linux/arm64"
    elif defect == "oversized":
        manifest["images"]["sea-ice-daily"]["archive_size"] = 3 * 1024 ** 3
    elif defect == "daemon-id-field":
        image = manifest["images"]["catalog-viewer"]
        image["image_id"] = image.pop("config_digest")
    else:
        manifest["images"]["another-image"] = copy.deepcopy(manifest["images"]["catalog-viewer"])
    with pytest.raises(ValueError):
        verify_results(plan, results)
