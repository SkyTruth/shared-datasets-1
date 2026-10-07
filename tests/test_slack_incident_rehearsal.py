"""Synthetic delivery exercises the real client without claiming recovery truth."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import subprocess
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import slack_incident_rehearsal as rehearsal
from scripts.slack_incident_api import Slack, SlackError
from workflow_helpers import load_workflow, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def environment():
    return {"GITHUB_REPOSITORY": rehearsal.REPOSITORY, "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_REF": "refs/heads/main", "GITHUB_WORKFLOW_REF": rehearsal.REPOSITORY + "/" + rehearsal.WORKFLOW + "@refs/heads/main",
            "GITHUB_ACTOR": "jonaraphael", "GITHUB_TRIGGERING_ACTOR": "jonaraphael", "GITHUB_WORKFLOW_SHA": SHA,
            "GITHUB_SHA": SHA, "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1"}


def slack():
    client = mock.Mock(spec=["identity", "post", "permalink", "update"])
    client.identity.return_value = {"team": "T123456789", "user": "U123456789", "channel": "C123456789"}
    client.post.side_effect = ["1791270000.000001", "1791270000.000002", "1791270000.000003"]
    client.permalink.return_value = "https://example.slack.com/archives/C123456789/p1791270000000001"
    return client


def test_rehearsal_opens_links_threads_and_resolves_only_clearly_labeled_synthetic_messages(tmp_path):
    client = slack()
    binding = rehearsal.context(environment(), SHA)
    result = rehearsal.rehearse(client, binding, tmp_path)
    assert result["status"] == "success" and result["stage"] == "complete"
    assert result["binding"] == binding
    assert result["acknowledgements"] == {"parent": "1791270000.000001", "retry": "1791270000.000002",
                                         "resolved-update": "1791270000.000001", "recovery": "1791270000.000003"}
    assert json.loads((tmp_path / "result.json").read_text()) == result
    assert client.method_calls == [
        mock.call.identity(),
        mock.call.post(mock.ANY), mock.call.permalink("1791270000.000001"),
        mock.call.post(mock.ANY, thread_ts="1791270000.000001"),
        mock.call.update("1791270000.000001", mock.ANY),
        mock.call.post(mock.ANY, thread_ts="1791270000.000001", broadcast=True),
    ]
    for call in client.post.call_args_list:
        assert "SYNTHETIC REHEARSAL 123/1" in call.args[0]["text"]
    assert "Resolved" in client.update.call_args.args[1]["text"]
    assert "does not represent production recovery" in client.update.call_args.args[1]["text"]


def test_failed_permalink_retains_parent_acknowledgement_and_does_not_retry_or_post_thread(tmp_path):
    client = slack()
    client.permalink.side_effect = SlackError("rejected request: invalid_arguments")
    with pytest.raises(SlackError, match="invalid_arguments"):
        rehearsal.rehearse(client, rehearsal.context(environment(), SHA), tmp_path)
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["status"] == "failed" and result["stage"] == "permalink"
    assert result["acknowledgements"] == {"parent": "1791270000.000001"}
    assert client.post.call_count == client.permalink.call_count == 1
    client.update.assert_not_called()


def test_complete_rehearsal_exercises_real_slack_http_boundary(tmp_path):
    parent = "1791270000.000001"
    channel = "C123456789"
    methods = ["auth.test", "chat.postMessage", "chat.getPermalink", "chat.postMessage", "chat.update", "chat.postMessage"]
    calls = []
    def opener(request, timeout):
        parsed = urlsplit(request.full_url)
        method = parsed.path.removeprefix("/api/")
        assert method == methods[len(calls)]
        calls.append(method)
        assert request.get_header("Authorization") == "Bearer xoxb-test-secret"
        assert timeout == 30
        if method == "chat.getPermalink":
            assert request.get_method() == "GET" and request.data is None
            assert parse_qs(parsed.query) == {"channel": [channel], "message_ts": [parent]}
            result = {"ok": True, "permalink": "https://example.slack.com/archives/" + channel + "/p" + parent.replace(".", "")}
        else:
            assert request.get_method() == "POST" and not parsed.query
            body = json.loads(request.data)
            if method == "auth.test":
                assert body == {}
                result = {"ok": True, "bot_id": "B123456789", "user_id": "U123456789", "team_id": "T123456789"}
            else:
                assert body["channel"] == channel and "SYNTHETIC REHEARSAL 123/1" in body["text"]
                if method == "chat.update":
                    assert body["ts"] == parent and "Resolved" in body["text"]
                elif len(calls) > 2:
                    assert body["thread_ts"] == parent
                    assert body["reply_broadcast"] is (len(calls) == 6)
                result = {"ok": True, "channel": channel, "ts": parent if method == "chat.update" else f"1791270000.{len(calls):06d}"}
                if len(calls) == 2:
                    result["ts"] = parent
        return io.BytesIO(json.dumps(result).encode())
    client = Slack("xoxb-test-secret", channel, opener=opener, clock=lambda: 0, sleep=mock.Mock())
    result = rehearsal.rehearse(client, rehearsal.context(environment(), SHA), tmp_path)
    assert calls == methods and result["status"] == "success"
    assert len(result["acknowledgements"]) == 4


@pytest.mark.parametrize("field,value", [
    ("GITHUB_REPOSITORY", "someone/fork"), ("GITHUB_EVENT_NAME", "pull_request"),
    ("GITHUB_REF", "refs/pull/1/merge"), ("GITHUB_REF", "refs/heads/feature"),
    ("GITHUB_WORKFLOW_REF", rehearsal.REPOSITORY + "/" + rehearsal.WORKFLOW + "@refs/heads/feature"),
    ("GITHUB_WORKFLOW_REF", rehearsal.REPOSITORY + "/.github/workflows/ci.yml@refs/heads/main"),
    ("GITHUB_ACTOR", "someone"), ("GITHUB_TRIGGERING_ACTOR", "someone"),
    ("GITHUB_SHA", "b" * 40), ("GITHUB_WORKFLOW_SHA", "not-a-sha"),
    ("GITHUB_RUN_ID", "0"), ("GITHUB_RUN_ATTEMPT", "1.0"),
])
def test_rehearsal_rejects_untrusted_context(field, value):
    env = environment()
    env[field] = value
    with pytest.raises(SlackError):
        rehearsal.context(env, SHA)


def test_rehearsal_rejects_changed_checkout():
    with pytest.raises(SlackError, match="checkout"):
        rehearsal.context(environment(), "b" * 40)


def test_rehearsal_workflow_is_protected_owner_dispatch_with_no_ledger_or_deployment_authority():
    workflow = load_workflow(ROOT / rehearsal.WORKFLOW)
    assert workflow_triggers(workflow) == {"workflow_dispatch": None}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"rehearsal"}
    job = workflow["jobs"]["rehearsal"]
    assert job["if"] == "github.ref == 'refs/heads/main' && github.actor == 'jonaraphael' && github.triggering_actor == 'jonaraphael'"
    assert job["environment"] == "shared-datasets-production"
    assert job["concurrency"] == {"group": "slack-incidents-${{ github.repository }}", "cancel-in-progress": False, "queue": "max"}
    assert "permissions" not in job
    steps = job["steps"]
    assert steps[1]["with"] == {"ref": "${{ github.workflow_sha }}", "fetch-depth": 0, "persist-credentials": False}
    assert steps[3]["with"]["python-version"] == "3.12.12"
    assert steps[4]["env"] == {"SHARED_DATASETS_SLACK_BOT_TOKEN": "${{ secrets.SHARED_DATASETS_SLACK_BOT_TOKEN }}",
                               "SHARED_DATASETS_SLACK_CHANNEL_ID": "${{ vars.SHARED_DATASETS_SLACK_CHANNEL_ID }}"}
    assert steps[4]["run"] == 'python scripts/slack_incident_rehearsal.py --work-dir "$RUNNER_TEMP/slack-incident-rehearsal"'
    assert steps[5]["if"] == "${{ always() }}"
    assert steps[5]["with"]["path"] == "${{ runner.temp }}/slack-incident-rehearsal/result.json"
    assert not any("env" in step for step in (steps[0], steps[1], steps[3], steps[5]))
    text = (ROOT / rehearsal.WORKFLOW).read_text() + (ROOT / "scripts/slack_incident_rehearsal.py").read_text()
    for forbidden in ("google-github-actions/auth", "id-token:", "deployments:", "attestations:", "slack_incidents.py", "deployment_revision", "terraform"):
        assert forbidden not in text


def test_actual_rehearsal_shell_guards_reject_wrong_ref_actor_trigger_or_checkout():
    steps = load_workflow(ROOT / rehearsal.WORKFLOW)["jobs"]["rehearsal"]["steps"]
    env = {**os.environ, **environment(), "ACTOR": "jonaraphael", "TRIGGERING_ACTOR": "jonaraphael", "WORKFLOW_SHA": SHA}
    owner_guard = 'git() { printf "%s\\n" "' + SHA + '"; }\n' + steps[2]["run"]
    for code, field, value in (
        (steps[0]["run"], None, None), (owner_guard, None, None),
        (steps[0]["run"], "GITHUB_REF", "refs/pull/1/merge"),
        (steps[0]["run"], "GITHUB_WORKFLOW_REF", rehearsal.REPOSITORY + "/.github/workflows/ci.yml@refs/heads/main"),
        (owner_guard, "ACTOR", "someone"), (owner_guard, "TRIGGERING_ACTOR", "someone"),
        (owner_guard, "GITHUB_EVENT_NAME", "push"), (owner_guard, "WORKFLOW_SHA", "b" * 40),
    ):
        changed = copy.copy(env)
        if field:
            changed[field] = value
        result = subprocess.run(["bash", "-c", code], env=changed, capture_output=True, text=True)
        assert (result.returncode == 0) is (field is None), result.stderr
