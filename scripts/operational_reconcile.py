"""Trusted notification-cycle orchestration; source workers retain repair authority.

Cloud collection first runs in shadow. A cycle advances only after complete
enumeration, signed incident persistence and verified delivery. Its own receipt
is then signed before a fresh durable read can authorize the heartbeat.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import cloud_operational_observations as cloud
from scripts import deployment_revision as deployments
from scripts import slack_incidents as incidents
from scripts.slack_incident_api import Slack

PROPOSAL = "cloud-cycle-proposal.json"
PROPOSAL_SCHEMA = "shared-datasets-cloud-cycle-proposal-v1"
DELIVERY_CONTEXT = "cloud-delivery-context.json"
TRANSPORT_FIELDS = {"public_delivery_count", "historical_parent_update_count", "independent_github_delivery_count", "public_transport_count"}


def output(name, value):
    if os.environ.get("GITHUB_OUTPUT"):
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"{name}={value}\n")


def utc_now():
    return datetime.now(timezone.utc)


def reader():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return cloud.CloudReader(AuthorizedSession(credentials, max_refresh_attempts=0, refresh_timeout=30))


def routing(ledger, cycles):
    from scripts.operational_delivery_activation import activation
    if not hasattr(ledger, "_verified_policy_receipts"):
        ledger._verified_policy_receipts = {}
    return activation(ledger.api, incidents.REPOSITORY, cycles.root, ledger.run["head_sha"], verified_receipts=ledger._verified_policy_receipts)


def write_context(ledger, cycles):
    value = {"context": cycles.context(), "activation": routing(ledger, cycles)}
    (ledger.directory / DELIVERY_CONTEXT).write_text(json.dumps(value, sort_keys=True) + "\n")
    return value


def read_context(ledger, cycles):
    from scripts.cloud_observation_ledger import strict_json
    value = strict_json((ledger.directory / DELIVERY_CONTEXT).read_bytes())
    incidents.require(isinstance(value, dict) and set(value) == {"context", "activation"}
                      and value["context"] == cycles.context(), "cloud cycle belongs to another worker, source or protocol")
    incidents.require(value["activation"] == routing(ledger, cycles), "cloud delivery activation changed during reconciliation")
    return value


def read_proposal(ledger, cycles):
    from scripts.cloud_observation_ledger import strict_json
    value = strict_json((ledger.directory / PROPOSAL).read_bytes())
    incidents.require(isinstance(value, dict)
                      and set(value) == {"schema", "context", "activation", "mode", "previous_cycle_id", "bundle"}
                      and value["schema"] == PROPOSAL_SCHEMA and value["mode"] in {"shadow", "unified"}, "invalid cloud cycle proposal")
    expected = read_context(ledger, cycles)
    incidents.require(all(value[key] == expected[key] for key in expected), "cloud cycle belongs to another worker, source or protocol")
    incidents.require(value["mode"] == expected["activation"]["mode"], "cloud cycle mode differs from verified activation")
    return value


def verified_states(ledger):
    states = ledger.load()
    for state in states.values():
        incidents.require(not incidents.pending_posts(state), "unconfirmed Slack delivery prevents complete reconciliation")
        incidents.require(not incidents.needs_parent_delivery(state), "unconfirmed incident update prevents complete reconciliation")
    return states


def verify_evaluation(value, states, *, now):
    bundle = value["bundle"]
    incidents.require(isinstance(bundle, dict) and set(bundle) == {"checkpoint", "evaluation"}
                      and isinstance(bundle["evaluation"], dict), "invalid cloud cycle evaluation bundle")
    classification = {key: item for key, item in bundle["evaluation"].items() if key not in TRANSPORT_FIELDS | {"collection_completed_at"}}
    context = value["context"]
    evaluated = classification.get("worker", {}).get("evaluated_at")
    collected = cloud.instant(bundle["evaluation"]["collection_completed_at"])
    incidents.require(incidents.policy.instant(incidents.policy.timestamp(collected)) <= incidents.policy.instant(evaluated) <= now
                      and collected <= cloud.instant(bundle["checkpoint"]["last_cycle"]["completed_at"]) <= now,
                      "cloud evaluation lies outside the verified collection/reconciliation clocks")
    worker = {"id": context["execution_run_id"], "attempt": context["execution_run_attempt"],
              "head_sha": context["source_sha"], "evaluated_at": evaluated}
    return incidents.verify_cloud_evaluation(bundle["checkpoint"], classification, states,
                                             shadow=value["mode"] == "shadow", worker=worker)


def current_worker_records(ledger):
    """Complete API enumeration, rather than a mutable local delivery plan."""
    from scripts.cloud_observation_ledger import canonical, positive, strict_json
    rows = ledger.api.pages(f"repos/{incidents.REPOSITORY}/deployments?environment={incidents.ENVIRONMENT}&task=slack-incident&per_page=100")
    incidents.require(isinstance(rows, list), "incomplete current-worker incident enumeration")
    unique, historical = {}, []
    for record in rows:
        incidents.require(isinstance(record, dict) and positive(record.get("id")), "invalid incident record identity")
        payload = strict_json(record["payload"]) if isinstance(record.get("payload"), str) else record.get("payload")
        incidents.require(isinstance(payload, dict) and payload.get("schema") in incidents.SCHEMAS, "invalid incident record payload")
        if (payload["execution_run_id"], payload["execution_run_attempt"]) != (ledger.run["id"], ledger.run["run_attempt"]):
            historical.append((record, payload))
            continue
        incidents.require(record.get("sha") == record.get("ref") == ledger.run["head_sha"], "incident record has another worker source")
        identifier = record["id"]
        incidents.require(identifier not in unique or canonical(unique[identifier]) == canonical(record), "conflicting repeated incident record")
        unique[identifier] = record
    current = [(record, ledger.verify(record)) for record in sorted(unique.values(), key=lambda row: row["id"])]
    first = {}
    for record, state in current:
        if state["incident"] and any(item.get("source") == "cloud" for item in state["incident"]["failures"]):
            first.setdefault(state["scope"], record["id"])
    previous = {}
    for record, payload in historical:
        scope = payload.get("state", {}).get("scope")
        if scope in first and record["id"] < first[scope] and (scope not in previous or record["id"] > previous[scope]["id"]):
            previous[scope] = record
    return current, {scope: ledger.verify(record) for scope, record in previous.items()}


def github_escalation(state):
    return any(fact.source_id.startswith("github:") and decision.disposition == incidents.policy.Disposition.ESCALATE
               for fact, decision in incidents.episode_decisions(state))


def public_delivery_counts(ledger, states, *, shadow=False):
    """Count actual transports with explicit native rollback attribution.

    Native mode permits quiet edits of existing bot parents and independently
    eligible GitHub escalation. Those remain recorded separately from new cloud
    route delivery; their sum is the actual transport count. Permalink reads
    never count. Historical webhooks never enter this bot ledger.
    """
    current, baseline = current_worker_records(ledger)
    previous = {(scope, state["incident"]["id"]): state for scope, state in baseline.items() if state["incident"]}
    changed = set()
    counts = {key: 0 for key in TRANSPORT_FIELDS}
    for _, state in current:
        incident = state["incident"]
        if incident is None or not any(item.get("source") == "cloud" for item in incident["failures"]):
            continue
        key = state["scope"], incident["id"]
        old = previous.get(key)
        parent = incident["posts"].get("parent")
        if parent and parent["state"] == "delivered":
            changed.add(key)
            incidents.require(old is not None, "confirmed public delivery has no signed predecessor or claim")
            old_parent = old["incident"]["posts"].get("parent")
            incidents.require(old_parent is not None, "confirmed public delivery has no signed claim")
            if old_parent["state"] == "claimed":
                incidents.require(not shadow or github_escalation(state), "native mode cannot create a cloud-only parent")
                counts["independent_github_delivery_count" if shadow else "public_delivery_count"] += 1
            else:
                incidents.require(parent["ts"] == old_parent["ts"], "cloud incident parent identity changed")
                counts["historical_parent_update_count" if shadow else "public_delivery_count"] += int(
                    incident["parent_view_sha256"] != old["incident"]["parent_view_sha256"])
        previous[key] = state
    for scope, incident_id in changed:
        incidents.require(states.get(scope) == previous[scope, incident_id], "cloud transport evidence is not the latest signed incident state")
    counts["public_transport_count"] = sum(value for key, value in counts.items() if key != "public_transport_count")
    return counts


def deliver_messages(ledger, cycles, slack, *, cloud_reader=None, github_only=False):
    """A partial prepare may deliver only its signed, currently authorized claims."""
    from scripts.cloud_observation_ledger import positive, strict_json
    records = strict_json((ledger.directory / "prepared.json").read_bytes())
    incidents.require(isinstance(records, list) and all(isinstance(record, dict) and positive(record.get("id")) for record in records)
                      and len({record["id"] for record in records}) == len(records), "invalid prepared incident plan")
    current_records = []
    cloud_context = None
    github_baselines = None
    for record in records:
        payload = strict_json(record["payload"]) if isinstance(record.get("payload"), str) else record.get("payload")
        incidents.require(isinstance(payload, dict) and payload.get("execution_run_id") == ledger.run["id"]
                          and payload.get("execution_run_attempt") == ledger.run["run_attempt"]
                          and record.get("sha") == record.get("ref") == ledger.run["head_sha"],
                          "prepared claim belongs to another worker or source")
        current = ledger.api.get(f"repos/{incidents.REPOSITORY}/deployments/{record['id']}")
        incidents.require({key: item for key, item in current.items() if key != "updated_at"}
                          == {key: item for key, item in record.items() if key != "updated_at"}, "prepared claim changed")
        state = ledger.verify(current)
        cloud_history = state["incident"] and any(item.get("source") == "cloud" for item in state["incident"]["failures"])
        if github_only and cloud_history:
            if github_baselines is None:
                _, github_baselines = current_worker_records(ledger)
            incidents.require(incidents.github_public_action(state, github_baselines.get(state["scope"])),
                              "GitHub phase cannot deliver a cloud-only action or canonical translation green")
            current_records.append(current)
            continue
        observer_authenticated = (ledger.run.get("event") == "schedule"
                                  and os.environ.get("SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED") == "true")
        if not observer_authenticated and cloud_reader is None and incidents.canonical_translation_recovery(state):
            # Only scheduled workers receive the observation identity. Keep the
            # signed green pending for that worker, without failing unrelated CI.
            continue
        if state["incident"] and any(item.get("source") == "cloud" for item in state["incident"]["failures"]):
            cloud_context = cloud_context or read_context(ledger, cycles)
            parent = state["incident"]["posts"].get("parent")
            incidents.require(cloud_context["activation"]["mode"] == "unified"
                              or parent and parent["state"] == "delivered" or github_escalation(state),
                              "cloud delivery requires verified unified activation or independent GitHub escalation")
        current_records.append(current)
    image_proofs = incidents.AuxiliaryImageProofs(ledger.api)

    def reverify(observation):
        nonlocal cloud_reader
        cloud_reader = cloud_reader or reader()
        return incidents.reverify_saved_cloud_success(cloud_reader, observation, github_api=ledger.api, clock=utc_now,
                                                     image_proofs=image_proofs)
    incidents.deliver(ledger, current_records, slack, recovery_verifier=None if github_only else reverify)


def prepare_github(ledger, identity, *, now):
    """Every scheduled tick covers GitHub before requesting cloud credentials."""
    plan = ledger.directory / "prepared.json"
    plan.write_text("[]\n")
    observations = incidents.replay_observations(
        ledger.api, incidents.job_targets(), os.environ.get("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", ""), now=now)
    records = []

    def retain(record):
        records.append(record)
        plan.write_text(json.dumps(records, sort_keys=True) + "\n")

    prepared = incidents.prepare(ledger, identity, observations, on_saved=retain, github_only=True,
                                 now=incidents.policy.timestamp(now), decision_clock=utc_now)
    plan.write_text(json.dumps(prepared, sort_keys=True) + "\n")
    return prepared


def prepare_cycle(ledger, cycles, identity, cloud_reader, *, now, clock=None):
    # A failed new attempt cannot leave an older local proposal eligible for
    # finalization. Partial new claims retain their own serialized delivery plan.
    (ledger.directory / PROPOSAL).unlink(missing_ok=True)
    (ledger.directory / DELIVERY_CONTEXT).unlink(missing_ok=True)
    plan = ledger.directory / "prepared.json"
    plan.write_text("[]\n")
    delivery = write_context(ledger, cycles)
    context, selected = delivery["context"], delivery["activation"]
    previous = cycles.load()
    proposal = cloud.collect(previous["checkpoint"] if previous else None, cloud_reader, now=now, clock=clock)
    records = []

    def retain(record):
        records.append(record)
        plan.write_text(json.dumps(records, sort_keys=True) + "\n")

    image_proofs = incidents.AuxiliaryImageProofs(ledger.api)
    prepared, evaluation = incidents.prepare_cloud(
        ledger, identity, proposal, shadow=selected["mode"] == "shadow", now=proposal["last_cycle"]["completed_at"], on_saved=retain,
        github_api=ledger.api, github_observations=(), clock=clock,
        translation_reader=lambda uri: cloud_reader.translation_manifest(uri), image_proofs=image_proofs,
        recovery_verifier=lambda observation: incidents.reverify_saved_cloud_success(
            cloud_reader, observation, github_api=ledger.api, clock=clock or utc_now, image_proofs=image_proofs))
    plan.write_text(json.dumps(prepared, sort_keys=True) + "\n")
    # Shadow has no cloud public delivery capability. GitHub episodes can still
    # escalate in the same worker; those are outside cloud cutover evidence.
    evaluation = {**evaluation, **{key: 0 for key in TRANSPORT_FIELDS},
                  "collection_completed_at": proposal["last_cycle"]["completed_at"]}
    bundle = cloud.evaluated_collection(proposal, evaluation)
    value = {"schema": PROPOSAL_SCHEMA, "context": context, "activation": selected, "mode": selected["mode"],
             "previous_cycle_id": previous["manifest_id"] if previous else None,
             "bundle": bundle}
    (ledger.directory / PROPOSAL).write_text(json.dumps(value, sort_keys=True) + "\n")
    output("cycle_proposed", "true")
    return value


def reverify_translation_recoveries(ledger, states, source_ids, cloud_reader=None, *, clock=None):
    """No later debt generation may authorize a stale green or heartbeat."""
    seen = set()
    for state in states.values():
        incident = state["incident"]
        if incident is None or incident["resolution"] is None:
            continue
        for observation in state["recoveries"].values():
            if observation.get("source") != "cloud" or observation["proof"]["fact"]["kind"] != "translation_manifest":
                continue
            source = observation["source_id"]
            if source in seen or source not in source_ids:
                continue
            seen.add(source)
            cloud_reader = cloud_reader or reader()
            incidents.reverify_saved_cloud_success(cloud_reader, observation, github_api=ledger.api, clock=clock or utc_now)


def finalize_cycle(ledger, cycles, *, clock=None, cloud_reader=None):
    """Called only after delivery/outcome attestation succeeded or was empty."""
    value = read_proposal(ledger, cycles)
    states = verified_states(ledger)
    reverify_translation_recoveries(ledger, states, {item["source_id"] for item in value["bundle"]["evaluation"].get("observations", [])}, cloud_reader, clock=clock)
    previous = cycles.load()
    incidents.require((previous["manifest_id"] if previous else None) == value["previous_cycle_id"],
                      "cloud predecessor changed during reconciliation")
    bundle = value["bundle"]
    incidents.require(all(bundle["evaluation"].get(key) == 0 for key in TRANSPORT_FIELDS), "unfinalized cycle contains an invented transport count")
    counts = public_delivery_counts(ledger, states, shadow=value["mode"] == "shadow")
    read_context(ledger, cycles)  # Signature and delivery reads may outlive a policy transition.
    completed = (clock or utc_now)()  # All authoritative observation, incident and delivery reads have completed.
    verify_evaluation(value, states, now=completed)
    bundle["evaluation"].update(counts)
    bundle["checkpoint"]["last_cycle"]["completed_at"] = cloud.timestamp(completed)
    saved = cycles.save(bundle["checkpoint"], bundle["evaluation"], mode=value["mode"],
                        previous_cycle_id=value["previous_cycle_id"], before_commit=lambda: read_context(ledger, cycles))
    (ledger.directory / PROPOSAL).write_text(json.dumps(value, sort_keys=True) + "\n")
    output("cycle_saved", "true")
    return saved


def verify_heartbeat(ledger, cycles, cloud_reader, *, clock=None):
    value = read_proposal(ledger, cycles)
    states = verified_states(ledger)
    reverify_translation_recoveries(ledger, states, {item["source_id"] for item in value["bundle"]["evaluation"].get("observations", [])}, cloud_reader, clock=clock)
    incidents.require(public_delivery_counts(ledger, states, shadow=value["mode"] == "shadow")
                      == {key: value["bundle"]["evaluation"].get(key) for key in TRANSPORT_FIELDS},
                      "cloud transport count differs from verified outcomes")
    durable = cycles.verify_cycle_id(cycles.local_cycle_id())
    incidents.require(all(durable["manifest"].get(key) == expected for key, expected in value["context"].items())
                      and durable["manifest"].get("mode") == value["mode"]
                      and durable["manifest"].get("previous_cycle_id") == value["previous_cycle_id"],
                      "heartbeat cycle belongs to another worker, source, protocol or mode")
    verified = {key: durable[key] for key in ("checkpoint", "evaluation")}
    verify_evaluation(value, states, now=(clock or utc_now)())
    incidents.require(cloud.instant(verified["checkpoint"]["last_cycle"]["completed_at"])
                      >= incidents.policy.instant(verified["evaluation"]["worker"]["evaluated_at"]),
                      "heartbeat predates policy evaluation")
    read_context(ledger, cycles)  # Recheck after durable signature/provenance verification.
    sent = cloud.confirm_heartbeat(value["bundle"], verified, delivery_verified=True,
                                   heartbeat=cloud_reader.heartbeat)
    output("heartbeat", str(sent).lower())
    return sent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare-github", "deliver-github", "prepare", "deliver", "verify", "finalize-cycle", "verify-heartbeat"])
    parser.add_argument("--work-dir", required=True, type=Path)
    args = parser.parse_args()
    enabled = os.environ.get("SHARED_DATASETS_CLOUD_OBSERVATIONS_ENABLED") == "true"
    if args.command in {"prepare-github", "deliver-github"}:
        incidents.require(os.environ.get("GITHUB_EVENT_NAME") == "schedule", "GitHub phase requires a trusted scheduled tick")
        api = deployments.GitHub()
        run = incidents.worker_context(api)
        incidents.require(run["event"] == "schedule", "GitHub phase requires a trusted scheduled tick")
        args.work_dir.mkdir(parents=True, exist_ok=True)
        ledger = incidents.Ledger(api, run, args.work_dir)
        slack = Slack(os.environ.get("SHARED_DATASETS_SLACK_BOT_TOKEN", ""), os.environ.get("SHARED_DATASETS_SLACK_CHANNEL_ID", ""))
        if args.command == "prepare-github":
            prepare_github(ledger, slack.identity(), now=utc_now())
        else:
            deliver_messages(ledger, None, slack, github_only=True)
        return
    if args.command == "verify" or args.command == "prepare" and (
            os.environ.get("GITHUB_EVENT_NAME") != "schedule" or not enabled):
        if args.command == "prepare":
            (args.work_dir / DELIVERY_CONTEXT).unlink(missing_ok=True)
        incidents.main()
        if args.command == "prepare" and enabled:
            from scripts.cloud_observation_ledger import CycleLedger
            api = deployments.GitHub()
            run = incidents.worker_context(api)
            write_context(incidents.Ledger(api, run, args.work_dir), CycleLedger(api, run, args.work_dir))
        return
    incidents.require(args.command == "deliver" or enabled and os.environ.get("GITHUB_EVENT_NAME") == "schedule",
                      "cloud reconciliation requires enabled trusted scheduled collection")
    from scripts.cloud_observation_ledger import CycleLedger
    api = deployments.GitHub()
    run = incidents.worker_context(api)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    ledger = incidents.Ledger(api, run, args.work_dir)
    cycles = CycleLedger(api, run, args.work_dir)
    if args.command in {"prepare", "deliver"}:
        slack = Slack(os.environ.get("SHARED_DATASETS_SLACK_BOT_TOKEN", ""),
                      os.environ.get("SHARED_DATASETS_SLACK_CHANNEL_ID", ""))
    if args.command == "deliver":
        deliver_messages(ledger, cycles, slack)
    elif args.command == "prepare":
        prepare_cycle(ledger, cycles, slack.identity(), reader(), now=utc_now(), clock=utc_now)
    elif args.command == "finalize-cycle":
        finalize_cycle(ledger, cycles)
    else:
        verify_heartbeat(ledger, cycles, reader())


if __name__ == "__main__":
    main()
