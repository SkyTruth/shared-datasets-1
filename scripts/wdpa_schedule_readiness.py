#!/usr/bin/env python3
"""Require a verified automatic WDPA worker before enabling its schedule."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import deployment_revision as deployment

PROJECT = "shared-datasets-1"
REGION = "us-central1"
JOB = "wdpa-monthly"


def worker_image(job):
    container = job["spec"]["template"]["spec"]["template"]["spec"]["containers"][0]
    image = container["image"]
    deployment.require(re.fullmatch(r"[a-z0-9./_-]+@sha256:[0-9a-f]{64}", image),
                       "WDPA worker must use an immutable image digest")
    env = {entry["name"]: entry.get("value") for entry in container.get("env", [])}
    deployment.require(not {"RUN_DATE", "WDPA_PROMOTION_BUNDLE"} & env.keys(),
                       "WDPA worker still has a persistent monthly date or promotion bundle")
    deployment.require(env.get("WDPA_BUILD_IMAGE") == image
                       and re.fullmatch(r"sha256:[0-9a-f]{64}", env.get("WDPA_BUILD_IMAGE_CONFIG_DIGEST", "")),
                       "WDPA worker lacks the exact tested automatic producer configuration")
    return image


def require_ready(api, repository, job, read_execution):
    image = worker_image(job)
    records = api.pages(f"repos/{repository}/deployments?environment=production-{JOB}&per_page=100")
    deployment.require(records, "WDPA has no protected deployment record")
    for record in sorted(records, key=lambda row: row["id"], reverse=True):
        payload = deployment.verify_record(api, repository, record)
        statuses = deployment.verified_statuses(api, repository, record)
        if not (statuses and statuses[0].get("state") == "pending"
                and statuses[0].get("description") == "deferred"):
            break
    else:
        raise deployment.DeploymentError("WDPA has no applied and verified protected deployment")
    deployment.require(payload["target"] == JOB and payload["artifact"] == image,
                       "live WDPA image differs from the latest protected deployment")
    deployment.require(statuses and statuses[0].get("state") == "success"
                       and statuses[0].get("description") == "verified",
                       "latest WDPA deployment has no signed verified canary outcome")
    match = re.fullmatch(
        r"https://console\.cloud\.google\.com/run/jobs/executions/details/us-central1/"
        r"([a-z][a-z0-9-]{0,62})\?project=shared-datasets-1",
        statuses[0].get("environment_url", ""),
    )
    deployment.require(match is not None, "verified WDPA deployment lacks its exact canary execution")
    deployment.require(deployment.terminal_execution(read_execution(match[1]), image) == "verified",
                       "WDPA canary has not completed successfully")


def main():
    def read_gcloud(*args):
        return json.loads(subprocess.check_output(
            ["gcloud", "run", "jobs", *args, f"--region={REGION}", f"--project={PROJECT}", "--format=json"],
        ))

    try:
        require_ready(deployment.GitHub(), os.environ["GITHUB_REPOSITORY"],
                      read_gcloud("describe", JOB),
                      lambda execution: read_gcloud("executions", "describe", execution))
    except deployment.DeploymentError as error:
        print(f"WDPA_SCHEDULE_NOT_READY: {error}", file=sys.stderr)
        return 1
    print("WDPA automatic worker and exact-image canary verified; schedule may resume.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
