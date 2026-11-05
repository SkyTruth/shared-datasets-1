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
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPOSITORY = "SkyTruth/shared-datasets-1"
WORKFLOW = ".github/workflows/operational-safety-check.yml"
JOB = "Check for operational work"
# Explicit, reviewed, expiring exceptions only; deployment rollout holds do not
# approve a Scheduler pause. Entries require owner, reason and until (UTC).
APPROVED_PAUSES = {}
CRON_ASSETS = {
    "wdpa-monthly": ("wdpa-marine", "wdpa-terrestrial"),
    "eamlis-monthly": ("eamlis-abandoned-mine-land-inventory",),
    "mpatlas-monthly": ("mpatlas-zone-assessments",),
    "sea-ice-daily": ("ims-sea-ice-extent",),
    "dataset-usage": (),
}
FRESHNESS_DAYS = {"daily": 2, "monthly": 62}
UNCHANGED_REASONS = {"source fingerprint unchanged", "monthly source already published",
                     "latest available source already published"}


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


def validate_safety_dispatch(api, event, environment=os.environ, *, workflow=".github/workflows/catalog-web-deploy.yml"):
    current = context(api, environment, workflow=workflow)
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
            and re.fullmatch(r"[0-9a-f]{40}", str(producer.get("head_sha")))
            and (producer["head_sha"] == current["head_sha"] or api.get(
                f"repos/{REPOSITORY}/compare/{producer['head_sha']}...{current['head_sha']}").get("status") == "ahead")
            and producer.get("head_branch") == "main"
            and producer.get("event") in {"schedule", "workflow_dispatch"}
            and all(producer.get(key, {}).get("id") == current[key]["id"]
                    and producer.get(key, {}).get("full_name") == REPOSITORY for key in ("repository", "head_repository")),
            "untrusted safety producer")
    jobs = api.pages(f"repos/{REPOSITORY}/actions/runs/{identifier}/attempts/{attempt}/jobs?per_page=100", field="jobs")
    found = [job for job in jobs if job.get("name") == JOB]
    require(len(found) == 1 and found[0].get("run_id") == identifier
            and found[0].get("run_attempt") == attempt and found[0].get("head_sha") == producer["head_sha"],
            "missing exact safety job")
    probes = [step for step in found[0].get("steps", []) if step.get("name") == "Check operational work"]
    require(len(probes) == 1 and probes[0].get("status") == "completed" and probes[0].get("conclusion") == "success",
            "safety discovery did not succeed")
    # A wake-up hint only. Each worker still verifies its live inputs and authority.


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
        def verify(self, record):
            state = super().verify(record)
            if state and incidents.pending_posts(state):
                self.claims.append({"incident": state["incident"]["id"], "since": record["created_at"]})
            return state

        def load(self):
            if not hasattr(self, "loaded"):
                self.claims = []
                self.loaded = super().load()
            return copy.deepcopy(self.loaded)

        def save(self, state, _phase):
            existing = self.loaded.get(state["scope"])
            if existing and {**state, "healthy": existing["healthy"],
                             "terminal_validation": existing["terminal_validation"]} == existing:
                # Advancing health alone cannot change the incident or outbox.
                # The real worker retains watermarks whenever it next has work.
                return {"payload": {"state": state}}
            raise SnapshotWrite("normal reconciliation must persist this transition")

    ledger = DryLedger(api, run, directory, verification_cache=cache)
    states = ledger.load()
    require(all(state["identity"] == identity for state in states.values()), "Slack identity differs from registry")
    safety_scopes = {"workflow:" + WORKFLOW.rsplit("/", 1)[-1],
                     incidents.scope_for_job(JOB, {}, WORKFLOW.rsplit("/", 1)[-1])[0]}
    safety_open = any(scope in safety_scopes and state["incident"]["resolution"] is None for scope, state in states.items())
    observations = []
    for source in source_runs:
        if source.get("status") != "completed":
            continue
        if source.get("path") == WORKFLOW and source.get("conclusion") == "success" and not safety_open:
            continue
        observations.extend(incidents.source_observations(api, {"repository": {"full_name": REPOSITORY}, "workflow_run": source}))
    observations = [item for item in observations if start <= incidents.instant(item["time"]) <= end]
    try:
        settled = not incidents.prepare(ledger, identity, observations)
    except SnapshotWrite:
        settled = False
    return settled, ledger.claims


