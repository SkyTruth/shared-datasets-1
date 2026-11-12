"""Authenticated receiver: one execution parent, with native receipts for other policies."""
from __future__ import annotations

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
import re

from google.api_core.exceptions import NotFound
from google.cloud import storage

from scripts.slack_incident_api import Slack, TIMESTAMP
from ingestion.common import decision_events
from services.monitoring_slack import decisions, executions, messages

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
    title = incident["policy_name"]
    summary = ("Monitoring condition cleared; recovery requires separate verification."
               if closed else incident.get("summary", ""))
    url = f"https://console.cloud.google.com/monitoring/alerting/alerts/{incident['incident_id']}?project={PROJECT}"
    action = ("Verify the affected execution or resource outcome."
              if closed else "Inspect the Monitoring incident and address the reported condition.")
    return messages.card(f"{'Closed by Monitoring' if closed else 'Open'} — {title}", details=[summary], action=action,
                         links=[(url, "Monitoring incident")],
                         status="warning" if closed or incident.get("severity") == "WARNING" else "error")


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
    if record is not None and record["schema"] == executions.RECEIPT_SCHEMA:
        require(set(record) == {"schema", "identity", "ts", "source", "closed"}
                and record["identity"] == identity and isinstance(record["ts"], str)
                and TIMESTAMP.fullmatch(record["ts"]) and type(record["closed"]) is bool, "incompatible execution receipt")
        shared, _ = executions.load(store, record["source"], identity)
        require(shared is not None and shared["ts"] == record["ts"], "missing original execution parent")
        # Owner-requested Monitoring closure reuses the execution alias, too.
        if incident["policy_name"] == executions.FAILURE_POLICY:
            executions.report(store, slack, identity, record["source"],
                              failure=(key, incident["state"] == "closed", ""))
        store.save(key, {**record, "closed": record["closed"] or incident["state"] == "closed"}, generation)
        return
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


def read_failure(alert):
    """Read bounded error evidence for the exact execution, never another attempt."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    labels = alert.get("log", {}).get("extractedLabels", {})
    if not labels.get("execution"):
        return ""
    resource = alert.get("resource", {})
    require(resource.get("type") == "cloud_run_job" and resource.get("labels", {}).get("location") == "us-central1", "foreign failure resource")
    ctx = executions.context({"project": resource["labels"].get("project_id"), "job": resource["labels"].get("job_name"), "execution": labels["execution"]})
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform.read-only"])
    response = AuthorizedSession(credentials).post("https://logging.googleapis.com/v2/entries:list", json={
        "resourceNames": [f"projects/{PROJECT}"], "orderBy": "timestamp desc", "pageSize": 20,
        "filter": (f'resource.type="cloud_run_job" resource.labels.project_id="{PROJECT}" '
                   f'resource.labels.location="us-central1" resource.labels.job_name="{ctx["job"]}" '
                   f'labels."run.googleapis.com/execution_name"="{ctx["execution"]}" severity>=ERROR'),
    }, timeout=30)
    response.raise_for_status()
    messages = []
    for row in response.json().get("entries", []):
        require(row.get("resource") == resource
                and row.get("labels", {}).get("run.googleapis.com/execution_name") == ctx["execution"], "foreign failure log evidence")
        text = row.get("textPayload") or row.get("jsonPayload", {}).get("message")
        if text:
            lines = [line.strip() for line in str(text).splitlines() if line.strip()]
            if lines:
                messages.append(lines[-1][:800])
    # System task-exit summaries arrive after application tracebacks. Prefer
    # the actual exception over the later generic "exit code 1" observation.
    return next((text for text in messages if re.match(r"[\w.]+(?:Error|Exception):", text)),
                messages[0] if messages else labels.get("failure_reason", ""))


def handler(store, slack, identity, *, alert_reader=None, failure_reader=None):
    alert_reader = read_alert if alert_reader is None else alert_reader
    failure_reader = read_failure if failure_reader is None else failure_reader
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                require(self.path == "/", "unknown endpoint")
                length = int(self.headers.get("Content-Length", "0"))
                require(0 < length <= 10 * 1024 * 1024, "invalid event size")
                incident = event(self.rfile.read(length))
                if incident.get("schema") in {decision_events.SCHEMA, decision_events.REVIEW_SCHEMA}:
                    decisions.deliver(incident, store, slack, identity)
                else:
                    logging.info("Monitoring event incident=%s state=%s", incident["incident_id"], incident["state"])
                    if incident["policy_name"] in {decisions.POLICY, executions.FAILURE_POLICY}:
                        alert = alert_reader(incident["incident_id"])
                        cause = failure_reader(alert) if incident["policy_name"] == executions.FAILURE_POLICY else ""
                        if executions.monitoring(incident, alert, store, slack, identity, cause=cause):
                            self.send_response(204)
                            self.end_headers()
                            return
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
