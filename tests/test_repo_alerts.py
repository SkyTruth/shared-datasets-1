from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import repo_alerts

REPO_ROOT = Path(__file__).resolve().parents[1]


class RepoAlertsTests(unittest.TestCase):
    def test_functionality_added_message_is_concise(self):
        title, body = repo_alerts.build_functionality_added_message(
            headline="Vector publishing helper added",
            summary="A new command builds FlatGeobuf and PMTiles artifacts from source vectors.",
            why_excited="Manual publishes are faster, more repeatable, and easier to review.",
        )

        self.assertEqual(title, "Vector publishing helper added")
        self.assertIn("A new command builds FlatGeobuf", body)
        self.assertIn("*Why this is exciting:* Manual publishes are faster", body)

    def test_alerts_from_commit_message_extracts_fenced_alerts(self):
        alerts = repo_alerts.alerts_from_commit_message(
            """Add vector publishing helper

```repo-alert
emoji: 🗺️
headline: Vector publishing helper added
summary: A new command builds FlatGeobuf and PMTiles artifacts from source vectors.
why_excited: Manual publishes are faster, more repeatable, and easier to review.
```
"""
        )

        self.assertEqual(
            alerts,
            [
                {
                    "emoji": "🗺️",
                    "headline": "Vector publishing helper added",
                    "summary": "A new command builds FlatGeobuf and PMTiles artifacts from source vectors.",
                    "why_excited": "Manual publishes are faster, more repeatable, and easier to review.",
                }
            ],
        )

    def test_alerts_from_commit_message_extracts_multiple_alerts(self):
        alerts = repo_alerts.alerts_from_commit_message(
            """Add repository alerts and SDK

```repo-alert
emoji: 📣
headline: Repository alerts added
summary: Commit messages can now carry Slack-ready release notes.
why: Maintainers get better updates without extra manual steps.
```

```repo-alert
emoji: 🐍
headline: Python SDK added
summary: Consumers can resolve shared dataset catalog entries in Python.
why_excited: Project code no longer needs hand-copied bucket paths.
```
"""
        )

        self.assertEqual([alert["emoji"] for alert in alerts], ["📣", "🐍"])
        self.assertEqual(alerts[0]["why_excited"], "Maintainers get better updates without extra manual steps.")

    def test_alerts_from_commit_message_returns_empty_when_unmarked(self):
        self.assertEqual(repo_alerts.alerts_from_commit_message("Fix typo in README"), [])

    def test_alerts_from_commit_message_rejects_incomplete_block(self):
        with self.assertRaisesRegex(ValueError, "missing required"):
            repo_alerts.alerts_from_commit_message(
                """Add helper

```repo-alert
emoji: 🗺️
headline: Vector publishing helper added
summary: A new command builds vector artifacts.
```
"""
            )

    def test_send_from_github_event_posts_all_fenced_alerts(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "event.json"
            event_path.write_text(
                json.dumps(
                    {
                        "commits": [
                            {
                                "message": (
                                    "Add SDK\n\n"
                                    "```repo-alert\n"
                                    "emoji: 🐍\n"
                                    "headline: Python SDK added\n"
                                    "summary: Consumers can resolve shared dataset catalog entries in Python.\n"
                                    "why_excited: Project code no longer needs hand-copied bucket paths.\n"
                                    "```"
                                )
                            },
                            {
                                "message": (
                                    "Add workflow\n\n"
                                    "```repo-alert\n"
                                    "emoji: 📣\n"
                                    "headline: Repo alerts added\n"
                                    "summary: Commit messages can carry Slack-ready release notes.\n"
                                    "why_excited: Maintainers get better updates without extra manual steps.\n"
                                    "```"
                                )
                            },
                        ]
                    }
                )
            )

            with mock.patch.object(repo_alerts, "send_functionality_added_alert", return_value=True) as send_alert:
                repo_alerts.send_from_github_event(event_path=event_path, dry_run=True)

        self.assertEqual(send_alert.call_count, 2)
        self.assertEqual(send_alert.call_args_list[0].kwargs["emoji"], "🐍")
        self.assertEqual(send_alert.call_args_list[1].kwargs["headline"], "Repo alerts added")

    def test_send_from_github_event_skips_when_no_fenced_alerts_exist(self):
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "event.json"
            event_path.write_text(json.dumps({"commits": [{"message": "Fix README typo"}]}))

            with mock.patch.object(repo_alerts, "send_functionality_added_alert", return_value=True) as send_alert:
                repo_alerts.send_from_github_event(event_path=event_path, dry_run=True)

        send_alert.assert_not_called()

    def test_github_workflow_posts_fenced_alerts_on_main_push(self):
        workflow = (REPO_ROOT / ".github/workflows/repo-functionality-alert.yml").read_text()

        self.assertIn("branches:", workflow)
        self.assertIn("- main", workflow)
        self.assertIn("send-from-github-event", workflow)
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", workflow)
        self.assertNotIn("github.run_attempt", workflow)


if __name__ == "__main__":
    unittest.main()
