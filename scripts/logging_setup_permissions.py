"""Read-only proof of the protected deployer's two-bucket Logging grant.

Logging exposes testIamPermissions only on views. Bucket get/list are proven by
their actual metadata APIs; creation is proven by the exact installed active
role and conditional binding. This verifies an allow grant, not all possible
deny policies or the authorization of a later mutation.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import quote, urlencode

PROJECT = "shared-datasets-1"
BUCKETS = ("_Default", "_Required")
PARENT_PERMISSIONS = ("logging.buckets.get", "logging.views.create", "logging.views.list")
VIEW_PERMISSIONS = ("logging.views.get", "logging.views.update", "logging.views.getIamPolicy", "logging.views.setIamPolicy")
SETUP_PERMISSIONS = PARENT_PERMISSIONS + VIEW_PERMISSIONS
ROLE = f"projects/{PROJECT}/roles/sharedDatasetsOperationalLogViewManager"
TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
TOKEN_ENV = "LOGGING_SETUP_ACCESS_TOKEN"
TOKEN_SCOPES = ("https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/userinfo.email")


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
    principal = os.environ.get("TERRAFORM_SERVICE_ACCOUNT", "")
    require(re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]@" + PROJECT + r"\.iam\.gserviceaccount\.com", principal), "configured protected service account is missing or invalid")
    account_name = f"projects/{PROJECT}/serviceAccounts/{principal}"
    account = read("https://iam.googleapis.com/v1/" + quote(account_name, safe="/@") + "?" + urlencode({"fields": "name,email,uniqueId,disabled"}))
    unique_id = account.get("uniqueId")
    require(account.get("name") == account_name and account.get("email") == principal
            and isinstance(unique_id, str) and re.fullmatch(r"[1-9][0-9]{5,31}", unique_id)
            and account.get("disabled", False) is False, "configured service-account metadata is inconsistent or disabled")
    identity = read(TOKENINFO, data=urlencode({"access_token": token}).encode(), method="POST", content_type="application/x-www-form-urlencoded", bearer=False)
    require(identity.get("aud") == unique_id, "access token does not identify the configured service account")
    expires = identity.get("expires_in")
    require(type(expires) is int and expires > 0 or isinstance(expires, str) and re.fullmatch(r"[1-9][0-9]*", expires), "access token is expired or has invalid expiry")
    require(identity.get("email") == principal and (identity.get("email_verified") is True or identity.get("email_verified") == "true"), "identity-scoped token lacks the configured service account's verified email")
    scope = identity.get("scope")
    require(isinstance(scope, str) and set(TOKEN_SCOPES) <= set(scope.split()), "access token lacks the explicit operational identity scopes")

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

    role = read("https://iam.googleapis.com/v1/" + ROLE + "?" + urlencode({"fields": "name,stage,deleted,includedPermissions"}))
    actual = role.get("includedPermissions")
    require(role.get("name") == ROLE and role.get("stage") == "GA" and role.get("deleted", False) is False
            and isinstance(actual, list) and all(isinstance(value, str) for value in actual)
            and len(actual) == len(SETUP_PERMISSIONS) and set(actual) == set(SETUP_PERMISSIONS), "installed custom role is missing, inactive or differs from the reviewed permissions")
    policy = read(f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}:getIamPolicy", method="POST", data=json.dumps({"options": {"requestedPolicyVersion": 3}}).encode())
    bindings = policy.get("bindings")
    require(policy.get("version") == 3 and isinstance(bindings, list)
            and all(isinstance(value, dict) and isinstance(value.get("role"), str)
                    and isinstance(value.get("members"), list) and all(isinstance(member, str) for member in value["members"]) for value in bindings), "live v3 project policy is unavailable or malformed")
    member = "serviceAccount:" + principal
    matching = [value for value in bindings if value["role"] == ROLE and member in value["members"]]
    require(len(matching) == 1 and matching[0]["members"] == [member] and matching[0].get("condition") == condition(), "live binding does not exactly match the protected principal and two-bucket condition")
    return []
