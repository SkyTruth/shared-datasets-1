"""One durable Slack parent for all reports about an exact Cloud Run execution."""
from __future__ import annotations

import html
import logging
import re
from urllib.parse import urlencode

from ingestion.common import decision_events as wire
from scripts.slack_incident_api import TIMESTAMP

SCHEMA = "execution-slack-v1"
RECEIPT_SCHEMA = "monitoring-slack-execution-v1"
FAILURE_POLICY = "Scheduled ingestion Cloud Run execution failed"


def context(value):
    wire.require(isinstance(value, dict) and set(value) == {"project", "job", "execution"}
                 and value["project"] == wire.PROJECT, "foreign execution context")
    job, execution = value["job"], value["execution"]
    wire.require(isinstance(job, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,57}", job)
                 and isinstance(execution, str) and len(execution) <= 63
                 and re.fullmatch(re.escape(job) + r"-[a-z0-9]+", execution), "invalid execution identity")
    return dict(value)


def source(event):
    return context({key: event[key] for key in ("project", "job", "execution")})


def path(source):
    return "executions/" + wire.digest(context(source)) + ".json"


def load(store, source, identity):
    value, generation = store.read(path(source))
    if value is not None:
        wire.require(set(value) == {"schema", "source", "identity", "ts", "failures", "reviews", "decisions", "view"}
                     and value["schema"] == SCHEMA and value["source"] == context(source)
                     and value["identity"] == identity, "incompatible execution receipt")
        wire.require(isinstance(value["ts"], str) and TIMESTAMP.fullmatch(value["ts"]),
                     "unconfirmed execution parent; repair its receipt before retrying")
        wire.require(all(isinstance(value[key], dict) for key in ("failures", "reviews", "decisions"))
                     and (value["view"] is None or re.fullmatch(r"[0-9a-f]{64}", value["view"])), "invalid execution view")
        for failure in value["failures"].values():
            wire.require(set(failure) == {"closed", "cause"} and type(failure["closed"]) is bool
                         and isinstance(failure["cause"], str), "invalid execution failure")
        for review in value["reviews"].values():
            wire.require(set(review) == {"asset_slug", "release"}, "invalid execution review")
        for decision in value["decisions"].values():
            wire.require(set(decision) == {"text", "resolved"} and isinstance(decision["text"], str)
                         and type(decision["resolved"]) is bool, "invalid execution decision view")
    return value, generation


def render(value):
    ctx = value["source"]
    pending = [key for key in value["reviews"] | value["decisions"]
               if not value["decisions"].get(key, {}).get("resolved", False)]
    failures = value["failures"]
    if failures:
        closed = all(item["closed"] for item in failures.values())
        status = ":white_check_mark: Closed by Monitoring" if closed and not pending else ":red_circle: Open"
        title = f"{status} · {ctx['job']} execution failed"
        causes = list(dict.fromkeys(item["cause"] for item in failures.values() if item["cause"]))
        body = "\n".join("*Cause:* " + html.escape(cause, quote=False) for cause in causes)
        body += "\n*Data state:* Publication outcome needs verification; a failed execution may have written data."
        body += "\n*Next action:* Inspect the linked execution and publication records, repair the reported failure, then use the existing recovery path."
        if closed:
            body += "\nMonitoring closure does not verify dataset or deployment recovery."
    else:
        title = (":raising_hand: Decision needed" if pending else ":white_check_mark: Decision recorded") + f" · {ctx['job']}"
        body = "Identity review and publication acceptance are tracked separately."
    body += f"\n*Execution:* `{ctx['execution']}`\n*Owner:* jonaraphael (triage and identity decisions)."
    for key in sorted(value["reviews"] | value["decisions"]):
        if key in value["decisions"]:
            decision = value["decisions"][key]
            status = "Decision recorded" if decision["resolved"] else "Decision needed"
            body += f"\n\n*Review status:* {status}\n*Original request:*\n" + decision["text"]
        else:
            review = value["reviews"][key]
            body += f"\n\nIdentity review requested for `{review['asset_slug']}` release `{review['release']}`; detailed case evidence is pending."
    url = f"https://console.cloud.google.com/run/jobs/executions/details/us-central1/{ctx['execution']}?project={wire.PROJECT}"
    query = (f'resource.type="cloud_run_job" resource.labels.job_name="{ctx["job"]}" '
             f'labels."run.googleapis.com/execution_name"="{ctx["execution"]}"')
    logs = "https://console.cloud.google.com/logs/query?" + urlencode({"project": wire.PROJECT, "query": query})
    links = [f"<{url}|Execution>", f"<{logs}|Exact execution logs>"]
    for key in sorted(failures):
        links.append(f"<https://console.cloud.google.com/monitoring/alerting/alerts/{key}?project={wire.PROJECT}|Monitoring incident>")
    return {"text": title + "\n" + body.strip() + "\n" + " · ".join(links), "blocks": []}


