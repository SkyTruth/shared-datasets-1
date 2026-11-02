"""Authenticated Pub/Sub receiver: one editable Slack parent per Monitoring incident."""
from __future__ import annotations

import base64
import html
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
import re

from google.api_core.exceptions import NotFound
from google.cloud import storage

from scripts.slack_incident_api import Slack, TIMESTAMP
from ingestion.common import decision_events
from services.monitoring_slack import decisions

PROJECT = "shared-datasets-1"
SUBSCRIPTION = f"projects/{PROJECT}/subscriptions/monitoring-slack"
INCIDENT = re.compile(r"[A-Za-z0-9_.-]{1,128}")
SCHEMA = "monitoring-slack-v1"
DECISION_SCHEMA = "monitoring-slack-decision-v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def event(raw):
    envelope = json.loads(raw)
    require(envelope.get("subscription") in {SUBSCRIPTION, decision_events.SUBSCRIPTION}, "foreign subscription")
    payload = json.loads(base64.b64decode(envelope["message"]["data"], validate=True))
    if envelope["subscription"] == decision_events.SUBSCRIPTION:
        return decision_events.event(payload)
    require(payload.get("version") == "1.2", "unsupported Monitoring event version")
    incident = payload["incident"]
    require(incident.get("scoping_project_id") == PROJECT, "foreign Monitoring project")
    require(isinstance(incident.get("incident_id"), str) and INCIDENT.fullmatch(incident["incident_id"]), "invalid incident ID")
    require(incident.get("state") in {"open", "closed"}, "invalid incident state")
    require(isinstance(incident.get("policy_name"), str) and incident["policy_name"], "missing policy name")
    return incident


def message(incident, closed):
    title = html.escape(incident["policy_name"][:512], quote=False)
    summary = ("Monitoring closed this incident. Dataset or deployment recovery requires separate verification."
               if closed else html.escape(str(incident.get("summary") or "")[:2000], quote=False))
    mark = ":warning:" if incident.get("severity") == "WARNING" else ":red_circle:"
    state = ":white_check_mark: Closed by Monitoring" if closed else mark + " Open"
    url = f"https://console.cloud.google.com/monitoring/alerting/alerts/{incident['incident_id']}?project={PROJECT}"
    action = ("Verify the affected execution and mutation outcome; Monitoring closure alone is not recovery proof."
              if closed else "Inspect the linked incident and affected execution; verify writes before any replay and use the existing recovery path.")
    return {"text": (f"{state} — {title}\n{summary}\n"
                     "*Impact:* Unknown; consumer impact has not been established.\n"
                     "*Mutation state:* Unknown; Monitoring does not verify data or resource changes.\n"
                     "*Owner:* jonaraphael (triage); operation owner unknown.\n"
                     f"*Required action:* {action}\n<{url}|Monitoring incident>"), "blocks": []}


class Store:
    def __init__(self, bucket):
        self.bucket = bucket

    def load(self, incident_id):
        return self.read(f"incidents/{incident_id}.json")

    def read(self, path):
        blob = self.bucket.blob(path)
        try:
            blob.reload()
        except NotFound:
            return None, 0
        generation = int(blob.generation)
        return json.loads(blob.download_as_bytes(if_generation_match=generation)), generation

    def save(self, incident_id, value, generation):
        return self.write(f"incidents/{incident_id}.json", value, generation)

    def write(self, path, value, generation):
        blob = self.bucket.blob(path)
        blob.upload_from_string(json.dumps(value, sort_keys=True), content_type="application/json", if_generation_match=generation, retry=None)
        return int(blob.generation)

    def paths(self, prefix):
        return [blob.name for blob in self.bucket.list_blobs(prefix=prefix)]


