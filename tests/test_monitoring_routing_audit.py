from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import monitoring_routing_audit as routing

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = f"projects/{routing.PROJECT}/notificationChannels/123"


def fixture():
    desired, live = [], []
    for index, address in enumerate(sorted(routing.SECURITY_ADDRESSES | {routing.LEGACY_SCHEMA_ADDRESS})):
        retired = address == routing.LEGACY_SCHEMA_ADDRESS
        name = routing.LEGACY_SCHEMA_ID if retired else f"projects/{routing.PROJECT}/alertPolicies/{index}"
        log_filter = 'resource.type="global"\njsonPayload.alert_type="dataset_schema_changed"' if retired else 'severity>=ERROR\nresource.type="gcs_bucket"'
        desired.append({"type": "google_monitoring_alert_policy", "address": address, "values": {
            "project": routing.PROJECT, "name": name, "display_name": address, "enabled": not retired,
            "combiner": "OR", "notification_channels": [] if retired else [CHANNEL],
            "conditions": [{"display_name": "expected", "condition_matched_log": [{"filter": log_filter, "label_extractors": {"object_resource": "EXTRACT(protoPayload.resourceName)"}}]}],
        }})
        live.append({"name": name, "displayName": address, "enabled": not retired, "combiner": "OR",
            "notificationChannels": [] if retired else [CHANNEL], "conditions": [{"name": name + "/conditions/1", "displayName": "actual", "conditionMatchedLog": {"filter": log_filter, "labelExtractors": {"object_resource": "EXTRACT(protoPayload.resourceName)"}}}],
        })
    return {"planned_values": {"root_module": {"resources": desired}}, "configuration": {"root_module": {"resources": [{"address": row["address"], "type": row["type"]} for row in desired]}}}, live, [{"name": CHANNEL, "type": "slack", "enabled": True}]


