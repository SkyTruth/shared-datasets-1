#!/usr/bin/env python3
"""Constrain protected metadata retirement to removals; preserve database bytes."""

from __future__ import annotations

import argparse
import copy
import json
import re


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_json")
    parser.add_argument("--database-only", choices=DATABASES)
    args = parser.parse_args()
    with open(args.plan_json) as handle:
        plan = json.load(handle)
        blocked = (
            blocked_database_changes(plan, args.database_only)
            if args.database_only
            else blocked_changes(plan)
        )
    if blocked:
        print("Refusing metadata retirement plan:")
        for item in blocked:
            print(f"- {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
