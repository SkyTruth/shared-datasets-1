"""Test transport guard, inherited by Python subprocesses via PYTHONPATH.

This guards trusted tests, not untrusted programs. Fake transports still
exercise HTTP construction and response validation.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
from urllib.parse import urlsplit


class NotificationIsolationError(BaseException):
    """Cannot be swallowed by production best-effort notification handling."""


def _slack_host(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    host = str(value).lower().rstrip(".")
    return host in {"slack.com", "slack-gov.com"} or host.endswith((".slack.com", ".slack-gov.com"))


def _audit(event, args):
    if event == "urllib.Request" and _slack_host(urlsplit(args[0]).hostname):
        raise NotificationIsolationError("Notification test isolation forbids live Slack transport")
    if event == "urllib.Request":
        url = urlsplit(args[0])
        if url.hostname == "secretmanager.googleapis.com" and "slack-webhook" in url.path and ":access" in url.path:
            raise NotificationIsolationError("Notification test isolation forbids production webhook secret resolution")
    if event in {"socket.getaddrinfo", "http.client.connect"}:
        host = args[0] if event == "socket.getaddrinfo" else args[1]
        if _slack_host(host):
            raise NotificationIsolationError("Notification test isolation forbids live Slack transport")
    if event in {"subprocess.Popen", "os.exec", "os.posix_spawn"}:
        command = args[1]
        text = command if isinstance(command, str) else " ".join(map(str, command))
        executable = os.fsdecode(args[0])
        if Path(executable).name in {"node", "nodejs", "node.exe"}:
            environment = args[3] if event == "subprocess.Popen" else args[2]
            options = (os.environ if environment is None else environment).get("NODE_OPTIONS", "")
            if str(Path(__file__).with_name("node_guard.cjs")) not in options:
                raise NotificationIsolationError("Notification test isolation forbids unguarded Node child environment")
        # Also check shell commands. Never echo arguments that can contain secrets.
        secret = "shared-datasets-slack-webhook-url" in text or "SHARED_DATASETS_SLACK_WEBHOOK_SECRET" in text
        if secret and ("access" in text or "secrets" in text):
            raise NotificationIsolationError("Notification test isolation forbids production webhook secret resolution")
        if any(host in text.lower() for host in ("hooks.slack.com/", "slack.com/api/", "hooks.slack-gov.com/")):
            raise NotificationIsolationError("Notification test isolation forbids live Slack subprocess transport")


def install():
    if not getattr(sys, "_shared_datasets_notification_guard", False):
        sys.addaudithook(_audit)
        sys._shared_datasets_notification_guard = True
    os.environ["SHARED_DATASETS_NOTIFICATION_TESTING"] = "1"


install()