def validate_record(record, identity):
    fields = {"schema", "identity", "ts", "closed"}
    require(isinstance(record, dict) and ((set(record) == fields and record["schema"] == SCHEMA)
            or (set(record) == fields | {"decision", "decision_view"} and record["schema"] == DECISION_SCHEMA))
            and record["identity"] == identity and type(record["closed"]) is bool, "incompatible incident receipt")
    require(isinstance(record["ts"], str) and TIMESTAMP.fullmatch(record["ts"]), "unconfirmed Slack post; repair the original receipt before retrying")
    if record["schema"] == DECISION_SCHEMA:
        decisions.resolved_monitoring({"incident_id": "validation", "policy_name": decisions.POLICY}, record["decision"])
        require(record["decision_view"] is None or isinstance(record["decision_view"], str)
                and re.fullmatch(r"[0-9a-f]{64}", record["decision_view"]), "invalid decision view acknowledgement")


def deliver(incident, store, slack, identity, *, create=True):
    key = incident["incident_id"]
    record, generation = store.load(key)
    if record is None:
        require(create, "no original bot-owned Monitoring message")
        # A durable claim precedes the non-idempotent Slack post. An interrupted
        # claim requires receipt repair, never an automatic duplicate parent.
        record = {"schema": SCHEMA, "identity": identity, "ts": None, "closed": incident["state"] == "closed"}
        generation = store.save(key, record, 0)
        ts = slack.post(message(incident, record["closed"]))
        logging.info("Monitoring Slack receipt incident=%s channel=%s ts=%s", key, identity["channel"], ts)
        store.save(key, {**record, "ts": ts}, generation)
        return
    validate_record(record, identity)
    if incident["state"] == "closed" and not record["closed"]:
        record = {**record, "closed": True}
        generation = store.save(key, record, generation)
    if record["schema"] == DECISION_SCHEMA:
        payload = decisions.resolved_monitoring(incident, record["decision"])
        fingerprint = decision_events.digest(payload)
        if record["decision_view"] != fingerprint:
            slack.update(record["ts"], payload)
            store.save(key, {**record, "decision_view": fingerprint}, generation)
    elif record["closed"]:
        # Persist closure before the idempotent update. Any retry, including an
        # old open event, finishes this update and can never reopen the parent.
        slack.update(record["ts"], message(incident, True))


def read_alert(incident_id):
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    response = AuthorizedSession(credentials).get(f"https://monitoring.googleapis.com/v3/projects/{PROJECT}/alerts/{incident_id}", timeout=30)
    response.raise_for_status()
    return response.json()


def handler(store, slack, identity, *, alert_reader=None):
    alert_reader = read_alert if alert_reader is None else alert_reader
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                require(self.path == "/", "unknown endpoint")
                length = int(self.headers.get("Content-Length", "0"))
                require(0 < length <= 10 * 1024 * 1024, "invalid event size")
                incident = event(self.rfile.read(length))
                if incident.get("schema") == decision_events.SCHEMA:
                    decisions.deliver(incident, store, slack, identity)
                else:
                    logging.info("Monitoring event incident=%s state=%s", incident["incident_id"], incident["state"])
                    deliver(incident, store, slack, identity)
                    if incident.get("policy_name") == decisions.POLICY:
                        decisions.link(incident, alert_reader(incident["incident_id"]), store, slack, identity)
            except Exception as error:
                # No request bodies, credentials or arbitrary provider errors in
                # logs/responses. Non-2xx leaves genuine delivery failures queued.
                logging.error("Monitoring Slack delivery failed: %s", type(error).__name__)
                self.send_response(503)
            else:
                self.send_response(204)
            self.end_headers()
    return Handler


def main():
    logging.basicConfig(level=logging.INFO)
    slack = Slack(os.environ["SHARED_DATASETS_SLACK_BOT_TOKEN"], os.environ["SHARED_DATASETS_SLACK_CHANNEL_ID"])
    identity = slack.identity()
    bucket = storage.Client(project=PROJECT).bucket(os.environ["MONITORING_SLACK_STATE_BUCKET"])
    permissions = {"storage.objects.get", "storage.objects.create", "storage.objects.delete", "storage.objects.list"}
    require(set(bucket.test_iam_permissions(sorted(permissions))) == permissions, "incident state permissions unavailable")
    # Cloud Run does not become ready (and policies cannot switch channels)
    # until bot authentication and the private state permission probe succeed.
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), handler(Store(bucket), slack, identity)).serve_forever()


if __name__ == "__main__":
    main()
