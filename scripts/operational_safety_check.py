#!/usr/bin/env python3
"""Read operational inputs; wake existing protected workers only for real work."""
from __future__ import annotations

import argparse
import copy
import csv
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
API_ROOT = f"repos/{REPOSITORY}"
WORKFLOW = ".github/workflows/operational-safety-check.yml"
JOB = "Check for operational work"
HEALTH_SCOPE = "health:operational-safety-check"

from scripts import incident_verification_cache as native
from scripts.ingestion_targets import TARGETS, DEPLOY_TARGETS
from scripts.ci_contract import INGESTION_ROLLOUT_HOLDS, rollout_holds as validate_suspensions
from scripts.dataset_mutation_authorization import GitHub
from scripts import workflow_failure_alert as alerts

ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "catalog/shared-datasets-catalog.csv").open() as stream:
    CATALOG = {row["asset_slug"]: row for row in csv.DictReader(stream)}
CRON_ASSETS = {job: tuple(slug for slug in CATALOG if slug == config["asset_slug"] or
               (not config["asset_slug"] and slug.startswith(config["selection"] + "-")))
               for job, config in TARGETS.items()} | {"dataset-usage": ()}
FRESHNESS_DAYS = {"daily": 2, "monthly": 45}
UNCHANGED_REASONS = {"source fingerprint unchanged", "monthly source already published",
                     "latest available source already published"}
PROBE_STEP = "Check operational work"
UPLOAD_STEP = "Retain operational safety snapshot"
SCHEMA = "shared-datasets-operational-snapshot-v2"


class SnapshotWrite(RuntimeError):
    """Reconciliation requires the signed worker."""


class Snapshot:
    """Cache catalog proof and receipts; reread live incidents."""
    def __init__(self, data):
        require(isinstance(data, dict) and set(data) == {"schema", "producer", "policy", "identity", "inventory_sha256", "entries"}
                and data["schema"] == SCHEMA, "invalid snapshot document")
        require(isinstance(data["producer"], dict) and native.execution(data["producer"]) == data["producer"], "invalid snapshot producer")
        native.validate_context(data["policy"], data["identity"], since=True)
        require(data["producer"]["head_sha"] == data["policy"]["head_sha"], "snapshot policy differs from producer")
        require(isinstance(data["inventory_sha256"], str) and native.SHA.fullmatch(data["inventory_sha256"]), "invalid inventory fingerprint")
        self.cache = native.VerificationCache(data["producer"], data["identity"],
            gh_version=data["policy"]["gh_version"], entries=data["entries"])
        self.data = copy.deepcopy(data)

    @classmethod
    def restore(cls, api, run, policy, identity, inventory):
        native.validate_context(policy, identity, since=True)
        require(native.execution(run)["head_sha"] == policy["head_sha"], "policy differs from execution")
        retained = native.retained_payload(api, run, workflow_path=WORKFLOW, job_name=JOB,
            required_steps=(PROBE_STEP, UPLOAD_STEP), events=("schedule", "workflow_dispatch"), name_prefix="safety-snapshot")
        if retained:
            producer, data = retained
            snapshot = cls(data)
            require(snapshot.data["producer"] == native.execution(producer), "snapshot artifact producer mismatch")
            return snapshot if (data["policy"] == policy and data["identity"] == identity
                                and data["inventory_sha256"] == native.digest(inventory)) else None
        return None


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def require(value, message):
    if not value:
        raise RuntimeError("OPERATIONAL_SAFETY: " + message)


def context(api, environment=os.environ, *, workflow=WORKFLOW, events=("schedule", "workflow_dispatch")):
    require(environment.get("GITHUB_REPOSITORY") == REPOSITORY and environment.get("GITHUB_REF") == "refs/heads/main"
            and environment.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + workflow + "@refs/heads/main",
            "require exact trusted main workflow")
    identifier, attempt = int(environment["GITHUB_RUN_ID"]), int(environment["GITHUB_RUN_ATTEMPT"])
    run = api.get(f"{API_ROOT}/actions/runs/{identifier}/attempts/{attempt}")
    require(run.get("id") == identifier and run.get("run_attempt") == attempt
            and run["event"] in events
            and run["path"] == workflow, "invalid execution provenance")
    definition = api.get(f"{API_ROOT}/actions/workflows/{workflow.rsplit('/', 1)[-1]}")
    alerts.source_identity(api, run, workflow, definition)
    return run