def verification_due(api, sources, pending, *, now):
    checks = [item for item in sources if item.get("path") == ".github/workflows/deployment-verification.yml"]
    if any(item.get("status") != "completed" for item in checks):
        return False
    latest = max(checks, key=lambda item: item["id"], default=None)
    if not latest or latest["conclusion"] != "success":
        return True
    checked = datetime.fromisoformat(latest["created_at"].replace("Z", "+00:00"))
    if now - checked >= timedelta(hours=6):
        return True
    return any(datetime.fromisoformat(api.pages(f"repos/{REPOSITORY}/deployments/{identifier}/statuses?per_page=100")[0]["created_at"].replace("Z", "+00:00")) > checked
               for identifier in pending)


def reconciliation_stalled(api, run):
    """Read the last two actual dispatch steps; no extra counter or ledger."""
    from scripts import slack_incidents as incidents
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = urlencode({"branch": "main", "created": ">=" + since, "per_page": 100})
    rows = api.pages(f"repos/{REPOSITORY}/actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}/runs?{query}", field="workflow_runs")
    prior = sorted((item for item in rows if item["id"] < run["id"]), key=lambda item: item["id"], reverse=True)[:2]
    if len(prior) < 2:
        return False
    for item in prior:
        if item.get("status") != "completed" or not incidents.alerts.verify_source_event(api, item):
            return False
        jobs = api.pages(f"repos/{REPOSITORY}/actions/runs/{item['id']}/attempts/{item['run_attempt']}/jobs?per_page=100", field="jobs")
        if not any(job["name"] == JOB and any(step["name"] == "Request operational reconciliation"
                   and step["status"] == "completed" and step["conclusion"] in {"success", "failure"}
                   for step in job.get("steps", [])) for job in jobs):
            return False
    return True


def fresh_activity(payload, cadence, *, now):
    latest, release = payload.get("latest_run") or {}, payload.get("latest_release") or {}
    healthy = latest.get("status") == "success" or (latest.get("status") == "skipped"
              and latest.get("reason") in UNCHANGED_REASONS and bool(release))
    # A metadata-only index rewrite cannot manufacture a new check-in date.
    date = latest.get("date") if healthy else release.get("date")
    if not date:
        return False
    checked = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    if healthy and payload.get("updated_at"):
        updated = datetime.fromisoformat(payload["updated_at"].replace("Z", "+00:00"))
        if updated.date() == checked.date():
            checked = updated
    return timedelta(0) <= now - checked < timedelta(days=FRESHNESS_DAYS[cadence])


