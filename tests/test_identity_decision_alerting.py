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
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from ingestion.common import feature_metadata
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
    def test_body_leads_with_the_decision_not_the_stoppage(self):
        body = feature_metadata.identity_ambiguity_alert_body(
            asset_slug="wdpa-terrestrial",
            release="2026-08-01",
            ambiguities=[ambiguity()],
        )

        self.assertIn("need a maintainer to confirm identity", body)
        self.assertIn("Nothing is broken and nothing is at risk", body)
        self.assertIn("published dataset is unchanged", body)
        self.assertIn("catalog/feature-identity-resolutions/wdpa-terrestrial.json", body)
        # Plain-language explanation rather than the internal enum name.
        self.assertIn("same footprint as an existing feature", body)
        for scary in ("failed", "failure", "error", "unresolved partial identity hash"):
            self.assertNotIn(scary, body.lower(), f"decision request should not read as {scary!r}")

    def test_body_singularizes_and_summarizes_the_remainder(self):
        one = feature_metadata.identity_ambiguity_alert_body(
            asset_slug="a", release="r", ambiguities=[ambiguity()]
        )
        self.assertIn("1 feature need", one.replace("features", "feature"))

        many = feature_metadata.identity_ambiguity_alert_body(
            asset_slug="a",
            release="r",
            ambiguities=[ambiguity(str(index)) for index in range(12)],
            limit=5,
        )
        self.assertIn("12 features need", many)
        self.assertIn("7 more of the same kind", many)
        self.assertIn("identity ambiguity evidence", many)

    def test_notification_is_titled_as_a_decision_and_marked_distinctly(self):
        sent = {}

        def fake_notify(**kwargs):
            sent.update(kwargs)
            return True

        with mock.patch("scripts.slack_notify.notify", fake_notify):
            feature_metadata.notify_identity_ambiguities(
                asset_slug="wdpa-terrestrial",
                release="2026-08-01",
                ambiguities=[ambiguity()],
            )

        self.assertEqual(sent["title"], "Decision needed: wdpa-terrestrial release 2026-08-01")
        self.assertEqual(sent["status"], "decision")
        self.assertEqual(sent["fields"]["published data"], "unchanged")
        self.assertEqual(sent["fields"]["awaiting decisions"], "1")

    def test_decision_status_has_its_own_mark_distinct_from_failure(self):
        decision = build_slack_payload(title="t", body="b", status="decision")
        error = build_slack_payload(title="t", body="b", status="error")
        warning = build_slack_payload(title="t", body="b", status="warning")

        mark = decision["blocks"][0]["text"]["text"]
        self.assertTrue(mark.startswith("🙋"), mark)
        self.assertNotEqual(mark, error["blocks"][0]["text"]["text"])
        self.assertNotEqual(mark, warning["blocks"][0]["text"]["text"])


class AlertPolicyTests(unittest.TestCase):
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
        self.assertIn("protoPayload.status.code=10", policy)
        self.assertNotIn(feature_metadata.RELEASE_BLOCKED_MARKER, policy)

    def test_alert_policies_have_a_protected_apply_path(self):
        policies = {
            "google_monitoring_alert_policy.scheduled_ingestion_cloud_run_failure",
            "google_monitoring_alert_policy.release_awaiting_identity_decision",
            "google_monitoring_alert_policy.scheduled_ingestion_scheduler_failure",
        }
        assert_target_apply_caller(
            self,
            ALERT_POLICY_SYNC,
            expected_name="Cron alert policy sync",
            push_paths={
                ".github/workflows/cron-alert-policy-sync.yml",
                ".github/workflows/prod-terraform-target-apply.yml",
                "terraform/envs/prod/monitoring.tf",
                "terraform/envs/prod/monitoring_alert_policy_iam.tf",
                "terraform/envs/prod/monitoring_variables.tf",
                "terraform/envs/prod/variables.tf",
                "terraform/envs/prod/versions.tf",
            },
            expected_job_if=None,
            expected_needs="bootstrap",
            sync_name="Cron alert policy sync",
            refusal_prefix="Refusing automatic cron alert policy sync",
            expected_targets=policies,
            blocked_resources={
                "google_storage_bucket.shared_bucket",
                "google_cloud_run_v2_job.this",
            },
        )
        workflow = load_workflow(ALERT_POLICY_SYNC)
        self.assertNotIn("pull_request", workflow.get("on") or workflow.get(True) or {})


if __name__ == "__main__":
    unittest.main()