def validate_safety_dispatch(api, event, environment=os.environ, *, workflow=".github/workflows/catalog-web-deploy.yml"):
    current = context(api, environment, workflow=workflow)
    require(current["event"] == "workflow_dispatch", "refresh admission requires dispatch")
    inputs = event.get("inputs", {})
    values = [str(inputs.get(key, "")) for key in ("safety_run_id", "safety_run_attempt")]
    require(all(re.fullmatch(r"[1-9][0-9]*", value) for value in values), "missing safety producer attempt")
    identifier, attempt = map(int, values)
    producer = api.get(f"{API_ROOT}/actions/runs/{identifier}")
    definition = api.get(f"{API_ROOT}/actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}")
    alerts.source_identity(api, producer, WORKFLOW, definition)
    require(producer.get("id") == identifier and producer.get("run_attempt") == attempt
            and producer["path"] == WORKFLOW
            and (producer["head_sha"] == current["head_sha"] or api.get(
                f"{API_ROOT}/compare/{producer['head_sha']}...{current['head_sha']}").get("status") == "ahead")
            and producer.get("event") in {"schedule", "workflow_dispatch"},
            "untrusted safety producer")
    jobs = api.pages(f"{API_ROOT}/actions/runs/{identifier}/attempts/{attempt}/jobs?per_page=100", field="jobs")
    found = [job for job in jobs if job.get("name") == JOB]
    require(len(found) == 1 and found[0].get("run_id") == identifier
            and found[0].get("run_attempt") == attempt and found[0].get("head_sha") == producer["head_sha"],
            "missing exact safety job")
    probes = [step for step in found[0].get("steps", []) if step.get("name") == "Check operational work"]
    require(len(probes) == 1 and probes[0].get("status") == "completed" and probes[0].get("conclusion") == "success",
            "safety discovery did not succeed")
    # Workers independently verify inputs and authority.


