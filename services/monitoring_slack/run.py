"""Scheduler-invoked Monitoring relay. No webhook, GitHub or dataset authority."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
import json
import os
import re
import sys
from uuid import uuid4

from google.api_core import exceptions as api_errors
import google.auth
from google.auth.transport.requests import AuthorizedSession
from google.cloud import storage

from ingestion.common import operational_events
from scripts import gcp_read_retry, operational_retry
from scripts.slack_incident_api import ReadOnlySlack, Slack
from services.monitoring_slack import model, receipts
from services.monitoring_slack.store import CAPABILITY_OBJECT, Conflict, Store

MAX_PAGES = 100
MAX_ROWS = 5000


class Monitoring:
    def __init__(self, session, config):
        self.session, self.config = session, config

    def _read(self, path, params=None, *, api="monitoring"):
        model.require(api in {"monitoring", "secret", "run"}, "unsupported relay read API")
        host, version = {"monitoring": ("monitoring.googleapis.com", "v3"),
                         "secret": ("secretmanager.googleapis.com", "v1"), "run": ("run.googleapis.com", "v2")}[api]
        response = self.session.get(f"https://{host}/{version}/{path}",
                                        params=params, timeout=operational_retry.request_timeout(15),
                                        allow_redirects=False, stream=True)
        try:
            if response.status_code != 200:
                raise api_errors.from_http_status(response.status_code, "relay read rejected")
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                model.require(len(body) <= model.MAX_BYTES, "provider response exceeds relay boundary")
                operational_retry.request_timeout(15)  # Streaming cannot extend the owning read deadline.
            operational_retry.request_timeout(15)
            # Parsing/provenance failures are deliberately outside retry classification.
            return model.parse_json(bytes(body))
        finally:
            response.close()

    def read(self, path, params=None, *, api="monitoring"):
        return operational_retry.run_read(lambda: self._read(path, params, api=api),
                                         classify=gcp_read_retry.classify, label="Monitoring relay read")

    def pages(self, path, params, field, allowed):
        return operational_retry.run_read(lambda: self._pages(path, params, field, allowed),
                                         classify=gcp_read_retry.classify, label="Monitoring complete inventory")

    def _pages(self, path, params, field, allowed):
        result, tokens, token = [], set(), None
        for _ in range(MAX_PAGES):
            query = {**params, **({"pageToken": token} if token else {})}
            value = self._read(path, query)
            model.require(isinstance(value, dict) and value.keys() <= allowed, "malformed Monitoring list response")
            model.require("executionErrors" not in value or value["executionErrors"] == [],
                          "Monitoring inventory reports incomplete execution")
            model.require("totalSize" not in value or type(value["totalSize"]) is int and value["totalSize"] >= 0,
                          "invalid Monitoring total estimate")
            rows = value.get(field, [])
            model.require(isinstance(rows, list), "invalid Monitoring result list")
            result.extend(rows)
            model.require(len(result) <= MAX_ROWS, "Monitoring inventory exceeds reviewed bound")
            token = value.get("nextPageToken", "")
            model.require(isinstance(token, str) and len(token) <= 8192, "invalid Monitoring page token")
            if not token:
                return result
            model.require(token not in tokens, "Monitoring pagination repeated")
            tokens.add(token)
        raise model.Invalid("Monitoring pagination exceeds reviewed bound")

    def alerts(self, policy, cutoff, *, open_only=False):
        model.instant(cutoff)
        # Alerts filter literals accept whole UTC seconds, unlike response and
        # heartbeat timestamps. Flooring widens the overlap without losing facts.
        cutoff = cutoff[:19] + "Z"
        predicate = "state=OPEN" if open_only else (
            f'(state=OPEN OR open_time>="{cutoff}" OR close_time>="{cutoff}")')
        return self.pages(f"projects/{self.config['project']}/alerts",
                          {"filter": f'policy.name="{policy}" AND {predicate}', "pageSize": 1000},
                          "alerts", {"alerts", "nextPageToken", "totalSize"})

    def alert(self, name):
        model.require(name.startswith(f"projects/{self.config['project']}/alerts/"), "wrong exact alert read")
        return self.read(name)

    def points(self, now):
        return self.pages(f"projects/{self.config['project']}/timeSeries", {
            "filter": f'metric.type="{self.config["health_metric"]}" AND resource.type="global" AND resource.labels.project_id="{self.config["project"]}"',
            "interval.startTime": model.before(now, model.FRESH_SECONDS), "interval.endTime": now,
            "view": "FULL", "pageSize": 1000}, "timeSeries", {"timeSeries", "nextPageToken", "unit", "executionErrors"})

    def token(self):
        value = self.read(self.config["slack_secret_version"] + ":access", api="secret")
        model.require(isinstance(value, dict) and value.get("name") == self.config["slack_secret_version"]
                      and value.keys() <= {"name", "payload"} and isinstance(value.get("payload"), dict)
                      and isinstance(value["payload"].get("data"), str), "wrong Slack secret version")
        try:
            token = base64.b64decode(value["payload"]["data"], validate=True).decode()
        except (KeyError, ValueError, UnicodeError) as error:
            raise model.Invalid("invalid Slack bot secret") from error
        model.require(token.startswith("xoxb-") and token.strip() == token and len(token) < 1000, "expected explicit Slack bot secret")
        return token

    def execution(self, name):
        model.require(isinstance(name, str) and re.fullmatch(re.escape(self.config["relay_job"])
                      + r"/executions/monitoring-slack-relay-[a-z0-9]{5}", name), "execution GET escaped the relay's own job")
        return self.read(name, api="run")


def loaded(store, config, now):
    snapshot = store.load()
    value = model.new_state(config) if snapshot.value is None else model.validate_state(snapshot.value, config, now)
    return snapshot, value


def discover(reader, state, config, now):
    """No persistent writes until ALL policy pages and exact pending reads finish."""
    model.require(model.instant(now) >= model.instant(config["activated_at"]), "relay activation is in the future")
    cutoff = max(config["activated_at"], model.before(state["covered_through"], model.OVERLAP_SECONDS), key=model.instant)
    found = {}
    for policy in config["policies"]:
        # Independent all-open read does not rely on age or the cursor filter.
        for raw in reader.alerts(policy["name"], cutoff, open_only=True) + reader.alerts(policy["name"], cutoff):
            alert = model.normalize(raw, config, now)
            model.require(alert["policy"] == policy["name"], "policy-filter response escaped its allowlist")
            if model.instant(alert["opened_at"]) < model.instant(config["activated_at"]):
                continue  # Explicit rollout boundary; historical native parents remain audit references.
            old = found.get(alert["name"])
            if old is not None:
                model.require(all(old[key] == alert[key] for key in ("policy", "opened_at", "kind")), "conflicting Monitoring identity")
                if old["state"] == "CLOSED":
                    model.require(alert["state"] == "OPEN" or old["closed_at"] == alert["closed_at"], "conflicting Monitoring closure")
                    continue
            found[alert["name"]] = alert
    for name, episode in state["episodes"].items():
        if (episode["alert"]["state"] == "OPEN" or episode["operation"] is not None
                or episode["ts"] is not None and episode["applied_sha256"] != episode["desired_sha256"]
                or episode["ts"] is not None and episode["alert"]["kind"] == "health" and episode["recovery"] is None):
            found.setdefault(name, episode["alert"])
    # An exact GET wins over lists. A stale GET still cannot regress saved CLOSED.
    for name in sorted(found):
        alert = model.normalize(reader.alert(name), config, now)
        model.require(alert["name"] == name, "exact GET returned another Monitoring alert")
        found[name] = alert
    proofs = {}
    closed_health = [alert for alert in found.values() if alert["kind"] == "health" and alert["state"] == "CLOSED"
                     and (not state["episodes"].get(alert["name"], {}).get("recovery")
                          or state["episodes"][alert["name"]]["applied_sha256"] != state["episodes"][alert["name"]]["desired_sha256"])]
    if closed_health:
        points = reader.points(now)
        for alert in closed_health:
            proof = model.heartbeat(points, alert, config, now)
            proofs[alert["name"]] = proof
    return found, proofs


def persist_scan(store, config, observations, proofs, through, now):
    for _ in range(8):
        snapshot, state = loaded(store, config, now)
        desired = model.merge(state, observations, proofs, config, through, now=now)
        try:
            return store.save(desired, snapshot.generation).value
        except Conflict:
            continue
    raise Conflict("complete scan could not persist; cursor was not advanced by this worker")


def acknowledge(store, config, name, operation, now, *, ts=None):
    for _ in range(8):
        snapshot, state = loaded(store, config, now)
        episode = state["episodes"][name]
        model.require(episode["operation"] == operation, "delivery claim changed before acknowledgement")
        model.require(operation["phase"] == "sending", "unstarted claim cannot be acknowledged as delivered")
        episode["ts"] = ts if operation["kind"] == "post" else episode["ts"]
        episode["applied_sha256"] = operation["sha256"]
        episode["operation"] = None
        model.validate_state(state, config, now)
        try:
            return store.save(state, snapshot.generation).value
        except Conflict:
            continue
    raise Conflict("delivery acknowledgement is unresolved; do not replay its claim")


def pending_action(episode):
    return (episode["applied_sha256"] != episode["desired_sha256"]
            and not (episode["ts"] is None and episode["alert"]["kind"] == "health"
                     and episode["alert"]["state"] == "CLOSED"))


def deliver(reader, store, config, slack, *, clock=model.utc_now, environment=None):
    environment = os.environ if environment is None else environment
    model.require(config["mode"] == "live", "public delivery requires explicit live mode")
    model.require(slack.identity() == config.identity, "Slack bot identity differs from the pinned destination")
    _, state = loaded(store, config, clock())
    sent = 0
    for name in sorted(state["episodes"]):
        for _ in range(8):
            now = clock()
            snapshot, current = loaded(store, config, now)
            episode = current["episodes"][name]
            if episode["operation"] is not None or not pending_action(episode):
                break
            writer_name = model.execution_name(config, environment)
            with operational_retry.operation(profile=operational_retry.OBSERVER_READ):
                writer = receipts.execution_proof(reader.execution(writer_name), config, writer_name, clock(), args=[])
            if episode["recovery"] is not None:
                # A formerly fresh proof cannot authorize a queued green. Recheck
                # exact closure and point before claiming its public update.
                with operational_retry.operation(profile=operational_retry.OBSERVER_READ):
                    raw = reader.alert(name)
                    points = reader.points(now) if raw.get("state") == "CLOSED" else []
                    now = clock()  # Freshness is measured after the potentially slow reads.
                    alert = model.normalize(raw, config, now)
                    model.require(alert["name"] == name, "recovery GET returned another alert")
                    proof = model.heartbeat(points, alert, config, now) if alert["state"] == "CLOSED" else None
                current = model.merge(current, {name: alert}, {name: proof}, config,
                                      current["covered_through"], now=clock())
                episode = current["episodes"][name]
            payload = model.render(episode["alert"], episode["recovery"])
            operation = {"id": uuid4().hex, "kind": "post" if episode["ts"] is None else "update",
                         "sha256": model.digest(payload), "payload": payload, "started_at": clock(), "phase": "claimed", "writer": writer}
            episode["operation"] = operation
            model.validate_state(current, config, clock())
            try:
                claimed = store.save(current, snapshot.generation)
            except Conflict:
                continue  # No transport was attempted; a new read must win the claim.
            # A distinct CAS is the only authority to start this exact send.
            # Losing it leaves a provably pre-send claim; never adopt another.
            current = deepcopy(claimed.value)
            operation = {**operation, "phase": "sending"}
            current["episodes"][name]["operation"] = operation
            model.validate_state(current, config, clock())
            store.save(current, claimed.generation)
            # Any exception/crash now leaves durable 'sending' uncertainty.
            ts = slack.post(payload) if operation["kind"] == "post" else None
            if operation["kind"] == "update":
                slack.update(episode["ts"], payload)
            acknowledge(store, config, name, operation, clock(), ts=ts)
            sent += 1
            break
        else:
            raise Conflict("Slack claim contention exhausted without a send")
    _, final = loaded(store, config, clock())
    uncertain = sum(episode["operation"] is not None for episode in final["episodes"].values())
    model.require(uncertain == 0, "unconfirmed Slack operation requires authoritative inspection; automatic replay is frozen")
    model.require(not any(pending_action(episode) for episode in final["episodes"].values()),
                  "actionable lifecycle work remains; a later complete cycle must reconcile it")
    return sent


def cycle(reader, store, config, *, clock=model.utc_now, slack_factory=Slack, environment=None):
    now = clock()
    _, state = loaded(store, config, now)
    with operational_retry.operation(profile=operational_retry.OBSERVER_READ):
        observations, proofs = discover(reader, state, config, now)
    # This cursor attests durable discovery, not Slack delivery. An ACK failure
    # leaves the complete observations and pre-send claim available for inspection.
    persist_scan(store, config, observations, proofs, now, clock())
    count = 0
    if config["mode"] == "live":
        count = deliver(reader, store, config, slack_factory(reader.token(), config["slack_channel"]), clock=clock, environment=environment)
        # Only a complete normal LIVE cycle establishes relay health. API-only
        # success, shadow evaluation and capability canaries cannot supply it.
        operational_events.emit("job_outcome", "success", details={
            "mutation_status": "completed", "published_data_state": "unchanged"})
    return {"observed": len(observations), "public_operations": count, "mode": config["mode"]}


def capability_context(environ):
    execution = environ.get("CLOUD_RUN_EXECUTION", "")
    github_run = environ.get("SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL", "")
    model.require(isinstance(execution, str) and re.fullmatch(r"monitoring-slack-relay-[a-z0-9]{5}", execution),
                  "capability verification requires the exact managed relay job execution")
    model.require(isinstance(github_run, str) and re.fullmatch(
        r"https://github\.com/SkyTruth/shared-datasets-1/actions/runs/[1-9][0-9]*", github_run),
        "capability verification requires its protected repository run URL")
    # The protected caller independently verifies this execution and immutable
    # image. Environment strings alone never constitute that external proof.
    return {"execution_name": execution, "github_run_url": github_run}


def verify_capabilities(reader, store, config, *, clock=model.utc_now, slack_factory=ReadOnlySlack, environ=None):
    environment = os.environ if environ is None else environ
    context = capability_context(environment)
    execution = model.execution_name(config, environment)
    now = clock()
    _, state = loaded(store, config, now)
    with operational_retry.operation(profile=operational_retry.OBSERVER_READ):
        receipts.execution_proof(reader.execution(execution), config, execution, clock(),
                                 args=["--verify-capabilities"], github_run_url=context["github_run_url"])
        observations, _ = discover(reader, state, config, now)
        reader.points(now)
        slack = slack_factory(reader.token(), config["slack_channel"])
        model.require(slack.identity() == config.identity, "Slack capability identity mismatch")
        model.require(slack.history_capability() is True, "Slack history capability is unverified")
    verified = {"own_execution_verified": True, "history_read_verified": True}
    marker = store.load(CAPABILITY_OBJECT)
    if marker.value is not None:
        model.require(marker.value.get("binding") == config.binding, "capability marker belongs to another configuration")
    saved = store.save({"schema": "monitoring-slack-capability-v1", "binding": config.binding,
                        "checked_at": clock(), "nonce": uuid4().hex, **context, **verified}, marker.generation, CAPABILITY_OBJECT)
    model.require(store.load(CAPABILITY_OBJECT) == saved, "capability CAS marker readback changed")
    return {"verified": True, "observed": len(observations), "public_operations": 0,
            "binding": config.binding, **context, **verified}


def self_test():
    # Dependency/import/image check is intentionally offline and needs no configuration.
    payload = model.render({"name": "projects/self-test/alerts/example", "policy": "unused", "kind": "security",
                            "title": "Offline relay check", "state": "CLOSED", "opened_at": "2026-01-01T00:00:00Z",
                            "closed_at": "2026-01-01T00:01:00Z"}, None)
    model.require("Repair remains unverified" in payload["text"] and model.parse_json(model.canonical(payload)) == payload,
                  "offline relay renderer check failed")
    return {"self_test": "passed", "public_operations": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--self-test", action="store_true")
    group.add_argument("--verify-capabilities", action="store_true")
    group.add_argument("--reconcile-receipt-json")
    args = parser.parse_args(argv)
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    config = model.Config.parse(model.parse_json(os.environ.get("MONITORING_SLACK_CONFIG", "").encode()))
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    reader = Monitoring(AuthorizedSession(credentials), config)
    store = Store(storage.Client(project=config["project"], credentials=credentials), config["state_bucket"])
    if args.reconcile_receipt_json is not None:
        result = receipts.reconcile(reader, store, config, args.reconcile_receipt_json, slack_factory=ReadOnlySlack)
    else:
        result = verify_capabilities(reader, store, config) if args.verify_capabilities else cycle(reader, store, config)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # Provider exceptions can contain arbitrary response text: type only, never tokens/headers/payloads.
        print(f"Monitoring relay failed ({type(error).__name__}); inspect private operational state", file=sys.stderr)
        sys.exit(1)
