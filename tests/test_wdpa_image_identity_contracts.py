"""Accepted WDPA config bytes stay binding across classic and containerd IDs."""

import copy
import json
from pathlib import Path
import re
import shlex
import sys

import pytest

from scripts import tested_image_bundle as images
from workflow_helpers import load_workflow, workflow_steps_by_name

ROOT = Path(__file__).resolve().parents[1]
CONFIG = "sha256:" + "a" * 64
DAEMON = "sha256:" + "b" * 64
REGISTRY = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-validation@sha256:" + "c" * 64
STEPS = (
    ("wdpa-monthly-deploy.yml", "deploy", "Prepare reviewed WDPA publication image", True),
)


def proof_step(filename, job, name):
    return workflow_steps_by_name(load_workflow(ROOT / ".github/workflows" / filename), job)[name]


def proof_source(step):
    line = next(line.strip() for line in step["run"].splitlines() if "verify_local_config" in line)
    args = shlex.split(line)
    assert args[1] == "-c"
    return args[2]


def proof_contract(step, registry):
    run = step["run"]
    source = proof_source(step)
    assert "directory=os.environ[\"RUNNER_TEMP\"]" in source
    assert "{{.Id}}" not in run, "daemon identity cannot substitute for accepted config bytes"
    if "docker run" in run:
        assert run.index("verify_local_config") < run.index("docker run"), "proof must precede executing accepted code"
    if registry:
        assert "require_manifest=True" in source
        assert "--raw \"${accepted_image}\"" in run
        assert run.index("imagetools inspect --raw") < run.index("docker pull") < run.index("verify_local_config")
        assert '["build"]["image_digest"]' in run
        assert '["build"]["source_tree_sha256"]' in run
        assert "Dockerfile.promotion" in run
    else:
        assert run.index("docker load") < run.index("verify_local_config")
        assert "wdpa-processing-benchmark:local" in source
        assert "docker build" not in run
        assert 'steps.staged-image.outputs.config_digest' in run or 'containerimage.config.digest' in run


@pytest.mark.parametrize("filename,job,name,registry", STEPS)
def test_monthly_consumer_proves_accepted_config_before_execution(filename, job, name, registry):
    proof_contract(proof_step(filename, job, name), registry)


@pytest.mark.parametrize("filename,job,name,registry", STEPS)
def test_actual_workflow_proof_accepts_a_distinct_daemon_id_and_rejects_wrong_config(monkeypatch, tmp_path, filename, job, name, registry):
    source = proof_source(proof_step(filename, job, name))
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    for config in (CONFIG, "sha256:" + "f" * 64):
        calls = []

        def verify(reference, actual, **options):
            calls.append((reference, actual, options))
            images.require(actual == CONFIG, "accepted config mismatch")
            return DAEMON

        monkeypatch.setattr(images, "verify_local_config", verify)
        monkeypatch.setattr(sys, "argv", ["workflow", REGISTRY, config] if registry else ["workflow", config])
        if config != CONFIG:
            with pytest.raises(images.ImageError, match="accepted config mismatch"):
                exec(compile(source, "workflow-image-proof", "exec"), {})
        else:
            exec(compile(source, "workflow-image-proof", "exec"), {})
        assert calls == [(REGISTRY if registry else "wdpa-processing-benchmark:local", config,
                          {"directory": str(tmp_path), **({"require_manifest": True} if registry else {})})]


@pytest.mark.parametrize("filename,job,name,registry", STEPS)
@pytest.mark.parametrize("defect", ("daemon-id-check", "late-proof", "missing-producer-config"))
def test_missing_or_replaced_wdpa_identity_proof_fails_contract(filename, job, name, registry, defect):
    step = copy.deepcopy(proof_step(filename, job, name))
    if defect == "daemon-id-check":
        step["run"] += "\ndocker inspect accepted --format '{{.Id}}'\n"
    elif defect == "late-proof":
        step["run"] = "docker run unverified-image arbitrary\n" + step["run"]
    elif registry:
        step["run"] = step["run"].replace('["build"]["image_digest"]', '["build"]["daemon_id"]')
    else:
        step["run"] = step["run"].replace("steps.staged-image.outputs.config_digest", "steps.staged-image.outputs.daemon_id").replace("containerimage.config.digest", "containerimage.daemon.id")
    with pytest.raises(AssertionError):
        proof_contract(step, registry)


@pytest.mark.parametrize("media_type", ("application/vnd.docker.distribution.manifest.v2+json", "application/vnd.oci.image.manifest.v1+json"))
@pytest.mark.parametrize("defect", (None, "foreign-config", "missing-config", "index", "mutable-ref"))
def test_real_registry_proof_rejects_mutable_or_incompatible_accepted_image(monkeypatch, tmp_path, media_type, defect):
    run = proof_step("wdpa-monthly-deploy.yml", "deploy", "Prepare reviewed WDPA publication image")["run"]
    source = re.search(r"python - [^\n]+ <<'PY'\n(.*?)\nPY", run, re.S)[1]
    manifest = {"mediaType": media_type, "config": {"digest": CONFIG}}
    reference = REGISTRY
    if defect == "foreign-config":
        manifest["config"]["digest"] = "sha256:" + "f" * 64
    elif defect == "missing-config":
        del manifest["config"]
    elif defect == "index":
        manifest["mediaType"] = "application/vnd.oci.image.index.v1+json"
    elif defect == "mutable-ref":
        reference = "wdpa-validation:latest"
    path = tmp_path / "registry-manifest.json"
    path.write_text(json.dumps(manifest))
    monkeypatch.setattr(sys, "argv", ["workflow", str(path), reference, CONFIG])
    if defect:
        with pytest.raises(SystemExit, match="does not bind"):
            exec(compile(source, "workflow-registry-proof", "exec"), {})
    else:
        exec(compile(source, "workflow-registry-proof", "exec"), {})
