from __future__ import annotations

import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from scripts import slack_notify

REPO_ROOT = Path(__file__).resolve().parents[1]


class SlackNotifyTests(unittest.TestCase):
    def test_payload_includes_title_body_and_fields(self):
        payload = slack_notify.build_slack_payload(
            title="Dataset upload",
            body="Uploaded asset",
            status="success",
            fields={"Asset": "wdpa"},
        )

        self.assertIn("✅ Dataset upload", payload["text"])
        self.assertEqual(payload["blocks"][0]["type"], "header")
        self.assertTrue(payload["blocks"][0]["text"]["emoji"])
        self.assertIn("Uploaded asset", payload["blocks"][1]["text"]["text"])
        self.assertIn("wdpa", payload["blocks"][2]["fields"][0]["text"])

    def test_payload_uses_status_emoji_prefixes(self):
        expected = {
            "success": "✅",
            "warning": "⚠️",
            "error": "❌",
            "info": "💡",
            "new": "🎉",
        }
        for status, emoji in expected.items():
            with self.subTest(status=status):
                payload = slack_notify.build_slack_payload(title="Alert", body="Body", status=status)
                self.assertTrue(payload["text"].startswith(f"{emoji} Alert"))

    def test_payload_can_override_status_emoji(self):
        payload = slack_notify.build_slack_payload(
            title="Vector publishing helper added",
            body="Body",
            status="info",
            emoji="🗺️",
        )

        self.assertTrue(payload["text"].startswith("🗺️ Vector publishing helper added"))

    def test_webhook_failure_raises(self):
        def failing_open(_request, timeout):
            raise urllib.error.URLError("network down")

        with self.assertRaisesRegex(slack_notify.SlackNotificationError, "network down"):
            slack_notify.post_webhook(slack_notify.webhook_capability("new_dataset", webhook_url="https://hooks.slack.test/example"), {"text": "hello"}, opener=failing_open)

    def test_load_webhook_prefers_env(self):
        self.assertEqual(
            slack_notify.load_webhook_url(env={slack_notify.WEBHOOK_ENV: "https://example"}),
            "https://example",
        )

    def test_missing_explicit_configuration_never_discovers_secret(self):
        with mock.patch("subprocess.run", side_effect=AssertionError("ambient secret discovery")):
            with self.assertRaisesRegex(slack_notify.SlackNotificationError, "Explicit webhook environment"):
                slack_notify.load_webhook_url(env={})

    def test_live_delivery_requires_explicit_capability(self):
        with self.assertRaisesRegex(slack_notify.SlackNotificationError, "explicit webhook capability"):
            slack_notify.notify(title="test", body="body", strict=True)
        with self.assertRaisesRegex(slack_notify.SlackNotificationError, "explicit webhook capability"):
            slack_notify.post_webhook("https://example.invalid", {})


class SlackWebhookTerraformTests(unittest.TestCase):
    def test_webhook_secret_has_local_summary_accessor_binding(self):
        monitoring_tf = (REPO_ROOT / "terraform/envs/prod/monitoring.tf").read_text()
        monitoring_variables_tf = (REPO_ROOT / "terraform/envs/prod/monitoring_variables.tf").read_text()

        self.assertIn('resource "google_secret_manager_secret_iam_member" "slack_webhook_accessors"', monitoring_tf)
        self.assertIn("roles/secretmanager.secretAccessor", monitoring_tf)
        self.assertIn("slack_webhook_secret_accessors", monitoring_variables_tf)
        self.assertIn("domain:skytruth.org", monitoring_variables_tf)


if __name__ == "__main__":
    unittest.main()
