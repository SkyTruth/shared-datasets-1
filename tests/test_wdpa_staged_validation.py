from __future__ import annotations

import copy
from contextlib import contextmanager
import json
from types import SimpleNamespace
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
    complete = accepted_evidence()
    build = complete["build"]
    small = {
        key: build[key]
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
        schema_version=3,
        source_tree_sha256=gate.source_digest(),
        disk_quota_approved=True,
        small_fixture=small,
        compatibility_sample=complete["compatibility_sample"],
    )


def test_staged_evidence_cannot_authorize_publication():
    evidence = staged_evidence()
    assert gate.check_precloud(evidence) == []
    assert gate.check(evidence), (
        "small/sampled evidence cannot open the production publication gate"
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
        "native",
        "digest",
    ],
)
def test_cloud_build_requires_matching_small_and_sampled_checks(defect):
    evidence = staged_evidence()
    if defect == "quota":
        evidence["disk_quota_approved"] = False
    if defect == "small":
        evidence["small_fixture"]["state"] = "failed"
    if defect == "sample":
        evidence["compatibility_sample"]["sample_fraction"] = 1
    if defect == "terrestrial":
        evidence["compatibility_sample"]["assets"].pop("wdpa-terrestrial")
    if defect == "cache":
        evidence["compatibility_sample"]["translation_index_built"] = False
    if defect == "memory":
        evidence["compatibility_sample"]["memory_limit_bytes"] = 16 * 1024**3
    if defect == "native":
        evidence["compatibility_sample"]["native_versions"] = {}
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


def test_single_build_smoke_never_runs_the_complete_marine_benchmark():
    workflow = load_workflow(ROOT / ".github/workflows/ci.yml")
    steps = workflow_steps_by_name(workflow, "wdpa-full-benchmark")
    assert (
        "!inputs.wdpa_build_smoke"
        in steps["Run marine WDPA with 4 CPU and 8 GiB"]["if"]
    )
    assert (
        "wdpa_build_smoke"
        not in steps[
            "Compare deterministic October sample with the old processing path"
        ]["if"]
    )


def test_cloud_build_workflow_is_protected_and_limits_staging_permissions():
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
        "Require small and sampled checks plus disk quota"
    ) < names.index("Publish the tested immutable validation image")
    assert (
        "--pre-cloud"
        in steps["Require small and sampled checks plus disk quota"]["run"]
    )
    assert names.index(
        "Enforce isolated resource and permission allowlist"
    ) < names.index("Apply the saved protected plan")
    tf = (ROOT / "terraform/envs/prod/wdpa_processing_validation.tf").read_text()
    assert "_scratch/wdpa-builds/" in tf and "scheduler" not in tf
    assert 'permissions = ["storage.folders.create", "storage.objects.create"]' in tf
    assert 'permissions = ["storage.objects.get"]' in tf


def test_cloud_deployment_reuses_the_tested_image_instead_of_rebuilding():
    workflow = load_workflow(
        ROOT / ".github/workflows/wdpa-processing-validation-deploy.yml"
    )
    steps = workflow_steps_by_name(workflow, "deploy")
    download = steps["Download the tested deployment image"]
    assert download["with"]["name"] == "wdpa-benchmark-image"
    assert "run-id" in download["with"] and "github-token" in download["with"]
    publish = steps["Publish the tested immutable validation image"]["run"]
    require = steps["Require small and sampled checks plus disk quota"]["run"]
    assert "docker load" in require and "docker tag" in publish
    assert "config_digest" in require and "--print-source-digest" in require
    assert "steps.staged-image.outputs.source_digest" in require
    assert "/app/catalog/wdpa-staged-validation.json:ro" in require
    assert "docker run" in require and "--pre-cloud" in require
    assert "uv run" not in require
    assert "docker build" not in publish.replace("docker buildx imagetools", "inspect")
    names = list(steps)
    assert names.index(
        "Refuse to replace an active validation execution"
    ) < names.index("Publish the tested immutable validation image")
    assert (
        "completionTime"
        in steps["Refuse to replace an active validation execution"]["run"]
    )


