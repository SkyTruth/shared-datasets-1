"""Tests for how a release paused on a maintainer decision is reported.

A pause is not an outage. If it reports like one, the alert gets escalated or
tuned out, and the one thing actually needed — a human decision — is buried.
These tests pin the distinction end to end: the exception type, the exit
status, the log marker, the Slack copy, and the alert policies.
"""

from __future__ import annotations

import io
import json
import logging
import re
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from ingestion.common import feature_metadata, decision_events
from services.monitoring_slack import decisions
from ingestion.common.runtime import run_job_main
from scripts import release_feature_model as model
from scripts.slack_notify import build_slack_payload
from workflow_helpers import assert_target_apply_caller, load_workflow


REPO_ROOT = Path(__file__).resolve().parents[1]
MONITORING_TF = REPO_ROOT / "terraform/envs/prod/monitoring.tf"
ALERT_POLICY_SYNC = REPO_ROOT / ".github/workflows/cron-alert-policy-sync.yml"


def ambiguity(key: str = "555884979") -> model.IdentityAmbiguity:
    return model.IdentityAmbiguity(
        ambiguity_type="same_geometry_changed_properties",
        identity_key=(key,),
        geometry_hash="sha256:" + "a" * 64,
        properties_hash="sha256:" + "b" * 64,
        matching_geometry_feature_ids=("297926",),
        matching_properties_feature_ids=(),
        matching_geometry_properties_hashes=("sha256:" + "c" * 64,),
        matching_properties_geometry_hashes=(),
    )


class BlockedReleaseExitTests(unittest.TestCase):
    def test_a_paused_release_exits_successfully_and_reports_itself(self):
        blocked = feature_metadata.IdentityDecisionRequired(
            "wdpa-terrestrial release 2026-08-01 is waiting on 7 maintainer identity decision(s)",
            asset_slug="wdpa-terrestrial",
            release="2026-08-01",
            ambiguity_count=7,
        )

        def run():
            raise blocked

        logger = logging.getLogger("test-blocked-release")
        stdout = io.StringIO()
        with self.assertLogs(logger, level="WARNING") as logs:
            with redirect_stdout(stdout):
                # Returns instead of raising: a non-zero exit would trip the
                # project-wide "Cloud Run Job execution failed" alarm.
                run_job_main(run, logger=logger, failure_message="job failed")

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reason"], "identity_decision_required")
        self.assertEqual(payload["awaiting_decisions"], 7)
        self.assertEqual(payload["asset_slug"], "wdpa-terrestrial")
        self.assertIn("catalog/feature-identity-resolutions/wdpa-terrestrial.json", payload["next_step"])

        joined = "\n".join(logs.output)
        self.assertIn(feature_metadata.RELEASE_BLOCKED_MARKER, joined)
        self.assertNotIn("ERROR", joined.split("\n")[0])

    def test_genuine_failures_still_raise_and_still_alarm(self):
        def run():
            raise RuntimeError("ogr2ogr segfaulted")

        logger = logging.getLogger("test-real-failure")
        with self.assertLogs(logger, level="ERROR"):
            with self.assertRaisesRegex(RuntimeError, "ogr2ogr segfaulted"):
                run_job_main(run, logger=logger, failure_message="job failed")

    def test_blocked_is_not_a_subclass_trap_for_other_runtime_errors(self):
        self.assertTrue(issubclass(feature_metadata.IdentityDecisionRequired, RuntimeError))
        self.assertNotIsInstance(RuntimeError("plain"), feature_metadata.IdentityDecisionRequired)


class DecisionAlertCopyTests(unittest.TestCase):
    def test_producer_queues_complete_cases_for_the_owned_decision_thread(self):
        with mock.patch.object(decision_events, "publish", return_value=True) as send:
            feature_metadata.notify_identity_ambiguities(
                asset_slug="wdpa-terrestrial", release="2026-08-01", ambiguities=[ambiguity()])
        sent = send.call_args.kwargs
        self.assertEqual(sent["ambiguities"], [model.identity_ambiguity_to_dict(ambiguity())])
        value = decision_events.event({"schema": decision_events.SCHEMA, "project": decision_events.PROJECT,
            "job": "wdpa-monthly", "execution": "wdpa-monthly-abcde", **{k: sent[k] for k in ("asset_slug", "release")},
            "cases": [{key: sent["ambiguities"][0][key] for key in decision_events.CASE_FIELDS}]})
        body = decisions.primary(value)["text"]
        self.assertIn("Decision needed: wdpa-terrestrial release 2026-08-01", body)
        self.assertIn("published data is unchanged", body)
        self.assertIn("catalog/feature-identity-resolutions/", body)

    def test_decision_status_has_its_own_mark_distinct_from_failure(self):
        decision = build_slack_payload(title="t", body="b", status="decision")
        error = build_slack_payload(title="t", body="b", status="error")
        warning = build_slack_payload(title="t", body="b", status="warning")

        mark = decision["blocks"][0]["text"]["text"]
        self.assertTrue(mark.startswith("🙋"), mark)
        self.assertNotEqual(mark, error["blocks"][0]["text"]["text"])
        self.assertNotEqual(mark, warning["blocks"][0]["text"]["text"])


