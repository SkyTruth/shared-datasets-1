#!/usr/bin/env python3
"""Constrain protected metadata retirement to removals; preserve database bytes."""

from __future__ import annotations

import argparse
import copy
import json
import re
import subprocess
import time
import urllib.request


# Immutable IDs observed in the reviewed retirement, not replaceable email names.
DELETE_ACCOUNT_IDENTITIES = {
    "module.metadata_index_loader_service_account.google_service_account.this": {
        "project": "shared-datasets-1",
        "account_id": "metadata-index-loader",
        "email": "metadata-index-loader@shared-datasets-1.iam.gserviceaccount.com",
        "unique_id": "117696104962177306505",
    },
    "module.preview_metadata_index_loader_service_account.google_service_account.this": {
        "project": "shared-datasets-1",
        "account_id": "metadata-index-loader-preview",
        "email": "metadata-index-loader-preview@shared-datasets-1.iam.gserviceaccount.com",
        "unique_id": "104364831142635248810",
    },
    "module.preview_metadata_service_account.google_service_account.this": {
        "project": "shared-datasets-1",
        "account_id": "metadata-service-preview",
        "email": "metadata-service-preview@shared-datasets-1.iam.gserviceaccount.com",
        "unique_id": "115924014602363410114",
    },
}
DELETE_ACCOUNT_IDS = frozenset(
    identity["unique_id"] for identity in DELETE_ACCOUNT_IDENTITIES.values()
)
DELETE_ACCOUNT_EMAILS = {
    identity["unique_id"]: identity["email"]
    for identity in DELETE_ACCOUNT_IDENTITIES.values()
}
DELETE_ROLE = "roles/iam.serviceAccountDeleter"
TERRAFORM_MEMBER = (
    "serviceAccount:shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com"
)


# Older preview identities survived a prior rename in production state.
# Require their observed identity as well as their address before removal.
LEGACY_DELETE_IDENTITIES = {
    **{
        address: identity
        for address, identity in DELETE_ACCOUNT_IDENTITIES.items()
        if ".preview_metadata_" in address
    },
    "google_project_iam_member.preview_metadata_index_loader_firestore_user": {
        "project": "shared-datasets-1",
        "role": "roles/datastore.user",
        "member": "serviceAccount:metadata-index-loader-preview@shared-datasets-1.iam.gserviceaccount.com",
        "condition": [
            {
                "title": "preview_firestore_write",
                "description": "Limit preview loader writes to the preview Firestore database.",
                "expression": "resource.name == 'projects/shared-datasets-1/databases/feature-metadata-preview' || resource.name.startsWith('projects/shared-datasets-1/databases/feature-metadata-preview/')",
            }
        ],
    },
    "google_project_iam_member.preview_metadata_service_firestore_viewer": {
        "project": "shared-datasets-1",
        "role": "roles/datastore.viewer",
        "member": "serviceAccount:metadata-service-preview@shared-datasets-1.iam.gserviceaccount.com",
        "condition": [
            {
                "title": "preview_firestore_read",
                "description": "Limit preview service reads to the preview Firestore database.",
                "expression": "resource.name == 'projects/shared-datasets-1/databases/feature-metadata-preview' || resource.name.startsWith('projects/shared-datasets-1/databases/feature-metadata-preview/')",
            }
        ],
    },
    "google_service_account_iam_member.preview_metadata_index_loader_github_wif": {
        "service_account_id": "projects/shared-datasets-1/serviceAccounts/metadata-index-loader-preview@shared-datasets-1.iam.gserviceaccount.com",
        "role": "roles/iam.workloadIdentityUser",
        "member": "principal://iam.googleapis.com/projects/12695949518/locations/global/workloadIdentityPools/github/subject/repo:SkyTruth/shared-datasets-1:environment:shared-datasets-production",
    },
}