def test_scratch_probe_precedes_processing_and_restores_only_after_terminal():
    workflow = load_workflow(
        ROOT / ".github/workflows/wdpa-processing-validation-deploy.yml"
    )
    steps = workflow_steps_by_name(workflow, "deploy")
    names = list(steps)
    assert names.index("Apply the saved protected plan") < names.index(
        "Verify a real upload before processing"
    )
    assert (
        names.index("Verify a real upload before processing")
        < names.index("Restore the processing command after the terminal probe")
        < names.index("Start validation only after controlled alert verification")
    )
    probe = steps["Plan the scratch upload probe with unchanged image and resources"][
        "run"
    ]
    restore = steps["Restore the processing command after the terminal probe"]
    assert terraform_targets(probe) == {policy.JOB}
    assert terraform_targets(restore["run"]) == {policy.JOB}
    assert "--staging-probe" in probe and "--block-deletes" in probe
    assert "always()" in restore["if"] and "STAGING_PROBE_DEPLOYED" in restore["if"]
    assert "completionTime" in restore["run"]
    assert "--wait" in steps["Verify a real upload before processing"]["run"]
    assert "succeededCount" in steps["Verify a real upload before processing"]["run"]


@pytest.mark.parametrize("fails", [False, True])
def test_scratch_probe_uses_actual_stager_for_both_realms_and_never_commits(
    tmp_path, monkeypatch, capsys, fails
):
    from ingestion.wdpa_monthly.artifact_bundle import BuildStager
    from ingestion.wdpa_monthly import resources

    uploaded = []

    def upload(_self, name, path):
        uploaded.append(name)
        assert path.read_bytes() == b"WDPA scratch upload preflight\n"
        if fails:
            raise PermissionError("storage.folders.create")
        return {
            "uri": name,
            "generation": 1,
            "size": path.stat().st_size,
            "sha256": "0" * 64,
        }

    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(resources, "prepare_scratch", lambda: None)
    monkeypatch.setattr(
        BuildStager, "from_runtime", lambda: BuildStager.__new__(BuildStager)
    )
    monkeypatch.setattr(BuildStager, "upload", upload)
    monkeypatch.setattr(
        BuildStager, "commit", lambda *_: pytest.fail("probe committed a build")
    )
    recipe = json.loads((ROOT / "catalog/wdpa-staging-probe.json").read_text())
    assert recipe["command"][:2] == ["python", "-c"]
    if fails:
        with pytest.raises(PermissionError):
            exec(recipe["command"][2], {})
        assert not capsys.readouterr().out
        assert uploaded == ["wdpa-marine/staging-probe.txt"]
    else:
        exec(recipe["command"][2], {})
        assert uploaded == [
            "wdpa-marine/staging-probe.txt",
            "wdpa-terrestrial/staging-probe.txt",
        ]
        result = json.loads(capsys.readouterr().out)
        assert result["state"] == "succeeded"
        assert result["scope"] == "scratch-permission-probe-not-acceptance"


@pytest.mark.parametrize(
    "defect",
    [
        None,
        "delete",
        "rename",
        "object_root",
        "folder_root",
        "canonical",
        "wrong_identity",
    ],
)
def test_hierarchical_staging_grants_only_folder_creation_in_build_prefix(defect):
    role = {
        "address": "google_project_iam_custom_role.wdpa_build_stager",
        "change": {
            "actions": ["update"],
            "after": {
                "project": "shared-datasets-1",
                "role_id": "wdpaBuildStager",
                "permissions": ["storage.folders.create", "storage.objects.create"],
            },
        },
    }
    binding = {
        "address": "google_storage_bucket_iam_member.wdpa_build_folder_stager",
        "change": {
            "actions": ["create"],
            "after": {
                "bucket": "skytruth-shared-datasets-1",
                "role": "projects/shared-datasets-1/roles/wdpaBuildStager",
                "member": "serviceAccount:wdpa-processing-validation@shared-datasets-1.iam.gserviceaccount.com",
                "condition": [{"expression": policy.FOLDER_SCOPE}],
            },
        },
    }
    if defect in ("delete", "rename"):
        role["change"]["after"]["permissions"].append("storage.folders." + defect)
    elif defect in ("object_root", "folder_root", "canonical"):
        binding["change"]["after"]["condition"][0]["expression"] = {
            "object_root": policy.OBJECT_SCOPE.replace("_scratch/wdpa-builds/", ""),
            "folder_root": policy.FOLDER_SCOPE.replace("_scratch/wdpa-builds/", ""),
            "canonical": policy.FOLDER_SCOPE.replace(
                "_scratch/wdpa-builds/", "biodiversity/"
            ),
        }[defect]
    elif defect == "wrong_identity":
        binding["change"]["after"]["member"] = (
            "serviceAccount:other@shared-datasets-1.iam.gserviceaccount.com"
        )
    plan = {"resource_changes": [role, binding]}
    if defect:
        with pytest.raises(ValueError, match="staging"):
            policy.check(plan, image=IMAGE, deployer=DEPLOYER)
    else:
        policy.check(plan, image=IMAGE, deployer=DEPLOYER)


