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

        self.assertIn("workflow_call:", workflow)
        self.assertIn("send-from-git-range", workflow)
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", workflow)
        self.assertNotIn("github.run_attempt", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("refs/heads/main", workflow)
        self.assertIn(".github/workflows/ci.yml@refs/heads/main", workflow)
        self.assertNotIn("pip install", workflow)
        self.assertIn("uv sync --locked --no-dev", workflow)

    def test_announcement_in_earlier_commit_is_not_hidden_by_plain_head(self):
        message = """Add reusable capability

```repo-alert
emoji: 🧰
headline: A new capability
summary: A reusable capability is available.
why_excited: Projects can use it now.
```
"""
        event = {"commits": [{"message": message}, {"message": "Fix a typo"}],
                 "head_commit": {"message": "Fix a typo"}}
        self.assertEqual(repo_alerts.alerts_from_github_event(event)[0]["headline"], "A new capability")

    def test_complete_git_range_finds_an_alert_outside_bounded_push_payload(self):
        alert_message = """Add reusable capability

```repo-alert
emoji: 🧰
headline: Earlier capability
summary: A reusable capability is available.
why_excited: Projects can use it now.
```
"""
        # GitHub's push payload caps its commits array; git history has no such cap.
        messages = alert_message + "\0" + ("Fix typo\0" * 2048)
        with mock.patch.object(repo_alerts.subprocess, "run") as ancestry, \
                mock.patch.object(repo_alerts.subprocess, "check_output", return_value=messages):
            alerts = repo_alerts.alerts_from_git_range("a" * 40, "b" * 40)
        self.assertEqual([alert["headline"] for alert in alerts], ["Earlier capability"])
        self.assertTrue(ancestry.call_args.kwargs["check"])

    def test_missing_comparison_history_fails_instead_of_using_partial_event(self):
        from subprocess import CalledProcessError
        with mock.patch.object(repo_alerts.subprocess, "run", side_effect=CalledProcessError(1, "git")):
            with self.assertRaises(CalledProcessError):
                repo_alerts.alerts_from_git_range("a" * 40, "b" * 40)

    def test_invalid_commit_reference_is_rejected_before_git(self):
        with mock.patch.object(repo_alerts.subprocess, "run") as git:
            with self.assertRaises(ValueError):
                repo_alerts.alerts_from_git_range("HEAD", "b" * 40)
        git.assert_not_called()


if __name__ == "__main__":
    unittest.main()
