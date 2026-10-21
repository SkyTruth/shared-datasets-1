"""Explicit Secret Manager resolution only for a named emergency entrypoint."""
from __future__ import annotations

import os
import subprocess

from scripts.slack_notify import WEBHOOK_ENV, SlackNotificationError, WebhookCapability


def emergency_capability(*, approved_emergency, project_id, secret_id):
    if approved_emergency is not True or project_id != "shared-datasets-1" or secret_id != "shared-datasets-slack-webhook-url":
        raise SlackNotificationError("A named approved emergency and exact webhook secret are required")

    def resolve():
        value = os.environ.get(WEBHOOK_ENV, "").strip()
        if value:
            return value
        # Resolution is deferred until actual authorized delivery. Neither the
        # token nor webhook bytes are included in errors or command arguments.
        try:
            result = subprocess.run(["gcloud", "secrets", "versions", "access", "latest", "--secret", secret_id, "--project", project_id], check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as error:
            raise SlackNotificationError("Explicit emergency webhook secret resolution failed") from error
        if not result.stdout.strip():
            raise SlackNotificationError("Explicit emergency webhook secret is empty")
        return result.stdout.strip()

    return WebhookCapability("named_breakglass", resolve)
