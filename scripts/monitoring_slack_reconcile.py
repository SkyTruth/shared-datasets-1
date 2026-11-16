#!/usr/bin/env python3
"""One owner-requested closure read for log alerts; never poll or create a parent."""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import storage
from services.monitoring_slack.run import INCIDENT, PROJECT, Store, deliver, require
from scripts.slack_incident_api import Slack
from services.monitoring_slack import executions


def verified_recovery(key, alert, store, identity, api, deployment_id):
    from scripts import slack_incidents as incidents
    from scripts.ingestion_targets import TARGETS

    require(deployment_id.isdecimal() and int(deployment_id) > 0, "invalid recovery deployment ID")
    alias, generation = store.load(key)
    require(alias is not None and alias.get("schema") == executions.RECEIPT_SCHEMA
            and alias["identity"] == identity, "recovery requires the original execution alias")
    ctx = executions.context(alias["source"])
    shared, _ = executions.load(store, ctx, identity)
    require(shared is not None and shared["ts"] == alias["ts"] and key in shared["failures"],
            "recovery differs from the original failure parent")
    resource = alert.get("resource", {})
    require(alert["policy"]["displayName"] == executions.FAILURE_POLICY
            and resource == {"type": "cloud_run_job", "labels": {"project_id": ctx["project"],
                "location": "us-central1", "job_name": ctx["job"]}}
            and alert.get("log", {}).get("extractedLabels", {}).get("execution") == ctx["execution"]
            and ctx["job"] in TARGETS, "recovery requires the exact managed execution failure")
    evidence, _ = incidents.terminal_success(api, "deployment:" + ctx["job"])
    require(evidence is not None and evidence["proof"]["record_id"] == int(deployment_id),
            "recovery requires the latest signed verified deployment")
    record = api.get(f"repos/{incidents.REPOSITORY}/deployments/{deployment_id}")
    failed_at = datetime.fromisoformat(alert["openTime"].replace("Z", "+00:00"))
    require(record["id"] == int(deployment_id) and incidents.instant(record["created_at"]) > failed_at
            and incidents.instant(evidence["time"]) > failed_at,
            "verified recovery predates the failure")
    proof = executions.recovery_proof({"deployment_id": evidence["proof"]["record_id"],
        "status_id": evidence["proof"]["status_id"], "run_id": evidence["run_id"], "attempt": evidence["attempt"]})
    return alias, generation, proof


def reconcile(key, session, store, slack, identity, *, deployment_id="", api=None):
    require(isinstance(key, str) and INCIDENT.fullmatch(key), "invalid incident ID")
    name = f"projects/{PROJECT}/alerts/{key}"
    response = session.get("https://monitoring.googleapis.com/v3/" + name, timeout=30)
    response.raise_for_status()
    alert = response.json()
    require(alert.get("name") == name and alert.get("state") == "CLOSED" and alert.get("closeTime"), "Monitoring has not confirmed closure")
    if deployment_id:
        alias, generation, proof = verified_recovery(key, alert, store, identity, api, deployment_id)
        executions.report(store, slack, identity, alias["source"], failure=(key, True, ""), recovery=proof)
        store.save(key, {**alias, "closed": True}, generation)
        return
    deliver({"incident_id": key, "state": "closed", "policy_name": alert["policy"]["displayName"],
             "summary": "Closed by Monitoring. This does not establish dataset or deployment recovery."},
            store, slack, identity, create=False)


def main():
    require(len(sys.argv) == 2 and os.environ.get("GITHUB_REF") == "refs/heads/main"
            and os.environ.get("GITHUB_ACTOR") == "jonaraphael", "owner-only main reconciliation required")
    slack = Slack(os.environ["SHARED_DATASETS_SLACK_BOT_TOKEN"], os.environ["SHARED_DATASETS_SLACK_CHANNEL_ID"])
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    store = Store(storage.Client(project=PROJECT).bucket("skytruth-shared-datasets-1-monitoring-slack"))
    from scripts.dataset_mutation_authorization import GitHub

    reconcile(sys.argv[1], AuthorizedSession(credentials), store, slack, slack.identity(),
              deployment_id=os.environ.get("RECOVERY_DEPLOYMENT_ID", ""), api=GitHub())


if __name__ == "__main__":
    main()
