#!/usr/bin/env python3
"""Compare complete Monitoring routing with reviewed Terraform planned values.

The live mode performs authenticated GETs only. Findings are review evidence;
this command never repairs, disables, or sends a notification.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode
import urllib.request

PROJECT = "shared-datasets-1"
LEGACY_SCHEMA_ID = f"projects/{PROJECT}/alertPolicies/10109535963709095250"
LEGACY_SCHEMA_ADDRESS = "google_monitoring_alert_policy.dataset_schema_changed"
SECURITY_ADDRESSES = {
    "google_monitoring_alert_policy.dataset_object_deleted",
    "google_monitoring_alert_policy.dataset_object_written_by_unapproved_principal",
}
DETECTION_KEYS = {
    "condition_matched_log": {"filter", "label_extractors"},
    "condition_threshold": {"filter", "comparison", "threshold_value", "duration", "evaluation_missing_data", "aggregations", "trigger", "denominator_filter", "denominator_aggregations"},
    "condition_absent": {"filter", "duration", "aggregations", "trigger"},
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def filter_text(value):
    require(isinstance(value, str) and bool(value.strip()), "A complete detection filter is required")
    # Ignore formatting outside literals; whitespace inside quoted values is data.
    return re.sub(r'"(?:[^"\\]|\\.)*"|\s+', lambda match: "" if match[0].isspace() else match[0], value)


def snake(value):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", value).lower()


def canonical(value, key=""):
    if key == "filter" or key == "denominator_filter":
        return filter_text(value)
    if isinstance(value, dict):
        if key == "label_extractors":
            return dict(sorted(value.items()))
        result = {snake(name): canonical(item, snake(name)) for name, item in value.items() if item not in (None, "", [], {}) and not (key == "trigger" and item == 0)}
        return dict(sorted(result.items()))
    if isinstance(value, list):
        rows = [canonical(item) for item in value]
        if key == "trigger":
            require(len(rows) == 1, "A trigger must have one configuration")
            return canonical(rows[0], "trigger")
        return rows
    return value


def detection(condition, *, terraform):
    normalized = {snake(key): value for key, value in condition.items()}
    kinds = [kind for kind in DETECTION_KEYS if normalized.get(kind)]
    require(len(kinds) == 1, "Unsupported or incomplete Monitoring condition")
    kind = kinds[0]
    body = normalized[kind]
    if terraform:
        require(isinstance(body, list) and len(body) == 1, "Terraform condition must have one configuration")
        body = body[0]
    require(isinstance(body, dict), "Monitoring condition configuration must be an object")
    fields = {snake(key): value for key, value in body.items()}
    require(not any(key not in DETECTION_KEYS[kind] and value not in (None, "", [], {}, False, 0) for key, value in fields.items()), "Unsupported Monitoring detection field")
    fields = {key: fields[key] for key in DETECTION_KEYS[kind] if key in fields and fields[key] not in (None, "", [], {})}
    require("filter" in fields, "Monitoring condition has no filter")
    return {kind: canonical(fields)}


def resources(module):
    yield from module.get("resources", [])
    for child in module.get("child_modules", []):
        yield from resources(child)


def configured_policies(module, prefix=""):
    result = {prefix + row["address"] for row in module.get("resources", []) if row.get("type") == "google_monitoring_alert_policy"}
    for name, child in module.get("module_calls", {}).items():
        result.update(configured_policies(child.get("module", {}), prefix + "module." + name + "."))
    return result


def expected_policies(plan):
    require(isinstance(plan, dict) and isinstance(plan.get("planned_values", {}).get("root_module"), dict), "Use terraform show -json from a reviewed saved plan, including planned_values")
    configuration = plan.get("configuration", {}).get("root_module")
    require(isinstance(configuration, dict), "Reviewed plan must include its complete configuration")
    result = {}
    for row in resources(plan["planned_values"]["root_module"]):
        if row.get("type") != "google_monitoring_alert_policy":
            continue
        address, value = row["address"], row["values"]
        require(address not in result, "Duplicate Terraform policy address")
        require(value.get("project") == PROJECT and isinstance(value.get("enabled"), bool), "Policy project and enabled state must be resolved")
        require(isinstance(value.get("notification_channels"), list), "Policy channel list must be resolved")
        require(isinstance(value.get("display_name"), str) and value["display_name"], "Policy display name must be resolved")
        require(value.get("conditions"), "Policy conditions must be resolved")
        result[address] = value
    require(set(result) == configured_policies(configuration), "Reviewed plan omits declared Monitoring policies or has unresolved policy instances")
    require(SECURITY_ADDRESSES | {LEGACY_SCHEMA_ADDRESS} <= set(result), "Reviewed plan omits security policies or the retired schema route")
    require(all(result[address]["enabled"] and result[address]["notification_channels"] for address in SECURITY_ADDRESSES), "Reviewed plan must preserve independent security delivery")
    legacy = result[LEGACY_SCHEMA_ADDRESS]
    require((legacy.get("name") or legacy.get("id")) == LEGACY_SCHEMA_ID and legacy["enabled"] is False and legacy["notification_channels"] == [], "Reviewed plan must adopt and disable the exact legacy schema policy with no channels")
    return result


def inventory(payload, field):
    if isinstance(payload, dict):
        require(not payload.get("nextPageToken"), "Inventory is incomplete: pagination remains")
        payload = payload.get(field, [])
    require(isinstance(payload, list) and all(isinstance(row, dict) for row in payload), "Inventory must contain objects")
    names = [row.get("name") for row in payload]
    require(all(isinstance(name, str) and name.startswith(f"projects/{PROJECT}/") for name in names) and len(names) == len(set(names)), "Inventory contains duplicate or foreign resource identities")
    return payload


def audit(plan, policies, channels):
    expected = expected_policies(plan)
    policies = inventory(policies, "alertPolicies")
    channels = {row["name"]: row for row in inventory(channels, "notificationChannels")}
    findings, managed = [], set()

    def report(code, identity, detail):
        findings.append({"code": code, "identity": identity, "detail": detail})

    for address, desired in sorted(expected.items()):
        name = desired.get("name") or desired.get("id")
        matches = [row for row in policies if row["name"] == name] if name else [row for row in policies if row.get("displayName") == desired["display_name"]]
        if len(matches) != 1:
            report("missing_or_ambiguous_policy", address, "Expected exactly one live policy for the reviewed identity")
            continue
        actual = matches[0]
        require(actual["name"] not in managed, "Two Terraform addresses resolve to one live policy")
        managed.add(actual["name"])
        if actual.get("enabled", True) != desired["enabled"]:
            report("enabled_drift", address, "Live enabled state differs from the reviewed plan")
        if sorted(actual.get("notificationChannels", [])) != sorted(desired["notification_channels"]):
            report("channel_drift", address, "Live notification channels differ from the reviewed plan")
        desired_detection = [detection(row, terraform=True) for row in desired["conditions"]]
        actual_detection = [detection(row, terraform=False) for row in actual.get("conditions", [])]
        if sorted(map(json.dumps, desired_detection)) != sorted(map(json.dumps, actual_detection)) or actual.get("combiner") != desired.get("combiner"):
            report("detection_drift", address, "Live filters or condition configuration differ from the reviewed plan")
        if desired["enabled"]:
            for channel in desired["notification_channels"]:
                if channel not in channels or channels[channel].get("enabled", True) is not True:
                    report("missing_or_disabled_channel", address, "A reviewed notification channel is missing or disabled")
    for policy in policies:
        if policy["name"] in managed or not policy.get("enabled", True):
            continue
        slack = [name for name in policy.get("notificationChannels", []) if channels.get(name, {}).get("type") == "slack"]
        if slack:
            report("unexpected_enabled_slack_policy", policy["name"], "Unmanaged enabled policy has a Slack route; review ownership before changing it")
    return {"schema_version": 1, "project": PROJECT, "expected_policies": len(expected), "live_policies": len(policies), "findings": findings}


def live_inventory(field, token, *, open_url=urllib.request.urlopen):
    rows, seen, page = [], set(), ""
    while True:
        query = {"pageSize": 100}
        if page:
            query["pageToken"] = page
        request = urllib.request.Request(f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/{field}?" + urlencode(query), headers={"Authorization": f"Bearer {token}"}, method="GET")
        with open_url(request, timeout=30) as response:
            payload = json.load(response)
        require(isinstance(payload, dict) and isinstance(payload.get(field, []), list), "Malformed Monitoring enumeration")
        rows.extend(payload.get(field, []))
        page = payload.get("nextPageToken", "")
        require(isinstance(page, str) and page not in seen, "Invalid or repeated Monitoring pagination token")
        if not page:
            return inventory(rows, field)
        seen.add(page)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--terraform-json", required=True, type=Path, help="terraform show -json from the reviewed saved plan")
    parser.add_argument("--live", action="store_true", help="Read complete Monitoring inventories using the active gcloud identity")
    parser.add_argument("--policies-json", type=Path)
    parser.add_argument("--channels-json", type=Path)
    args = parser.parse_args(argv)
    if args.live == bool(args.policies_json or args.channels_json) or bool(args.policies_json) != bool(args.channels_json):
        parser.error("Choose --live or both --policies-json and --channels-json")
    try:
        plan = json.loads(args.terraform_json.read_text())
        expected_policies(plan)  # Reject incomplete review input before using credentials.
        if args.live:
            token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
            require(bool(token), "No active gcloud access token")
            policies, channels = live_inventory("alertPolicies", token), live_inventory("notificationChannels", token)
        else:
            policies, channels = json.loads(args.policies_json.read_text()), json.loads(args.channels_json.read_text())
        result = audit(plan, policies, channels)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Monitoring routing audit incomplete: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return int(bool(result["findings"]))


if __name__ == "__main__":
    raise SystemExit(main())
