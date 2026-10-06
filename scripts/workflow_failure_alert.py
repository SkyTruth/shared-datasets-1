#!/usr/bin/env python3
"""Notify only for failed unattended GitHub runs; never execute their artifacts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.slack_notify import notify
from scripts.dataset_mutation_authorization import GitHub

REPOSITORY = "SkyTruth/shared-datasets-1"
FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
PUBLICATION_JOBS = ("Apply approved PR mutation plans", "Install reviewed feature-ID reset",
                    "Detect reviewed PR mutation plans", "Build and publish catalog web",
                    "Build image and apply", "Prepare publication image and apply",
                    "Apply PMTiles CDN route sync", "Apply Scheduled ingestion deploy IAM sync",
                    "Apply Translation notice secret IAM bootstrap")
VALIDATION_JOBS = {"lint", "tests", "geospatial-changes", "geospatial-integration", "production-images", "browser", "ci-ready",
                   "sdk-validation (Node 22)", "sdk-validation (Node 24)"}
OBSERVER_WORKFLOWS = {"Deployment terminal verification", "Deployment read-only reconciliation"}
WORKFLOW_PATHS = {
    "CI": "ci.yml", "Bucket hygiene audit": "bucket-hygiene-audit.yml",
    "Scratch cleanup audit": "scratch-cleanup-audit.yml", "Catalog web deploy": "catalog-web-deploy.yml",
    "Catalog viewer deploy": "catalog-viewer-deploy.yml", "PMTiles CDN sync": "pmtiles-cdn-sync.yml",
    "Publish TypeScript SDK": "publish-typescript-sdk.yml",
    "Deployment terminal verification": "deployment-verification.yml",
    "Deployment read-only reconciliation": "deployment-recovery.yml",
    "EAMLIS monthly deploy": "eamlis-monthly-deploy.yml",
    "WDPA monthly deploy": "wdpa-monthly-deploy.yml",
    "Sea ice daily deploy": "sea-ice-daily-deploy.yml",
    "WDPA isolated processing validation deploy": "wdpa-processing-validation-deploy.yml",
    "Artifact Registry IAM sync": "artifact-registry-iam-sync.yml",
    "Scheduled ingestion deploy IAM sync": "scheduled-ingestion-deploy-iam-sync.yml",
    "Preview Terraform IAM sync": "preview-terraform-iam-sync.yml",
    "Scratch cleanup IAM sync": "scratch-cleanup-iam-sync.yml",
    "Cron alert policy sync": "cron-alert-policy-sync.yml",
    "Approved dataset mutation": "publish-dataset.yml",
}


def failed_ci_delivery_jobs(jobs: list[dict]) -> list[dict]:
    ready = any(job.get("name") == "ci-ready" and job.get("status") == "completed"
                and job.get("conclusion") == "success" for job in jobs)
    return [job for job in jobs
            if job["conclusion"] in FAILED_CONCLUSIONS and job["status"] == "completed"
            and (job["name"].rsplit(" / ", 1)[-1].startswith(PUBLICATION_JOBS)
                 or (ready and job["name"] not in VALIDATION_JOBS))]


def alert_for_run(event: dict, *, jobs: list[dict] | None = None) -> dict | None:
    run = event["workflow_run"]
    # Maintenance and automatic follow-ups are unattended. CI's main-push
    # publication jobs share that coverage; other supervised checks stay in GitHub.
    if (
        not (run["event"] in {"schedule", "workflow_run"}
             or (run["event"] == "push" and run["name"] == "CI")
             or (run["event"] == "workflow_dispatch" and run["name"] in OBSERVER_WORKFLOWS))
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
    if run["name"] == "CI":
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


def ci_jobs_for_event(api: GitHub, run: dict) -> list[dict]:
    if not verify_source_event(api, run):
        return []
    return api.pages(
        f"repos/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100",
        field="jobs",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    event = json.loads(args.event_path.read_text())
    run = event["workflow_run"]
    jobs = None
    alert = alert_for_run(event)
    # CI needs job evidence before its pure routing decision can produce an alert.
    eligible_ci = (run["name"] == "CI" and run["event"] == "push"
                   and run["conclusion"] in FAILED_CONCLUSIONS and run["status"] == "completed"
                   and run["head_branch"] == "main" and run["head_repository"]["full_name"] == REPOSITORY
                   and event["repository"]["full_name"] == REPOSITORY)
    if eligible_ci:
        jobs = ci_jobs_for_event(GitHub(), run)
        alert = alert_for_run(event, jobs=jobs)
    elif alert is not None and not verify_source_event(GitHub(), run):
        alert = None
    if alert is None:
        print("No unattended workflow failure to announce.")
    elif args.dry_run:
        print(json.dumps(alert, indent=2))
    else:
        notify(**alert, strict=True)


if __name__ == "__main__":
    main()
