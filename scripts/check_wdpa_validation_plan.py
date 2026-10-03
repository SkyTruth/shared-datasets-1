#!/usr/bin/env python3
"""Allow only the isolated build job and narrowly scoped staging/read grants."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

SA = "module.wdpa_validation_service_account.google_service_account.this"
JOB = "module.wdpa_processing_validation_job.google_cloud_run_v2_job.this"
IAM = "google_service_account_iam_member.wdpa_validation_deployer"
ALLOWED = {SA, JOB, IAM}
STAGING = {
    "google_project_iam_custom_role.wdpa_build_stager": (
        "wdpaBuildStager",
        {"storage.folders.create", "storage.objects.create"},
    ),
    "google_project_iam_custom_role.wdpa_build_reader": (
        "wdpaBuildReader",
        {"storage.objects.get"},
    ),
}
BINDINGS = {
    "google_storage_bucket_iam_member.wdpa_build_stager": (
        "wdpaBuildStager",
        "wdpa-processing-validation",
    ),
    "google_storage_bucket_iam_member.wdpa_build_folder_stager": (
        "wdpaBuildStager",
        "wdpa-processing-validation",
    ),
    "google_storage_bucket_iam_member.wdpa_build_reader": (
        "wdpaBuildReader",
        "wdpa-monthly-job",
    ),
}
ALLOWED |= set(STAGING) | set(BINDINGS)
OBJECT_SCOPE = "resource.name.startsWith('projects/_/buckets/skytruth-shared-datasets-1/objects/_scratch/wdpa-builds/')"
FOLDER_SCOPE = "resource.name.startsWith('projects/_/buckets/skytruth-shared-datasets-1/folders/_scratch/wdpa-builds/')"


def check(plan, *, image, deployer, runtime_inspection=False, staging_probe=False):
    if runtime_inspection and staging_probe:
        raise ValueError("Inspection and staging probe are distinct commands")
    command = ["python", "scripts/cloud_wdpa_validation.py"]
    if runtime_inspection or staging_probe:
        recipe_name = (
            "wdpa-runtime-inspection" if runtime_inspection else "wdpa-staging-probe"
        )
        recipe = json.loads(
            (
                Path(__file__).resolve().parents[1] / f"catalog/{recipe_name}.json"
            ).read_text()
        )
        if recipe["schema_version"] != 1:
            raise ValueError("Unsupported runtime inspection recipe")
        command = recipe["command"]
    if not re.fullmatch(
        r"us-central1-docker\.pkg\.dev/shared-datasets-1/shared-datasets-jobs/wdpa-validation@sha256:[0-9a-f]{64}",
        image,
    ):
        raise ValueError("An immutable validation image is required")
    for resource in plan.get("resource_changes", []):
        actions = resource["change"]["actions"]
        if actions in (["no-op"], ["read"]) and resource["address"] not in ALLOWED:
            continue
        if resource["address"] not in ALLOWED or actions not in (
            ["create"],
            ["update"],
            ["no-op"],
        ):
            raise ValueError(
                f"Isolated validation refuses {resource['address']} {actions}"
            )
        after = resource["change"]["after"]
        if resource["address"] in STAGING:
            role, permission = STAGING[resource["address"]]
            if (
                after["project"] != "shared-datasets-1"
                or after["role_id"] != role
                or set(after["permissions"]) != permission
            ):
                raise ValueError("Unexpected staging role permissions")
        elif resource["address"] in BINDINGS:
            role, account = BINDINGS[resource["address"]]
            if (
                after["bucket"] != "skytruth-shared-datasets-1"
                or after["role"] != "projects/shared-datasets-1/roles/" + role
                or after["member"]
                != f"serviceAccount:{account}@shared-datasets-1.iam.gserviceaccount.com"
                or len(after["condition"]) != 1
                or after["condition"][0]["expression"]
                != (
                    FOLDER_SCOPE
                    if resource["address"]
                    == "google_storage_bucket_iam_member.wdpa_build_folder_stager"
                    else OBJECT_SCOPE
                )
            ):
                raise ValueError("Unexpected staging scope or identity")
        elif resource["address"] == SA:
            if (
                after["project"] != "shared-datasets-1"
                or after["account_id"] != "wdpa-processing-validation"
            ):
                raise ValueError("Unexpected validation identity")
        elif resource["address"] == IAM:
            if (
                after["service_account_id"]
                != "projects/shared-datasets-1/serviceAccounts/wdpa-processing-validation@shared-datasets-1.iam.gserviceaccount.com"
                or after["role"] != "roles/iam.serviceAccountUser"
                or after["member"] != f"serviceAccount:{deployer}"
            ):
                raise ValueError("Unexpected validation deployer permissions")
        else:
            task = after["template"][0]["template"][0]
            container = task["containers"][0]
            volume = task["volumes"][0]
            if (
                after["project"] != "shared-datasets-1"
                or after["location"] != "us-central1"
                or after["name"] != "wdpa-processing-validation"
                or after["launch_stage"] != "BETA"
                or task["service_account"]
                != "wdpa-processing-validation@shared-datasets-1.iam.gserviceaccount.com"
                or task["timeout"] != "86400s"
                or task["max_retries"] != 0
                or after["template"][0]["task_count"] != 1
                or after["template"][0]["parallelism"] != 1
                or len(task["containers"]) != 1
                or len(task["volumes"]) != 1
                or container["image"] != image
                or container["command"] != command
                or container["resources"][0]["limits"] != {"cpu": "4", "memory": "8Gi"}
                or volume["empty_dir"][0] != {"medium": "DISK", "size_limit": "100Gi"}
                or [
                    (m["name"], m["mount_path"], m.get("sub_path") in (None, ""))
                    for m in container["volume_mounts"]
                ]
                != [("work", "/work", True)]
            ):
                raise ValueError("Unexpected validation job configuration")
            env = {e["name"]: e.get("value") for e in container["env"]}
            expected = {
                "TMPDIR": "/work/tmp",
                "SHARED_DATASETS_WORKDIR": "/work/shared-datasets-1",
            }
            if not runtime_inspection:
                digest = env.get("WDPA_BUILD_IMAGE_CONFIG_DIGEST", "")
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                    raise ValueError("Verified build configuration digest is required")
                expected.update(
                    WDPA_BUILD_IMAGE=image, WDPA_BUILD_IMAGE_CONFIG_DIGEST=digest
                )
            if env != expected:
                raise ValueError("Unexpected validation job environment")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    parser.add_argument("--image", required=True)
    parser.add_argument("--deployer", required=True)
    parser.add_argument("--runtime-inspection", action="store_true")
    parser.add_argument("--staging-probe", action="store_true")
    args = parser.parse_args()
    with open(args.plan) as source:
        check(
            json.load(source),
            image=args.image,
            deployer=args.deployer,
            runtime_inspection=args.runtime_inspection,
            staging_probe=args.staging_probe,
        )


if __name__ == "__main__":
    main()