@pytest.mark.parametrize("defect", [None, "command", "cpu", "inspection"])
def test_probe_plan_pins_command_identity_and_resources(defect):
    probe_plan = plan()
    task = probe_plan["resource_changes"][0]["change"]["after"]["template"][0][
        "template"
    ][0]
    task["containers"][0]["command"] = json.loads(
        (ROOT / "catalog/wdpa-staging-probe.json").read_text()
    )["command"]
    if defect == "command":
        task["containers"][0]["command"] = ["python", "-c", "print('wrong')"]
    elif defect == "cpu":
        task["containers"][0]["resources"][0]["limits"]["cpu"] = "8"
    if defect:
        with pytest.raises(ValueError):
            policy.check(
                probe_plan,
                image=IMAGE,
                deployer=DEPLOYER,
                staging_probe=True,
                runtime_inspection=defect == "inspection",
            )
    else:
        policy.check(probe_plan, image=IMAGE, deployer=DEPLOYER, staging_probe=True)


@pytest.mark.parametrize("address", sorted(policy.BINDINGS))
@pytest.mark.parametrize(
    "bucket",
    [
        "skytruth-shared-datasets-1",
        "b/skytruth-shared-datasets-1",
        "b/another-bucket",
        "another-bucket",
        "b/b/skytruth-shared-datasets-1",
    ],
)
def test_staging_binding_accepts_only_exact_configured_or_refreshed_bucket_names(
    address, bucket
):
    role, account = policy.BINDINGS[address]
    resource = {
        "address": address,
        "change": {
            "actions": ["no-op"],
            "after": {
                "bucket": bucket,
                "role": "projects/shared-datasets-1/roles/" + role,
                "member": f"serviceAccount:{account}@shared-datasets-1.iam.gserviceaccount.com",
                "condition": [
                    {
                        "expression": policy.FOLDER_SCOPE
                        if address.endswith("wdpa_build_folder_stager")
                        else policy.OBJECT_SCOPE
                    }
                ],
            },
        },
    }
    plan = {"resource_changes": [resource]}
    if bucket in ("skytruth-shared-datasets-1", "b/skytruth-shared-datasets-1"):
        policy.check(plan, image=IMAGE, deployer=DEPLOYER)
    else:
        with pytest.raises(ValueError, match="staging scope"):
            policy.check(plan, image=IMAGE, deployer=DEPLOYER)


def test_sample_comparison_precedes_full_marine_and_is_not_resource_evidence():
    workflow = load_workflow(ROOT / ".github/workflows/ci.yml")
    steps = workflow_steps_by_name(workflow, "wdpa-full-benchmark")
    names = list(steps)
    sample = "Compare deterministic October sample with the old processing path"
    assert names.index(sample) < names.index("Run marine WDPA with 4 CPU and 8 GiB")
    command = steps[sample]["run"]
    assert "--fraction 0.001 --seed 7919 --compare-legacy" in command
    assert "--asset wdpa-marine" not in command
    assert "compatibility/benchmark.json" in command and "compatibility.json" in command


@pytest.mark.parametrize(
    "cpu,memory,peak,reason",
    [
        (None, 8 * 1024**3, 1, "kernel CPU"),
        (8, 8 * 1024**3, 1, "kernel CPU"),
        (3.72, 16 * 1024**3, 1, "8 GiB"),
        (3.72, 8 * 1024**3, None, "peak-memory"),
    ],
)
def test_cloud_preflight_stops_before_download_for_invalid_or_unmeasured_limits(
    monkeypatch, cpu, memory, peak, reason
):
    monkeypatch.delenv("WDPA_FAIL_BEFORE_DATASET_WRITES", raising=False)
    monkeypatch.setattr(cloud, "prepare_scratch", lambda: None)
    monkeypatch.setattr(cloud, "cgroup_limits", lambda: (cpu, memory))
    monkeypatch.setattr(cloud, "cgroup_memory", lambda: (1, peak))
    monkeypatch.setattr(
        cloud.subprocess,
        "run",
        lambda *_a, **_k: pytest.fail("preflight downloaded inputs"),
    )
    with pytest.raises(RuntimeError, match=reason):
        cloud.main()


