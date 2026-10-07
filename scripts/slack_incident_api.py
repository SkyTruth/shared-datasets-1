"""Slack Web API boundary for persistent operational incident messages."""
from __future__ import annotations

import json
import re
import time
import urllib.error
from urllib.parse import urlencode
import urllib.request

CHANNEL = re.compile(r"[CG][A-Z0-9]{8,}")
TIMESTAMP = re.compile(r"[0-9]+\.[0-9]{6}")


class SlackError(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise SlackError(message)


class Slack:
    def __init__(self, token, channel, *, opener=urllib.request.urlopen, clock=time.monotonic, sleep=time.sleep):
        require(isinstance(token, str) and bool(token.strip()), "Slack bot token is not configured")
        require(isinstance(channel, str) and CHANNEL.fullmatch(channel), "Slack incident channel must be a channel ID")
        self.token, self.channel, self.opener = token, channel, opener
        self.clock, self.sleep, self.last_post = clock, sleep, None

    def call(self, method, payload):
        require(method in {"auth.test", "chat.postMessage", "chat.update", "chat.getPermalink"}, "unsupported Slack method")
        url = "https://slack.com/api/" + method
        headers = {"Authorization": "Bearer " + self.token}
        if method == "chat.getPermalink":
            # This read method uses GET parameters in Slack's documented
            # contract and official SDK; message timestamps stay strings.
            url += "?" + urlencode(payload)
            data, verb = None, "GET"
        else:
            headers["Content-Type"] = "application/json; charset=utf-8"
            data, verb = json.dumps(payload).encode(), "POST"
        request = urllib.request.Request(
            url, data=data, headers=headers, method=verb,
        )
        try:
            with self.opener(request, timeout=30) as response:
                result = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError) as error:
            # Never include request/headers or arbitrary response bytes in logs.
            raise SlackError(f"Slack {method} delivery is unconfirmed ({type(error).__name__})") from None
        require(isinstance(result, dict), "invalid Slack API response")
        if result.get("ok") is not True:
            code = str(result.get("error", "invalid_response"))
            code = code if re.fullmatch(r"[a-z_]+", code) else "invalid_response"
            raise SlackError(f"Slack {method} rejected request: {code}")
        return result

    def identity(self):
        result = self.call("auth.test", {})
        require(result.get("bot_id") and re.fullmatch(r"U[A-Z0-9]+", str(result.get("user_id", ""))), "Slack token must identify a bot")
        require(re.fullmatch(r"T[A-Z0-9]+", str(result.get("team_id", ""))), "Slack workspace identity unavailable")
        return {"team": result["team_id"], "user": result["user_id"], "channel": self.channel}

    def post(self, payload, *, thread_ts=None, broadcast=False):
        require(not ({"channel", "thread_ts", "reply_broadcast"} & payload.keys()), "message cannot override routing")
        body = {**payload, "channel": self.channel, "unfurl_links": False, "unfurl_media": False}
        if thread_ts is not None:
            require(TIMESTAMP.fullmatch(thread_ts), "invalid parent message timestamp")
            body.update(thread_ts=thread_ts, reply_broadcast=broadcast)
        else:
            require(not broadcast, "broadcast requires a parent message")
        if self.last_post is not None:
            delay = 1.1 - (self.clock() - self.last_post)
            if delay > 0:
                self.sleep(delay)
        self.last_post = self.clock()
        result = self.call("chat.postMessage", body)
        require(result.get("channel") == self.channel and isinstance(result.get("ts"), str)
                and TIMESTAMP.fullmatch(result["ts"]), "Slack returned an invalid message identity")
        return result["ts"]

    def update(self, ts, payload):
        require(TIMESTAMP.fullmatch(ts), "invalid message timestamp")
        require(not ({"channel", "ts"} & payload.keys()), "message cannot override update identity")
        result = self.call("chat.update", {**payload, "channel": self.channel, "ts": ts})
        require(result.get("channel") == self.channel and result.get("ts") == ts, "Slack updated a different message")

    def permalink(self, ts):
        require(TIMESTAMP.fullmatch(ts), "invalid message timestamp")
        result = self.call("chat.getPermalink", {"channel": self.channel, "message_ts": ts})
        link = result.get("permalink", "")
        require(isinstance(link, str) and re.fullmatch(r"https://[a-z0-9-]+\.slack\.com/archives/" + self.channel + r"/p" + ts.replace(".", ""), link), "invalid Slack message permalink")
        return link
