"""Offline request contracts for failure-only Slack incident delivery."""
from __future__ import annotations

import io
import json
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts.slack_incident_api import Slack, SlackError


CHANNEL = "C123456789"
PARENT = "1791270000.000001"
RETRY = "1791270000.000002"


def test_real_client_posts_failures_in_thread_then_only_updates_parent_on_recovery():
    methods = ["auth.test", "chat.postMessage", "chat.getPermalink", "chat.postMessage", "chat.update"]
    calls = []

    def opener(request, timeout):
        parsed = urlsplit(request.full_url)
        method = parsed.path.removeprefix("/api/")
        assert method == methods[len(calls)]
        assert request.get_header("Authorization") == "Bearer xoxb-test-secret"
        assert timeout == 30
        calls.append(method)
        if method == "chat.getPermalink":
            assert request.get_method() == "GET" and request.data is None
            assert parse_qs(parsed.query) == {"channel": [CHANNEL], "message_ts": [PARENT]}
            result = {"ok": True, "permalink": f"https://example.slack.com/archives/{CHANNEL}/p{PARENT.replace('.', '')}"}
        else:
            assert request.get_method() == "POST" and not parsed.query
            body = json.loads(request.data)
            if method == "auth.test":
                assert body == {}
                result = {"ok": True, "bot_id": "B123456789", "user_id": "U123456789", "team_id": "T123456789"}
            else:
                assert body["channel"] == CHANNEL
                if method == "chat.update":
                    assert body["ts"] == PARENT and body["text"] == "Resolved"
                    assert "thread_ts" not in body and "reply_broadcast" not in body
                elif len(calls) == 4:
                    assert body["thread_ts"] == PARENT and body["reply_broadcast"] is False
                else:
                    assert "thread_ts" not in body and "reply_broadcast" not in body
                result = {"ok": True, "channel": CHANNEL, "ts": RETRY if len(calls) == 4 else PARENT}
        return io.BytesIO(json.dumps(result).encode())

    client = Slack("xoxb-test-secret", CHANNEL, opener=opener, clock=lambda: 0, sleep=mock.Mock())
    assert client.identity() == {"team": "T123456789", "user": "U123456789", "channel": CHANNEL}
    assert client.post({"text": "Open"}) == PARENT
    assert client.permalink(PARENT).endswith(PARENT.replace(".", ""))
    assert client.post({"text": "Another failed attempt"}, thread_ts=PARENT) == RETRY
    client.update(PARENT, {"text": "Resolved"})
    assert calls == methods


@pytest.mark.parametrize("routing", [{"reply_broadcast": True}, {"thread_ts": PARENT}, {"channel": "C987654321"}])
def test_payload_cannot_override_thread_routing_or_broadcast(routing):
    opener = mock.Mock()
    client = Slack("xoxb-test-secret", CHANNEL, opener=opener)
    with pytest.raises(SlackError, match="override routing"):
        client.post({"text": "Failure", **routing}, thread_ts=PARENT)
    opener.assert_not_called()


@pytest.mark.parametrize("routing", [{"reply_broadcast": True}, {"thread_ts": PARENT}, {"channel": "C987654321"}, {"ts": RETRY}])
def test_parent_update_cannot_broadcast_or_override_its_identity(routing):
    opener = mock.Mock()
    client = Slack("xoxb-test-secret", CHANNEL, opener=opener)
    with pytest.raises(SlackError, match="override update"):
        client.update(PARENT, {"text": "Resolved", **routing})
    opener.assert_not_called()