def freshness(client, *, now, scheduler, pauses=APPROVED_PAUSES):
    """A healthy unchanged-source check-in counts; an old publication alone does not."""
    import csv
    from ingestion.common.release_index import coerce_release_index
    from scripts.catalog_drift_guard import DEFAULT_BUCKET
    from scripts import collect_release_indexes as indexes

    with (Path(__file__).resolve().parents[1] / "catalog/shared-datasets-catalog.csv").open() as stream:
        rows = {row["asset_slug"]: row for row in csv.DictReader(stream)}
    require({slug for slugs in CRON_ASSETS.values() for slug in slugs} ==
            {slug for slug, row in rows.items() if row["status"] == "active" and row["update_cadence"] in FRESHNESS_DAYS},
            "scheduled asset inventory differs from catalog")
    problems = []
    for job, slugs in CRON_ASSETS.items():
        state = scheduler(job)  # Missing jobs/failed reads are failures, not pauses.
        require(state.get("name") == f"projects/shared-datasets-1/locations/us-central1/jobs/{job}", "Scheduler identity changed")
        pause = pauses.get(job)
        approved = bool(state["state"] != "ENABLED" and pause and pause["owner"] and pause["reason"] and
                        now < datetime.fromisoformat(pause["until"].replace("Z", "+00:00")))
        if state["state"] != "ENABLED" and not approved:
            updated = datetime.fromisoformat(state["userUpdateTime"].replace("Z", "+00:00"))
            if now - updated >= timedelta(hours=72):
                problems.append(f"{job}: Scheduler {state['state']} for >=72 h without an approved pause")
        if approved:
            continue
        for slug in slugs:
            cadence = rows[slug]["update_cadence"]
            blob = client.bucket(DEFAULT_BUCKET).blob(indexes.PREFIX + slug + ".json")
            blob.reload()
            require(0 < blob.size <= indexes.MAX_INDEX_BYTES, "invalid release-index size")
            payload = coerce_release_index(json.loads(blob.download_as_bytes(if_generation_match=int(blob.generation))), slug)
            if not fresh_activity(payload, cadence, now=now):
                problems.append(f"{slug}: no successful release or unchanged-source check-in within 2x {cadence} cadence")
    return problems


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
    snapshot = None if pending else Snapshot.restore(api, run, policy, identity, inventory)
    if snapshot is not None and snapshot.matches(api):
        (directory / "claims.json").write_text("[]")
        snapshot.save(directory, run)
        print(f"OPERATIONAL_SAFETY: unchanged; {len(snapshot.data['requests'])} live input reads; no worker requested")
        return {"catalog": False, "reconcile": False, "verify": False, "snapshot": True}

    changed = catalog_changed(client, directory)
    recording = RecordingAPI(api)
    cache = VerificationCache.restore(api, run, identity, gh_version=policy["gh_version"])
    settled, claims = dry_reconciliation(recording, run, identity, all_sources, start, end, directory, cache)
    (directory / "claims.json").write_text(json.dumps(claims))
    # Do not certify bytes that arrived while the full catalog check ran.
    stable_catalog = catalog_inventory(client) == generations
    snapshot = Snapshot.capture(run, policy, identity, inventory, recording) if not pending and not claims and settled and not changed and stable_catalog else None
    if snapshot:
        snapshot.save(directory, run)
    return {"catalog": changed or not stable_catalog, "reconcile": not settled,
            "verify": bool(pending) and verification_due(api, all_sources, pending, now=end), "snapshot": snapshot is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-catalog-dispatch", action="store_true")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    if args.validate_catalog_dispatch:
        from scripts.deployment_revision import GitHub
        validate_safety_dispatch(GitHub(), json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()))
        return
    from scripts import catalog_drift_guard as catalog, slack_incidents as incidents
    require(args.work_dir is not None, "require a working directory")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    api = incidents.GitHub()
    run = context(api)
    client = catalog.get_client()
    if args.health:
        now = datetime.now(timezone.utc)
        def scheduler(job):
            return json.loads(subprocess.check_output(["gcloud", "scheduler", "jobs", "describe", job,
                "--location=us-central1", "--project=shared-datasets-1", "--format=json"], text=True))
        problems = freshness(client, now=now, scheduler=scheduler)
        result = json.loads((args.work_dir / "result.json").read_text())
        if os.environ.get("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED") != "true":
            sources, _, _, _ = source_inventory(api, run, (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"))
            for source in {item["path"]: item for item in sources}.values():
                if source.get("event") == "workflow_dispatch" and source.get("conclusion") in incidents.alerts.FAILED_CONCLUSIONS:
                    event = {"repository": {"full_name": REPOSITORY}, "workflow_run": source}
                    jobs = incidents.alerts.jobs_for_event(api, source)
                    if jobs is not None and incidents.alerts.alert_for_run(event, jobs=jobs):
                        problems.append(f"Failed dispatched run: https://github.com/{REPOSITORY}/actions/runs/{source['id']}")
        if result["reconcile"] and reconciliation_stalled(api, run):
            problems.append("reconcile=true for >=3 consecutive safety probes; inspect the batch worker")
        for claim in json.loads((args.work_dir / "claims.json").read_text()):
            if now - incidents.instant(claim["since"]) >= timedelta(hours=24):
                problems.append(f"{claim['incident']}: unchanged unconfirmed Slack claim >=24 h; owner reconciliation required")
        require(not problems, "; ".join(problems))
        return
    if os.environ.get("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED") == "true":
        identity = incidents.Slack(os.environ.get("SHARED_DATASETS_SLACK_BOT_TOKEN", ""),
                                   os.environ.get("SHARED_DATASETS_SLACK_CHANNEL_ID", "")).identity()
        result = probe(api, run, identity, os.environ["SHARED_DATASETS_SLACK_INCIDENTS_SINCE"], client, args.work_dir)
    else:
        from scripts.deployment_revision import pending
        sources, _, _, end = source_inventory(api, run, (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        records = pending(api, REPOSITORY)
        (args.work_dir / "claims.json").write_text("[]")
        result = {"catalog": catalog_changed(client, args.work_dir),
                  "reconcile": False, "verify": bool(records) and verification_due(api, sources, records, now=end), "snapshot": False}
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        for key, value in result.items():
            stream.write(f"{key}={str(value).lower()}\n")
    (args.work_dir / "result.json").write_text(json.dumps(result))
    print("OPERATIONAL_SAFETY: " + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
