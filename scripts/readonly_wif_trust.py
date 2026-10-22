"""One reviewed trust-condition repair, never general provider update authority.

The temporary project grant requires separate human approval. IAM does not offer
a provider permission-test endpoint or supported provider-ID condition. An exact
child GET and installed allow-grant proof do not defeat independent deny rules.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from scripts import setup_grant_proof as proof, wif_setup_permissions as wif

PROJECT = proof.PROJECT
PROVIDER_ID = "github-actions-readonly"
ADDRESS = "google_iam_workload_identity_pool_provider.github_readonly"
PROVIDER_URL = wif.POOL_URL + "/providers/" + PROVIDER_ID
UPDATE = "iam.workloadIdentityPoolProviders.update"
ROLE = f"projects/{PROJECT}/roles/sharedDatasetsReadonlyTrustUpdater"
EXPIRES = "2026-10-22T02:00:00Z"
WORKFLOWS = ("Catalog drift guard", "Bucket hygiene audit", "Dataset breaking change alert")
MAPPING = {"google.subject": "assertion.sub", **{"attribute." + name: "assertion." + name
           for name in ("actor", "repository", "repository_owner", "workflow")}}
ISSUER = "https://token.actions.githubusercontent.com"
DISPLAY = "GitHub Actions read-only"
DESCRIPTION = "Restricts GitHub OIDC tokens to read-only shared-datasets bucket checks."


def trust_condition(workflows=WORKFLOWS):
    return "assertion.repository == 'SkyTruth/shared-datasets-1' && (" + " || ".join(
        "assertion.workflow == '" + name + "'" for name in workflows) + ")"


def condition():
    return {"title": "readonly-trust-update-expires",
            "description": "Expire temporary read-only provider update authority at 2026-10-22 02:00 UTC.",
            "expression": "request.time < timestamp('" + EXPIRES + "')"}


def require(value, message):
    if not value:
        raise ValueError("READONLY_WIF_TRUST: " + message)


def known(value):
    if isinstance(value, dict):
        return all(known(item) for item in value.values())
    if isinstance(value, list):
        return all(known(item) for item in value)
    return value is False


def validate_plan_rows(rows):
    """Only the exact old→desired condition may change in this saved plan."""
    changed = [row for row in rows if row.get("mode", "managed") != "data"
               and row.get("change", {}).get("actions") not in ([], ["read"], ["no-op"])]
    require(len(changed) <= 1, "saved trust repair must change at most the one existing provider")
    if not changed:
        return
    row = changed[0]
    require(row.get("address") == ADDRESS and row.get("type") == "google_iam_workload_identity_pool_provider"
            and row.get("mode", "managed") == "managed", "saved trust repair targets another resource")
    change = row["change"]
    require(change.get("actions") == ["update"], "trust repair permits update only; no create, delete or replacement")
    before, after = change.get("before"), change.get("after")
    require(isinstance(before, dict) and isinstance(after, dict) and known(change.get("after_unknown", {})),
            "saved trust repair has unavailable or unknown provider values")
    require(before.get("attribute_condition") == trust_condition(WORKFLOWS[:2])
            and after.get("attribute_condition") == trust_condition(), "saved trust repair is not the exact reviewed workflow addition")
    require({key: value for key, value in before.items() if key != "attribute_condition"}
            == {key: value for key, value in after.items() if key != "attribute_condition"},
            "saved trust repair changes provider fields besides attribute_condition")
    require(after.get("workload_identity_pool_id") == "github"
            and after.get("workload_identity_pool_provider_id") == PROVIDER_ID,
            "saved trust repair identifies another pool or provider")


def verify_provider(url, permissions, token, read, *, now=None):
    require(url == PROVIDER_URL and set(permissions) in ({wif.GET}, {wif.GET, UPDATE}),
            "unsupported read-only trust resource or operation")
    name = wif.canonical_pool(read) + "/providers/" + PROVIDER_ID
    # Missing/deleted/unauthorized providers cannot enter a creation fallback.
    value = read("https://iam.googleapis.com/v1/" + name + "?" + urlencode({
        "fields": "name,state,disabled,displayName,description,attributeCondition,attributeMapping,oidc"}))
    require(value.get("name") == name and value.get("state") == "ACTIVE"
            and value.get("disabled", False) is False, "exact existing read-only provider is unavailable or disabled")
    oidc = value.get("oidc")
    require(value.get("attributeMapping") == MAPPING and isinstance(oidc, dict)
            and set(oidc) <= {"issuerUri", "allowedAudiences", "jwksJson"}
            and oidc.get("issuerUri") == ISSUER and oidc.get("allowedAudiences", []) == []
            and oidc.get("jwksJson", "") == "" and value.get("displayName") == DISPLAY
            and value.get("description") == DESCRIPTION, "live read-only provider differs from the unchanged reviewed configuration")
    if value.get("attributeCondition") == trust_condition():
        require(UPDATE not in permissions, "saved trust update plan is stale; the provider already has the desired condition")
        return []  # A later no-op never requires expired update authority.
    require(value.get("attributeCondition") == trust_condition(WORKFLOWS[:2]),
            "live read-only trust is not the exact reviewed predecessor")
    current = now if now is not None else datetime.now(timezone.utc)
    require(current.tzinfo is not None and current < datetime.fromisoformat(EXPIRES.replace("Z", "+00:00")),
            "temporary trust update authority has expired; new reviewed approval is required")
    account = proof.principal(token, read)
    proof.grants(account, [(wif.READER_ROLE, (wif.GET,), None), (ROLE, (UPDATE,), condition())], read)
    return []
