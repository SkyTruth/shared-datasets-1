#!/usr/bin/env python3
"""Classify trusted unattended runs and close owner-identified legacy parents."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.slack_notify import build_slack_payload
from scripts.dataset_mutation_authorization import GitHub
from scripts.workflow_identity import WORKFLOW_NAMES
from scripts.deployment_revision import TARGET_WORKFLOWS, TERRAFORM_WORKFLOWS

REPOSITORY = "SkyTruth/shared-datasets-1"
FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
PUBLICATION_JOBS = ("Apply approved PR mutation plans", "Install reviewed feature-ID reset",
                    "Detect reviewed PR mutation plans", "Build and publish catalog web",
                    "Build image and apply", "Prepare publication image and apply",
                    "Apply PMTiles CDN route sync", "Apply Scheduled ingestion deploy IAM sync",
                    "Apply Translation notice secret IAM bootstrap")
VALIDATION_JOBS = {"lint", "tests", "geospatial-changes", "geospatial-integration", "production-images", "browser", "ci-ready",
                   "sdk-validation (Node 22)", "sdk-validation (Node 24)"}
MONITORED_WORKFLOWS = set((TARGET_WORKFLOWS | TERRAFORM_WORKFLOWS).values()) - {"dataset-usage-deploy.yml"} | {
    "bucket-hygiene-audit.yml", "ci.yml", "deployment-recovery.yml", "deployment-verification.yml",
    "operational-safety-check.yml", "unattended-workflow-alert.yml", "publish-dataset.yml", "scratch-cleanup-audit.yml",
}
WORKFLOW_PATHS = {name: filename for filename in MONITORED_WORKFLOWS for name in WORKFLOW_NAMES[filename]}
OBSERVER_WORKFLOWS = {name for filename in ("deployment-verification.yml", "deployment-recovery.yml") for name in WORKFLOW_NAMES[filename]}
UNATTENDED_DISPATCH_WORKFLOWS = OBSERVER_WORKFLOWS | {
    name for path in {"operational-safety-check.yml", "catalog-web-deploy.yml", "unattended-workflow-alert.yml", "catalog-viewer-deploy.yml"} for name in WORKFLOW_NAMES[path]
}


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
    # A retained health report owns covered probe failures. Dispatch, setup,
    # notification and upload failures still need infrastructure incident routing.
    for job in jobs:
        steps = job.get("steps", [])
        failed = {step["name"] for step in steps if step.get("conclusion") in FAILED_CONCLUSIONS}
        if (failed and failed <= {"Check operational work", "Check freshness and stalled operations"}
                and any(step.get("name") == "Retain safety health report" and step.get("conclusion") == "success" for step in steps)
                and ("Check operational work" not in failed or any(step.get("name") == "Check freshness and stalled operations"
                     and step.get("conclusion") == "success" for step in steps))):
            return True
    return False


def alert_for_run(event: dict, *, jobs: list[dict] | None = None) -> dict | None:
    run = event["workflow_run"]
    # Maintenance and automatic follow-ups are unattended. CI's main-push
    # publication jobs share that coverage; other supervised checks stay in GitHub.
    if (
        not (run["event"] in {"schedule", "workflow_run"}
             or (run["event"] == "push" and run["name"] in WORKFLOW_NAMES["ci.yml"])
             or (run["event"] == "workflow_dispatch" and run["name"] in UNATTENDED_DISPATCH_WORKFLOWS))
        or run["conclusion"] not in FAILED_CONCLUSIONS
        or run["status"] != "completed"
        or run["head_branch"] != "main"
        or run["head_repository"]["full_name"] != REPOSITORY
        or event["repository"]["full_name"] != REPOSITORY
    ):
        return None
    run_id = run["id"]
    if type(run_id) is not int or run_id <= 0:
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
                f"<https://github.com/{REPOSITORY}/actions/runs/{run_id}|Open workflow run>",
        "status": "error",
    }


def verify_source_event(api: GitHub, run: dict) -> bool:
    """Verify every source identity before notification; obsolete attempts are no-ops."""
    run_id = run["id"]
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("workflow run ID must be a positive integer")
    if type(run.get("run_attempt")) is not int or run["run_attempt"] <= 0 or not re.fullmatch(r"[0-9a-f]{40}", str(run.get("head_sha"))):
        raise ValueError("invalid source attempt or revision")
    if run.get("name") not in WORKFLOW_PATHS:
        raise ValueError("unknown unattended workflow identity")
    authoritative = api.get(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    workflow = api.get(f"repos/{REPOSITORY}/actions/workflows/{authoritative['workflow_id']}")
    repository = api.get(f"repos/{REPOSITORY}")
    if (workflow.get("id") != authoritative["workflow_id"]
            or workflow.get("path") != ".github/workflows/" + WORKFLOW_PATHS[run["name"]]
            or authoritative.get("path") != workflow.get("path")
            or authoritative.get("id") != run_id
            or authoritative.get("name") != run["name"]
            or authoritative.get("head_sha") != run["head_sha"]
            or authoritative.get("event") != run["event"]
            or authoritative.get("head_branch") != "main"
            or any(authoritative.get(key, {}).get("id") != repository.get("id")
                   or authoritative.get(key, {}).get("full_name") != REPOSITORY
                   for key in ("repository", "head_repository"))
            or repository.get("full_name") != REPOSITORY
            or type(repository.get("id")) is not int or repository["id"] <= 0):
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
