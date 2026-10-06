#!/usr/bin/env python3
"""Read-only permission probes for the authenticated protected deployer.

Only trusted main runs this code. A grant declared in Terraform is not live
readiness. Safe permission reads may retry for propagation; mutations never do.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import urllib.request
from urllib.parse import urlencode

PROJECT = "shared-datasets-1"
REGION = "us-central1"
SECRET = "shared-datasets-slack-webhook-url"
PROJECT_PERMISSIONS = (
    "run.jobs.get", "run.jobs.update", "run.jobs.run", "run.jobs.runWithOverrides",
    "run.executions.get", "run.executions.list", "run.operations.get",
)
BUCKET_PERMISSIONS = ("storage.buckets.get", "storage.managedFolders.create", "storage.managedFolders.get", "storage.managedFolders.list", "storage.managedFolders.getIamPolicy", "storage.managedFolders.setIamPolicy")
SECRET_PERMISSIONS = (
    "secretmanager.secrets.get", "secretmanager.secrets.getIamPolicy", "secretmanager.secrets.setIamPolicy",
)
MONITORING_PERMISSIONS = (
    "logging.notificationRules.create", "logging.notificationRules.delete",
    "monitoring.alertPolicies.create", "monitoring.alertPolicies.delete",
    "monitoring.alertPolicies.get", "monitoring.alertPolicies.list", "monitoring.alertPolicies.update",
    "monitoring.notificationChannels.get", "monitoring.notificationChannels.list",
)


def request(url, permissions, token):
    raw = json.dumps({"permissions": list(permissions)}).encode()
    req = urllib.request.Request(url, data=None if "storage.googleapis.com" in url else raw, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, method="GET" if "storage.googleapis.com" in url else "POST")
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.load(response)
    actual = payload.get("permissions", [])
    if not isinstance(actual, list) or not all(isinstance(value, str) for value in actual):
        raise RuntimeError("invalid testIamPermissions response")
    return sorted(set(permissions) - set(actual))


def checks(target):
    secret = (f"https://secretmanager.googleapis.com/v1/projects/{PROJECT}/secrets/{SECRET}:testIamPermissions", SECRET_PERMISSIONS)
    project_url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}:testIamPermissions"
    if target == "iam-bootstrap":
        return [(project_url, ("iam.roles.get", "iam.roles.create", "iam.roles.update", "resourcemanager.projects.getIamPolicy", "resourcemanager.projects.setIamPolicy"))]
    if target == "artifact-registry":
        return [(f"https://artifactregistry.googleapis.com/v1/projects/{PROJECT}/locations/{REGION}/repositories/shared-datasets-jobs:testIamPermissions", ("artifactregistry.repositories.getIamPolicy", "artifactregistry.repositories.setIamPolicy"))]
    if target == "monitoring-alerts":
        return [(project_url, MONITORING_PERMISSIONS)]
    if target == "preview-service-account-iam":
        return [(project_url, ("iam.serviceAccounts.create", "iam.serviceAccounts.get", "iam.serviceAccounts.getIamPolicy", "iam.serviceAccounts.setIamPolicy"))]
    if target == "bucket-iam":
        expected = ("storage.buckets.get", "storage.buckets.getIamPolicy", "storage.buckets.setIamPolicy")
        return [("https://storage.googleapis.com/storage/v1/b/skytruth-shared-datasets-1/iam/testPermissions?" + urlencode([("permissions", value) for value in expected]), expected)]
    if target in {"pmtiles-cdn", "pmtiles-cdn-bootstrap"}:
        expected = BUCKET_PERMISSIONS if target == "pmtiles-cdn" else ("storage.buckets.get", "storage.buckets.getIamPolicy", "storage.buckets.setIamPolicy", "storage.buckets.update")
        bucket = ("https://storage.googleapis.com/storage/v1/b/skytruth-shared-datasets-1/iam/testPermissions?" + urlencode([("permissions", value) for value in expected]), expected)
        return [bucket] if target == "pmtiles-cdn" else checks("iam-bootstrap") + [bucket]
    if target == "catalog-viewer":
        signing_secret = (f"https://secretmanager.googleapis.com/v1/projects/{PROJECT}/secrets/pmtiles-cdn-signed-request-key:testIamPermissions", SECRET_PERMISSIONS)
        return [(f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}:testIamPermissions", ("run.services.get", "run.services.update", "run.services.getIamPolicy", "run.services.setIamPolicy", "run.operations.get")), signing_secret]
    if target == "translation-bootstrap":
        return [secret]
    if target == "ingestion-iam":
        return checks("iam-bootstrap") + checks("bucket-iam") + [secret]
    project = (f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}:testIamPermissions", PROJECT_PERMISSIONS)
    return [project, secret] if target in {"wdpa-monthly", "eamlis-monthly"} else [project]


def verify(target, probe, *, attempts=7, pause=time.sleep):
    for attempt in range(attempts):
        missing = [(url, probe(url, permissions)) for url, permissions in checks(target)]
        missing = [(url, permissions) for url, permissions in missing if permissions]
        if not missing:
            return
        if attempt + 1 < attempts:
            pause(10)
    detail = "; ".join(f"{url}: {', '.join(permissions)}" for url, permissions in missing)
    raise RuntimeError(f"deployment identity is not ready after bounded propagation checks: {detail}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=["artifact-registry", "monitoring-alerts", "preview-service-account-iam", "bucket-iam", "ingestion-iam", "pmtiles-cdn-bootstrap", "pmtiles-cdn", "catalog-viewer", "iam-bootstrap", "translation-bootstrap", "eamlis-monthly", "wdpa-monthly", "sea-ice-daily", "wdpa-processing-validation"])
    args = parser.parse_args()
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    if not token:
        parser.error("authenticated deployment identity has no access token")
    verify(args.target, lambda url, permissions: request(url, permissions, token))
    print(f"{args.target}: deployment permission prerequisites verified.")


if __name__ == "__main__":
    main()
