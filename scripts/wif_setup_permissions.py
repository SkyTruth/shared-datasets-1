"""Exact-child WIF metadata readiness and explicitly temporary create authority.

Google does not expose provider testIamPermissions or supported IAM resource-name
conditions for this service. Project grants are therefore stated honestly;
the reviewed protected target/plan allowlist limits the actual mutation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from urllib.parse import urlencode

from scripts import setup_grant_proof as proof

PROJECT = proof.PROJECT
POOL = f"projects/{PROJECT}/locations/global/workloadIdentityPools/github"
POOL_URL = "https://iam.googleapis.com/v1/" + POOL
PROVIDER_ID = "github-operational-observer"
PROVIDER_URL = POOL_URL + "/providers/" + PROVIDER_ID
CREATE = "iam.workloadIdentityPoolProviders.create"
GET = "iam.workloadIdentityPoolProviders.get"
CREATOR_ROLE = f"projects/{PROJECT}/roles/sharedDatasetsOperationalProviderCreator"
READER_ROLE = f"projects/{PROJECT}/roles/sharedDatasetsOperationalProviderReader"
EXPIRES = "2026-10-22T02:00:00Z"


def condition():
    return {"title": "operational-provider-create-expires",
            "description": "Expire temporary provider creation authority at 2026-10-22 02:00 UTC.",
            "expression": "request.time < timestamp('" + EXPIRES + "')"}


def verify_provider(url, permissions, token, read, *, now=None):
    proof.require(url == PROVIDER_URL and set(permissions) in ({GET}, {GET, CREATE}), "unsupported operational provider resource or operation")
    project = read(f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}?" + urlencode({"fields": "projectId,projectNumber"}))
    number = project.get("projectNumber")
    proof.require(project.get("projectId") == PROJECT and isinstance(number, str) and re.fullmatch(r"[1-9][0-9]*", number), "provider project metadata is inconsistent")
    canonical_pool = f"projects/{number}/locations/global/workloadIdentityPools/github"
    pool = read(POOL_URL + "?" + urlencode({"fields": "name,state,disabled"}))
    proof.require(pool.get("name") == canonical_pool and pool.get("state") == "ACTIVE" and not pool.get("disabled", False), "existing GitHub pool metadata is inconsistent or disabled")
    provider_name = canonical_pool + "/providers/" + PROVIDER_ID
    try:
        provider = read("https://iam.googleapis.com/v1/" + provider_name + "?" + urlencode({"fields": "name,state,disabled"}))
    except proof.ProbeHTTPError as error:
        if error.code != 404 or error.status != "NOT_FOUND":
            raise
    else:
        proof.require(provider.get("name") == provider_name and provider.get("state") == "ACTIVE", "provider metadata identifies another or deleted resource")
        proof.require(CREATE not in permissions, "saved provider create plan is stale; the exact provider now exists")
        return []  # Existing resources never require expired creation authority.

    current = now if now is not None else datetime.now(timezone.utc)
    proof.require(current.tzinfo is not None and current < datetime.fromisoformat(EXPIRES.replace("Z", "+00:00")), "temporary provider creation authority has expired; a new reviewed approval is required")
    account = proof.principal(token, read)
    proof.grants(account, [(READER_ROLE, (GET,), None), (CREATOR_ROLE, (CREATE,), condition())], read)
    # Creation is authorized on the existing parent. This does not claim that a
    # permission test on the parent proves conditional access to a child.
    actual = read(POOL_URL + ":testIamPermissions", method="POST", data=json.dumps({"permissions": [CREATE]}).encode()).get("permissions", [])
    proof.require(isinstance(actual, list) and all(isinstance(value, str) for value in actual), "invalid parent create permission response")
    return sorted({CREATE} - set(actual))
