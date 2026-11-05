#!/usr/bin/env python3
"""Record accepted identity decisions and finish their original Slack threads."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.common import decision_events as wire
from scripts import dataset_mutation_authorization as auth
from scripts import deployment_revision as deployment
from scripts import release_feature_model as model
from services.monitoring_slack import decisions

REPOSITORY = decisions.REPOSITORY
WORKFLOW = ".github/workflows/identity-decision-reconcile.yml"
ACCOUNT = "shared-datasets-reconciler@shared-datasets-1.iam.gserviceaccount.com"
PROTOCOL = "feature-identity-decisions-v1"


def verify_source(args, api, env):
    wire.require(env.get("GITHUB_REPOSITORY") == REPOSITORY and env.get("GITHUB_REF") == "refs/heads/main"
                 and env.get("GITHUB_WORKFLOW_REF") in {f"{REPOSITORY}/{path}@refs/heads/main" for path in (WORKFLOW, ".github/workflows/ci.yml")}, "trusted main decision workflow required")
    wire.require(re.fullmatch(r"[0-9a-f]{40}", args.executor_sha)
                 and subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == args.executor_sha,
                 "checkout differs from tested decision executor")
    deployment.verify_ci(api, REPOSITORY, args.executor_sha, args.source_run_id, args.source_run_attempt)
    return api.get(f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"] == args.executor_sha


def current_decisions(root):
    result = {}
    for slug in wire.ASSET_JOBS:
        path = root / "catalog" / "feature-identity-resolutions" / f"{slug}.json"
        if not path.exists():
            continue
        value = auth.plans.strict_json_loads(path.read_bytes())
        wire.require(set(value) == {"schema_version", "asset_slug", "decisions"}
                     and value["schema_version"] == 1 and value["asset_slug"] == slug, "invalid decision file")
        for decision in value["decisions"]:
            case_id = wire.case_key(slug, decision["release"], {key: decision[key] for key in wire.CASE_FIELDS})
            wire.require(case_id not in result, "duplicate release identity case")
            result[case_id] = (slug, decision)
    return result


def accepted_proof(api, executor, slug, decision, cache):
    match = re.fullmatch(r"https://github\.com/" + REPOSITORY + r"/pull/([1-9][0-9]*)", decision["pr_reference"])
    wire.require(match is not None and decision["reviewer"] == auth.REVIEWER, "decision lacks its real maintainer PR")
    number = int(match[1])
    if number not in cache:
        pr = api.get(f"repos/{REPOSITORY}/pulls/{number}")
        wire.require(pr.get("number") == number and pr.get("state") == "closed" and pr.get("merged") is True
                     and all(pr.get(side, {}).get("repo", {}).get("full_name") == REPOSITORY for side in ("base", "head"))
                     and pr["base"].get("ref") == "main", "decision PR is not a merged same-repository main PR")
        acceptance = auth.effective_acceptance(pr, api.pages(f"repos/{REPOSITORY}/pulls/{number}/reviews?per_page=100"), REPOSITORY)
        wire.require(deployment.git_ancestor(pr["merge_commit_sha"], executor), "decision PR is outside tested main history")
        files = api.pages(f"repos/{REPOSITORY}/pulls/{number}/files?per_page=100")
        cache[number] = (pr, acceptance, {item["filename"] for item in files if item.get("status") in {"added", "modified"}}, {})
    pr, acceptance, changed, documents = cache[number]
    path = f"catalog/feature-identity-resolutions/{slug}.json"
    wire.require(path in changed, "decision PR did not review this resolution file")
    if path not in documents:
        raw, _blob = auth.git_file(api, REPOSITORY, pr["head"]["sha"], path)
        documents[path] = auth.plans.strict_json_loads(raw)
    wire.require(documents[path].get("schema_version") == 1 and documents[path].get("asset_slug") == slug
                 and documents[path]["decisions"].count(decision) == 1, "current decision differs from its accepted head")
    case = {key: decision[key] for key in wire.CASE_FIELDS}
    ambiguity = model.IdentityAmbiguity(ambiguity_type=model.IDENTITY_AMBIGUITY_TYPE_UNKNOWN,
        identity_key=tuple(case["new_identity_key"]), geometry_hash=case["new_geometry_hash"], properties_hash=case["new_properties_hash"],
        matching_geometry_feature_ids=tuple(case["matching_geometry_feature_ids"]), matching_properties_feature_ids=tuple(case["matching_properties_feature_ids"]),
        matching_geometry_properties_hashes=tuple(case["matching_geometry_properties_hashes"]), matching_properties_geometry_hashes=tuple(case["matching_properties_geometry_hashes"]))
    model.validate_identity_resolutions(release=decision["release"], ambiguities=[ambiguity], decisions=[decision],
                                      previous_feature_ids=model.declared_previous_feature_ids([decision]))
    return decisions.approved({"asset_slug": slug, "decision": decision, "head_sha": pr["head"]["sha"],
                               "merge_sha": pr["merge_commit_sha"], "acceptance": acceptance})


def adoption_documents(root):
    result = []
    for path in sorted((root / ".github" / "identity-decision-adoptions").glob("*.json")):
        value = auth.plans.strict_json_loads(path.read_bytes())
        wire.require(set(value) == {"schema", "decision_pr", "event", "channel", "parent_ts", "reply_ts", "monitoring"}
                     and value["schema"] == "identity-decision-adoption-v1", "invalid reviewed decision adoption")
        wire.event(value["event"])
        result.append(value)
    return result


def reconcile_all(store, slack, identity):
    for path in store.paths("identity/events/"):
        match = re.fullmatch(r"identity/events/([0-9a-f]{64})\.json", path)
        wire.require(match is not None, "invalid decision event object")
        decisions.reconcile(store, match[1], slack, identity)


def install_adoption(value, store, slack, identity, read_alert):
    event = wire.event(value["event"])
    key = wire.event_key(event)
    wire.require(value["channel"] == identity["channel"] and all(isinstance(value[field], str)
                 and re.fullmatch(r"[0-9]+\.[0-9]{6}", value[field]) for field in ("parent_ts", "reply_ts")), "foreign legacy decision thread")
    existing, generation = decisions.load(store, key, identity)
    if existing is not None:
        wire.require(existing["event"] == event and existing["parent"] == {"owner": "legacy", "ts": value["parent_ts"]}, "legacy adoption conflicts with an existing parent")
    index, _ = decisions.approvals(store)
    records = {wire.case_key(event["asset_slug"], event["release"], item): None for item in event["cases"]}
    if existing is not None:
        replies = [reply for reply in existing["replies"].values() if reply["owner"] == "legacy" and reply["ts"] == value["reply_ts"]]
        wire.require(len(replies) == 1 and set(replies[0]["records"]) == set(records), "legacy reply acknowledgement changed")
        records = replies[0]["records"]
    else:
        records = {case_id: index["records"][case_id] for case_id in records}
    for case_id in records:
        wire.require(records[case_id]["decision"]["pr_reference"] == f"https://github.com/{REPOSITORY}/pull/{value['decision_pr']}", "legacy reply differs from its decision of record")
    links = []
    from services.monitoring_slack.run import validate_record
    for item in value["monitoring"]:
        wire.require(set(item) == {"incident_id", "parent_ts"}, "invalid legacy linked alert")
        alert = read_alert(item["incident_id"])
        resource = alert.get("resource", {})
        wire.require(alert.get("name") == f"projects/{wire.PROJECT}/alerts/{item['incident_id']}"
                     and alert.get("policy", {}).get("displayName") == decisions.POLICY
                     and alert["policy"].get("userLabels", {}).get("kind") == "decision-required"
                     and resource.get("type") == "cloud_run_job" and resource.get("labels", {}).get("project_id") == wire.PROJECT
                     and resource["labels"].get("location") == "us-central1" and resource["labels"].get("job_name") == event["job"], "legacy alert is outside this reviewed event")
        receipt, _ = store.load(item["incident_id"])
        wire.require(receipt is not None, "legacy Monitoring message has no bot receipt")
        validate_record(receipt, identity)
        wire.require(receipt["ts"] == item["parent_ts"], "legacy Monitoring parent differs from reviewed adoption")
        links.append({"context": wire.context({field: event[field] for field in ("project", "job", "execution", "asset_slug", "release")}), "incident_id": item["incident_id"]})
    fingerprint = wire.digest(records)
    record = {"schema": decisions.EVENT_SCHEMA, "event": event, "identity": identity,
              "parent": {"owner": "legacy", "ts": value["parent_ts"]}, "parent_view": None,
              "replies": {fingerprint: {"records": records, "owner": "legacy", "ts": value["reply_ts"]}}}
    if existing is None:
        decisions.save(store, key, record, generation)
    for link in links:
        path = f"identity/links/{key}/{link['incident_id']}.json"
        previous, version = store.read(path)
        wire.require(previous is None or previous == link, "legacy incident already belongs to another event")
        if previous is None:
            store.write(path, link, version)


def sync(args, api, root, store, slack, identity, read_alert):
    index, generation = decisions.approvals(store)
    if index["revision"] is not None:
        wire.require(deployment.git_ancestor(index["revision"], args.executor_sha), "newer or divergent decision index already exists")
    current = current_decisions(root)
    known = {key: wire.digest(value) for key, (_slug, value) in current.items()}
    needed = set()
    for path in store.paths("identity/events/"):
        value, _ = store.read(path)
        source = wire.event(value["event"])
        needed.update(wire.case_key(source["asset_slug"], source["release"], item) for item in source["cases"])
    adoptions = adoption_documents(root)
    for adoption in adoptions:
        source = wire.event(adoption["event"])
        needed.update(wire.case_key(source["asset_slug"], source["release"], item) for item in source["cases"])
    if index["revision"] is not None:
        needed.update(key for key in current if index["known"].get(key) != known[key])
    records, cache = {}, {}
    for key, (slug, decision) in current.items():
        previous = index["records"].get(key)
        if previous is not None and previous["decision"] == decision:
            records[key] = previous
        elif key in needed:
            records[key] = accepted_proof(api, args.executor_sha, slug, decision, cache)
    desired = {"schema": decisions.APPROVAL_SCHEMA, "revision": args.executor_sha, "known": known, "records": records}
    wire.require(api.get(f"repos/{REPOSITORY}/git/ref/heads/main")["object"]["sha"] == args.executor_sha,
                 "decision executor was superseded before reconciliation")
    for pr, acceptance, _files, _documents in cache.values():
        live = api.get(f"repos/{REPOSITORY}/pulls/{pr['number']}")
        wire.require(live.get("merged") is True and live["head"]["sha"] == pr["head"]["sha"]
                     and auth.effective_acceptance(live, api.pages(f"repos/{REPOSITORY}/pulls/{pr['number']}/reviews?per_page=100"), REPOSITORY) == acceptance,
                     "decision acceptance changed before reconciliation")
    if desired != index:
        store.write("identity/approvals.json", desired, generation)
    for adoption in adoptions:
        install_adoption(adoption, store, slack, identity, read_alert)
    reconcile_all(store, slack, identity)
    return len(records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executor-sha", required=True)
    parser.add_argument("--source-run-id", required=True, type=int)
    parser.add_argument("--source-run-attempt", required=True, type=int)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args(argv)
    api = deployment.GitHub()
    if not verify_source(args, api, dict(os.environ)):
        print("Decision reconciliation deferred: this tested revision is superseded by current main.")
        return 0
    if args.check_only:
        print("Tested main decision executor verified; live transport and review gates remain.")
        return 0
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    from google.cloud import storage
    from services.monitoring_slack.run import Store
    from scripts.slack_incident_api import Slack
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    wire.require(getattr(credentials, "service_account_email", None) == ACCOUNT, "protected reconciliation identity required")
    session = AuthorizedSession(credentials)
    service = session.get(f"https://run.googleapis.com/v2/projects/{wire.PROJECT}/locations/us-central1/services/monitoring-slack", timeout=30)
    service.raise_for_status()
    settings = service.json()["template"]["containers"][0]
    env = {item["name"]: item.get("value") for item in settings.get("env", [])}
    wire.require(env.get("IDENTITY_DECISION_PROTOCOL") == PROTOCOL and re.fullmatch(r"[^@]+@sha256:[0-9a-f]{64}", settings["image"]), "deploy the reviewed decision-capable transport first")
    def read_alert(key):
        wire.require(re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", key), "invalid incident ID")
        response = session.get(f"https://monitoring.googleapis.com/v3/projects/{wire.PROJECT}/alerts/{key}", timeout=30)
        response.raise_for_status()
        return response.json()
    slack = Slack(os.environ["SHARED_DATASETS_SLACK_BOT_TOKEN"], os.environ["SHARED_DATASETS_SLACK_CHANNEL_ID"])
    identity = slack.identity()
    store = Store(storage.Client(project=wire.PROJECT, credentials=credentials).bucket("skytruth-shared-datasets-1-monitoring-slack"))
    count = sync(args, api, Path.cwd(), store, slack, identity, read_alert)
    print(f"{count} reviewed identity decisions indexed; original notification threads reconciled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