def catalog_inventory(client):
    from scripts import catalog_drift_guard as catalog, collect_release_indexes as indexes
    result = {}
    for row in client.list_blobs(catalog.DEFAULT_BUCKET, prefix=indexes.PREFIX):
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
    now = now or datetime.now(timezone.utc)
    start = max(native.timestamp(since), now - timedelta(hours=24))
    query = urlencode({"branch": "main", "created": start.strftime("%Y-%m-%dT%H:%M:%SZ")
                       + ".." + now.strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": 100})
    rows = api.pages(f"{API_ROOT}/actions/runs?{query}", field="workflow_runs")
    return [item for item in rows if (run is None or item.get("id") != run["id"])
                and item.get("name") in alerts.WORKFLOW_PATHS and item.get("head_branch") == "main"
                and item.get("head_repository", {}).get("full_name") == REPOSITORY], start, now


def catalog_changed(client, directory):
    from scripts import catalog_drift_guard as catalog, collect_release_indexes as indexes
    destination = directory / "release-indexes"
    indexes.collect(client, catalog.DEFAULT_BUCKET, destination)
    return any(not check.ok for check in catalog.run_checks(catalog.parse_args([
        "--bucket", catalog.DEFAULT_BUCKET, "--release-index-dir", str(destination.resolve()), "--latest-from-release-index",
    ])))


def dry_reconciliation(api, run, identity, source_runs, start, end, directory, cache):
    from scripts import slack_incidents as incidents

    claims = []
    class DryLedger(incidents.Ledger):
        def verify(self, record):
            state = super().verify(record)
            if state and incidents.pending_posts(state):
                claims.append({"incident": state["incident"]["id"], "since": record["created_at"]})
            return state

        def save(self, state, _phase):
            existing = states.get(state["scope"])
            if existing and {**state, "healthy": existing["healthy"],
                             "terminal_validation": existing["terminal_validation"]} == existing:
                # Watermarks alone need no wake-up; the next worker retains them.
                return {"payload": {"state": state}}
            raise SnapshotWrite("normal reconciliation must persist this transition")

    ledger = DryLedger(api, run, directory, verification_cache=cache)
    states = ledger.load()
    ledger.load = lambda: copy.deepcopy(states)
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
    return settled, claims


def verification_due(api, sources, pending, *, now):
    checks = [item for item in sources if item.get("path") == ".github/workflows/deployment-verification.yml"]
    if any(item.get("status") != "completed" for item in checks):
        return False
    latest = max(checks, key=lambda item: item["id"], default=None)
    if not latest or latest["conclusion"] != "success":
        return True
    # Live hints wake the observer; signed exact-image proof still gates completion.
    statuses = [api.pages(f"{API_ROOT}/deployments/{identifier}/statuses?per_page=100")[0] for identifier in pending]
    for status in statuses:
        match = re.fullmatch(r"https://console\.cloud\.google\.com/run/jobs/executions/details/us-central1/(wdpa-monthly-[a-z0-9]{5})\?project=shared-datasets-1", status["environment_url"])
        require(match, "pending deployment lacks exact execution identity")
        execution = json.loads(subprocess.check_output(["gcloud", "run", "jobs", "executions", "describe", match[1],
            "--region=us-central1", "--project=shared-datasets-1", "--format=json(status)"], text=True))
        if any(execution.get("status", {}).get(key) for key in ("completionTime", "failedCount", "cancelledCount")):
            return True
    checked = utc(latest["created_at"])
    return now - checked >= timedelta(hours=3) or any(utc(status["created_at"]) > checked for status in statuses)


def reconciliation_stalled(api, run):
    """Read the last two dispatch steps without a separate counter."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = urlencode({"branch": "main", "created": ">=" + since, "per_page": 100})
    rows = api.pages(f"{API_ROOT}/actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}/runs?{query}", field="workflow_runs")
    prior = sorted((item for item in rows if item["id"] < run["id"]), key=lambda item: item["id"], reverse=True)[:2]
    return len(prior) == 2 and all(item.get("status") == "completed" and alerts.verify_source_event(api, item)
        and any(job["name"] == JOB and any(step["name"] == "Request operational reconciliation"
            and step["status"] == "completed" and step["conclusion"] in {"success", "failure"} for step in job.get("steps", []))
            for job in api.pages(f"{API_ROOT}/actions/runs/{item['id']}/attempts/{item['run_attempt']}/jobs?per_page=100", field="jobs"))
        for item in prior)


def fresh_activity(payload, cadence, *, now):
    latest, release = payload.get("latest_run") or {}, payload.get("latest_release") or {}
    healthy = latest.get("status") == "success" or (latest.get("status") == "skipped"
              and latest.get("reason") in UNCHANGED_REASONS and bool(release))
    # Metadata rewrites cannot advance the check-in date.
    date = latest.get("date") if healthy else release.get("date")
    if not date:
        return False
    checked = datetime.fromisoformat(date).replace(tzinfo=timezone.utc)
    updated = utc(payload["updated_at"]) if healthy and payload.get("updated_at") else checked
    checked = updated if updated.date() == checked.date() else checked
    return timedelta(0) <= now - checked < timedelta(days=FRESHNESS_DAYS[cadence])


def freshness(client, *, now, scheduler, pauses=None):
    """Count healthy unchanged-source checks toward freshness."""
    from ingestion.common.release_index import coerce_release_index
    from scripts.catalog_drift_guard import DEFAULT_BUCKET
    from scripts import collect_release_indexes as indexes

    pauses = json.loads((ROOT / ".github/scheduler-pauses.json").read_text()) if pauses is None else pauses
    validate_suspensions(pauses, targets=CRON_ASSETS)
    require({slug for slugs in CRON_ASSETS.values() for slug in slugs} ==
            {slug for slug, row in CATALOG.items() if row["status"] == "active" and row["update_cadence"] in FRESHNESS_DAYS},
            "scheduled asset inventory differs from catalog")
    problems = []
    for job, slugs in CRON_ASSETS.items():
        state = scheduler(job)  # Missing jobs/failed reads are failures, not pauses.
        require(state.get("name") == f"projects/shared-datasets-1/locations/us-central1/jobs/{job}", "Scheduler identity changed")
        pause = pauses.get(job)
        if state["state"] == "PAUSED" and pause and now < utc(pause["expires_at"]):
            continue
        if state["state"] != "ENABLED" and now - utc(state["userUpdateTime"]) >= timedelta(hours=72):
            problems.append(f"{job}: Scheduler {state['state']} for >=72 h without an approved pause")
        for slug in slugs:
            cadence = CATALOG[slug]["update_cadence"]
            blob = client.bucket(DEFAULT_BUCKET).blob(indexes.PREFIX + slug + ".json")
            blob.reload()
            require(0 < blob.size <= indexes.MAX_INDEX_BYTES, "invalid release-index size")
            payload = coerce_release_index(json.loads(blob.download_as_bytes(if_generation_match=int(blob.generation))), slug)
            if not fresh_activity(payload, cadence, now=now):
                problems.append(f"{slug}: no successful release or unchanged-source check-in within {FRESHNESS_DAYS[cadence]} days ({cadence})")
    return problems


def probe(api, run, identity, since, client, directory):
    from scripts import deployment_revision as deployments

    pending = deployments.pending(api, REPOSITORY)
    since = since if identity else (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    all_sources, start, end = source_inventory(api, run, since) if identity or pending else ([], None, None)
    generations = catalog_inventory(client)
    policy = {"head_sha": run["head_sha"], "gh_version": subprocess.check_output(["gh", "--version"], text=True), "since": since} if identity else None
    snapshot = Snapshot.restore(api, run, policy, identity, generations) if identity and not pending else None
    changed = False if snapshot else catalog_changed(client, directory)
    cache = (snapshot.cache if snapshot else native.VerificationCache.restore(api, run, identity, gh_version=policy["gh_version"])) if identity else None
    settled, claims = dry_reconciliation(api, run, identity, all_sources, start, end, directory, cache) if identity else (True, [])
    (directory / "claims.json").write_text(json.dumps(claims))
    # Reject catalog changes during comparison.
    stable_catalog = catalog_inventory(client) == generations
    retain = identity is not None and not pending and not claims and settled and not changed and stable_catalog
    if retain:
        data = Snapshot({"schema": SCHEMA, "producer": native.execution(run), "policy": policy, "identity": identity,
                         "inventory_sha256": native.digest(generations), "entries": cache.used}).data
        raw = native.canonical(data)
        require(len(raw) <= native.MAX_BYTES, "snapshot exceeds size limit")
        (directory / native.MEMBER).write_bytes(raw)
    return {"catalog": changed or not stable_catalog, "reconcile": not settled,
            "verify": bool(pending) and verification_due(api, all_sources, pending, now=end), "snapshot": retain}


def policy_warnings(now):
    warnings, problems = [], []
    for filename in (INGESTION_ROLLOUT_HOLDS, ".github/scheduler-pauses.json"):
        entries = json.loads((ROOT / filename).read_text())
        validate_suspensions(entries, targets=(CRON_ASSETS if filename.endswith("scheduler-pauses.json") else {config["selection"] for config in TARGETS.values()}))
        for target, entry in entries.items():
            remaining = utc(entry["expires_at"]) - now
            if remaining <= timedelta(hours=75):  # Three-hour sampling still warns >=72 hours ahead.
                message = f"{target}: {filename} {'expired' if remaining <= timedelta(0) else 'expires'} {entry['expires_at']} (owner {entry['owner']})"
                (problems if remaining <= timedelta(0) else warnings).append(message)
    return warnings, problems


def incident_problems():
    disabled = [] if os.environ.get("SHARED_DATASETS_SLACK_INCIDENTS_ENABLED") == "true" else ["Event incident alerting disabled: SHARED_DATASETS_SLACK_INCIDENTS_ENABLED is not true"]
    return disabled + [f"Event incident alerting unavailable: SHARED_DATASETS_SLACK_{key} missing"
                       for key in ("INCIDENTS_SINCE", "BOT_TOKEN") if not os.environ.get("SHARED_DATASETS_SLACK_" + key, "").strip()]


def deployed_image_lag(api, *, now):
    """Compare live images with admitted protected starts."""
    from scripts import deployment_revision as deployments
    holds = json.loads((ROOT / INGESTION_ROLLOUT_HOLDS).read_text())
    validate_suspensions(holds)
    problems = []
    for target, config in DEPLOY_TARGETS.items():
        if config["selection"] in holds and now < utc(holds[config["selection"]]["expires_at"]):
            continue
        records = api.pages(f"{API_ROOT}/deployments?environment=production-{target}&per_page=100")
        if not records:
            continue
        latest = max(records, key=lambda row: row["id"])
        payload = deployments.verify_record(api, REPOSITORY, latest)
        require(payload["target"] == target, "deployment inventory target mismatch")
        if now - utc(latest["created_at"]) <= timedelta(hours=72):
            continue
        live = subprocess.check_output(["gcloud", "run", "jobs", "describe", target, "--region=us-central1",
            "--project=shared-datasets-1", "--format=value(spec.template.spec.template.spec.containers[0].image)"], text=True, timeout=60).strip()
        if live != payload["artifact"]:
            problems.append(f"{target}: deployed image trails accepted main deployment {latest['id']} ({latest['sha']}) for >72 h")
    return problems


def audit_freshness(api, *, now):
    """Require successful weekly audits within nine days."""
    problems = []
    for filename in ("bucket-hygiene-audit.yml", "scratch-cleanup-audit.yml"):
        definition = api.get(f"{API_ROOT}/actions/workflows/{filename}")
        require(definition.get("path") == ".github/workflows/" + filename, "weekly audit workflow identity changed")
        query = urlencode({"branch": "main", "status": "success", "per_page": 100,
                           "created": ">=" + (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        runs = api.pages(f"{API_ROOT}/actions/workflows/{filename}/runs?{query}", field="workflow_runs")
        if not any(run.get("path") == definition["path"] and run.get("workflow_id") == definition["id"]
                   and run.get("status") == "completed" and run.get("conclusion") == "success"
                   and alerts.verify_source_event(api, run)
                   and timedelta(0) <= now - utc(run["updated_at"]) < timedelta(days=9)
                   for run in runs):
            problems.append(f"{filename}: no successful main completion within 9 days")
    return problems


def previous_health(api, run):
    query = urlencode({"branch": "main", "created": ">=" + (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": 100})
    prior = api.pages(f"{API_ROOT}/actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}/runs?{query}", field="workflow_runs")
    for producer in sorted((row for row in prior if row["id"] < run["id"] and row.get("status") == "completed" and row.get("path") == WORKFLOW), key=lambda row: row["id"], reverse=True):
        artifacts = api.pages(f"{API_ROOT}/actions/runs/{producer['id']}/artifacts?per_page=100", field="artifacts")
        selected = [row for row in artifacts if row["name"] == f"safety-health-{producer['id']}-{producer['run_attempt']}" and not row["expired"]]
        if not selected:
            continue
        require(len(selected) == 1 and producer.get("event") in {"schedule", "workflow_dispatch"} and alerts.verify_source_event(api, producer), "untrusted safety health report")
        jobs = api.pages(f"{API_ROOT}/actions/runs/{producer['id']}/attempts/{producer['run_attempt']}/jobs?per_page=100", field="jobs")
        job, = [row for row in jobs if row["name"] == JOB]
        require(any(step["name"] == "Retain safety health report" and step.get("status") == "completed" and step["conclusion"] == "success" for step in job["steps"]), "health report upload did not succeed")
        report = native.artifact_payload(api, {**producer, "cache_job_started_at": job["started_at"], "cache_job_completed_at": job["completed_at"]}, selected[0])
        require(set(report) in ({"problems", "warnings", "notified_at"}, {"scope", "problems", "warnings", "notified_at"})
                and report.get("scope", HEALTH_SCOPE) == HEALTH_SCOPE
                and all(isinstance(report[key], list) and all(isinstance(item, str) for item in report[key]) for key in ("problems", "warnings")), "invalid safety health report")
        return report
    return {"problems": [], "warnings": [], "notified_at": None}


def report_health(api, run, directory, problems, warnings, now):
    from scripts.slack_notify import notify
    previous = previous_health(api, run)
    report = {"scope": HEALTH_SCOPE, "problems": sorted(set(problems)), "warnings": sorted(set(warnings)), "notified_at": previous["notified_at"]}
    findings = report["problems"] + report["warnings"]
    due = findings and (any(report[key] != previous[key] for key in ("problems", "warnings")) or not report["notified_at"] or
                        now - datetime.fromisoformat(report["notified_at"]) >= timedelta(days=1))
    if due:
        notify(title="Operational safety findings", body="\n".join(findings) + f"\nhttps://github.com/{REPOSITORY}/actions/runs/{run['id']}",
               status="error" if problems else "warning", fields={"Incident scope": HEALTH_SCOPE}, strict=True)
        report["notified_at"] = now.isoformat()
    (directory / "health").mkdir(exist_ok=True)
    (directory / "health/cache.json").write_bytes(native.canonical(report))
    for finding in findings:
        print("::warning::" + finding)


def heartbeat_series(now):
    """Absence detection requires an existing metric series."""
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/monitoring.read"])
    query = {"filter": 'metric.type="logging.googleapis.com/user/operational-safety-heartbeat" AND resource.type="global"',
             "interval.startTime": (now - timedelta(hours=7)).isoformat(), "interval.endTime": now.isoformat(),
             "view": "HEADERS", "pageSize": 1}
    with AuthorizedSession(credentials) as session:
        response = session.get("https://monitoring.googleapis.com/v3/projects/shared-datasets-1/timeSeries", params=query, timeout=30)
        response.raise_for_status()
        series = response.json().get("timeSeries", [])
        require(isinstance(series, list), "invalid heartbeat time-series response")
        return [] if series else ["Heartbeat metric has no recent time series; absence detection is not established"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-catalog-dispatch", action="store_true")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--work-dir", type=Path)
    args = parser.parse_args()
    if args.validate_catalog_dispatch:
        validate_safety_dispatch(GitHub(), json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text()))
        return
    require(args.work_dir is not None, "require a working directory")
    args.work_dir.mkdir(parents=True, exist_ok=True)
    api = GitHub()
    run = context(api)
    if args.health:
        now = datetime.now(timezone.utc)
        def scheduler(job):
            return json.loads(subprocess.check_output(["gcloud", "scheduler", "jobs", "describe", job,
                "--location=us-central1", "--project=shared-datasets-1", "--format=json"], text=True))
        warnings = []
        configuration = incident_problems()
        problems = list(configuration)
        probe_outcome = os.environ.get("SAFETY_PROBE_OUTCOME", "success")
        def dataset_freshness():
            from scripts import catalog_drift_guard as catalog
            return freshness(catalog.get_client(), now=now, scheduler=scheduler)

        def reconciliation_problems():
            if probe_outcome != "success":
                return []
            findings = []
            result = json.loads((args.work_dir / "result.json").read_text())
            if result["reconcile"] and reconciliation_stalled(api, run):
                findings.append("reconcile=true for >=3 consecutive safety probes; inspect the batch worker")
            for claim in (json.loads((args.work_dir / "claims.json").read_text()) if not configuration else []):
                if now - native.timestamp(claim["since"]) >= timedelta(hours=24):
                    findings.append(f"{claim['incident']}: unchanged unconfirmed Slack claim >=24 h; owner reconciliation required")
            return findings

        readers = [
            ("policy_warnings", lambda: policy_warnings(now)),
            ("audit_freshness", lambda: ([], audit_freshness(api, now=now))),
            ("deployed_image_lag", lambda: ([], deployed_image_lag(api, now=now))),
            ("freshness", lambda: ([], dataset_freshness())),
            ("reconciliation", lambda: ([], reconciliation_problems())),
        ]
        if probe_outcome == "success":
            readers.append(("heartbeat_series", lambda: (heartbeat_series(now), [])))
        for name, reader in readers:
            try:
                reader_warnings, reader_problems = reader()
                warnings += reader_warnings
                problems += reader_problems
            except Exception as error:  # Each external reader owns its failure finding.
                problems.append(f"Health check incomplete ({name}): {type(error).__name__}: {error}")
        if probe_outcome == "skipped":
            problems.append("Operational setup failed before discovery: " + os.environ.get("SAFETY_SETUP_OUTCOMES", "inspect setup steps"))
        if probe_outcome != "success":
            problems.append(f"Operational discovery {probe_outcome}; inspect Check operational work")
        report_health(api, run, args.work_dir, problems, warnings, now)
        if probe_outcome == "success":
            subprocess.check_call(["gcloud", "logging", "write", "operational-safety-heartbeat", '{"workflow":"operational-safety-check.yml"}',
                "--payload-type=json", "--severity=INFO", "--project=shared-datasets-1"])
        return
    from scripts import catalog_drift_guard as catalog, slack_incidents as incidents
    client = catalog.get_client()
    identity = None if incident_problems() else incidents.Slack(os.environ["SHARED_DATASETS_SLACK_BOT_TOKEN"],
                               os.environ.get("SHARED_DATASETS_SLACK_CHANNEL_ID", "")).identity()
    result = probe(api, run, identity, os.environ.get("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", ""), client, args.work_dir)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.writelines(f"{key}={str(value).lower()}\n" for key, value in result.items())
    (args.work_dir / "result.json").write_text(json.dumps(result))
    print("OPERATIONAL_SAFETY: " + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
