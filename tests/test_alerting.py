from __future__ import annotations

import subprocess
import unittest
import urllib.error

from scripts import slack_notify


class SlackNotifyTests(unittest.TestCase):
    def test_payload_includes_title_body_and_fields(self):
        payload = slack_notify.build_slack_payload(
            title="Dataset upload",
            body="Uploaded asset",
            status="success",
            fields={"Asset": "wdpa"},
        )

        self.assertIn("[success] Dataset upload", payload["text"])
        self.assertEqual(payload["blocks"][0]["type"], "header")
        self.assertIn("Uploaded asset", payload["blocks"][1]["text"]["text"])
        self.assertIn("wdpa", payload["blocks"][2]["fields"][0]["text"])

    def test_webhook_failure_raises(self):
        def failing_open(_request, timeout):
            raise urllib.error.URLError("network down")

        with self.assertRaisesRegex(slack_notify.SlackNotificationError, "network down"):
            slack_notify.post_webhook("https://hooks.slack.test/example", {"text": "hello"}, opener=failing_open)

    def test_load_webhook_prefers_env(self):
        def runner(_command, **_kwargs):
            raise AssertionError("runner should not be called")

        self.assertEqual(
            slack_notify.load_webhook_url(env={slack_notify.WEBHOOK_ENV: "https://example"}, runner=runner),
            "https://example",
        )

    def test_load_webhook_uses_gcloud_secret(self):
        def runner(command, **kwargs):
            self.assertIn("shared-datasets-slack-webhook-url", command)
            return subprocess.CompletedProcess(command, 0, stdout="https://secret\n", stderr="")

        self.assertEqual(slack_notify.load_webhook_url(env={}, runner=runner), "https://secret")


if __name__ == "__main__":
    unittest.main()