def report(store, slack, identity, ctx, *, failure=None, review=None, decision=None, parent_ts=None):
    ctx = context(ctx)
    value, generation = load(store, ctx, identity)
    new = value is None
    if new:
        value = {"schema": SCHEMA, "source": ctx, "identity": identity, "ts": parent_ts,
                 "failures": {}, "reviews": {}, "decisions": {}, "view": None}
    if failure is not None:
        key, closed, cause = failure
        previous = value["failures"].get(key)
        value["failures"][key] = {"closed": closed or bool(previous and previous["closed"]),
                                  "cause": cause or (previous["cause"] if previous else "Execution failed; inspect the linked error logs.")}
    if review is not None:
        key, request = review
        wire.require(key not in value["reviews"] or value["reviews"][key] == request, "execution review changed")
        value["reviews"][key] = request
    if decision is not None:
        key, text, resolved = decision
        value["decisions"][key] = {"text": text, "resolved": resolved}
    # A generation-checked claim precedes every non-idempotent parent post.
    # A competing producer retries its queued event; it cannot claim a second parent.
    generation = store.write(path(ctx), value, generation)
    payload = render(value)
    fingerprint = wire.digest(payload)
    if new and parent_ts is None:
        ts = slack.post(payload)
        logging.info("Execution Slack receipt execution=%s channel=%s ts=%s", ctx["execution"], identity["channel"], ts)
        value = {**value, "ts": ts, "view": fingerprint}
        store.write(path(ctx), value, generation)
    else:
        # Repeat the idempotent update on a queued retry even when its view
        # hash matches. Another writer may have updated Slack out of order
        # before losing its generation-checked acknowledgement.
        slack.update(value["ts"], payload)
        store.write(path(ctx), {**value, "view": fingerprint}, generation)
    return value["ts"]


def monitoring(incident, alert, store, slack, identity, *, cause=""):
    """Correlate only authoritative exact-execution labels; retain legacy receipts."""
    from services.monitoring_slack import decisions
    wire.require(alert.get("name") == f"projects/{wire.PROJECT}/alerts/{incident['incident_id']}", "Monitoring identity mismatch")
    policy = alert.get("policy", {})
    wire.require(policy.get("displayName") == incident["policy_name"], "Monitoring policy mismatch")
    labels = alert.get("log", {}).get("extractedLabels", {})
    if incident["policy_name"] == decisions.POLICY:
        request = decisions.monitoring_context(alert)
        if request is None:
            return False
        ctx = source(request)
        event_id = wire.event_key(request)
        receipt, _ = decisions.load(store, event_id, identity)
        if receipt is not None and receipt["parent"]["owner"] == "legacy":
            return False  # Another bot's historical parent cannot be adopted.
        parent_ts = receipt["parent"]["ts"] if receipt else None
        review = (event_id, {key: request[key] for key in ("asset_slug", "release")})
    else:
        wire.require(incident["policy_name"] == FAILURE_POLICY, "unmanaged execution policy")
        if not labels.get("execution"):
            return False  # Pre-rollout incidents cannot be correlated by time or job alone.
        resource = alert.get("resource", {})
        resource_labels = resource.get("labels", {})
        wire.require(resource.get("type") == "cloud_run_job" and resource_labels.get("location") == "us-central1", "foreign execution resource")
        ctx = context({"project": resource_labels.get("project_id"), "job": resource_labels.get("job_name"), "execution": labels["execution"]})
        review, parent_ts = None, None
    key = incident["incident_id"]
    receipt, generation = store.load(key)
    if receipt is not None and receipt["schema"] != RECEIPT_SCHEMA:
        return False  # Never replace an already-delivered legacy parent with another.
    if receipt is not None:
        wire.require(set(receipt) == {"schema", "identity", "ts", "source", "closed"}
                     and receipt["identity"] == identity and receipt["source"] == ctx
                     and type(receipt["closed"]) is bool, "execution receipt changed identity")
    failure = ((key, incident["state"] == "closed", cause or labels.get("failure_reason", ""))
               if review is None else None)
    ts = report(store, slack, identity, ctx, failure=failure, review=review, parent_ts=parent_ts)
    wire.require(receipt is None or receipt["ts"] == ts, "Monitoring alias differs from its execution parent")
    desired = {"schema": RECEIPT_SCHEMA, "identity": identity, "ts": ts, "source": ctx,
               "closed": incident["state"] == "closed" or bool(receipt and receipt["closed"])}
    store.save(key, desired, generation)
    return True
