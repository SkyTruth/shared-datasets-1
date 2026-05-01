#!/usr/bin/env python3
"""Repository-level Slack announcements."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.slack_notify import notify


app = typer.Typer(no_args_is_help=True)
ALERT_FENCE = "repo-alert"
REQUIRED_ALERT_FIELDS = ("emoji", "headline", "summary", "why_excited")


@app.callback()
def main() -> None:
    """Post repo-level Slack announcements."""


def build_functionality_added_message(
    *,
    headline: str,
    summary: str,
    why_excited: str,
) -> tuple[str, str]:
    body = "\n".join(
        [
            summary,
            f"*Why this is exciting:* {why_excited}",
        ]
    )
    return headline, body


def parse_alert_block(block: str) -> dict[str, str]:
    alert: dict[str, str] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = key.strip().replace("-", "_").lower()
        if normalized_key == "why":
            normalized_key = "why_excited"
        alert[normalized_key] = value.strip()

    missing = [field for field in REQUIRED_ALERT_FIELDS if not alert.get(field)]
    if missing:
        raise ValueError(f"repo-alert block is missing required field(s): {', '.join(missing)}")
    return {field: alert[field] for field in REQUIRED_ALERT_FIELDS}


def extract_alert_blocks(message: str) -> list[str]:
    blocks: list[str] = []
    in_alert = False
    current: list[str] = []
    for raw_line in message.splitlines():
        line = raw_line.strip()
        if not in_alert and line.startswith("```") and line[3:].strip() == ALERT_FENCE:
            in_alert = True
            current = []
            continue
        if in_alert and line.startswith("```"):
            blocks.append("\n".join(current))
            in_alert = False
            current = []
            continue
        if in_alert:
            current.append(raw_line)

    if in_alert:
        raise ValueError("Unclosed repo-alert fenced block in commit message")
    return blocks


def alerts_from_commit_message(message: str) -> list[dict[str, str]]:
    return [parse_alert_block(block) for block in extract_alert_blocks(message)]


def alerts_from_github_event(event: dict) -> list[dict[str, str]]:
    alerts: list[dict[str, str]] = []
    commits = event.get("commits") or []
    if not commits and event.get("head_commit"):
        commits = [event["head_commit"]]
    for commit in commits:
        alerts.extend(alerts_from_commit_message(str(commit.get("message", ""))))
    return alerts


def send_functionality_added_alert(
    *,
    emoji: str,
    headline: str,
    summary: str,
    why_excited: str,
    dry_run: bool = False,
    strict: bool = False,
) -> bool:
    title, body = build_functionality_added_message(
        headline=headline,
        summary=summary,
        why_excited=why_excited,
    )
    return notify(title=title, body=body, emoji=emoji, dry_run=dry_run, strict=strict)


@app.command("send-from-github-event")
def send_from_github_event(
    event_path: Path = typer.Option(..., exists=True, dir_okay=False, help="GitHub push event JSON path."),
    dry_run: bool = typer.Option(False, help="Print Slack payloads instead of posting."),
) -> None:
    """Send Slack alerts for repo-alert blocks in a GitHub push event."""

    event = json.loads(event_path.read_text())
    alerts = alerts_from_github_event(event)
    if not alerts:
        print("No repo-alert blocks found in pushed commit messages; skipping Slack notification.")
        return

    for alert in alerts:
        send_functionality_added_alert(
            emoji=alert["emoji"],
            headline=alert["headline"],
            summary=alert["summary"],
            why_excited=alert["why_excited"],
            dry_run=dry_run,
            strict=not dry_run,
        )


if __name__ == "__main__":
    app()
