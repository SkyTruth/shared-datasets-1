#!/usr/bin/env python3
"""One owner-requested closure read for log alerts; never poll or create a parent."""
from __future__ import annotations

import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import storage
from services.monitoring_slack.run import INCIDENT, PROJECT, Store, deliver, require
from scripts.slack_incident_api import Slack


def reconcile(key, session, store, slack, identity):
    require(isinstance(key, str) and INCIDENT.fullmatch(key), "invalid incident ID")
    name = f"projects/{PROJECT}/alerts/{key}"
    response = session.get("https://monitoring.googleapis.com/v3/" + name, timeout=30)
    response.raise_for_status()
    alert = response.json()
    require(alert.get("name") == name and alert.get("state") == "CLOSED" and alert.get("closeTime"), "Monitoring has not confirmed closure")
    deliver({"incident_id": key, "state": "closed", "policy_name": alert["policy"]["displayName"],
             "summary": "Closed by Monitoring. This does not establish dataset or deployment recovery."},
            store, slack, identity, create=False)


def main():
    require(len(sys.argv) == 2 and os.environ.get("GITHUB_REF") == "refs/heads/main"
            and os.environ.get("GITHUB_ACTOR") == "jonaraphael", "owner-only main reconciliation required")
    slack = Slack(os.environ["SHARED_DATASETS_SLACK_BOT_TOKEN"], os.environ["SHARED_DATASETS_SLACK_CHANNEL_ID"])
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    store = Store(storage.Client(project=PROJECT).bucket("skytruth-shared-datasets-1-monitoring-slack"))
    reconcile(sys.argv[1], AuthorizedSession(credentials), store, slack, slack.identity())


if __name__ == "__main__":
    main()
