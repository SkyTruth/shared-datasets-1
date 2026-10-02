#!/usr/bin/env python3
"""Allow only the isolated validation job, empty runtime identity and deployer binding."""

from __future__ import annotations

import argparse
import json
import re

SA = "module.wdpa_validation_service_account.google_service_account.this"
JOB = "module.wdpa_processing_validation_job.google_cloud_run_v2_job.this"
IAM = "google_service_account_iam_member.wdpa_validation_deployer"
ALLOWED = {SA, JOB, IAM}


def check(plan, *, image, deployer):
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
        if resource["address"] == SA:
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
                or container["command"]
                != ["python", "scripts/cloud_wdpa_validation.py"]
                or container["resources"][0]["limits"] != {"cpu": "4", "memory": "8Gi"}
                or volume["empty_dir"][0] != {"medium": "DISK", "size_limit": "100Gi"}
                or container["volume_mounts"]
                != [{"name": "work", "mount_path": "/work"}]
                or {e["name"]: e.get("value") for e in container["env"]}
                != {
                    "TMPDIR": "/work/tmp",
                    "SHARED_DATASETS_WORKDIR": "/work/shared-datasets-1",
                }
            ):
                raise ValueError("Unexpected validation job configuration")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan")
    parser.add_argument("--image", required=True)
    parser.add_argument("--deployer", required=True)
    args = parser.parse_args()
    with open(args.plan) as source:
        check(json.load(source), image=args.image, deployer=args.deployer)


if __name__ == "__main__":
    main()
