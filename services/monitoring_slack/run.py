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

PROJECT = "shared-datasets-1"
SUBSCRIPTION = f"projects/{PROJECT}/subscriptions/monitoring-slack"
INCIDENT = re.compile(r"[A-Za-z0-9_.-]{1,128}")
SCHEMA = "monitoring-slack-v1"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def event(raw):
    envelope = json.loads(raw)
    require(envelope.get("subscription") == SUBSCRIPTION, "foreign subscription")
    payload = json.loads(base64.b64decode(envelope["message"]["data"], validate=True))
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
    return {"text": f"{state} — {title}\n{summary}\n<{url}|Monitoring incident>", "blocks": []}


class Store:
    def __init__(self, bucket):
        self.bucket = bucket

    def load(self, incident_id):
        blob = self.bucket.blob(f"incidents/{incident_id}.json")
        try:
            blob.reload()
        except NotFound:
            return None, 0
        generation = int(blob.generation)
        return json.loads(blob.download_as_bytes(if_generation_match=generation)), generation

    def save(self, incident_id, value, generation):
        blob = self.bucket.blob(f"incidents/{incident_id}.json")
        blob.upload_from_string(json.dumps(value, sort_keys=True), content_type="application/json", if_generation_match=generation, retry=None)
        return int(blob.generation)


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
    require(isinstance(record, dict) and set(record) == {"schema", "identity", "ts", "closed"}
            and record["schema"] == SCHEMA and record["identity"] == identity
            and type(record["closed"]) is bool, "incompatible incident receipt")
    require(isinstance(record["ts"], str) and TIMESTAMP.fullmatch(record["ts"]), "unconfirmed Slack post; repair the original receipt before retrying")
    if incident["state"] == "closed" and not record["closed"]:
        record = {**record, "closed": True}
        store.save(key, record, generation)
    if record["closed"]:
        # Persist closure before the idempotent update. Any retry, including an
        # old open event, finishes this update and can never reopen the parent.
        slack.update(record["ts"], message(incident, True))


def handler(store, slack, identity):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                require(self.path == "/", "unknown endpoint")
                length = int(self.headers.get("Content-Length", "0"))
                require(0 < length <= 128 * 1024, "invalid event size")
                incident = event(self.rfile.read(length))
                logging.info("Monitoring event incident=%s state=%s", incident["incident_id"], incident["state"])
                deliver(incident, store, slack, identity)
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
    permissions = {"storage.objects.get", "storage.objects.create", "storage.objects.delete"}
    require(set(bucket.test_iam_permissions(sorted(permissions))) == permissions, "incident state permissions unavailable")
    # Cloud Run does not become ready (and policies cannot switch channels)
    # until bot authentication and the private state permission probe succeed.
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), handler(Store(bucket), slack, identity)).serve_forever()


if __name__ == "__main__":
    main()
