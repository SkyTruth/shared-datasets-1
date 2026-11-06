#!/usr/bin/env python3
"""Read operational inputs; wake existing protected workers only for real work."""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

WORKFLOW = ".github/workflows/operational-safety-check.yml"
JOB = "Check for operational work"
HEALTH_SCOPE = "health:operational-safety-check"

from scripts import incident_verification_cache as native
from scripts.ingestion_targets import TARGETS, DEPLOY_TARGETS
from scripts.ci_contract import INGESTION_ROLLOUT_HOLDS, rollout_holds as validate_suspensions
from scripts.dataset_mutation_authorization import GitHub
from scripts import workflow_failure_alert as alerts
from scripts.workflow_failure_alert import API_ROOT, REPOSITORY, source_inventory
from scripts.catalog_refresh_check import catalog_changed

ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "catalog/shared-datasets-catalog.csv").open() as stream:
    CATALOG = {row["asset_slug"]: row for row in csv.DictReader(stream)}
CRON_ASSETS = {job: tuple(slug for slug in CATALOG if slug == config["asset_slug"] or
               (not config["asset_slug"] and slug.startswith(config["selection"] + "-")))
               for job, config in TARGETS.items()} | {"dataset-usage": ()}
FRESHNESS_DAYS = {"daily": 2, "monthly": 31 + 7}
UNCHANGED_REASONS = {"source fingerprint unchanged", "monthly source already published",
                     "latest available source already published"}


class ReconciliationWrite(RuntimeError):
    """Reconciliation requires the signed worker."""


def utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def require(value, message):
    if not value:
        raise RuntimeError("OPERATIONAL_SAFETY: " + message)


