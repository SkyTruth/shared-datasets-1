"""Durable decision parents, reviewed replies and exactly correlated review alerts."""
from __future__ import annotations

from collections import defaultdict
import html
import logging
import os
import re

from ingestion.common import decision_events as wire
from scripts.slack_incident_api import TIMESTAMP

POLICY = "Shared datasets release waiting on a maintainer decision"
EVENT_SCHEMA = "identity-decision-notification-v1"
REVIEW_EVENT_SCHEMA = "identity-review-notification-v1"
EXECUTION_EVENT_SCHEMA = "identity-execution-notification-v1"
APPROVAL_SCHEMA = "identity-decision-approvals-v1"
REPOSITORY = "SkyTruth/shared-datasets-1"


def require(condition, message):
    wire.require(condition, message)


def approved(value):
    require(isinstance(value, dict) and set(value) == {"asset_slug", "decision", "head_sha", "merge_sha", "acceptance"}, "invalid decision authority")
    decision = value["decision"]
    required = set(wire.CASE_FIELDS) | {"release", "action", "previous_feature_id_for_key", "rationale", "reviewer", "pr_reference"}
    require(isinstance(decision, dict) and required <= decision.keys() and not decision.keys() - required - {"reuse_feature_id"}, "invalid approved decision fields")
    wire.case({key: decision[key] for key in wire.CASE_FIELDS})
    require(value["asset_slug"] in wire.ASSET_JOBS and decision["reviewer"] == "jonaraphael"
            and isinstance(decision["rationale"], str) and decision["rationale"].strip(), "missing decision reviewer or rationale")
    require(isinstance(decision["pr_reference"], str) and re.fullmatch(r"https://github\.com/" + REPOSITORY + r"/pull/[1-9][0-9]*", decision["pr_reference"]), "foreign decision reference")
    for field in ("head_sha", "merge_sha"):
        require(isinstance(value[field], str) and re.fullmatch(r"[0-9a-f]{40}", value[field]), "invalid reviewed revision")
    acceptance = value["acceptance"]
    require(isinstance(acceptance, dict) and acceptance.get("reviewed_commit") == value["head_sha"]
            and ((set(acceptance) == {"kind", "reviewed_commit"} and acceptance["kind"] == "self_authored_merge")
                 or (set(acceptance) == {"kind", "reviewed_commit", "review_id"} and acceptance["kind"] == "review"
                     and type(acceptance["review_id"]) is int and acceptance["review_id"] > 0)), "unaccepted decision head")
    previous = decision["previous_feature_id_for_key"]
    require(previous is None or isinstance(previous, str) and re.fullmatch(r"[1-9][0-9]*", previous), "invalid previous identity declaration")
    action, reuse = decision["action"], decision.get("reuse_feature_id")
    if action == "keep_previous_key_mapping":
        require(previous is not None and reuse is None, "cannot keep an absent identity key")
    elif action == "reuse_previous_feature_id":
        require(reuse in decision["matching_geometry_feature_ids"] + decision["matching_properties_feature_ids"]
                and previous in {None, reuse}, "decision would merge existing identities")
    else:
        require(action == "assign_new_feature_id" and reuse is None, "unsupported identity decision")
    return value


def proof_key(value):
    value = approved(value)
    decision = value["decision"]
    return wire.case_key(value["asset_slug"], decision["release"], {key: decision[key] for key in wire.CASE_FIELDS})


def approvals(store):
    value, generation = store.read("identity/approvals.json")
    if value is None:
        return {"schema": APPROVAL_SCHEMA, "revision": None, "records": {}, "known": {}}, generation
    require(set(value) == {"schema", "revision", "records", "known"} and value["schema"] == APPROVAL_SCHEMA
            and isinstance(value["revision"], str) and re.fullmatch(r"[0-9a-f]{40}", value["revision"])
            and isinstance(value["records"], dict) and isinstance(value["known"], dict), "invalid approval index")
    require(all(re.fullmatch(r"[0-9a-f]{64}", key) and isinstance(item, str) and re.fullmatch(r"[0-9a-f]{64}", item)
                for key, item in value["known"].items()), "invalid known decision snapshot")
    require(all(proof_key(proof) == key for key, proof in value["records"].items()), "decision authority index mismatch")
    return value, generation


