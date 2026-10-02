from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_wdpa_validation_plan as policy
from scripts import cloud_wdpa_validation as cloud
from scripts import wdpa_processing_gate as gate
from tests.test_wdpa_execution_observer import accepted_evidence
from tests.workflow_helpers import (
    load_workflow,
    terraform_targets,
    workflow_steps_by_name,
)

ROOT = Path(__file__).resolve().parents[1]
IMAGE = (
    "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-validation@sha256:"
    + "a" * 64
)
DEPLOYER = "shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com"


def staged_evidence():
    marine = accepted_evidence()["runs"][0]
    marine["assets"].pop("wdpa-terrestrial")
    small = {
        key: marine[key]
        for key in (
            "source_tree_sha256",
            "image_digest",
            "cpu_limit",
            "memory_limit_bytes",
        )
    }
    small.update(
        scope="small-sea-ice-fixture", state="succeeded", contracts_verified=True
    )
    return dict(
        schema_version=1,
        source_tree_sha256=gate.source_digest(),
        disk_quota_approved=True,
        small_fixture=small,
        marine=marine,
    )


def test_staged_evidence_cannot_authorize_publication():
    evidence = staged_evidence()
    assert gate.check_precloud(evidence) == []
    assert gate.check(evidence), (
        "small/marine evidence cannot open the production publication gate"
    )


@pytest.mark.parametrize(
    "defect",
    [
        "quota",
        "small",
        "sample",
        "terrestrial",
        "cache",
        "memory",
        "no_spill",
        "digest",
    ],
)
def test_cloud_validation_requires_all_prior_stages_and_measured_disk_spill(defect):
    evidence = staged_evidence()
    if defect == "quota":
        evidence["disk_quota_approved"] = False
    if defect == "small":
        evidence["small_fixture"]["state"] = "failed"
    if defect == "sample":
        evidence["marine"]["sample_fraction"] = 0.001
    if defect == "terrestrial":
        evidence["marine"]["assets"]["wdpa-terrestrial"] = {}
    if defect == "cache":
        evidence["marine"]["translation_index_built"] = False
    if defect == "memory":
        evidence["marine"]["memory_peak_bytes"] = 7 * 1024**3
    if defect == "no_spill":
        evidence["marine"]["scratch_peak_bytes"] = 7 * 1024**3
    if defect == "digest":
        evidence["small_fixture"]["image_digest"] = "sha256:" + "f" * 64
    assert gate.check_precloud(evidence)


def test_hosted_stages_run_small_before_marine_and_never_terrestrial():
    workflow = load_workflow(ROOT / ".github/workflows/ci.yml")
    job = workflow["jobs"]["wdpa-full-benchmark"]
    assert job["needs"] == ["wdpa-benchmark-image", "wdpa-small-smoke"]
    run = workflow_steps_by_name(workflow, "wdpa-full-benchmark")[
        "Run marine WDPA with 4 CPU and 8 GiB"
    ]["run"]
    assert "--asset wdpa-marine" in run and "--asset wdpa-terrestrial" not in run
    assert "--cpus=4 --memory=8g --memory-swap=8g" in run
    assert "timeout --signal=TERM --kill-after=120s 330m docker run" in run
    assert job["timeout-minutes"] == 360


def test_isolated_cloud_workflow_is_protected_and_cannot_target_worker_or_bucket_iam():
    workflow = load_workflow(
        ROOT / ".github/workflows/wdpa-processing-validation-deploy.yml"
    )
    job = workflow["jobs"]["deploy"]
    assert job["environment"] == "shared-datasets-production"
    assert job["concurrency"] == dict(
        group="prod-terraform-state", queue="max", **{"cancel-in-progress": False}
    )
    steps = workflow_steps_by_name(workflow, "deploy")
    assert (
        terraform_targets(steps["Plan only isolated validation resources"]["run"])
        == policy.ALLOWED
    )
    names = list(steps)
    assert names.index(
        "Require small and marine validation plus disk quota"
    ) < names.index("Build and push the immutable validation image")
    assert (
        "--pre-cloud"
        in steps["Require small and marine validation plus disk quota"]["run"]
    )
    assert names.index(
        "Enforce isolated resource and permission allowlist"
    ) < names.index("Apply the saved protected plan")
    tf = (ROOT / "terraform/envs/prod/wdpa_processing_validation.tf").read_text()
    assert "google_storage_bucket_iam" not in tf and "scheduler" not in tf