def validate_safety_dispatch(api, event, environment=os.environ, *, workflow=".github/workflows/catalog-web-deploy.yml"):
    current = alerts.context(api, environment, workflow=workflow)
    require(current["event"] == "workflow_dispatch", "refresh admission requires dispatch")
    inputs = event.get("inputs", {})
    values = [str(inputs.get(key, "")) for key in ("safety_run_id", "safety_run_attempt")]
    require(all(re.fullmatch(r"[1-9][0-9]*", value) for value in values), "missing safety producer attempt")
    identifier, attempt = map(int, values)
    producer = api.get(f"{API_ROOT}/actions/runs/{identifier}")
    definition = api.get(f"{API_ROOT}/actions/workflows/{WORKFLOW.rsplit('/', 1)[-1]}")
    alerts.source_identity(api, producer, WORKFLOW, definition)
    require(producer.get("id") == identifier and producer.get("run_attempt") == attempt
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


def catalog_inventory(client):
    from scripts import catalog_drift_guard as catalog, collect_release_indexes as indexes
    # Collection validates index names, generations, sizes and payloads once.
    result = {row.name: str(row.generation) for row in client.list_blobs(catalog.DEFAULT_BUCKET, prefix=indexes.PREFIX)}
    for name in (catalog.REMOTE_CSV_OBJECT, catalog.REMOTE_WEB_CATALOG_OBJECT):
        blob = client.bucket(catalog.DEFAULT_BUCKET).blob(name)
        blob.reload()  # Missing objects and failed reads remain failures.
        require(re.fullmatch(r"[1-9][0-9]*", str(blob.generation)), "catalog object lacks an immutable generation")
        result[name] = str(blob.generation)
    return result


def dry_reconciliation(api, run, identity, source_runs, start, end, directory):
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
            raise ReconciliationWrite("normal reconciliation must persist this transition")

    ledger = DryLedger(api, run, directory)
    states = ledger.load()
    ledger.load = lambda: copy.deepcopy(states)
    safety_scopes = {"workflow:" + WORKFLOW.rsplit("/", 1)[-1],
                     incidents.scope_for_job(JOB, {}, WORKFLOW.rsplit("/", 1)[-1])[0]}
    safety_open = any(scope in safety_scopes and state["incident"]["resolution"] is None for scope, state in states.items())
    observations = [item for source in source_runs if source.get("status") == "completed"
        and (source.get("path") != WORKFLOW or source.get("conclusion") != "success" or safety_open)
        for item in incidents.source_observations(api, {"repository": {"full_name": REPOSITORY}, "workflow_run": source})
        if start <= incidents.instant(item["time"]) <= end]
    try:
        settled = not incidents.prepare(ledger, identity, observations)
    except ReconciliationWrite:
        settled = False
    return settled, claims


def verification_due(api, sources, pending, *, now):
    from scripts.deployment_revision import execution_name
    checks = [item for item in sources if item.get("path") == ".github/workflows/deployment-verification.yml"]
    if any(item.get("status") != "completed" for item in checks):
        return False
    latest = max(checks, key=lambda item: item["id"], default=None)
    if not latest or latest["conclusion"] != "success":
        return True
    # Live hints wake the observer; signed exact-image proof still gates completion.
    statuses = [api.pages(f"{API_ROOT}/deployments/{identifier}/statuses?per_page=100")[0] for identifier in pending]
    for status in statuses:
        execution = json.loads(subprocess.check_output(["gcloud", "run", "jobs", "executions", "describe", execution_name(status["environment_url"]),
            "--region=us-central1", "--project=shared-datasets-1", "--format=json(status)"], text=True))
        if any(execution.get("status", {}).get(key) for key in ("completionTime", "failedCount", "cancelledCount")):
            return True
    return now - utc(latest["created_at"]) >= timedelta(hours=3) or any(utc(status["created_at"]) > utc(latest["created_at"]) for status in statuses)


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
    changed = catalog_changed(client, directory)
    settled, claims = dry_reconciliation(api, run, identity, all_sources, start, end, directory) if identity else (True, [])
    (directory / "claims.json").write_text(json.dumps(claims))
    return {"catalog": changed or catalog_inventory(client) != generations, "reconcile": not settled,
            "verify": bool(pending) and verification_due(api, all_sources, pending, now=end)}


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
    problems = []
    for filename in ("bucket-hygiene-audit.yml", "scratch-cleanup-audit.yml"):
        query = urlencode({"branch": "main", "status": "success", "per_page": 100,
                           "created": ">=" + (now - timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")})
        runs = api.pages(f"{API_ROOT}/actions/workflows/{filename}/runs?{query}", field="workflow_runs")
        if not any(run.get("path") == ".github/workflows/" + filename and run.get("conclusion") == "success"
                   and alerts.verify_source_event(api, run)
                   and timedelta(0) <= now - utc(run["updated_at"]) < timedelta(days=9)
                   for run in runs):
            problems.append(f"{filename}: no successful main completion within 9 days")
    return problems


def recovery_identity(api):
    from scripts import deployment_revision as deployments
    target = "terraform-" + hashlib.sha256(b"PR plan identity provisioning").hexdigest()[:16]
    records = api.pages(f"{API_ROOT}/deployments?environment=production-{target}&per_page=100")
    require(records, "Recovery identity readiness unproven: complete the protected manual PR plan identity provisioning scope")
    record = max(records, key=lambda row: row["id"])
    require(deployments.verify_record(api, REPOSITORY, record)["target"] == target, "recovery provisioning target mismatch")
    statuses = deployments.verified_statuses(api, REPOSITORY, record)
    require(statuses and (statuses[0].get("state"), statuses[0].get("description")) == ("success", "applied"),
            "Recovery identity provisioning has no signed successful apply")
    runs = api.pages(f"{API_ROOT}/actions/workflows/deployment-recovery.yml/runs?branch=main&status=completed&per_page=1", field="workflow_runs")
    if runs and alerts.verify_source_event(api, runs[0]):
        require(not any(step.get("name") == "Authenticate read-only recovery" and step.get("conclusion") == "failure"
            for job in api.pages(f"{API_ROOT}/actions/runs/{runs[0]['id']}/attempts/{runs[0]['run_attempt']}/jobs?per_page=100", field="jobs")
            for step in job.get("steps", [])), "Recovery authentication failed: " + deployments.emissions.invocation(REPOSITORY, runs[0]['id'], runs[0]['run_attempt']))
    return []


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
    run = alerts.context(api, workflow=WORKFLOW)
    if args.health:
        now = datetime.now(timezone.utc)
        def scheduler(job):
            return json.loads(subprocess.check_output(["gcloud", "scheduler", "jobs", "describe", job,
                "--location=us-central1", "--project=shared-datasets-1", "--format=json"], text=True))
        configuration = incident_problems()
        warnings, problems = [], list(configuration)
        probe_outcome = os.environ.get("SAFETY_PROBE_OUTCOME", "success")
        def dataset_freshness():
            from scripts import catalog_drift_guard as catalog
            return freshness(catalog.get_client(), now=now, scheduler=scheduler)

        def reconciliation_problems():
            stalled = json.loads((args.work_dir / "result.json").read_text())["reconcile"] and reconciliation_stalled(api, run)
            return (["reconcile=true for >=3 consecutive safety probes; inspect the batch worker"] if stalled else []) + [
                f"{claim['incident']}: unchanged unconfirmed Slack claim >=24 h; owner reconciliation required"
                for claim in (json.loads((args.work_dir / "claims.json").read_text()) if not configuration else [])
                if now - native.timestamp(claim["since"]) >= timedelta(hours=24)]

        readers = [
            ("policy_warnings", lambda: policy_warnings(now)),
            ("audit_freshness", lambda: ([], audit_freshness(api, now=now))),
            ("deployed_image_lag", lambda: ([], deployed_image_lag(api, now=now))),
            ("freshness", lambda: ([], dataset_freshness())),
            ("recovery_identity", lambda: ([], recovery_identity(api))),
        ]
        if probe_outcome == "success":
            readers += [("reconciliation", lambda: ([], reconciliation_problems())),
                        ("heartbeat_series", lambda: (heartbeat_series(now), []))]
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
    identity = None if incident_problems() else incidents.Slack(os.environ["SHARED_DATASETS_SLACK_BOT_TOKEN"],
                               os.environ.get("SHARED_DATASETS_SLACK_CHANNEL_ID", "")).identity()
    result = probe(api, run, identity, os.environ.get("SHARED_DATASETS_SLACK_INCIDENTS_SINCE", ""), catalog.get_client(), args.work_dir)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.writelines(f"{key}={str(value).lower()}\n" for key, value in result.items())
    (args.work_dir / "result.json").write_text(json.dumps(result))
    print("OPERATIONAL_SAFETY: " + json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