class RoutingAuditTests(unittest.TestCase):
    def test_reviewed_inventory_passes_through_real_cli_serialization(self):
        plan, policies, channels = fixture()
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / name for name in ("plan.json", "policies.json", "channels.json")]
            for path, value in zip(paths, (plan, {"alertPolicies": policies}, {"notificationChannels": channels})):
                path.write_text(json.dumps(value))
            output = io.StringIO()
            with patch("sys.stdout", output), patch.object(routing.subprocess, "check_output", side_effect=AssertionError("offline audit used credentials")):
                result = routing.main(["--terraform-json", str(paths[0]), "--policies-json", str(paths[1]), "--channels-json", str(paths[2])])
            self.assertEqual(result, 0)
            self.assertEqual(json.loads(output.getvalue())["findings"], [])

    def test_enabled_legacy_policy_channel_filter_and_unmanaged_routes_are_reported(self):
        plan, policies, channels = fixture()
        legacy = next(row for row in policies if row["name"] == routing.LEGACY_SCHEMA_ID)
        legacy["enabled"], legacy["notificationChannels"] = True, [CHANNEL]
        policies[0]["conditions"][0]["conditionMatchedLog"]["filter"] = "severity>=INFO"
        policies.append({**copy.deepcopy(policies[0]), "name": f"projects/{routing.PROJECT}/alertPolicies/unknown"})
        findings = routing.audit(plan, policies, channels)["findings"]
        self.assertEqual({row["code"] for row in findings}, {"enabled_drift", "channel_drift", "detection_drift", "unexpected_enabled_slack_policy"})
        self.assertTrue(any(row["identity"] == routing.LEGACY_SCHEMA_ADDRESS for row in findings))

    def test_missing_protections_and_disabled_channels_are_actionable(self):
        plan, policies, channels = fixture()
        policies = [row for row in policies if not row["name"].endswith("/0")]
        channels[0]["enabled"] = False
        findings = routing.audit(plan, policies, channels)["findings"]
        self.assertEqual({row["code"] for row in findings}, {"missing_or_ambiguous_policy", "missing_or_disabled_channel"})

    def test_incomplete_foreign_duplicate_or_unsafe_review_inputs_fail_closed(self):
        plan, policies, channels = fixture()
        for bad in ({"alertPolicies": policies, "nextPageToken": "remaining"}, policies + [policies[0]], [{**policies[0], "name": "projects/other/alertPolicies/1"}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                routing.audit(plan, bad, channels)
        for mutate in ("missing-security", "disabled-security", "wrong-schema", "enabled-schema", "schema-channel"):
            value = copy.deepcopy(plan)
            rows = value["planned_values"]["root_module"]["resources"]
            legacy = next(row["values"] for row in rows if row["address"] == routing.LEGACY_SCHEMA_ADDRESS)
            if mutate == "missing-security":
                rows.pop(0)
            elif mutate == "disabled-security":
                rows[0]["values"]["enabled"] = False
            elif mutate == "wrong-schema":
                legacy["name"] = f"projects/{routing.PROJECT}/alertPolicies/other"
            elif mutate == "enabled-schema":
                legacy["enabled"] = True
            else:
                legacy["notification_channels"] = [CHANNEL]
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                routing.audit(value, policies, channels)
        plan["configuration"]["root_module"]["resources"].append({"type": "google_monitoring_alert_policy", "address": "google_monitoring_alert_policy.future_job"})
        with self.assertRaisesRegex(ValueError, "omits declared"):
            routing.audit(plan, policies, channels)

    def test_filter_formatting_and_threshold_values_are_compared(self):
        plan, policies, channels = fixture()
        policies[0]["conditions"][0]["conditionMatchedLog"]["filter"] = 'severity >= ERROR resource.type = "gcs_bucket"'
        self.assertEqual(routing.audit(plan, policies, channels)["findings"], [])
        threshold = {"filter": 'metric.type="run.googleapis.com/job/completed_execution_count"', "duration": "120s", "comparison": "COMPARISON_LT", "threshold_value": 1,
            "aggregations": [{"alignment_period": "900s", "per_series_aligner": "ALIGN_SUM"}], "trigger": [{"count": 1, "percent": 0}]}
        actual = {"filter": threshold["filter"], "duration": "120s", "comparison": "COMPARISON_LT", "thresholdValue": 1,
            "aggregations": [{"alignmentPeriod": "900s", "perSeriesAligner": "ALIGN_SUM"}], "trigger": {"count": 1}}
        plan["planned_values"]["root_module"]["resources"][0]["values"]["conditions"] = [{"condition_threshold": [threshold]}]
        policies[0]["conditions"] = [{"conditionThreshold": actual}]
        self.assertEqual(routing.audit(plan, policies, channels)["findings"], [])
        actual["duration"] = "60s"
        self.assertEqual(routing.audit(plan, policies, channels)["findings"][0]["code"], "detection_drift")

    def test_live_transport_enumerates_all_pages_and_uses_only_gets(self):
        _, policies, _ = fixture()
        replies = iter(({"alertPolicies": policies[:1], "nextPageToken": "second"}, {"alertPolicies": policies[1:]}))
        calls = []

        def open_url(request, *, timeout):
            calls.append((request.get_method(), request.full_url, timeout))
            return io.StringIO(json.dumps(next(replies)))

        self.assertEqual(routing.live_inventory("alertPolicies", "test-token", open_url=open_url), policies)
        self.assertEqual([row[0] for row in calls], ["GET", "GET"])
        self.assertIn("pageToken=second", calls[1][1])
        with self.assertRaisesRegex(OSError, "enumeration failed"):
            routing.live_inventory("alertPolicies", "test-token", open_url=lambda *args, **kwargs: (_ for _ in ()).throw(OSError("enumeration failed")))
        with self.assertRaisesRegex(ValueError, "pagination token"):
            routing.live_inventory("alertPolicies", "test-token", open_url=lambda *args, **kwargs: io.StringIO(json.dumps({"nextPageToken": "same"})))

    def test_schema_policy_import_is_exact_and_cannot_destroy_its_history(self):
        text = (ROOT / "terraform/envs/prod/monitoring.tf").read_text()
        self.assertIn('to = google_monitoring_alert_policy.dataset_schema_changed\n  id = "' + routing.LEGACY_SCHEMA_ID + '"', text)
        policy = text.split('resource "google_monitoring_alert_policy" "dataset_schema_changed"', 1)[1]
        self.assertIn("enabled      = false", policy)
        self.assertIn("notification_channels = []", policy)
        self.assertIn("prevent_destroy = true", policy)


if __name__ == "__main__":
    unittest.main()