def load(store, key, identity):
    require(re.fullmatch(r"[0-9a-f]{64}", key), "invalid decision event key")
    value, generation = store.read(f"identity/events/{key}.json")
    if value is None:
        return None, generation
    fields = {"schema", "event", "identity", "parent", "replies", "parent_view"}
    schema = EVENT_SCHEMA
    if value["event"]["schema"] == wire.REVIEW_SCHEMA:
        fields.add("catalog_url")
        wire.catalog_origin(value["catalog_url"])
        schema = REVIEW_EVENT_SCHEMA
    require(set(value) == fields and value["schema"] in {schema, EXECUTION_EVENT_SCHEMA}
            and value["identity"] == identity and wire.event_key(wire.event(value["event"])) == key, "incompatible decision receipt")
    require(value["parent_view"] is None or isinstance(value["parent_view"], str)
            and re.fullmatch(r"[0-9a-f]{64}", value["parent_view"]), "invalid decision parent acknowledgement")
    parent = value["parent"]
    require(set(parent) == {"owner", "ts"} and parent["owner"] in {"bot", "legacy"}
            and (parent["ts"] is None and parent["owner"] == "bot" or isinstance(parent["ts"], str)
                 and TIMESTAMP.fullmatch(parent["ts"])), "invalid decision parent")
    require(isinstance(value["replies"], dict), "invalid decision reply receipts")
    wanted = {wire.case_key(value["event"]["asset_slug"], value["event"]["release"], item) for item in value["event"]["cases"]}
    for fingerprint, reply in value["replies"].items():
        require(set(reply) == {"records", "owner", "ts"} and reply["owner"] in {"bot", "legacy"}
                and wire.digest(reply["records"]) == fingerprint and isinstance(reply["records"], dict), "invalid decision reply identity")
        for case_id, proof in reply["records"].items():
            require(proof_key(proof) == case_id and case_id in wanted, "reply differs from captured case")
        require(reply["ts"] is None or isinstance(reply["ts"], str) and TIMESTAMP.fullmatch(reply["ts"]), "invalid decision reply timestamp")
    return value, generation


def save(store, key, value, generation):
    return store.write(f"identity/events/{key}.json", value, generation)


def primary(event, *, catalog_url=None):
    text = f":raising_hand: Decision needed: {event['asset_slug']} release {event['release']}\n"
    text += f"{len(event['cases'])} feature identity case(s) need maintainer review. The published data is unchanged by this review stop.\n"
    for item in event["cases"][:5]:
        matched = item["matching_geometry_feature_ids"] + item["matching_properties_feature_ids"]
        text += f"\nSource key `{html.escape('/'.join(item['new_identity_key']), quote=False)}`; existing matching IDs `{', '.join(matched)}`."
        if event["schema"] == wire.REVIEW_SCHEMA:
            text += f" <{wire.review_url(event, catalog_url, item)}|Compare this case>"
    text += "\n\nRecord reviewed release-scoped decisions in `catalog/feature-identity-resolutions/` to settle these cases."
    if event["schema"] == wire.REVIEW_SCHEMA:
        text += f"\n<{wire.review_url(event, catalog_url, event['cases'][0])}|Open captured review ({len(event['cases'])} cases)> · SkyTruth sign-in required."
    url = f"https://console.cloud.google.com/run/jobs/executions/details/us-central1/{event['execution']}?project={wire.PROJECT}"
    return {"text": text + f"\n<{url}|Source execution>", "blocks": []}


def reply_message(records):
    values = list(records.values())
    reference = values[0]["decision"]["pr_reference"]
    lines = [f"*Decision of record* — <{reference}|Reviewed PR>", "*Decided by:* `jonaraphael`"]
    for proof in values[:5]:
        decision = proof["decision"]
        action = decision["action"]
        description = (f"keep existing feature_id `{decision['previous_feature_id_for_key']}`" if action == "keep_previous_key_mapping"
                       else f"reuse feature_id `{decision['reuse_feature_id']}`" if action == "reuse_previous_feature_id"
                       else "assign a new feature_id")
        lines += [f"\nSource key `{html.escape('/'.join(decision['new_identity_key']), quote=False)}`: {description}.",
                  "*Why recommended:* " + html.escape(decision["rationale"][:1400], quote=False)]
    if len(values) > 5:
        lines.append(f"\n{len(values) - 5} additional decisions and their complete rationale are recorded in the linked PR.")
    lines.append("\nThis records the identity decision. Publication and deployment acceptance remain separate checks.")
    return {"text": "\n".join(lines), "blocks": []}


