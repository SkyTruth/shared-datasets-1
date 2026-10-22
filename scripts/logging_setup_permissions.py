"""Read-only proof of the protected deployer's two-bucket Logging grant.

Logging exposes testIamPermissions only on views. Bucket get/list are proven by
their actual metadata APIs; creation is proven by the exact installed active
role and conditional binding. This verifies an allow grant, not all possible
deny policies or the authorization of a later mutation.
"""
from __future__ import annotations

import re
from urllib.parse import urlencode

from scripts import setup_grant_proof as proof
from scripts.operational_log_views import BUCKETS

PROJECT = "shared-datasets-1"
PARENT_PERMISSIONS = ("logging.buckets.get", "logging.views.create", "logging.views.list")
VIEW_PERMISSIONS = ("logging.views.get", "logging.views.update", "logging.views.getIamPolicy", "logging.views.setIamPolicy")
SETUP_PERMISSIONS = PARENT_PERMISSIONS + VIEW_PERMISSIONS
ROLE = f"projects/{PROJECT}/roles/sharedDatasetsOperationalLogViewManager"
TOKEN_ENV = proof.TOKEN_ENV
TOKEN_SCOPES = proof.TOKEN_SCOPES


def bucket_url(bucket):
    if bucket not in BUCKETS:
        raise ValueError("unsupported operational Logging bucket")
    return f"https://logging.googleapis.com/v2/projects/{PROJECT}/locations/global/buckets/{bucket}"


def condition():
    """Exact expression rendered by operational_observer.tf's two-bucket loop."""
    return {
        "title": "operational-view-buckets-only",
        "description": "No access to views in other Logging buckets.",
        "expression": " || ".join(
            f"(resource.name == 'projects/{PROJECT}/locations/global/buckets/{bucket}' || "
            f"resource.name.startsWith('projects/{PROJECT}/locations/global/buckets/{bucket}/views/'))"
            for bucket in BUCKETS
        ),
    }


def require(value, message):
    if not value:
        raise RuntimeError("Logging setup grant proof: " + message)


def verify_parent(url, permissions, token, read):
    """Use supported APIs without pretending a project test covers bucket IAM.

    ``read`` constructs authenticated, bounded JSON HTTP requests. OAuth's token
    is sent only in the POST body, with no bearer header or query parameter.
    The explicit identity-scoped token must identify the configured service
    account by verified email as well as IAM uniqueId. Audience alone also
    appears on delegated user tokens and cannot establish the principal.
    https://docs.cloud.google.com/docs/authentication/token-types
    """
    require(url in {bucket_url(bucket) for bucket in BUCKETS}, "unexpected bucket resource")
    require(permissions and set(permissions) <= set(PARENT_PERMISSIONS), "unexpected bucket permissions")
    principal = proof.principal(token, read)

    resource = url.removeprefix("https://logging.googleapis.com/v2/")
    require(read(url + "?" + urlencode({"fields": "name"})).get("name") == resource, "bucket metadata identifies another resource")
    page_token, seen_tokens, seen_views = None, set(), set()
    for _ in range(100):
        query = {"fields": "views(name),nextPageToken", "pageSize": "100"}
        if page_token is not None:
            query["pageToken"] = page_token
        page = read(url + "/views?" + urlencode(query))
        views = page.get("views", [])
        require(isinstance(views, list), "view enumeration is malformed")
        for view in views:
            name = view.get("name") if isinstance(view, dict) else None
            require(isinstance(name, str) and re.fullmatch(re.escape(resource) + r"/views/[A-Za-z0-9_-]{1,100}", name)
                    and name not in seen_views, "view enumeration has duplicate or foreign identities")
            seen_views.add(name)
        page_token = page.get("nextPageToken")
        if page_token is None or page_token == "":
            break
        require(isinstance(page_token, str) and len(page_token) <= 4096 and page_token not in seen_tokens, "view enumeration has an invalid or repeated page token")
        seen_tokens.add(page_token)
    else:
        raise RuntimeError("Logging setup grant proof: view enumeration exceeded its complete-page bound")

    proof.grants(principal, [(ROLE, SETUP_PERMISSIONS, condition())], read)
    return []