def plan():
    after = {
        "project": "shared-datasets-1",
        "location": "us-central1",
        "name": "wdpa-processing-validation",
        "launch_stage": "BETA",
        "template": [
            {
                "task_count": 1,
                "parallelism": 1,
                "template": [
                    {
                        "service_account": "wdpa-processing-validation@shared-datasets-1.iam.gserviceaccount.com",
                        "timeout": "86400s",
                        "max_retries": 0,
                        "volumes": [
                            {
                                "name": "work",
                                "empty_dir": [
                                    {"medium": "DISK", "size_limit": "100Gi"}
                                ],
                            }
                        ],
                        "containers": [
                            {
                                "image": IMAGE,
                                "command": [
                                    "python",
                                    "scripts/cloud_wdpa_validation.py",
                                ],
                                "resources": [
                                    {"limits": {"cpu": "4", "memory": "8Gi"}}
                                ],
                                "volume_mounts": [
                                    {"name": "work", "mount_path": "/work"}
                                ],
                                "env": [
                                    {"name": "TMPDIR", "value": "/work/tmp"},
                                    {
                                        "name": "SHARED_DATASETS_WORKDIR",
                                        "value": "/work/shared-datasets-1",
                                    },
                                ],
                            }
                        ],
                    }
                ],
            }
        ],
    }
    return {
        "resource_changes": [
            {"address": policy.JOB, "change": {"actions": ["create"], "after": after}}
        ]
    }


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "null_subpath",
        "empty_subpath",
        "subdirectory",
        "worker",
        "bucket_iam",
        "delete",
        "size",
        "command",
        "extra_container",
        "tag",
    ],
)
def test_validation_plan_rejects_publication_and_resource_escalation(defect):
    document = copy.deepcopy(plan())
    resource = document["resource_changes"][0]
    container = resource["change"]["after"]["template"][0]["template"][0]["containers"][
        0
    ]
    if defect == "worker":
        resource["address"] = "module.wdpa_monthly_job.google_cloud_run_v2_job.this"
    if defect == "bucket_iam":
        resource["address"] = "google_storage_bucket_iam_member.write_datasets"
    if defect == "delete":
        resource["change"]["actions"] = ["delete", "create"]
    if defect == "size":
        container["resources"][0]["limits"]["memory"] = "16Gi"
    if defect == "command":
        container["command"] = ["python", "-m", "ingestion.wdpa_monthly.run"]
    if defect == "extra_container":
        resource["change"]["after"]["template"][0]["template"][0]["containers"].append(
            copy.deepcopy(container)
        )
    if defect == "null_subpath":
        container["volume_mounts"][0]["sub_path"] = None
    if defect == "empty_subpath":
        container["volume_mounts"][0]["sub_path"] = ""
    if defect == "subdirectory":
        container["volume_mounts"][0]["sub_path"] = "other"
    if defect in (None, "null_subpath", "empty_subpath"):
        policy.check(document, image=IMAGE, deployer=DEPLOYER)
    else:
        with pytest.raises(ValueError):
            policy.check(
                document,
                image=IMAGE if defect != "tag" else IMAGE.split("@")[0] + ":latest",
                deployer=DEPLOYER,
            )


def test_controlled_cloud_failure_precedes_downloads_and_processing(monkeypatch):
    monkeypatch.setenv("WDPA_FAIL_BEFORE_DATASET_WRITES", "1")
    monkeypatch.setattr(
        cloud.subprocess,
        "run",
        lambda *a, **k: pytest.fail("failure probe started processing"),
    )
    with pytest.raises(RuntimeError, match="before any dataset writes"):
        cloud.main()


def inspection_recipe():
    return json.loads((ROOT / "catalog/wdpa-runtime-inspection.json").read_text())


def test_runtime_inspection_is_distinct_from_source_processing():
    document = plan()
    container = document["resource_changes"][0]["change"]["after"]["template"][0][
        "template"
    ][0]["containers"][0]
    container["command"] = inspection_recipe()["command"]
    policy.check(document, image=IMAGE, deployer=DEPLOYER, runtime_inspection=True)
    with pytest.raises(ValueError, match="configuration"):
        policy.check(document, image=IMAGE, deployer=DEPLOYER)
    container["command"] = ["python", "-c", "print('unreviewed')"]
    with pytest.raises(ValueError, match="configuration"):
        policy.check(document, image=IMAGE, deployer=DEPLOYER, runtime_inspection=True)


def test_runtime_inspection_cannot_increase_resources_or_gain_dataset_permissions():
    for defect in ("memory", "identity", "bucket_iam"):
        document = plan()
        task = document["resource_changes"][0]["change"]["after"]["template"][0][
            "template"
        ][0]
        task["containers"][0]["command"] = inspection_recipe()["command"]
        if defect == "memory":
            task["containers"][0]["resources"][0]["limits"]["memory"] = "16Gi"
        elif defect == "identity":
            task["service_account"] = (
                "wdpa-monthly@shared-datasets-1.iam.gserviceaccount.com"
            )
        else:
            document["resource_changes"][0]["address"] = (
                "google_storage_bucket_iam_member.writer"
            )
        with pytest.raises(ValueError):
            policy.check(
                document, image=IMAGE, deployer=DEPLOYER, runtime_inspection=True
            )


def test_runtime_inspection_reports_only_diagnostics():
    recipe = inspection_recipe()
    assert recipe["schema_version"] == 1
    output = subprocess.run(
        [sys.executable, *recipe["command"][1:]],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    report = json.loads(output.stdout)
    assert report["event"] == "wdpa_runtime_inspection"
    assert report["scope"] == "runtime-inspection-not-acceptance"
    assert report["source_tree_sha256"] == gate.source_digest()
    assert "assets" not in report and "contracts_verified" not in report
    assert "/proc/self/cgroup" in report["files"]


def test_runtime_inspection_workflow_cannot_apply_worker_or_iam_changes():
    workflow = load_workflow(ROOT / ".github/workflows/wdpa-runtime-inspection.yml")
    job = workflow["jobs"]["inspect"]
    assert job["environment"] == "shared-datasets-production"
    assert job["concurrency"] == dict(
        group="prod-terraform-state", queue="max", **{"cancel-in-progress": False}
    )
    steps = workflow_steps_by_name(workflow, "inspect")
    assert terraform_targets(
        steps["Plan only the existing isolated job command"]["run"]
    ) == {policy.JOB}
    check = steps["Enforce the exact read-only command and resource limits"]["run"]
    assert "--runtime-inspection" in check and "--block-deletes" in check
    names = list(steps)
    assert names.index(
        "Enforce the exact read-only command and resource limits"
    ) < names.index("Apply the saved protected plan")
    assert "wdpa_processing_gate.py --pre-cloud" not in str(steps), (
        "inspection does not build source or claim staged acceptance"
    )
