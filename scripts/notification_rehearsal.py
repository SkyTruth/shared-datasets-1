#!/usr/bin/env python3
"""Exercise notification delivery only in an explicitly isolated Slack channel."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.slack_incident_api import CHANNEL, Slack, SlackError
from scripts.slack_notify import build_slack_payload


SHARED_CHANNEL = "C0B12LDMB1A"


def rehearse(*, token, channel, production_channel, client=Slack):
    if not CHANNEL.fullmatch(channel or ""):
        raise SlackError("An explicit isolated test channel is required")
    if not CHANNEL.fullmatch(production_channel or ""):
        raise SlackError("The configured production channel is required for isolation verification")
    if channel in {SHARED_CHANNEL, production_channel}:
        raise SlackError("Synthetic delivery to the shared production channel is forbidden")
    slack = client(token, channel)
    identity = slack.identity()
    payload = build_slack_payload(title="Isolated notification rehearsal", body="Synthetic transport verification; no production incident or dataset mutation.")
    timestamp = slack.post(payload)
    return {"channel": channel, "team": identity["team"], "timestamp": timestamp,
            "permalink": slack.permalink(timestamp), "production_mutations": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = rehearse(token=os.environ.get("SHARED_DATASETS_SLACK_TEST_BOT_TOKEN", ""),
                      channel=os.environ.get("SHARED_DATASETS_SLACK_TEST_CHANNEL_ID", ""),
                      production_channel=os.environ.get("SHARED_DATASETS_SLACK_CHANNEL_ID", ""))
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
