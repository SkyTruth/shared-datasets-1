#!/usr/bin/env python3
"""Small Slack webhook helper for shared-datasets operational summaries."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any


DEFAULT_PROJECT_ID = "shared-datasets-1"
DEFAULT_SECRET_ID = "shared-datasets-slack-webhook-url"
WEBHOOK_ENV = "SHARED_DATASETS_SLACK_WEBHOOK_URL"
SECRET_ENV = "SHARED_DATASETS_SLACK_WEBHOOK_SECRET"


class SlackNotificationError(RuntimeError):
    """Raised when Slack notification delivery fails."""


def build_slack_payload(
    *,
    title: str,
    body: str,
    status: str = "info",
    fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact Block Kit message payload."""

    status_prefix = {
        "success": "✅",
        "warning": "⚠️",
        "error": "❌",
        "info": "💡",
        "new": "🎉",
    }.get(status, "💡")
    text = f"{status_prefix} {title}\n{body}".strip()
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"{status_prefix} {title}"[:150],
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": body[:3000]},
        },
    ]
    if fields:
        blocks.append(
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*{key}*\n{value}",
                    }
                    for key, value in fields.items()
                ][:10],
            }
        )
    return {"text": text, "blocks": blocks}


def load_webhook_url(
    *,
    project_id: str = DEFAULT_PROJECT_ID,
    secret_id: str | None = None,
    env: Mapping[str, str] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Load Slack webhook URL from env or Secret Manager via gcloud."""

    environ = env or os.environ
    if environ.get(WEBHOOK_ENV):
        return environ[WEBHOOK_ENV].strip()

    resolved_secret_id = secret_id or environ.get(SECRET_ENV) or DEFAULT_SECRET_ID
    command = [
        "gcloud",
        "secrets",
        "versions",
        "access",
        "latest",
        "--secret",
        resolved_secret_id,
        "--project",
        project_id,
    ]
    try:
        result = runner(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise SlackNotificationError(f"Unable to access Secret Manager secret {resolved_secret_id}: {detail}") from exc
    webhook_url = result.stdout.strip()
    if not webhook_url:
        raise SlackNotificationError(f"Secret {resolved_secret_id} returned an empty webhook URL")
    return webhook_url


def post_webhook(
    webhook_url: str,
    payload: Mapping[str, Any],
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Post a payload to Slack."""

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with opener(request, timeout=30) as response:
            status = int(getattr(response, "status", response.getcode()))
            if status >= 400:
                raise SlackNotificationError(f"Slack webhook returned HTTP {status}")
    except urllib.error.URLError as exc:
        raise SlackNotificationError(f"Slack webhook request failed: {exc}") from exc


def notify(
    *,
    title: str,
    body: str,
    status: str = "info",
    fields: Mapping[str, Any] | None = None,
    webhook_url: str | None = None,
    project_id: str = DEFAULT_PROJECT_ID,
    dry_run: bool = False,
    strict: bool = False,
) -> bool:
    """Send a Slack message, returning False on best-effort delivery failures."""

    payload = build_slack_payload(title=title, body=body, status=status, fields=fields)
    if dry_run:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return True

    try:
        resolved_webhook_url = webhook_url or load_webhook_url(project_id=project_id)
        post_webhook(resolved_webhook_url, payload)
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort notification helper.
        if strict:
            raise
        print(f"Slack notification skipped: {exc}", file=sys.stderr)
        return False


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if len(args) < 2:
        print("usage: slack_notify.py TITLE BODY", file=sys.stderr)
        return 2
    return 0 if notify(title=args[0], body=args[1]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
