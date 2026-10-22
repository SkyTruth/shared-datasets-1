"""The relay's persisted boundary; no GitHub or dataset recovery authority.

An authoritative Monitoring alert name identifies one episode. CLOSED is
terminal for that name. Every Slack operation is durably claimed before its
network call; a surviving claim is never automatically reassigned to a writer.
"""
from __future__ import annotations

import calendar
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
import re
from urllib.parse import urlencode
from uuid import UUID

from scripts.slack_incident_api import CHANNEL, TIMESTAMP
from scripts.slack_notify import build_slack_payload

SCHEMA = "monitoring-slack-state-v1"
CONFIG_SCHEMA = "monitoring-slack-config-v1"
FRESH_SECONDS = 600
OVERLAP_SECONDS = 600
MAX_BYTES = 2 * 1024 * 1024
MAX_EPISODES = 2000
MAX_RECEIPTS = 200
STAMP = re.compile(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?Z")


class Invalid(ValueError):
    """An external or persisted boundary failed its explicit contract."""


def require(condition, message):
    if not condition:
        raise Invalid(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return sha256(canonical(value)).hexdigest()


def parse_json(body):
    require(isinstance(body, bytes) and len(body) <= MAX_BYTES, "JSON exceeds relay boundary")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON field")
            result[key] = value
        return result

    try:
        return json.loads(body, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(Invalid("non-finite JSON")))
    except (UnicodeError, ValueError) as error:
        raise Invalid("invalid relay JSON") from error


def instant(value):
    require(isinstance(value, str) and (match := STAMP.fullmatch(value)), "expected UTC RFC3339 timestamp")
    try:
        seconds = calendar.timegm(datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%S").timetuple())
    except ValueError as error:
        raise Invalid("invalid timestamp") from error
    return seconds * 1_000_000_000 + int((match[2] or "").ljust(9, "0"))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def before(value, seconds):
    return datetime.fromtimestamp(instant(value) / 1e9 - seconds, timezone.utc).isoformat(
        timespec="microseconds").replace("+00:00", "Z")


def uuid4(value):
    require(isinstance(value, str), "expected canonical UUID4")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise Invalid("expected canonical UUID4") from error
    require(str(parsed) == value and parsed.version == 4, "expected canonical UUID4")
    return value


def image(value, config):
    region = config["relay_job"].split("/")[3]
    prefix = f"{region}-docker.pkg.dev/{config['project']}/shared-datasets-jobs/monitoring-slack-relay@sha256:"
    require(isinstance(value, str) and re.fullmatch(re.escape(prefix) + r"[0-9a-f]{64}", value), "relay image must be an exact immutable reference")
    return value


def execution_name(config, environment):
    require(environment.get("CLOUD_RUN_JOB") == "monitoring-slack-relay"
            and environment.get("CLOUD_RUN_TASK_INDEX") == "0" and environment.get("CLOUD_RUN_TASK_ATTEMPT") == "0",
            "relay writer must be the managed single task without retries")
    short = environment.get("CLOUD_RUN_EXECUTION")
    require(isinstance(short, str) and re.fullmatch(r"monitoring-slack-relay-[a-z0-9]{5}", short), "invalid managed relay execution")
    return config["relay_job"] + "/executions/" + short


def validate_writer(writer, config, now):
    require(isinstance(writer, dict) and writer.keys() == {"execution", "uid", "image", "config_binding", "created_at", "started_at"}, "invalid writer proof")
    require(isinstance(writer["execution"], str) and re.fullmatch(re.escape(config["relay_job"]) + r"/executions/monitoring-slack-relay-[a-z0-9]{5}", writer["execution"]), "writer belongs to another job")
    uuid4(writer["uid"])
    image(writer["image"], config)
    require(writer["config_binding"] == config.binding, "writer configuration changed")
    require(instant(writer["created_at"]) <= instant(writer["started_at"]) <= instant(now), "invalid writer chronology")
    return writer


@dataclass(frozen=True)
class Config:
    value: dict

    @classmethod
    def parse(cls, value):
        required = {"schema", "project", "policies", "health_metric", "health_policy_created_at",
                    "state_bucket", "slack_secret_version", "slack_channel", "slack_team", "slack_user", "activated_at", "relay_job", "relay_image"}
        require(isinstance(value, dict) and required <= value.keys()
                and value.keys() <= required | {"mode"}, "invalid relay configuration fields")
        value = deepcopy(value)
        value.setdefault("mode", "shadow")
        require(value["schema"] == CONFIG_SCHEMA and value["mode"] in {"shadow", "live"}, "invalid relay configuration")
        project = value["project"]
        require(isinstance(project, str) and re.fullmatch(r"[a-z][a-z0-9-]{4,61}[a-z0-9]", project), "invalid project")
        require(isinstance(value["relay_job"], str) and re.fullmatch(
            r"projects/" + re.escape(project) + r"/locations/[a-z]+-[a-z0-9]+/jobs/monitoring-slack-relay", value["relay_job"]), "invalid relay job resource")
        image(value["relay_image"], value)
        require(isinstance(value["state_bucket"], str) and re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", value["state_bucket"]), "invalid state bucket")
        require(isinstance(value["slack_secret_version"], str) and re.fullmatch(
            r"projects/(?:" + re.escape(project) + r"|[1-9][0-9]*)/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*",
            value["slack_secret_version"]), "secret must pin an exact resource and numeric version")
        for key, pattern in (("slack_channel", CHANNEL), ("slack_team", re.compile(r"T[A-Z0-9]+")),
                             ("slack_user", re.compile(r"U[A-Z0-9]+"))):
            require(isinstance(value[key], str) and pattern.fullmatch(value[key]), "invalid pinned Slack identity")
        require(isinstance(value["health_metric"], str) and re.fullmatch(
            r"custom\.googleapis\.com/[a-zA-Z0-9_/-]+", value["health_metric"]), "invalid heartbeat metric")
        instant(value["health_policy_created_at"])
        instant(value["activated_at"])
        policies = value["policies"]
        require(isinstance(policies, list) and len(policies) == 3, "exactly three independent policies are required")
        names, kinds = [], []
        for policy in policies:
            require(isinstance(policy, dict) and policy.keys() == {"name", "kind"}, "invalid policy configuration")
            require(isinstance(policy["name"], str) and re.fullmatch(
                r"projects/" + re.escape(project) + r"/alertPolicies/[1-9][0-9]*", policy["name"]), "invalid policy resource")
            require(policy["kind"] in {"health", "security"}, "unsupported independent policy kind")
            names.append(policy["name"])
            kinds.append(policy["kind"])
        require(len(set(names)) == 3 and sorted(kinds) == ["health", "security", "security"], "invalid independent policy set")
        value["policies"] = sorted(policies, key=lambda item: item["name"])
        return cls(value)

    def __getitem__(self, key):
        return self.value[key]

    @property
    def binding(self):
        # Deployment image and mode are not episode identity. Claims retain their
        # own verified image; an upgrade cannot erase parents or grant takeover.
        return digest({key: value for key, value in self.value.items() if key not in {"mode", "relay_image"}})

    @property
    def identity(self):
        return {"channel": self["slack_channel"], "team": self["slack_team"], "user": self["slack_user"]}

    def kind(self, name):
        matches = [item["kind"] for item in self["policies"] if item["name"] == name]
        require(len(matches) == 1, "alert policy is outside the independent allowlist")
        return matches[0]


def normalize(raw, config, now):
    require(isinstance(raw, dict) and raw.keys() <= {
        "name", "state", "openTime", "closeTime", "resource", "metadata", "metric", "log", "policy"}, "malformed Monitoring alert")
    name = raw.get("name")
    require(isinstance(name, str) and re.fullmatch(
        r"projects/" + re.escape(config["project"]) + r"/alerts/[A-Za-z0-9._-]+", name), "wrong alert resource")
    policy = raw.get("policy")
    require(isinstance(policy, dict) and policy.keys() <= {"name", "displayName", "severity", "userLabels"}, "invalid policy snapshot")
    kind = config.kind(policy.get("name"))
    require(raw.get("state") in {"OPEN", "CLOSED"}, "unspecified Monitoring state")
    opened = raw.get("openTime")
    require(instant(opened) <= instant(now), "future alert opening")
    closed = raw.get("closeTime")
    if raw["state"] == "CLOSED":
        require(instant(opened) <= instant(closed) <= instant(now), "invalid alert closure")
    else:
        require(closed is None, "OPEN alert carries a closure")
    title = policy.get("displayName", "Monitoring alert")
    require(isinstance(title, str) and 0 < len(title) <= 500, "invalid policy display name")
    for key in ("resource", "metric", "metadata", "log"):
        require(key not in raw or isinstance(raw[key], dict), "invalid Monitoring alert metadata")
    resource = raw.get("resource", {})
    metric = raw.get("metric", {})
    if kind == "health":
        require(resource == {"type": "global", "labels": {"project_id": config["project"]}}
                and metric.get("type") == config["health_metric"] and metric.get("labels", {}) == {}, "wrong heartbeat alert scope")
    return {"name": name, "policy": policy["name"], "kind": kind, "title": title,
            "state": raw["state"], "opened_at": opened, "closed_at": closed}


def valid_alert(alert, config, now):
    require(isinstance(alert, dict) and alert.keys() == {
        "name", "policy", "kind", "title", "state", "opened_at", "closed_at"}, "invalid saved alert")
    raw = {"name": alert["name"], "state": alert["state"], "openTime": alert["opened_at"],
           "policy": {"name": alert["policy"], "displayName": alert["title"]}}
    if alert["closed_at"] is not None:
        raw["closeTime"] = alert["closed_at"]
    if alert["kind"] == "health":
        raw.update(resource={"type": "global", "labels": {"project_id": config["project"]}}, metric={"type": config["health_metric"]})
    require(normalize(raw, config, now) == alert, "saved alert classification changed")


def heartbeat(points, alert, config, now):
    """A point proves pipeline recovery only with CLOSED and fresh exact scope."""
    require(alert["kind"] == "health" and alert["state"] == "CLOSED", "heartbeat cannot close an open/security alert")
    valid = []
    for series in points:
        require(isinstance(series, dict) and isinstance(series.get("metric"), dict)
                and series["metric"].keys() <= {"type", "labels"}
                and series["metric"].get("type") == config["health_metric"] and series["metric"].get("labels", {}) == {}
                and series.get("resource") == {"type": "global", "labels": {"project_id": config["project"]}}
                and series.get("metricKind") == "GAUGE" and series.get("valueType") == "INT64", "wrong heartbeat series")
        require(isinstance(series.get("points"), list), "missing heartbeat points")
        for point in series["points"]:
            require(isinstance(point, dict) and point.keys() == {"interval", "value"}
                    and isinstance(point["interval"], dict) and point["interval"].keys() <= {"startTime", "endTime"}, "invalid heartbeat point")
            ended = point["interval"].get("endTime")
            require(instant(ended) <= instant(now), "future heartbeat")
            if "startTime" in point["interval"]:
                require(instant(point["interval"]["startTime"]) <= instant(ended), "invalid heartbeat interval")
            require(point["value"] == {"int64Value": "1"}, "heartbeat value must be exact INT64 one")
            if (instant(ended) > instant(alert["opened_at"])
                    and instant(ended) >= instant(config["health_policy_created_at"])
                    and instant(ended) >= instant(now) - FRESH_SECONDS * 1_000_000_000):
                valid.append(ended)
    return None if not valid else {"point_at": max(valid, key=instant), "verified_at": now,
                                   "policy_created_at": config["health_policy_created_at"]}


def render(alert, proof):
    recovered = alert["kind"] == "health" and proof is not None
    if recovered:
        title, status = "Notification pipeline recovered", "success"
        action = "No further pipeline-recovery action; inspect any remaining source incidents."
    elif alert["kind"] == "health":
        title, status = "Notification pipeline needs attention", "error"
        action = "Restore complete reconciliation and verify a fresh heartbeat."
    else:
        title, status = "Security or data-integrity action required", "error"
        action = "Inspect the authoritative evidence and verify containment, data state, and repair."
    observation = "OPEN" if alert["state"] == "OPEN" else "CLOSED (native observation only)"
    if recovered:
        observation = "RECOVERED — native CLOSED and fresh verified heartbeat"
    link = "https://console.cloud.google.com/monitoring/alerting/incidents/" + alert["name"].rsplit("/", 1)[1]
    link += "?" + urlencode({"project": alert["name"].split("/")[1]})
    lines = [f"*{escape(alert['title'], quote=False)}*", f"*Status:* {observation}",
             "*Impact:* notification coverage may be impaired." if alert["kind"] == "health" else "*Impact:* security or data-integrity risk requires inspection.",
             "*Published-data state:* unknown; Monitoring closure does not establish dataset correctness.",
             "*Responsible owner:* unassigned; repository operator must take responsibility.",
             f"*Required action:* {action}", "*Recovery attempted:* observation only; this relay has no production mutation authority.",
             f"*Evidence:* <{link}|authoritative Monitoring alert> · opened {alert['opened_at']}"]
    if alert["closed_at"]:
        lines.append(f"Native closure: {alert['closed_at']}")
    if proof:
        lines.append(f"Verified heartbeat: {proof['point_at']}")
    if alert["kind"] == "security" and alert["state"] == "CLOSED":
        lines.append("*Repair remains unverified; operator action is still required.*")
    return build_slack_payload(title=title, body="\n".join(lines), status=status)


def new_state(config):
    return {"schema": SCHEMA, "binding": config.binding, "covered_through": config["activated_at"], "episodes": {}, "receipts": {}}


def merge(state, observations, proofs, config, through, *, now):
    result = deepcopy(state)
    for name, alert in sorted(observations.items()):
        require(name == alert["name"], "miskeyed alert")
        episode = result["episodes"].get(name)
        if episode is None:
            episode = {"alert": alert, "recovery": None, "ts": None, "applied_sha256": None, "operation": None}
            result["episodes"][name] = episode
        else:
            old = episode["alert"]
            require(all(old[key] == alert[key] for key in ("name", "policy", "kind", "opened_at")), "Monitoring episode identity changed")
            if old["state"] == "CLOSED":
                if alert["state"] == "CLOSED":
                    require(old["closed_at"] == alert["closed_at"], "Monitoring closure changed")
                continue  # Stale OPEN may not regress a retained closure/proof.
            episode["alert"] = alert
        episode["desired_sha256"] = digest(render(episode["alert"], episode["recovery"]))
    # Pending green is replaceable evidence, not an acknowledged historical fact.
    for name, proof in proofs.items():
        episode = result["episodes"][name]
        require(episode["alert"]["kind"] == "health" and episode["alert"]["state"] == "CLOSED", "invalid recovery kind")
        if episode["recovery"] is None or episode["applied_sha256"] != episode["desired_sha256"]:
            episode["recovery"] = proof
            episode["desired_sha256"] = digest(render(episode["alert"], proof))
    if instant(through) > instant(result["covered_through"]):
        result["covered_through"] = through
    validate_state(result, config, now)
    return result


def validate_state(state, config, now):
    require(isinstance(state, dict) and state.keys() == {"schema", "binding", "covered_through", "episodes", "receipts"}
            and state["schema"] == SCHEMA and state["binding"] == config.binding, "state configuration/schema mismatch")
    require(instant(config["activated_at"]) <= instant(state["covered_through"]) <= instant(now), "invalid coverage checkpoint")
    require(isinstance(state["episodes"], dict) and len(state["episodes"]) <= MAX_EPISODES, "invalid episode inventory")
    for name, episode in state["episodes"].items():
        require(isinstance(episode, dict) and episode.keys() == {"alert", "recovery", "ts", "applied_sha256", "operation", "desired_sha256"}, "invalid delivery state")
        alert = episode["alert"]
        valid_alert(alert, config, now)
        require(name == alert["name"], "miskeyed saved alert")
        proof = episode["recovery"]
        if proof is not None:
            require(alert["kind"] == "health" and alert["state"] == "CLOSED" and isinstance(proof, dict)
                    and proof.keys() == {"point_at", "verified_at", "policy_created_at"}
                    and proof["policy_created_at"] == config["health_policy_created_at"], "invalid recovery proof")
            require(instant(proof["point_at"]) > instant(alert["opened_at"])
                    and instant(proof["point_at"]) >= instant(proof["policy_created_at"])
                    and instant(proof["point_at"]) <= instant(proof["verified_at"]) <= instant(now)
                    and instant(proof["verified_at"]) - instant(proof["point_at"]) <= FRESH_SECONDS * 1_000_000_000, "unproven recovery")
        require(episode["desired_sha256"] == digest(render(alert, proof)), "desired rendering changed")
        ts, applied, operation = episode["ts"], episode["applied_sha256"], episode["operation"]
        require(ts is None or isinstance(ts, str) and TIMESTAMP.fullmatch(ts), "invalid Slack parent timestamp")
        require((ts is None) == (applied is None), "parent/applied identity mismatch")
        require(applied is None or isinstance(applied, str) and re.fullmatch(r"[0-9a-f]{64}", applied), "invalid applied rendering")
        if operation is not None:
            require(isinstance(operation, dict) and operation.keys() == {"id", "kind", "sha256", "payload", "started_at", "phase", "writer"}
                    and re.fullmatch(r"[0-9a-f]{32}", str(operation["id"]))
                    and operation["kind"] == ("post" if ts is None else "update")
                    and operation["phase"] in {"claimed", "sending"}
                    and operation["sha256"] == digest(operation["payload"]), "invalid durable Slack claim")
            require(instant(operation["started_at"]) <= instant(now), "future delivery claim")
            writer = validate_writer(operation["writer"], config, now)
            require(instant(writer["started_at"]) <= instant(operation["started_at"]), "claim predates its writer")
            payload = operation["payload"]
            require(isinstance(payload, dict) and payload.keys() == {"text", "blocks"}
                    and isinstance(payload["text"], str) and isinstance(payload["blocks"], list), "invalid claimed payload")
    require(isinstance(state["receipts"], dict) and len(state["receipts"]) <= MAX_RECEIPTS, "receipt audit requires explicit retention review")
    for request_id, receipt in state["receipts"].items():
        validate_receipt(receipt, config, now)
        require(receipt["request"]["request_id"] == request_id and receipt["request"]["alert_name"] in state["episodes"], "miskeyed receipt audit")
        if receipt["request"]["outcome"] == "confirm_message":
            require(state["episodes"][receipt["request"]["alert_name"]]["ts"] == receipt["request"]["message_ts"], "confirmed parent identity changed")
    require(len(canonical(state)) <= MAX_BYTES, "relay state is full; explicit retention review required")
    return state


def receipt_request(value, config):
    require(isinstance(value, dict) and value.keys() == {"schema", "request_id", "config_binding", "alert_name",
            "operation_id", "operation_sha256", "expected_generation", "outcome", "message_ts"}, "invalid receipt request fields")
    require(value["schema"] == "monitoring-slack-receipt-request-v1" and value["config_binding"] == config.binding, "receipt request configuration mismatch")
    uuid4(value["request_id"])
    require(isinstance(value["alert_name"], str) and re.fullmatch(r"projects/" + re.escape(config["project"]) + r"/alerts/[A-Za-z0-9._-]+", value["alert_name"]), "invalid receipt alert")
    require(isinstance(value["operation_id"], str) and re.fullmatch(r"[0-9a-f]{32}", value["operation_id"])
            and isinstance(value["operation_sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", value["operation_sha256"]), "invalid exact claim identity")
    require(type(value["expected_generation"]) is int and value["expected_generation"] > 0, "receipt needs an exact state generation")
    require(value["outcome"] in {"confirm_message", "confirm_not_delivered"}, "unsupported receipt outcome")
    require(value["message_ts"] is None if value["outcome"] == "confirm_not_delivered" else
            isinstance(value["message_ts"], str) and TIMESTAMP.fullmatch(value["message_ts"]), "invalid receipt timestamp")
    return value


def validate_receipt(receipt, config, now):
    require(isinstance(receipt, dict) and receipt.keys() == {"request", "request_sha256", "operation_kind", "phase",
            "writer", "writer_completed_at", "controller", "github_run_url", "confirmed_at"}, "invalid receipt audit")
    request = receipt_request(receipt["request"], config)
    require(receipt["request_sha256"] == digest(request) and receipt["operation_kind"] in {"post", "update"}
            and receipt["phase"] in {"claimed", "sending"}, "invalid receipt claim evidence")
    writer = validate_writer(receipt["writer"], config, now)
    controller = validate_writer(receipt["controller"], config, now)
    require(writer["execution"] != controller["execution"]
            and writer["image"] == controller["image"]
            and instant(controller["started_at"]) <= instant(receipt["confirmed_at"])
            and instant(writer["started_at"]) <= instant(receipt["writer_completed_at"]) <= instant(receipt["confirmed_at"]) <= instant(now), "invalid terminal receipt chronology")
    require(isinstance(receipt["github_run_url"], str) and re.fullmatch(
        r"https://github\.com/SkyTruth/shared-datasets-1/actions/runs/[1-9][0-9]*", receipt["github_run_url"]), "invalid protected receipt run")
    require(receipt["phase"] == ("claimed" if request["outcome"] == "confirm_not_delivered" else "sending"), "receipt phase cannot establish its outcome")
    return receipt
