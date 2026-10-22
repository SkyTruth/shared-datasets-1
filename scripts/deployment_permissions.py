#!/usr/bin/env python3
"""Read-only permission probes for the authenticated protected deployer.

Only trusted main runs this code. A grant declared in Terraform is not live
readiness. Safe permission reads may retry for propagation; mutations never do.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote_plus, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.terraform_plan_permissions import plan_checks, probe_call_checks
from scripts import logging_setup_permissions as logging_setup
from scripts import readonly_wif_trust, setup_grant_proof, wif_setup_permissions as wif_setup

PROJECT = "shared-datasets-1"
REGION = "us-central1"
SECRET = "shared-datasets-slack-webhook-url"
IMAGE_REPOSITORY = f"projects/{PROJECT}/locations/{REGION}/repositories/shared-datasets-jobs"
# Docker pushes write image versions and SHA tags; retained-image promotion
# and digest inspection also read images. All are scoped to the existing
# repository, with no repository creation/deletion or IAM policy authority.
# https://docs.cloud.google.com/artifact-registry/docs/docker/pushing-and-pulling
# https://docs.cloud.google.com/iam/docs/roles-permissions/artifactregistry#artifactregistry.writer
IMAGE_PERMISSIONS = (
    "artifactregistry.repositories.get", "artifactregistry.repositories.downloadArtifacts",
    "artifactregistry.repositories.uploadArtifacts", "artifactregistry.dockerimages.get",
    "artifactregistry.tags.get", "artifactregistry.tags.create", "artifactregistry.tags.update",
)
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
    "monitoring.timeSeries.list",
)
OPERATIONAL_LOG_SETUP_PERMISSIONS = logging_setup.SETUP_PERMISSIONS
OPERATIONAL_METRIC_SETUP_PERMISSIONS = (
    "monitoring.metricDescriptors.create", "monitoring.metricDescriptors.get", "monitoring.metricDescriptors.list",
)


def read_json(url, token, *, data=None, method="GET", content_type="application/json", bearer=True):
    headers = {"Content-Type": content_type}
    if bearer:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise RuntimeError("permission probe JSON exceeds 1 MiB")
            payload = json.loads(body)
    except urllib.error.HTTPError as error:
        # Capture structured Google error diagnostics, never request headers or
        # an incomplete body that might contain a truncated credential value.
        body = error.read(16 * 1024 + 1)
        diagnostic = 'response body unavailable, unstructured or over 16 KiB'
        status = None
        if len(body) <= 16 * 1024:
            try:
                vendor = json.loads(body)
            except (ValueError, UnicodeDecodeError):
                vendor = None
            if isinstance(vendor, dict) and isinstance(vendor.get('error'), dict):
                status = vendor['error'].get('status')
                safe = {key: vendor['error'][key] for key in ('code', 'status', 'message', 'details') if key in vendor['error']}
                encoded_token = json.dumps(token, ensure_ascii=True)[1:-1]
                diagnostic = json.dumps(safe, ensure_ascii=True).replace(encoded_token, '[REDACTED]').replace(quote_plus(token), '[REDACTED]')[:4096]
        raise setup_grant_proof.ProbeHTTPError(f'permission probe HTTP {error.code}: {url}; {diagnostic}', code=error.code, status=status) from error
    if not isinstance(payload, dict):
        raise RuntimeError("invalid permission probe JSON response")
    return payload


def request(url, permissions, token):
    if url in {logging_setup.bucket_url(bucket) for bucket in logging_setup.BUCKETS}:
        return logging_setup.verify_parent(url, permissions, token, lambda url, **kwargs: read_json(url, token, **kwargs))
    if url == wif_setup.PROVIDER_URL:
        return wif_setup.verify_provider(url, permissions, token, lambda url, **kwargs: read_json(url, token, **kwargs))
    if url == readonly_wif_trust.PROVIDER_URL:
        return readonly_wif_trust.verify_provider(url, permissions, token, lambda url, **kwargs: read_json(url, token, **kwargs))
    storage = "storage.googleapis.com" in url
    raw = None if storage else json.dumps({"permissions": list(permissions)}).encode()
    payload = read_json(url, token, data=raw, method="GET" if storage else "POST")
    actual = payload.get("permissions", [])
    if not isinstance(actual, list) or not all(isinstance(value, str) for value in actual):
        raise RuntimeError("invalid testIamPermissions response")
    return sorted(set(permissions) - set(actual))


def checks(target):
    secret = (f"https://secretmanager.googleapis.com/v1/projects/{PROJECT}/secrets/{SECRET}:testIamPermissions", SECRET_PERMISSIONS)
    project_url = f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}:testIamPermissions"
    if target == "readonly-wif-trust":
        return [(readonly_wif_trust.PROVIDER_URL, (wif_setup.GET,))]
    if target == "iam-bootstrap":
        return [(project_url, ("iam.roles.get", "iam.roles.create", "iam.roles.update", "resourcemanager.projects.getIamPolicy", "resourcemanager.projects.setIamPolicy"))]
    if target == "artifact-registry":
        return [(f"https://artifactregistry.googleapis.com/v1/{IMAGE_REPOSITORY}:testIamPermissions", ("artifactregistry.repositories.getIamPolicy", "artifactregistry.repositories.setIamPolicy"))]
    if target == "artifact-registry-images":
        return [(f"https://artifactregistry.googleapis.com/v1/{IMAGE_REPOSITORY}:testIamPermissions", IMAGE_PERMISSIONS)]
    if target == "monitoring-alerts":
        return [(project_url, MONITORING_PERMISSIONS),
                *[(f"https://logging.googleapis.com/v2/projects/{PROJECT}/locations/global/buckets/{bucket}/views/shared-datasets-operations:testIamPermissions", ("logging.views.get",)) for bucket in ("_Default", "_Required")]]
    if target == "operational-observer-setup":
        pool = f"projects/{PROJECT}/locations/global/workloadIdentityPools/github"
        return checks("iam-bootstrap") + checks("bucket-iam") + [
            (project_url, OPERATIONAL_METRIC_SETUP_PERMISSIONS + ("iam.serviceAccounts.create", "iam.serviceAccounts.get", "serviceusage.services.list")),
            (f"https://iam.googleapis.com/v1/{pool}:testIamPermissions", ("iam.workloadIdentityPools.get", "iam.workloadIdentityPools.getAttestationRules")),
            (wif_setup.PROVIDER_URL, (wif_setup.GET,)),
            *[(logging_setup.bucket_url(bucket), logging_setup.PARENT_PERMISSIONS) for bucket in logging_setup.BUCKETS],
            *[(logging_setup.bucket_url(bucket) + "/views/_AllLogs:testIamPermissions", logging_setup.VIEW_PERMISSIONS) for bucket in logging_setup.BUCKETS],
        ]
    if target == "preview-service-account-iam":
        # The preview WIF binding references the managed GitHub pool, while
        # its signing binding references a managed custom role. Terraform
        # refreshes both and their enabled-service dependencies before planning.
        # The locked Google provider also lists pool attestation rules and
        # enabled services unconditionally, even when only IAM members target
        # the pool. Keep the complete read closure bound in the provider fixture.
        # Test the pool on its actual resource so conditional authority cannot
        # pass merely because a project-wide permission hint succeeded.
        # https://docs.cloud.google.com/iam/docs/reference/rest/v1/projects.locations.workloadIdentityPools/testIamPermissions
        pool = f"projects/{PROJECT}/locations/global/workloadIdentityPools/github"
        return [
            (project_url, ("iam.serviceAccounts.create", "iam.serviceAccounts.get", "iam.serviceAccounts.getIamPolicy", "iam.serviceAccounts.setIamPolicy", "iam.roles.get", "resourcemanager.projects.get", "serviceusage.services.list")),
            (f"https://iam.googleapis.com/v1/{pool}:testIamPermissions", ("iam.workloadIdentityPools.get", "iam.workloadIdentityPools.getAttestationRules")),
        ]
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


def verify_checks(required, probe, *, attempts=7, pause=time.sleep):
    # Calling Compute testIamPermissions without its list prerequisite returns
    # 403 before it can report the actual resource-operation permissions. Keep
    # these phases ordered; success in the first phase cannot satisfy the second.
    for phase, checks in (("permission-probe call", probe_call_checks(required)), ("resource operation", required)):
        for attempt in range(attempts):
            missing = [(url, probe(url, permissions)) for url, permissions in checks]
            missing = [(url, permissions) for url, permissions in missing if permissions]
            if not missing:
                break
            if attempt + 1 < attempts:
                pause(10)
        else:
            detail = "; ".join(f"{url}: {', '.join(permissions)}" for url, permissions in missing)
            raise RuntimeError(f"deployment identity is not ready for {phase} after bounded propagation checks: {detail}")


def verify(target, probe, *, attempts=7, pause=time.sleep):
    verify_checks(checks(target), probe, attempts=attempts, pause=pause)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=["artifact-registry", "artifact-registry-images", "monitoring-alerts", "operational-observer-setup", "readonly-wif-trust", "preview-service-account-iam", "bucket-iam", "ingestion-iam", "pmtiles-cdn-bootstrap", "pmtiles-cdn", "catalog-viewer", "iam-bootstrap", "translation-bootstrap", "mpatlas-monthly", "eamlis-monthly", "wdpa-monthly", "sea-ice-daily", "dataset-usage"])
    parser.add_argument("--plan-json", type=Path, help="JSON from the exact saved, allowlisted plan to be applied")
    args = parser.parse_args()
    if not (args.target or args.plan_json):
        parser.error("--target or --plan-json is required")
    if args.target == "artifact-registry-images" and args.plan_json:
        parser.error("image operations require the repository permission probe, not Terraform plan permissions")
    if args.plan_json:
        plan = json.loads(args.plan_json.read_text())
        project_number = subprocess.check_output(["gcloud", "projects", "describe", PROJECT, "--format=value(projectNumber)"], text=True).strip()
        required = plan_checks(plan, project_number=project_number, target=args.target)
    else:
        required = checks(args.target)
    logging_parent = any(url in {logging_setup.bucket_url(bucket) for bucket in logging_setup.BUCKETS}
                         | {wif_setup.PROVIDER_URL, readonly_wif_trust.PROVIDER_URL} for url, _ in required)
    if logging_parent:
        # Minted by the same protected WIF auth step with identity metadata
        # scope; an email-less gcloud token cannot prove the actual principal.
        token = os.environ.get(logging_setup.TOKEN_ENV, "").strip()
        if not token:
            parser.error("Logging parent permission checks require the protected identity-scoped access token")
    else:
        token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    if not token:
        parser.error("authenticated deployment identity has no access token")
    verify_checks(required, lambda url, permissions: request(url, permissions, token))
    if args.plan_json:
        print("Saved-plan mutation permissions and declared post-apply operations verified.")
    else:
        print(f"{args.target}: deployment permission prerequisites verified.")


if __name__ == "__main__":
    main()