DELETE_ADDRESSES = frozenset(
    {
        "module.metadata_service_account.google_service_account.this",
        "module.metadata_index_loader_service_account.google_service_account.this",
        "google_cloud_run_v2_service.metadata_service",
        "google_cloud_run_v2_service_iam_member.metadata_service_iap_invoker",
        "google_project_iam_member.metadata_service_firestore_viewer",
        "google_project_iam_member.metadata_index_loader_firestore_user",
        "google_storage_bucket_iam_member.metadata_service_object_viewer",
        "google_storage_bucket_iam_member.metadata_index_loader_object_viewer",
        "google_storage_bucket_iam_member.metadata_index_loader_index_load_creator",
        "google_storage_bucket_iam_member.metadata_index_loader_index_load_folder_admin",
        "google_service_account_iam_member.metadata_index_loader_github_wif",
        "google_monitoring_alert_policy.metadata_service_error_logs",
        "google_project_iam_member.feature_preview_service_firestore_viewer",
        "google_project_iam_member.feature_preview_loader_firestore_user",
    }
    | LEGACY_DELETE_IDENTITIES.keys()
)
UPDATE_ADDRESSES = frozenset(
    {
        "google_project_iam_custom_role.preview_terraform",
        "google_monitoring_alert_policy.dataset_object_written_by_unapproved_principal",
    }
)
ACCESSOR_RE = re.compile(
    r'^google_iap_web_cloud_run_service_iam_member\.metadata_service_accessors\["[^"\n]+"\]$'
)
DATABASES = {
    "production": ("google_firestore_database.feature_metadata", "(default)"),
    "preview": ("google_firestore_database.feature_preview", "feature-preview"),
}


def removes_only_loader_alert_exemption(before: dict, after: dict) -> bool:
    expected = copy.deepcopy(before)
    matched_log = expected["conditions"][0]["condition_matched_log"][0]
    clause = ' AND protoPayload.authenticationInfo.principalEmail!="metadata-index-loader@shared-datasets-1.iam.gserviceaccount.com"'
    if clause not in matched_log["filter"]:
        return False
    matched_log["filter"] = matched_log["filter"].replace(clause, "", 1)
    return after == expected


def blocked_database_changes(plan: dict, database: str) -> list[str]:
    """Detach only the named database; never mutate a live resource."""
    database_address, database_name = DATABASES[database]
    blocked = []
    for resource in plan.get("resource_changes", []):
        address = resource["address"]
        change = resource["change"]
        actions = change["actions"]
        if address == database_address:
            if actions == ["forget"] and change["before"]["name"] == database_name:
                continue
        elif actions in (["no-op"], ["read"]):
            continue
        blocked.append(f"{'/'.join(actions)} {address}")
    return blocked


def blocked_changes(plan: dict) -> list[str]:
    blocked = []
    for resource in plan.get("resource_changes", []):
        change = resource["change"]
        actions = change["actions"]
        if actions in (["no-op"], ["read"]):
            continue
        address = resource["address"]
        if address in DELETE_ADDRESSES or ACCESSOR_RE.fullmatch(address):
            if actions == ["delete"]:
                identity = DELETE_ACCOUNT_IDENTITIES.get(
                    address, LEGACY_DELETE_IDENTITIES.get(address, {})
                )
                if any(
                    change["before"].get(key) != value
                    for key, value in identity.items()
                ):
                    blocked.append(f"unexpected metadata identity {address}")
                    continue
                continue
        elif address in UPDATE_ADDRESSES and actions == ["update"]:
            if address == "google_project_iam_custom_role.preview_terraform":
                before = set(change["before"]["permissions"])
                after = set(change["after"]["permissions"])
                expected = {
                    **change["before"],
                    "permissions": change["after"]["permissions"],
                }
                if (
                    not after <= before
                    or any(not p.startswith("datastore.") for p in before - after)
                    or change["after"] != expected
                ):
                    blocked.append(
                        f"unexpected preview role permission change {address}"
                    )
                    continue
            elif not removes_only_loader_alert_exemption(
                change["before"], change["after"]
            ):
                blocked.append(f"unexpected canonical write alert change {address}")
                continue
            continue
        blocked.append(f"{'/'.join(actions)} {address}")
    return blocked