class AlertPolicyTests(unittest.TestCase):
    def test_alert_policy_role_can_replace_log_notification_rules(self):
        iam = (MONITORING_TF.parent / "monitoring_alert_policy_iam.tf").read_text(encoding="utf-8")
        role = iam.split('resource "google_project_iam_custom_role" "monitoring_alert_policy_manager"', 1)[1]
        role = role.split("\nresource ", 1)[0]
        permissions = re.search(r"permissions\s*=\s*\[(.*?)\]", role, re.DOTALL)
        self.assertIsNotNone(permissions)
        self.assertEqual(
            set(re.findall(r'"([^"]+)"', permissions.group(1))),
            {
                "logging.logMetrics.create", "logging.logMetrics.get", "logging.logMetrics.list", "logging.logMetrics.update", "logging.logMetrics.delete",
                "logging.notificationRules.create",
                "logging.notificationRules.delete",
                "monitoring.alertPolicies.create",
                "monitoring.alertPolicies.delete",
                "monitoring.alertPolicies.get",
                "monitoring.alertPolicies.list",
                "monitoring.alertPolicies.update",
                "monitoring.notificationChannels.create",
                "monitoring.notificationChannels.update",
                "pubsub.topics.create",
                "pubsub.topics.get",
                "pubsub.topics.getIamPolicy",
                "pubsub.topics.setIamPolicy",
                "pubsub.subscriptions.create",
                "pubsub.subscriptions.get",
                "pubsub.subscriptions.update",
                "pubsub.topics.attachSubscription",
                "secretmanager.secrets.create",
                "secretmanager.secrets.get",
                "monitoring.alerts.get",
                "monitoring.notificationChannels.get",
                "monitoring.notificationChannels.list",
            },
        )

    def test_decision_policy_matches_the_marker_at_warning_severity(self):
        monitoring = MONITORING_TF.read_text(encoding="utf-8")

        self.assertIn('resource "google_monitoring_alert_policy" "release_awaiting_identity_decision"', monitoring)
        policy = monitoring.split('"release_awaiting_identity_decision"', 1)[1].split("\nresource ", 1)[0]
        self.assertIn('severity     = "WARNING"', policy)
        self.assertIn(feature_metadata.RELEASE_BLOCKED_MARKER, policy)
        self.assertIn("This is a request for a decision, not a failure", policy)
        self.assertIn("catalog/feature-identity-resolutions", policy)
        self.assertIn('kind      = "decision-required"', policy)

    def test_failure_policy_disclaims_the_decision_case(self):
        monitoring = MONITORING_TF.read_text(encoding="utf-8")
        policy = monitoring.split('"scheduled_ingestion_cloud_run_failure"', 1)[1].split("\nresource ", 1)[0]

        self.assertIn('severity     = "ERROR"', policy)
        self.assertIn("does **not** reach this policy", policy)
        # The failure policy must keep matching only real execution failures.
        self.assertIn("protoPayload.status.code>0", policy)
        self.assertNotIn(feature_metadata.RELEASE_BLOCKED_MARKER, policy)

    def test_alert_policies_have_a_protected_apply_path(self):
        policies = {
            "google_monitoring_alert_policy.scheduled_ingestion_cloud_run_failure",
            "google_monitoring_alert_policy.release_awaiting_identity_decision",
            "google_monitoring_alert_policy.scheduled_ingestion_scheduler_failure",
            "google_monitoring_alert_policy.dataset_object_written_by_unapproved_principal",
            "google_monitoring_alert_policy.dataset_object_deleted",
            "terraform_data.cron_alert_channel_configured",
            "google_cloud_run_v2_service.monitoring_slack",
            "google_cloud_run_v2_service_iam_member.monitoring_slack_push",
            "google_pubsub_subscription.monitoring_slack",
            "google_pubsub_subscription.identity_decision_events",
            "google_monitoring_notification_channel.monitoring_slack",
            "google_pubsub_topic_iam_member.monitoring_slack_publisher",
        }
        assert_target_apply_caller(
            self,
            ALERT_POLICY_SYNC,
            expected_name="Infrastructure / Cron alert policy sync",
            push_paths=None,
            expected_job_if="${{ !cancelled() && github.ref == 'refs/heads/main' && (github.event_name == 'push' || github.event_name == 'workflow_dispatch') && ((github.event_name == 'push' && !inputs.sync_iam) || (needs.bootstrap.result == 'success' && needs.resources.result == 'success')) }}",
            expected_needs=["bootstrap", "resources"],
            sync_name="Cron alert policy sync",
            refusal_prefix="Refusing automatic cron alert policy sync",
            expected_targets=policies,
            expected_block_deletes=True,
            blocked_resources={
                "google_storage_bucket.shared_bucket",
                "google_cloud_run_v2_job.this",
            },
        )
        workflow = load_workflow(ALERT_POLICY_SYNC)
        self.assertNotIn("pull_request", workflow.get("on") or workflow.get(True) or {})


if __name__ == "__main__":
    unittest.main()
