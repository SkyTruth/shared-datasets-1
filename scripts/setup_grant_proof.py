"""Strict read-only identity and installed allow-grant proof for protected setup.

This proves the reviewed allow grant, not independent deny-policy effects or
authorization of a later mutation. Actual supported API probes remain required.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import quote, urlencode

PROJECT = "shared-datasets-1"
TOKENINFO = "https://oauth2.googleapis.com/tokeninfo"
TOKEN_ENV = "LOGGING_SETUP_ACCESS_TOKEN"
TOKEN_SCOPES = ("https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/userinfo.email")


class ProbeHTTPError(RuntimeError):
    def __init__(self, message, *, code, status):
        super().__init__(message)
        self.code = code
        self.status = status


def require(value, message):
    if not value:
        raise RuntimeError("Protected setup grant proof: " + message)


def principal(token, read):
    """Bind the actual scoped token to the configured protected service account."""
    account_email = os.environ.get("TERRAFORM_SERVICE_ACCOUNT", "")
    require(re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]@" + PROJECT + r"\.iam\.gserviceaccount\.com", account_email), "configured protected service account is missing or invalid")
    name = f"projects/{PROJECT}/serviceAccounts/{account_email}"
    account = read("https://iam.googleapis.com/v1/" + quote(name, safe="/@") + "?" + urlencode({"fields": "name,email,uniqueId,disabled"}))
    unique_id = account.get("uniqueId")
    require(account.get("name") == name and account.get("email") == account_email
            and isinstance(unique_id, str) and re.fullmatch(r"[1-9][0-9]{5,31}", unique_id)
            and account.get("disabled", False) is False, "configured service-account metadata is inconsistent or disabled")
    # Audience also occurs on delegated user tokens. Verified email is mandatory
    # for this explicitly identity-scoped token; never infer it from aud/azp.
    identity = read(TOKENINFO, data=urlencode({"access_token": token}).encode(), method="POST", content_type="application/x-www-form-urlencoded", bearer=False)
    require(identity.get("aud") == unique_id, "access token does not identify the configured service account")
    expires = identity.get("expires_in")
    require(type(expires) is int and expires > 0 or isinstance(expires, str) and re.fullmatch(r"[1-9][0-9]*", expires), "access token is expired or has invalid expiry")
    require(identity.get("email") == account_email and (identity.get("email_verified") is True or identity.get("email_verified") == "true"), "identity-scoped token lacks the configured service account's verified email")
    scope = identity.get("scope")
    require(isinstance(scope, str) and set(TOKEN_SCOPES) <= set(scope.split()), "access token lacks the explicit operational identity scopes")
    return account_email


def grants(account_email, expected, read):
    """Each tuple binds one exact active role, permission set and condition."""
    for role_name, permissions, _ in expected:
        role = read("https://iam.googleapis.com/v1/" + role_name + "?" + urlencode({"fields": "name,stage,deleted,includedPermissions"}))
        actual = role.get("includedPermissions")
        require(role.get("name") == role_name and role.get("stage") == "GA" and role.get("deleted", False) is False
                and isinstance(actual, list) and all(isinstance(value, str) for value in actual)
                and len(actual) == len(permissions) and set(actual) == set(permissions), "installed custom role is missing, inactive or differs from the reviewed permissions")
    policy = read(f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}:getIamPolicy", method="POST", data=json.dumps({"options": {"requestedPolicyVersion": 3}}).encode())
    bindings = policy.get("bindings")
    require(policy.get("version") == 3 and isinstance(bindings, list)
            and all(isinstance(value, dict) and isinstance(value.get("role"), str)
                    and isinstance(value.get("members"), list) and all(isinstance(member, str) for member in value["members"]) for value in bindings), "live v3 project policy is unavailable or malformed")
    member = "serviceAccount:" + account_email
    for role_name, _, condition in expected:
        matching = [value for value in bindings if value["role"] == role_name and member in value["members"]]
        require(len(matching) == 1 and matching[0]["members"] == [member] and matching[0].get("condition") == condition,
                "live binding does not exactly match the protected principal and reviewed condition")