def retirement_account_ids(plan: dict) -> list[str]:
    """Derive temporary IAM authority only from a validated retirement plan."""
    blocked = blocked_changes(plan)
    if blocked:
        raise ValueError("Refusing metadata retirement plan: " + "; ".join(blocked))
    return sorted(
        resource["change"]["before"]["unique_id"]
        for resource in plan.get("resource_changes", [])
        if resource["address"] in DELETE_ACCOUNT_IDENTITIES
        and resource["change"]["actions"] == ["delete"]
    )


def blocked_delete_iam_changes(plan: dict, allowed_creates: set[str]) -> list[str]:
    """Allow temporary deletion grants on only the three reviewed identities."""
    blocked = []
    for resource in plan.get("resource_changes", []):
        change = resource["change"]
        actions = change["actions"]
        if actions in (["no-op"], ["read"]):
            continue
        address = resource["address"]
        for account_id in DELETE_ACCOUNT_IDS:
            if (
                address
                == f'google_service_account_iam_member.retirement_deleter["{account_id}"]'
            ):
                break
        else:
            blocked.append(f"{'/'.join(actions)} {address}")
            continue
        if actions != ["delete"] and not (
            actions == ["create"] and account_id in allowed_creates
        ):
            blocked.append(f"unexpected temporary deletion grant {address}")
            continue
        values = change["before"] if actions == ["delete"] else change["after"]
        expected = {
            "service_account_id": f"projects/shared-datasets-1/serviceAccounts/{DELETE_ACCOUNT_EMAILS[account_id]}",
            "role": DELETE_ROLE,
            "member": TERRAFORM_MEMBER,
        }
        if any(
            values.get(key) != value for key, value in expected.items()
        ) or values.get("condition"):
            blocked.append(f"unexpected temporary deletion grant {address}")
    return blocked


def test_delete_permission(account_id: str, token: str) -> bool:
    request = urllib.request.Request(
        f"https://iam.googleapis.com/v1/projects/shared-datasets-1/serviceAccounts/{account_id}:testIamPermissions",
        data=json.dumps({"permissions": ["iam.serviceAccounts.delete"]}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response).get("permissions", []) == [
            "iam.serviceAccounts.delete"
        ]


def wait_for_delete_permissions(plan: dict) -> None:
    """Wait for external IAM propagation; never retry a failed deletion apply."""
    pending = set(retirement_account_ids(plan))
    if not pending:
        return
    token = subprocess.check_output(
        ["gcloud", "auth", "print-access-token"], text=True
    ).strip()
    deadline = time.monotonic() + 300
    while pending:
        pending = {
            account_id
            for account_id in pending
            if not test_delete_permission(account_id, token)
        }
        if not pending:
            return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                "Deletion permission did not propagate for: "
                + ", ".join(sorted(pending))
            )
        time.sleep(min(10, remaining))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--database-only", choices=DATABASES)
    mode.add_argument("--service-account-ids", action="store_true")
    mode.add_argument("--delete-iam-grants-from", metavar="RETIREMENT_PLAN_JSON")
    mode.add_argument("--delete-iam-cleanup", action="store_true")
    mode.add_argument("--wait-for-delete-permission", action="store_true")
    args = parser.parse_args()
    with open(args.plan_json) as handle:
        plan = json.load(handle)
    if args.service_account_ids:
        print(json.dumps({"retirement_account_ids": retirement_account_ids(plan)}))
        return 0
    if args.wait_for_delete_permission:
        wait_for_delete_permissions(plan)
        return 0
    if args.database_only:
        blocked = blocked_database_changes(plan, args.database_only)
    elif args.delete_iam_grants_from:
        with open(args.delete_iam_grants_from) as handle:
            allowed_creates = set(retirement_account_ids(json.load(handle)))
        blocked = blocked_delete_iam_changes(plan, allowed_creates)
    elif args.delete_iam_cleanup:
        blocked = blocked_delete_iam_changes(plan, set())
    else:
        blocked = blocked_changes(plan)
    if blocked:
        print("Refusing metadata retirement plan:")
        for item in blocked:
            print(f"- {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
