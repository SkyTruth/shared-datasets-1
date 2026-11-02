#!/usr/bin/env python3
"""Read operational inputs; wake existing protected workers only for real work."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPOSITORY = "SkyTruth/shared-datasets-1"
WORKFLOW = ".github/workflows/operational-safety-check.yml"
JOB = "Check for operational work"


def require(value, message):
    if not value:
        raise RuntimeError("OPERATIONAL_SAFETY: " + message)


def context(api, environment=os.environ, *, workflow=WORKFLOW):
    require(environment.get("GITHUB_REPOSITORY") == REPOSITORY and environment.get("GITHUB_REF") == "refs/heads/main"
            and environment.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + workflow + "@refs/heads/main",
            "require exact trusted main workflow")
    identifier, attempt = int(environment["GITHUB_RUN_ID"]), int(environment["GITHUB_RUN_ATTEMPT"])
    run = api.get(f"repos/{REPOSITORY}/actions/runs/{identifier}/attempts/{attempt}")
    repository = api.get(f"repos/{REPOSITORY}")
    definition = api.get(f"repos/{REPOSITORY}/actions/workflows/{workflow.rsplit('/', 1)[-1]}")
    require(run.get("id") == identifier and run.get("run_attempt") == attempt
            and run.get("head_branch") == "main" and run.get("event") in {"schedule", "workflow_dispatch"}
            and run.get("path") == definition.get("path") == workflow and run.get("workflow_id") == definition.get("id")
            and re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha", "")))
            and repository.get("full_name") == REPOSITORY and type(repository.get("id")) is int
            and repository["id"] > 0
            and all(run.get(key, {}).get("id") == repository["id"] and run.get(key, {}).get("full_name") == REPOSITORY
                    for key in ("repository", "head_repository")), "invalid execution provenance")
    return run


def validate_catalog_dispatch(api, event, environment=os.environ):
    current = context(api, environment, workflow=".github/workflows/catalog-web-deploy.yml")
    require(current["event"] == "workflow_dispatch", "refresh admission requires dispatch")
    inputs = event.get("inputs", {})
    values = [str(inputs.get(key, "")) for key in ("safety_run_id", "safety_run_attempt")]
    require(all(re.fullmatch(r"[1-9][0-9]*", value) for value in values), "missing safety producer attempt")
    identifier, attempt = map(int, values)
    producer = api.get(f"repos/{REPOSITORY}/actions/runs/{identifier}")
    definition = api.get(f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}")
    require(producer.get("id") == identifier and producer.get("run_attempt") == attempt
            and producer.get("path") == definition.get("path") == WORKFLOW
            and producer.get("workflow_id") == definition.get("id")
            and producer.get("head_sha") == current["head_sha"] and producer.get("head_branch") == "main"
            and producer.get("event") in {"schedule", "workflow_dispatch"}
            and all(producer.get(key, {}).get("id") == current[key]["id"]
                    and producer.get(key, {}).get("full_name") == REPOSITORY for key in ("repository", "head_repository")),
            "untrusted safety producer")
    jobs = api.pages(f"repos/{REPOSITORY}/actions/runs/{identifier}/attempts/{attempt}/jobs?per_page=100", field="jobs")
    found = [job for job in jobs if job.get("name") == JOB]
    require(len(found) == 1 and found[0].get("run_id") == identifier
            and found[0].get("run_attempt") == attempt and found[0].get("head_sha") == current["head_sha"],
            "missing exact safety job")
    probes = [step for step in found[0].get("steps", []) if step.get("name") == "Check operational work"]
    require(len(probes) == 1 and probes[0].get("status") == "completed" and probes[0].get("conclusion") == "success",
            "safety discovery did not succeed")
    # This is only a wake-up hint. The catalog workflow still compares live
    # inputs and independently verifies exact CI and publishing authority.


def safety_parent(api, identifier, attempt, *, workflow_path=WORKFLOW, events=("schedule", "workflow_dispatch")):
    require(type(identifier) is int and identifier > 0 and type(attempt) is int and attempt > 0, "invalid safety attempt")
    run = api.get(f"repos/{REPOSITORY}/actions/runs/{identifier}")
    repository = api.get(f"repos/{REPOSITORY}")
    workflow = api.get(f"repos/{REPOSITORY}/actions/workflows/{workflow_path.rsplit('/', 1)[-1]}")
    require(run.get("id") == identifier and run.get("run_attempt") == attempt
            and run.get("path") == workflow.get("path") == workflow_path and run.get("workflow_id") == workflow.get("id")
            and run.get("head_branch") == "main" and run.get("event") in events
            and re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha", "")))
            and repository.get("full_name") == REPOSITORY and type(repository.get("id")) is int and repository["id"] > 0
            and all(run.get(key, {}).get("id") == repository["id"] and run.get(key, {}).get("full_name") == REPOSITORY
                    for key in ("repository", "head_repository")), "untrusted safety handoff")
    return run


def wait_for_safety(api, event, environment=os.environ, *, clock=time.monotonic, sleep=time.sleep):
    context(api, environment, workflow=".github/workflows/deployment-verification.yml")
    inputs = event.get("inputs", {})
    values = [str(inputs.get(key, "")) for key in ("safety_run_id", "safety_run_attempt")]
    require(all(re.fullmatch(r"[1-9][0-9]*", value) for value in values), "require both safety run and attempt")
    deadline = clock() + 120
    while True:
        run = safety_parent(api, *map(int, values))
        if run.get("status") == "completed":
            return  # Success or failure is now visible to authoritative replay.
        require(clock() < deadline, "safety handoff did not finish; reconciliation remains incomplete")
        sleep(2)


def catalog_inventory(client):
    from scripts import catalog_drift_guard as catalog, collect_release_indexes as indexes
    rows = list(client.list_blobs(catalog.DEFAULT_BUCKET, prefix=indexes.PREFIX))
    result = {}
    for row in rows:
        relative = row.name.removeprefix(indexes.PREFIX)
        require(row.name.startswith(indexes.PREFIX) and re.fullmatch(r"[a-z0-9][a-z0-9-]*\.json", relative)
                and row.name not in result and re.fullmatch(r"[1-9][0-9]*", str(row.generation))
                and type(row.size) is int and 0 < row.size <= indexes.MAX_INDEX_BYTES, "invalid release-index metadata")
        result[row.name] = str(row.generation)
    for name in (catalog.REMOTE_CSV_OBJECT, catalog.REMOTE_WEB_CATALOG_OBJECT):
        blob = client.bucket(catalog.DEFAULT_BUCKET).blob(name)
        blob.reload()  # Missing objects and failed reads remain failures.
        require(re.fullmatch(r"[1-9][0-9]*", str(blob.generation)), "catalog object lacks an immutable generation")
        result[name] = str(blob.generation)
    return result


def source_inventory(api, run, since, *, now=None):
    from scripts import slack_incidents as incidents
    now = now or datetime.now(timezone.utc)
    start = max(incidents.instant(since), now - timedelta(hours=24))
    query = urlencode({"branch": "main", "created": start.strftime("%Y-%m-%dT%H:%M:%SZ")
                       + ".." + now.strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": 100})
    rows = api.pages(f"repos/{REPOSITORY}/actions/runs?{query}", field="workflow_runs")
    selected = [item for item in rows if item.get("id") != run["id"]
                and item.get("name") in incidents.alerts.WORKFLOW_PATHS and item.get("head_branch") == "main"
                and item.get("head_repository", {}).get("full_name") == REPOSITORY]
    # Healthy timer runs are not new operational events. Their success is read
    # below when an existing safety incident actually needs recovery.
    substantive = [item for item in selected if item.get("path") != WORKFLOW or item.get("conclusion") != "success"]
    return sorted(substantive, key=lambda item: item["id"]), selected, start, now


def unadmitted_safety_failures(api, run, *, now=None):
    """Recover missing legacy handoffs without retrying an admitted webhook."""
    from scripts import workflow_failure_alert as alerts

    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _, sources, _, _ = source_inventory(api, run, since, now=now)
    pending = set()
    for source in sources:
        if source.get("path") == WORKFLOW and source.get("conclusion") in alerts.FAILED_CONCLUSIONS:
            parent = safety_parent(api, source["id"], source["run_attempt"])
            require(parent.get("status") == "completed" and parent.get("conclusion") in alerts.FAILED_CONCLUSIONS,
                    "failed safety handoff is no longer a completed failure")
            pending.add((parent["id"], parent["run_attempt"]))
    observer = ".github/workflows/deployment-verification.yml"
    for source in sources:
        if source.get("path") != observer or source.get("event") != "workflow_dispatch":
            continue
        match = re.fullmatch(r"Operational follow-up: safety ([1-9][0-9]*)/([1-9][0-9]*)", source.get("display_title", ""))
        if match is None or tuple(map(int, match.groups())) not in pending:
            continue
        admitted = safety_parent(api, source["id"], source["run_attempt"], workflow_path=observer, events=("workflow_dispatch",))
        require(admitted.get("display_title") == source["display_title"], "observer handoff title changed")
        pending.remove(tuple(map(int, match.groups())))
    return [{"id": identifier, "run_attempt": attempt} for identifier, attempt in sorted(pending)]


def catalog_changed(client, directory):
    from scripts import catalog_drift_guard as catalog, collect_release_indexes as indexes
    destination = directory / "release-indexes"
    indexes.collect(client, catalog.DEFAULT_BUCKET, destination)
    checks = catalog.run_checks(catalog.parse_args([
        "--bucket", catalog.DEFAULT_BUCKET, "--release-index-dir", str(destination.resolve()), "--latest-from-release-index",
    ]))
    return any(not check.ok for check in checks)


def dry_reconciliation(api, run, identity, source_runs, start, end, directory, cache):
    from scripts import slack_incidents as incidents
    from scripts.operational_snapshot import SnapshotWrite

    class DryLedger(incidents.Ledger):
        def load(self):
            if not hasattr(self, "loaded"):
                self.loaded = super().load()
            return copy.deepcopy(self.loaded)

        def save(self, *_args, **_kwargs):
            raise SnapshotWrite("normal reconciliation must persist this transition")

    ledger = DryLedger(api, run, directory, verification_cache=cache)
    states = ledger.load()
    require(all(state["identity"] == identity for state in states.values()), "Slack identity differs from registry")
    if any(state["deferred"] or incidents.pending_posts(state) or incidents.needs_parent_delivery(state)
           for state in states.values()):
        return False
    targets = incidents.job_targets()
    safety_scopes = {"workflow:" + WORKFLOW.rsplit("/", 1)[-1],
                     incidents.scope_for_job(JOB, {}, WORKFLOW.rsplit("/", 1)[-1])[0]}
    safety_open = any(scope in safety_scopes and state["incident"]["resolution"] is None for scope, state in states.items())
    observations = []
    for source in source_runs:
        if source.get("status") != "completed":
            continue
        if source.get("path") == WORKFLOW and source.get("conclusion") == "success" and not safety_open:
            continue
        observations.extend(incidents.source_observations(api, {"repository": {"full_name": REPOSITORY}, "workflow_run": source}, targets))
    observations = [item for item in observations if start <= incidents.instant(item["time"]) <= end]
    try:
        return not incidents.prepare(ledger, identity, observations)
    except SnapshotWrite:
        return False


def probe(api, run, identity, since, client, directory):
    from scripts import deployment_revision as deployments
    from scripts.incident_verification_cache import VerificationCache
    from scripts.operational_snapshot import RecordingAPI, Snapshot

    sources, all_sources, start, end = source_inventory(api, run, since)
    generations = catalog_inventory(client)
    inventory = {"sources": sources, "catalog": generations}
    policy = {"head_sha": run["head_sha"], "gh_version": subprocess.check_output(["gh", "--version"], text=True), "since": since}
    # Every pending WDPA record matters, including older revisions hidden behind
    # a newer deployment. The native observer still verifies before mutation.
    pending = deployments.pending(api, REPOSITORY)
    recovery_due = bool(pending) or any(item.get("path") == WORKFLOW and item.get("status") != "completed" for item in sources)
    snapshot = None if recovery_due else Snapshot.restore(api, run, policy, identity, inventory)
    if snapshot is not None and snapshot.matches(api):
        snapshot.save(directory, run)
        print(f"OPERATIONAL_SAFETY: unchanged; {len(snapshot.data['requests'])} live input reads; no worker requested")
        return {"catalog": False, "reconcile": False, "snapshot": True}

    changed = catalog_changed(client, directory)
    recording = RecordingAPI(api)
    settled = False
    if not recovery_due:
        cache = VerificationCache.restore(api, run, identity, gh_version=policy["gh_version"])
        settled = dry_reconciliation(recording, run, identity, all_sources, start, end, directory, cache)
    # Do not certify bytes that arrived while the full catalog check ran.
    stable_catalog = catalog_inventory(client) == generations
    snapshot = Snapshot.capture(run, policy, identity, inventory, recording) if settled and not changed and stable_catalog else None
    if snapshot:
        snapshot.save(directory, run)
    return {"catalog": changed or not stable_catalog, "reconcile": not settled, "snapshot": snapshot is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-catalog-dispatch", action="store_true")
    parser.add_argument("--wait-for-safety", action="store_true")
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    if args.validate_catalog_dispatch or args.wait_for_safety:
        from scripts.deployment_revision import GitHub
        require(not (args.validate_catalog_dispatch and args.wait_for_safety), "choose one admission operation")
        operation = validate_catalog_dispatch if args.validate_catalog_dispatch else wait_for_safety
        operation(GitHub(), json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()))
        return
    from scripts import catalog_drift_guard as catalog, slack_incidents as incidents
    require(args.work_dir is not None, "require a working directory")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    api = incidents.GitHub()
    run = context(api)
    client = catalog.get_client()
    missed_handoffs = []
    if os.environ.get("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED") == "true":
        identity = incidents.Slack(os.environ.get("SHARED_DATASETS_SLACK_BOT_TOKEN", ""),
                                   os.environ.get("SHARED_DATASETS_SLACK_CHANNEL_ID", "")).identity()
        result = probe(api, run, identity, os.environ["SHARED_DATASETS_SLACK_INCIDENTS_SINCE"], client, args.work_dir)
    else:
        # Legacy webhook mode has no authenticated registry to certify idle.
        # Preserve its completion polling without requiring a configured bot.
        result = {"catalog": catalog_changed(client, args.work_dir), "reconcile": True, "snapshot": False}
        missed_handoffs = unadmitted_safety_failures(api, run)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in result.items():
            stream.write(f"{key}={str(value).lower()}\n")
        stream.write("missed_handoffs=" + json.dumps(missed_handoffs, separators=(",", ":")) + "\n")
    print("OPERATIONAL_SAFETY: " + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