def resolved_monitoring(incident, resolution):
    require(set(resolution) == {"event_id", "records"} and re.fullmatch(r"[0-9a-f]{64}", resolution["event_id"])
            and resolution["records"], "invalid Monitoring decision proof")
    for key, value in resolution["records"].items():
        require(proof_key(value) == key, "Monitoring resolution differs from approved decision")
    references = sorted({value["decision"]["pr_reference"] for value in resolution["records"].values()})
    links = ", ".join(f"<{reference}|Decision of record>" for reference in references)
    url = f"https://console.cloud.google.com/monitoring/alerting/alerts/{incident['incident_id']}?project={wire.PROJECT}"
    return {"text": (f":white_check_mark: Decision recorded — {html.escape(incident['policy_name'], quote=False)}\n"
                     f"The identity review for this exact execution is resolved by {links}.\n"
                     "*Impact:* The maintainer decision is recorded; consumer impact is not inferred.\n"
                     "*Mutation state:* A decision does not establish dataset publication or deployment success.\n"
                     "*Owner:* jonaraphael (decision reviewer).\n"
                     "*Required action:* Ingestion verifies publication and runtime acceptance separately.\n"
                     f"<{url}|Monitoring incident>"), "blocks": []}


def reconcile(store, key, slack, identity):
    value, generation = load(store, key, identity)
    if value is None:
        return
    require(value["parent"]["ts"] is not None, "unconfirmed decision parent; repair its receipt before retrying")
    index, _ = approvals(store)
    event = value["event"]
    wanted = {wire.case_key(event["asset_slug"], event["release"], item) for item in event["cases"]}
    current = {item: index["records"][item] for item in wanted if item in index["records"]}
    confirmed = set()
    for reply in value["replies"].values():
        require(reply["ts"] is not None, "unconfirmed decision reply; repair its receipt before retrying")
        confirmed.update(wire.digest(proof) for proof in reply["records"].values())
    groups = defaultdict(dict)
    for item, proof in current.items():
        if wire.digest(proof) not in confirmed:
            groups[proof["decision"]["pr_reference"]][item] = proof
    for reference in sorted(groups):
        records = groups[reference]
        fingerprint = wire.digest(records)
        if fingerprint not in value["replies"]:
            value = {**value, "replies": {**value["replies"], fingerprint: {"records": records, "owner": "bot", "ts": None}}}
            generation = save(store, key, value, generation)
            ts = slack.post(reply_message(records), thread_ts=value["parent"]["ts"])
            logging.info("Identity decision Slack receipt event=%s operation=reply:%s channel=%s ts=%s", key, fingerprint, identity["channel"], ts)
            value = {**value, "replies": {**value["replies"], fingerprint: {**value["replies"][fingerprint], "ts": ts}}}
            generation = save(store, key, value, generation)
        require(value["replies"][fingerprint]["ts"] is not None, "unconfirmed decision reply; repair its receipt before retrying")
    if value["parent"]["owner"] == "bot":
        payload = primary(event, catalog_url=value.get("catalog_url"))
        fingerprint = wire.digest(payload)
        from services.monitoring_slack import executions
        shared, _ = executions.load(store, executions.source(event), identity)
        if shared is not None:
            require(shared["ts"] == value["parent"]["ts"], "decision differs from its execution parent")
            executions.report(store, slack, identity, executions.source(event),
                              decision=(key, payload["text"], set(current) == wanted))
        elif value["parent_view"] != fingerprint:
            slack.update(value["parent"]["ts"], payload)
            value = {**value, "parent_view": fingerprint}
            generation = save(store, key, value, generation)
    if set(current) != wanted:
        return
    for path in store.paths(f"identity/links/{key}/"):
        link, _ = store.read(path)
        require(set(link) == {"context", "incident_id"} and wire.event_key(link["context"]) == key, "foreign linked incident")
        incident_id = link["incident_id"]
        receipt, version = store.load(incident_id)
        from services.monitoring_slack.run import validate_record
        require(receipt is not None, "missing linked Monitoring parent")
        if receipt["schema"] == "monitoring-slack-execution-v1":
            continue  # The shared execution view above owns this parent's rendering.
        validate_record(receipt, identity)
        resolution = {"event_id": key, "records": current}
        payload = resolved_monitoring({"incident_id": incident_id, "policy_name": POLICY}, resolution)
        fingerprint = wire.digest(payload)
        desired = {**receipt, "schema": "monitoring-slack-decision-v1", "decision": resolution,
                   "decision_view": receipt.get("decision_view")}
        if desired != receipt:
            version = store.save(incident_id, desired, version)
        if desired["decision_view"] != fingerprint:
            slack.update(receipt["ts"], payload)
            store.save(incident_id, {**desired, "decision_view": fingerprint}, version)


