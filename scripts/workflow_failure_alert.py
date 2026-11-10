#!/usr/bin/env python3
"""Classify trusted unattended runs and close owner-identified legacy parents."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import os
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.slack_notify import build_slack_payload
from scripts.dataset_mutation_authorization import GitHub
from scripts.workflow_identity import WORKFLOW_NAMES
from scripts import deployment_revision as deployments
from scripts.incident_health_artifacts import timestamp
from scripts.terraform_target_contracts import registry

REPOSITORY = "SkyTruth/shared-datasets-1"
API_ROOT = f"repos/{REPOSITORY}"
FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
# Historical producer names remain routable after their workflow jobs are retired.
PUBLICATION_JOBS = tuple(name for row in registry(section='workflows').values() for name in row.get('publication_jobs', []))
VALIDATION_JOBS = {"lint", "tests", "tests-control-plane", "geospatial-changes", "geospatial-integration", "production-images", "browser", "ci-ready",
                   "sdk-validation (Node 22)", "sdk-validation (Node 24)"}
WORKFLOW_EVENTS = {filename: set(row['events']) for filename, row in registry(section='workflows').items() if 'events' in row}
WORKFLOW_PATHS = {name: filename for filename in WORKFLOW_EVENTS for name in WORKFLOW_NAMES[filename]}


def unattended(run):
    return run["event"] in WORKFLOW_EVENTS.get(WORKFLOW_PATHS.get(run["name"]), set())


def failed_ci_delivery_jobs(jobs: list[dict]) -> list[dict]:
    ready = any(job.get("name") == "ci-ready" and job.get("status") == "completed"
                and job.get("conclusion") == "success" for job in jobs)
    return [job for job in jobs
            if job["conclusion"] in FAILED_CONCLUSIONS and job["status"] == "completed"
            and (job["name"].rsplit(" / ", 1)[-1].startswith(PUBLICATION_JOBS)
                 or (ready and job["name"] not in VALIDATION_JOBS))]


def catalog_probe_only(run: dict, jobs: list[dict]) -> bool:
    """Recognize an idle automatic probe from authoritative same-attempt jobs."""
    if run["name"] not in WORKFLOW_NAMES["catalog-web-deploy.yml"] or run["event"] not in {"schedule", "workflow_dispatch"}:
        return False
    probes = [job for job in jobs if job["name"] == "Check for catalog input changes"]
    publishers = [job for job in jobs if job["name"] == "Build and publish catalog web"]
    return (len(probes) == len(publishers) == 1
            and probes[0]["status"] == "completed"
            and publishers[0]["status"] == "completed" and publishers[0]["conclusion"] == "skipped"
            and all(job["name"] == probes[0]["name"] or job["conclusion"] not in FAILED_CONCLUSIONS for job in jobs))


def safety_health_only(run, jobs):
    if run["name"] not in WORKFLOW_NAMES["operational-safety-check.yml"]:
        return False
    # Retained reports own setup/probe findings; dispatch and delivery still alert.
    for job in jobs:
        steps = job.get("steps", [])
        probe = next((index for index, step in enumerate(steps) if step["name"] == "Check operational work"), 0)
        covered = {step["name"] for step in steps[:probe]} | {"Check operational work", "Check freshness and stalled operations"}
        failed = {step["name"] for step in steps if step.get("conclusion") in FAILED_CONCLUSIONS}
        if (failed and failed <= covered
                and any(step.get("name") == "Retain safety health report" and step.get("conclusion") == "success" for step in steps)
                and (failed == {"Check freshness and stalled operations"} or any(step.get("name") == "Check freshness and stalled operations"
                     and step.get("conclusion") == "success" for step in steps))):
            return True
    return False


def alert_for_run(event: dict, *, jobs: list[dict] | None = None) -> dict | None:
    run = event["workflow_run"]
    # Maintenance and automatic follow-ups are unattended. CI's main-push
    # publication jobs share that coverage; other supervised checks stay in GitHub.
    if (
        not unattended(run)
        or run["conclusion"] not in FAILED_CONCLUSIONS
        or run["status"] != "completed"
        or run["head_branch"] != "main"
        or run["head_repository"]["full_name"] != REPOSITORY
        or event["repository"]["full_name"] != REPOSITORY
    ):
        return None
    if type(run.get("id")) is not int or run["id"] <= 0:
        raise ValueError("workflow run ID must be a positive integer")
    if catalog_probe_only(run, jobs or []):
        return None
    if run["name"] in WORKFLOW_NAMES["ci.yml"]:
        # CI now owns reusable publication/catalog jobs. A validation failure
        # alone still belongs in GitHub; inspect only this exact run attempt.
        if not failed_ci_delivery_jobs(jobs or []):
            return None
    name = str(run["name"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return {
        "title": "Unattended GitHub workflow failed",
        "body": f"{name} finished with {run['conclusion']}.\n"
                f"Trigger: {run['event']}.\n"
                f"<https://github.com/{REPOSITORY}/actions/runs/{run['id']}|Open workflow run>",
        "status": "error",
    }


def context(api, environment=os.environ, *, workflow, events=("schedule", "workflow_dispatch")):
    deployments.require(environment.get("GITHUB_REPOSITORY") == REPOSITORY and environment.get("GITHUB_REF") == "refs/heads/main"
            and environment.get("GITHUB_WORKFLOW_REF") == REPOSITORY + "/" + workflow + "@refs/heads/main",
            "require exact trusted main workflow")
    identifier, attempt = int(environment["GITHUB_RUN_ID"]), int(environment["GITHUB_RUN_ATTEMPT"])
    run = api.get(f"{API_ROOT}/actions/runs/{identifier}/attempts/{attempt}")
    deployments.require(run.get("id") == identifier and run.get("run_attempt") == attempt
            and run["event"] in events
            and run["path"] == workflow, "invalid execution provenance")
    definition = api.get(f"{API_ROOT}/actions/workflows/{workflow.rsplit('/', 1)[-1]}")
    source_identity(api, run, workflow, definition)
    return run


def source_inventory(api, run, since, *, now=None):
    now = now or datetime.now(timezone.utc)
    # Three daily intervals cover a skipped probe plus one full interval of overlap.
    start = max(timestamp(since), now - timedelta(hours=72))
    query = urlencode({"branch": "main", "created": start.strftime("%Y-%m-%dT%H:%M:%SZ")
                       + ".." + now.strftime("%Y-%m-%dT%H:%M:%SZ"), "per_page": 100})
    rows = api.pages(f"{API_ROOT}/actions/runs?{query}", field="workflow_runs")
    return [item for item in rows if (run is None or item.get("id") != run["id"])
                and item.get("name") in WORKFLOW_PATHS and item.get("head_branch") == "main"
                and item.get("head_repository", {}).get("full_name") == REPOSITORY], start, now


def source_identity(api: GitHub, run: dict, path: str, workflow: dict) -> None:
    """Authenticate an API run against its declared main workflow path."""
    if type(run.get("id")) is not int or run["id"] <= 0:
        raise ValueError("workflow run ID must be a positive integer")
    if type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0 or not re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha"))):
        raise ValueError("invalid source attempt or revision")
    repository = api.get(f"repos/{REPOSITORY}")
    if (workflow.get("id") != run["workflow_id"]
            or workflow.get("path") != path or run.get("path") != path
            or run.get("head_branch") != "main"
            or any(run.get(key, {}).get("id") != repository.get("id")
                   or run.get(key, {}).get("full_name") != REPOSITORY
                   for key in ("repository", "head_repository"))
            or repository.get("full_name") != REPOSITORY
            or type(repository.get("id")) is not int or repository["id"] <= 0):
        raise ValueError("failure event does not identify a trusted main source workflow")


def verify_source_event(api: GitHub, run: dict) -> bool:
    """Verify every source identity before notification; obsolete attempts are no-ops."""
    if type(run.get("id")) is not int or run["id"] <= 0 or type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0:
        raise ValueError("invalid source run or attempt")
    if run.get("name") not in WORKFLOW_PATHS:
        raise ValueError("unknown unattended workflow identity")
    authoritative = api.get(f"repos/{REPOSITORY}/actions/runs/{run['id']}")
    workflow = api.get(f"repos/{REPOSITORY}/actions/workflows/{authoritative['workflow_id']}")
    source_identity(api, authoritative, ".github/workflows/" + WORKFLOW_PATHS[run["name"]], workflow)
    if any(authoritative.get(key) != run.get(key) for key in ("id", "name", "head_sha", "event")):
        raise ValueError("failure event does not identify a trusted main source workflow")
    if authoritative["run_attempt"] > run["run_attempt"]:
        return False
    if (authoritative["run_attempt"] != run["run_attempt"]
            or authoritative["status"] != "completed"
            or authoritative["conclusion"] != run["conclusion"]):
        raise ValueError("failure event does not identify the completed source attempt")
    return True


def jobs_for_event(api: GitHub, run: dict) -> list[dict] | None:
    if not verify_source_event(api, run):
        return None
    return api.pages(
        f"repos/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100",
        field="jobs",
    )


def resolve_legacy(api, slack, run_id, message_ts):
    """Update an owner-identified webhook parent after all failed targets recover."""
    from scripts import slack_incidents as incidents

    incidents.require(type(run_id) is int and run_id > 0, "invalid original run ID")
    run = api.get(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    incidents.require(run["id"] == run_id, "original run identity changed")
    jobs = jobs_for_event(api, run)
    event = {"repository": {"full_name": REPOSITORY}, "workflow_run": run}
    incidents.require(jobs is not None and alert_for_run(event, jobs=jobs) is not None,
                      "source is not a current unattended failure")
    targets = incidents.job_targets(revision=run["head_sha"], workflow=WORKFLOW_PATHS[run["name"]])
    failures = [incidents.from_job(run, job, targets) for job in jobs
                if job["conclusion"] in FAILED_CONCLUSIONS]
    incidents.require(failures, "source has no failed jobs")
    links = []
    for failure in failures:
        recovery = incidents.verified_recovery(api, [failure], ancestor=incidents.deployments.git_ancestor)
        incidents.require(recovery is not None, "failed target has no newer verified recovery")
        links.append(f"<{incidents.emissions.invocation(REPOSITORY, recovery['run_id'], recovery['attempt'])}|Verified {failure['label']} recovery>")
    payload = build_slack_payload(title="Unattended GitHub workflow resolved", status="success",
                                 body=f"<https://github.com/{REPOSITORY}/actions/runs/{run_id}|Original failed run>\n" + "\n".join(dict.fromkeys(links)))
    slack.update(message_ts, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolve-run-id", type=int, required=True)
    parser.add_argument("--message-ts", required=True)
    args = parser.parse_args()
    from scripts.slack_incident_api import Slack
    from scripts.deployment_revision import GitHub as DeploymentGitHub
    resolve_legacy(DeploymentGitHub(), Slack(os.environ.get("SHARED_DATASETS_SLACK_BOT_TOKEN", ""),
                                   os.environ.get("SHARED_DATASETS_SLACK_CHANNEL_ID", "")),
                   args.resolve_run_id, args.message_ts)


if __name__ == "__main__":
    main()
