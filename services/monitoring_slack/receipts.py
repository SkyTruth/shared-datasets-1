"""State-only confirmation of exact existing delivery facts; never sends Slack."""
from __future__ import annotations

from copy import deepcopy
import os
import re

from scripts import operational_retry
from scripts.slack_incident_api import ReadOnlySlack
from services.monitoring_slack import model


def protected_run(environment):
    value = environment.get("SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL", "")
    model.require(isinstance(value, str) and re.fullmatch(
        r"https://github\.com/SkyTruth/shared-datasets-1/actions/runs/[1-9][0-9]*", value), "receipt needs its protected repository run")
    return value


def execution_proof(raw, config, name, now, *, args, github_run_url=None, terminal=False):
    """Validate the small exact Cloud Run v2 Execution contract this job owns."""
    model.require(isinstance(raw, dict) and raw.get("name") == name
                  and raw.get("job") == config["relay_job"].rsplit("/", 1)[-1], "wrong exact execution")
    model.uuid4(raw.get("uid"))
    model.require(type(raw.get("taskCount")) is int and raw["taskCount"] == 1
                  and type(raw.get("parallelism")) is int and raw["parallelism"] == 1, "relay execution must have one task")
    template = raw.get("template")
    model.require(isinstance(template, dict) and template.get("serviceAccount") == f"monitoring-slack-relay@{config['project']}.iam.gserviceaccount.com"
                  and type(template.get("maxRetries")) is int and template["maxRetries"] == 0, "wrong execution identity or retries")
    containers = template.get("containers")
    model.require(isinstance(containers, list) and len(containers) == 1 and isinstance(containers[0], dict), "incomplete execution container")
    container = containers[0]
    model.require(container.get("image") == config["relay_image"]
                  and container.get("command") == ["python", "-m", "services.monitoring_slack.run"]
                  and container.get("args", []) == args, "execution image or command changed")
    entries = container.get("env")
    model.require(isinstance(entries, list) and all(isinstance(row, dict) and row.keys() == {"name", "value"}
                  and isinstance(row["name"], str) and isinstance(row["value"], str) for row in entries), "invalid execution environment")
    env = {row["name"]: row["value"] for row in entries}
    expected = {"MONITORING_SLACK_CONFIG"} | ({"SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL"} if github_run_url else set())
    model.require(len(env) == len(entries) and env.keys() == expected, "execution environment differs from the reviewed job")
    installed = model.Config.parse(model.parse_json(env["MONITORING_SLACK_CONFIG"].encode()))
    model.require(installed.binding == config.binding and installed["relay_image"] == config["relay_image"], "execution configuration changed")
    if github_run_url:
        model.require(env["SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL"] == github_run_url
                      and installed.value == config.value
                      and raw.get("creator") == f"shared-datasets-terraform@{config['project']}.iam.gserviceaccount.com", "control is not the protected execution")
    else:
        model.require(installed["mode"] == "live", "claim writer was not a normal live cycle")
    proof = {"execution": name, "uid": raw["uid"], "image": container["image"], "config_binding": installed.binding,
             "created_at": raw.get("createTime"), "started_at": raw.get("startTime")}
    model.validate_writer(proof, config, now)
    conditions = raw.get("conditions")
    model.require(isinstance(conditions, list) and all(isinstance(item, dict) for item in conditions), "missing execution conditions")
    completed = [item for item in conditions if item.get("type") == "Completed"]
    model.require(len(completed) == 1, "ambiguous execution completion")
    if terminal:
        model.require(raw.get("reconciling", False) is False and completed[0].get("state") in {"CONDITION_SUCCEEDED", "CONDITION_FAILED"}
                      and model.instant(proof["started_at"]) <= model.instant(raw.get("completionTime")) <= model.instant(now), "old writer is not definitively terminal")
        counts = [raw.get(key, 0) for key in ("runningCount", "retriedCount", "succeededCount", "failedCount", "cancelledCount")]
        model.require(all(type(value) is int and value >= 0 for value in counts)
                      and counts[0] == counts[1] == 0 and sum(counts[2:]) == 1,
                      "old writer still has active, retried or incomplete tasks")
        model.require(isinstance(raw.get("generation"), str) and raw["generation"].isdigit()
                      and int(raw["generation"]) > 0 and raw.get("observedGeneration") == raw["generation"], "terminal generation is unobserved")
    else:
        model.require(raw.get("completionTime") is None and completed[0].get("state") in {"CONDITION_PENDING", "CONDITION_RECONCILING"}, "current writer is not active")
    return proof


