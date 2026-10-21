#!/usr/bin/env python3
"""Small Slack webhook helper for shared-datasets operational summaries."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


WEBHOOK_ENV = "SHARED_DATASETS_SLACK_WEBHOOK_URL"
PURPOSES = {"new_dataset", "breaking_change", "repo_functionality", "named_breakglass", "legacy_runtime_bridge"}


class SlackNotificationError(RuntimeError):
    """Raised when Slack notification delivery fails."""


@dataclass(frozen=True)
class WebhookCapability:
    purpose: str
    resolve: Callable[[], str] = field(repr=False)

    def __post_init__(self):
        if self.purpose not in PURPOSES or not callable(self.resolve):
            raise SlackNotificationError("Unregistered public webhook capability")


def webhook_capability(purpose: str, *, webhook_url: str | None = None, env: Mapping[str, str] | None = None) -> WebhookCapability:
    """Approved entrypoints explicitly hold delivery; no secret discovery."""
    value = webhook_url if webhook_url is not None else (os.environ if env is None else env).get(WEBHOOK_ENV, "")

    def resolve():
        if not value or not value.strip():
            raise SlackNotificationError(f"Explicit webhook environment {WEBHOOK_ENV} is not configured")
        return value.strip()

    return WebhookCapability(purpose, resolve)


def build_slack_payload(
    *,
    title: str,
    body: str,
    status: str = "info",
    emoji: str | None = None,
    fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact Block Kit message payload."""

    status_prefix = emoji or {
        "success": "✅",
        "warning": "⚠️",
        "error": "❌",
        "info": "💡",
        "new": "🎉",
        # A pipeline paused for human input is not a failure. It gets its own
        # mark so readers can tell "someone needs to decide something" from
        # "something is broken" at a glance in a busy channel.
        "decision": "🙋",
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
    env: Mapping[str, str] | None = None,
) -> str:
    """Read explicit configuration only; ambient GCP credentials are irrelevant."""
    value = (os.environ if env is None else env).get(WEBHOOK_ENV, "").strip()
    if not value:
        raise SlackNotificationError(f"Explicit webhook environment {WEBHOOK_ENV} is not configured")
    return value


def post_webhook(
    capability: WebhookCapability,
    payload: Mapping[str, Any],
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> None:
    """Post through an explicit approved entrypoint capability."""

    if not isinstance(capability, WebhookCapability):
        raise SlackNotificationError("Public delivery requires an explicit webhook capability")
    webhook_url = capability.resolve()

    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with opener(request, timeout=30) as response:
            status = int(response.status if hasattr(response, "status") else response.getcode())
            if status >= 400:
                raise SlackNotificationError(f"Slack webhook returned HTTP {status}")
    except urllib.error.URLError as exc:
        raise SlackNotificationError(f"Slack webhook request failed: {exc}") from exc


def notify(
    *,
    title: str,
    body: str,
    status: str = "info",
    emoji: str | None = None,
    fields: Mapping[str, Any] | None = None,
    capability: WebhookCapability | None = None,
    dry_run: bool = False,
    strict: bool = False,
) -> bool:
    """Send a Slack message, returning False on best-effort delivery failures."""

    payload = build_slack_payload(title=title, body=body, status=status, emoji=emoji, fields=fields)
    if dry_run:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return True

    try:
        if capability is None:
            raise SlackNotificationError("Public delivery requires an explicit webhook capability")
        post_webhook(capability, payload)
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort notification helper.
        if strict:
            raise
        print(f"Slack notification skipped: {exc}", file=sys.stderr)
        return False


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if len(args) < 2:
        print("usage: slack_notify.py TITLE BODY (payload preview only)", file=sys.stderr)
        return 2
    return 0 if notify(title=args[0], body=args[1], dry_run=True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
