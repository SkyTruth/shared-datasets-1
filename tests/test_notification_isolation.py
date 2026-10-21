"""A forgotten mock must fail before production transport or secret lookup."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request

import pytest

from scripts import notification_rehearsal, slack_notify
from scripts.slack_incident_api import SlackError


def test_unmocked_best_effort_notification_cannot_swallow_guard():
    with pytest.raises(BaseException, match="test isolation forbids live Slack transport"):
        slack_notify.notify(title="test", body="test", webhook_url="https://hooks.slack.com/services/never/send")


def test_unmocked_secret_lookup_is_forbidden(monkeypatch):
    monkeypatch.delenv(slack_notify.WEBHOOK_ENV, raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(BaseException, match="test isolation forbids production webhook secret"):
        slack_notify.load_webhook_url()


def test_direct_http_transport_is_also_guarded():
    with pytest.raises(BaseException, match="test isolation forbids live Slack transport"):
        urllib.request.urlopen("https://slack.com/api/chat.postMessage", timeout=1)


@pytest.mark.parametrize("action", ["http", "secret", "shell"])
def test_python_children_inherit_guard_before_entrypoint(action):
    # Construct forbidden targets in the child so this proves child isolation,
    # in addition to the parent's direct subprocess-command audit.
    code = '''
import subprocess, sys, urllib.request
action = sys.argv[1]
if action == "http":
    urllib.request.urlopen("https://hooks." + "slack.com/services/never/send", timeout=1)
elif action == "secret":
    subprocess.run(["gcloud", "secrets", "versions", "access", "latest", "--secret",
                    "shared-datasets-" + "slack-webhook-url"], check=True)
else:
    subprocess.run("curl https://hooks." + "slack.com/services/never/send", shell=True, check=True)
'''
    result = subprocess.run([sys.executable, "-c", code, action], capture_output=True, text=True)
    assert result.returncode != 0
    assert "Notification test isolation forbids" in result.stderr
    assert "Traceback" in result.stderr


@pytest.mark.parametrize("channel,production", [("", "C123456789"), ("C123456789", ""),
                                               ("C123456789", "C123456789"),
                                               ("C0B12LDMB1A", "C123456789")])
def test_rehearsal_refuses_unset_or_production_destination(channel, production):
    def no_client(*args, **kwargs):
        pytest.fail("invalid routing reached transport construction")
    with pytest.raises(SlackError):
        notification_rehearsal.rehearse(token="unused", channel=channel, production_channel=production, client=no_client)


def test_isolated_rehearsal_exercises_real_http_construction():
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            method = requests[-1].full_url.split("/api/")[1].split("?")[0]
            return json.dumps({"auth.test": {"ok": True, "team_id": "T123456789", "user_id": "U123456789", "bot_id": "B123456789"},
                               "chat.postMessage": {"ok": True, "channel": "C987654321", "ts": "1234567890.000001"},
                               "chat.getPermalink": {"ok": True, "permalink": "https://skytruth.slack.com/archives/C987654321/p1234567890000001"}}[method]).encode()

    def opener(request, timeout):
        requests.append(request)
        return Response()

    def client(token, channel):
        return notification_rehearsal.Slack(token, channel, opener=opener)

    report = notification_rehearsal.rehearse(token="fixture", channel="C987654321", production_channel="C123456789", client=client)
    assert report["production_mutations"] is False
    assert len(requests) == 3
    assert json.loads(requests[1].data)["channel"] == "C987654321"
    assert requests[2].method == "GET"


@pytest.mark.parametrize("action", ["fetch", "https", "dns", "secret", "nested", "stripped", "caught"])
def test_node_children_inherit_guard_for_constructed_targets(action):
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node is required to verify child notification isolation")
    code = r'''
const action = process.argv[1];
const host = ["hooks", "slack", "com"].join(".");
const target = new URL("https://" + host + process.env.TEST_NOTIFICATION_PATH);
if (action === "fetch") fetch(target);
else if (action === "https") require("node:https").get({hostname: host, path: target.pathname});
else if (action === "dns") require("node:dns").promises.lookup(host);
else if (action === "secret") require("node:child_process").execFileSync("gcloud", ["secrets", "versions", "access", "latest", "--secret", ["shared-datasets", "slack-webhook-url"].join("-")]);
else if (action === "nested") require("node:child_process").execFileSync(process.execPath, ["-e", 'fetch("https://" + ["hooks", "slack", "com"].join(".") + process.env.TEST_NOTIFICATION_PATH)'], {stdio: "inherit"});
else if (action === "stripped") require("node:child_process").execFileSync(process.execPath, ["-e", "process.exit(0)"], {env: {PATH: process.env.PATH}});
else if (action === "caught") { try { fetch(target); } catch {} }
'''
    result = subprocess.run([node, "-e", code, action], capture_output=True, text=True,
                            env={**os.environ, "TEST_NOTIFICATION_PATH": "/services/never/send"})
    assert result.returncode != 0
    if action != "caught":
        assert "Notification test isolation forbids" in result.stderr


def test_explicitly_stripped_node_child_env_is_refused_before_spawn():
    node = shutil.which("node")
    if node is None:
        pytest.fail("Node is required to verify child notification isolation")
    with pytest.raises(BaseException, match="unguarded Node child environment"):
        subprocess.run([node, "-e", "process.exit(0)"], check=True, env={"PATH": os.environ["PATH"]})