def message_payload(message, expected):
    """Only inert generated block IDs/default mrkdwn flags may be normalized."""
    model.require(isinstance(message.get("text"), str) and isinstance(message.get("blocks"), list)
                  and message.get("attachments", []) == [], "message content is incomplete")
    blocks = deepcopy(message["blocks"])
    model.require(len(blocks) == len(expected["blocks"]), "message block count differs")
    for actual, wanted in zip(blocks, expected["blocks"], strict=True):
        model.require(isinstance(actual, dict), "invalid message block")
        if "block_id" in actual and "block_id" not in wanted:
            model.require(isinstance(actual["block_id"], str) and 0 < len(actual["block_id"]) <= 255, "invalid generated block ID")
            del actual["block_id"]
        text, wanted_text = actual.get("text"), wanted.get("text")
        if isinstance(text, dict) and isinstance(wanted_text, dict) and wanted_text.get("type") == "mrkdwn" and "verbatim" not in wanted_text and text.get("verbatim") is False:
            del text["verbatim"]
    payload = {"text": message["text"], "blocks": blocks}
    model.require(payload == expected, "exact claimed message content differs")
    return payload


def reconcile(reader, store, config, request_json, *, environment=None, clock=model.utc_now, slack_factory=ReadOnlySlack):
    """One strict generation-CAS; conflicts never retarget another operation."""
    environment = os.environ if environment is None else environment
    request = model.receipt_request(model.parse_json(request_json.encode()), config)
    model.require(model.canonical(request).decode() == request_json, "receipt request must use canonical JSON")
    controller_name = model.execution_name(config, environment)
    github_run = protected_run(environment)
    now = clock()
    snapshot = store.load()
    model.require(snapshot.value is not None, "receipt state is missing")
    state = model.validate_state(snapshot.value, config, now)
    with operational_retry.operation(profile=operational_retry.OBSERVER_READ):
        controller = execution_proof(reader.execution(controller_name), config, controller_name, clock(),
                                     args=["--reconcile-receipt-json", request_json], github_run_url=github_run)
        previous = state["receipts"].get(request["request_id"])
        if previous is not None:
            model.require(previous["request_sha256"] == model.digest(request), "request UUID was reused with different content")
            return {"verified": True, "public_operations": 0, "replayed": True, "receipt": previous,
                    "state_generation": snapshot.generation, "controller": controller, "github_run_url": github_run}
        model.require(snapshot.generation == request["expected_generation"], "receipt state generation changed")
        episode = state["episodes"].get(request["alert_name"])
        model.require(episode is not None and episode["operation"] is not None, "exact pending claim is missing")
        operation = episode["operation"]
        model.require(operation["id"] == request["operation_id"] and operation["sha256"] == request["operation_sha256"], "receipt identifies a different claim")
        model.require(operation["writer"]["image"] == config["relay_image"], "historical writer image needs separate reviewed proof")
        old_name = operation["writer"]["execution"]
        model.require(old_name != controller_name, "controller cannot certify its own in-flight claim")
        raw = reader.execution(old_name)
        writer = execution_proof(raw, config, old_name, clock(), args=[], terminal=True)
        model.require(writer == operation["writer"] and model.instant(operation["started_at"]) <= model.instant(raw["completionTime"]), "terminal writer differs from the exact claim owner")
        if request["outcome"] == "confirm_not_delivered":
            model.require(operation["phase"] == "claimed", "sending or elapsed time cannot prove non-delivery")
        else:
            model.require(operation["phase"] == "sending", "a pre-send claim cannot identify a delivered message")
            ts = request["message_ts"]
            model.require(operation["kind"] != "update" or episode["ts"] == ts, "update receipt changed parent timestamp")
            model.require(not any(other != request["alert_name"] and value["ts"] == ts for other, value in state["episodes"].items()), "message timestamp belongs to another incident")
            slack = slack_factory(reader.token(), config["slack_channel"])
            model.require(slack.identity() == config.identity, "receipt bot identity changed")
            message = slack.message(ts)
            model.require(message.get("ts") == ts and message.get("type") == "message" and message.get("thread_ts") in {None, ts}
                          and message.get("user") == config["slack_user"] and message.get("team", config["slack_team"]) == config["slack_team"]
                          and isinstance(message.get("bot_id"), str) and bool(message["bot_id"])
                          and message.get("subtype") in {None, "bot_message"}, "message is not the exact configured bot parent")
            model.require(model.digest(message_payload(message, operation["payload"])) == operation["sha256"], "message receipt hash differs")
    receipt = {"request": deepcopy(request), "request_sha256": model.digest(request), "operation_kind": operation["kind"],
               "phase": operation["phase"], "writer": writer, "writer_completed_at": raw["completionTime"],
               "controller": controller, "github_run_url": github_run, "confirmed_at": clock()}
    if request["outcome"] == "confirm_message":
        episode["ts"] = request["message_ts"]
        episode["applied_sha256"] = operation["sha256"]
    episode["operation"] = None
    state["receipts"][request["request_id"]] = receipt
    model.validate_state(state, config, clock())
    store.save(state, snapshot.generation)
    cold = store.load()
    model.validate_state(cold.value, config, clock())
    model.require(cold.value["receipts"].get(request["request_id"]) == receipt, "receipt persistence is unconfirmed")
    return {"verified": True, "public_operations": 0, "replayed": False, "receipt": receipt,
            "state_generation": cold.generation, "controller": controller, "github_run_url": github_run}