def deliver(event, store, slack, identity):
    event = wire.event(event)
    key = wire.event_key(event)
    value, generation = load(store, key, identity)
    if value is None:
        value = {"schema": EXECUTION_EVENT_SCHEMA, "event": event, "identity": identity,
                 "parent": {"owner": "bot", "ts": None}, "replies": {}, "parent_view": None}
        if event["schema"] == wire.REVIEW_SCHEMA:
            value["catalog_url"] = wire.catalog_origin(os.environ["CATALOG_VIEWER_URL"])
        # Freeze the complete event before another producer can render its
        # shared parent. The execution receipt alone owns the Slack post claim.
        generation = save(store, key, value, generation)
    else:
        def core(source):
            return {field: item for field, item in source.items() if field not in {"schema", "review_bundle"}}
        require(core(value["event"]) == core(event), "decision event content changed after capture")
        if event["schema"] == wire.REVIEW_SCHEMA:
            if value["event"]["schema"] == wire.REVIEW_SCHEMA:
                require(value["event"] == event, "review reference changed after capture")
            else:
                value = {**value, "schema": EXECUTION_EVENT_SCHEMA if value["schema"] == EXECUTION_EVENT_SCHEMA else REVIEW_EVENT_SCHEMA,
                         "event": event, "catalog_url": wire.catalog_origin(os.environ["CATALOG_VIEWER_URL"])}
                generation = save(store, key, value, generation)
    if value["parent"]["ts"] is None:
        require(value["schema"] == EXECUTION_EVENT_SCHEMA, "unconfirmed historical decision parent; repair its receipt before retrying")
        payload = primary(value["event"], catalog_url=value.get("catalog_url"))
        from services.monitoring_slack import executions
        ts = executions.report(store, slack, identity, executions.source(value["event"]), decision=(key, payload["text"], False))
        logging.info("Identity decision Slack receipt event=%s operation=parent channel=%s ts=%s", key, identity["channel"], ts)
        save(store, key, {**value, "parent": {"owner": "bot", "ts": ts}, "parent_view": wire.digest(payload)}, generation)
    reconcile(store, key, slack, identity)


def monitoring_context(alert):
    policy = alert.get("policy", {})
    if policy.get("userLabels", {}).get("kind") != "decision-required":
        return None
    require(policy.get("displayName") == POLICY, "foreign identity review policy")
    resource = alert.get("resource", {})
    require(resource.get("type") == "cloud_run_job" and resource.get("labels", {}).get("project_id") == wire.PROJECT
            and resource["labels"].get("location") == "us-central1", "foreign identity review resource")
    labels = alert.get("log", {}).get("extractedLabels", {})
    if not labels:
        return None  # Pre-correlation incidents require an explicit reviewed adoption.
    source = {"project": wire.PROJECT, "job": alert["resource"]["labels"]["job_name"],
              "execution": labels["decision_execution"], "asset_slug": labels["decision_asset"],
              "release": labels["decision_release"]}
    return wire.context(source)


def link(incident, alert, store, slack, identity):
    require(alert.get("name") == f"projects/{wire.PROJECT}/alerts/{incident['incident_id']}", "Monitoring identity mismatch")
    source = monitoring_context(alert)
    if source is None:
        return
    key = wire.event_key(source)
    path = f"identity/links/{key}/{incident['incident_id']}.json"
    previous, version = store.read(path)
    value = {"context": source, "incident_id": incident["incident_id"]}
    require(previous is None or previous == value, "incident changed decision event")
    if previous is None:
        store.write(path, value, version)
    reconcile(store, key, slack, identity)