def test_successful_cloud_build_retains_actual_peak_and_advisory_in_final_report(
    monkeypatch, tmp_path, capsys
):
    from ingestion.wdpa_monthly.artifact_bundle import BuildStager

    build = accepted_evidence()["build"]
    reference = build.pop("artifact_bundle")
    peak = 7732400128
    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    monkeypatch.setenv("CLOUD_RUN_EXECUTION", build["cloud_execution"])
    monkeypatch.setenv("WDPA_BUILD_IMAGE", build["cloud_image"])
    monkeypatch.setenv("WDPA_BUILD_IMAGE_CONFIG_DIGEST", build["image_digest"])
    monkeypatch.delenv("WDPA_FAIL_BEFORE_DATASET_WRITES", raising=False)
    monkeypatch.setattr(cloud, "prepare_scratch", lambda: None)
    monkeypatch.setattr(cloud, "cgroup_limits", lambda: (3.72, 8 * 1024**3))
    monkeypatch.setattr(cloud, "cgroup_memory", lambda: (1, peak))
    monkeypatch.setattr(cloud.wdpa, "native_versions", lambda: {})

    class Profiler:
        def __init__(self, *_args, **_kwargs):
            self.records = []

        @contextmanager
        def phase(self, _name):
            yield
            self.records.append({"scratch_peak_bytes": 1, "elapsed_seconds": 1})

    def run(command, **_kwargs):
        if "--stage-build" in command:
            directory = Path(command[command.index("--workdir") + 1])
            directory.mkdir()
            (directory / "benchmark.json").write_text(json.dumps(build))

    def commit(report, _root):
        assert gate.check_build(report, require_bundle=False) == []
        assert report["memory_peak_bytes"] == peak
        assert report["resource_warnings"] == gate.memory_warnings(report)
        return reference

    monkeypatch.setattr(cloud, "PhaseProfiler", Profiler)
    monkeypatch.setattr(cloud.subprocess, "run", run)
    monkeypatch.setattr(
        BuildStager, "from_runtime", lambda: SimpleNamespace(commit=commit)
    )
    cloud.main()
    report = json.loads((tmp_path / "cloud-validation/benchmark.json").read_text())
    assert report["state"] == "succeeded"
    assert report["memory_peak_bytes"] == peak
    assert report["artifact_bundle"] == reference
    logged = json.loads(capsys.readouterr().out)["report"]
    assert logged == report and logged["resource_warnings"]


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
                                    {"name": "WDPA_BUILD_IMAGE", "value": IMAGE},
                                    {
                                        "name": "WDPA_BUILD_IMAGE_CONFIG_DIGEST",
                                        "value": "sha256:" + "a" * 64,
                                    },
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


@pytest.mark.parametrize("defect", ["delete", "wide_prefix"])
def test_build_staging_cannot_grant_replacement_or_bucket_wide_access(defect):
    resource = {
        "address": "google_project_iam_custom_role.wdpa_build_stager",
        "change": {
            "actions": ["create"],
            "after": {
                "project": "shared-datasets-1",
                "role_id": "wdpaBuildStager",
                "permissions": ["storage.objects.create", "storage.objects.delete"],
            },
        },
    }
    if defect == "wide_prefix":
        resource = {
            "address": "google_storage_bucket_iam_member.wdpa_build_stager",
            "change": {
                "actions": ["create"],
                "after": {
                    "bucket": "skytruth-shared-datasets-1",
                    "role": "projects/shared-datasets-1/roles/wdpaBuildStager",
                    "member": "serviceAccount:wdpa-processing-validation@shared-datasets-1.iam.gserviceaccount.com",
                    "condition": [
                        {
                            "expression": "resource.name.startsWith('projects/_/buckets/skytruth-shared-datasets-1/objects/')"
                        }
                    ],
                },
            },
        }
    with pytest.raises(ValueError, match="staging"):
        policy.check({"resource_changes": [resource]}, image=IMAGE, deployer=DEPLOYER)


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
    container["env"] = [
        e for e in container["env"] if not e["name"].startswith("WDPA_BUILD_")
    ]
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
