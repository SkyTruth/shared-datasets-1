#!/usr/bin/env python3
"""Notify only for failed unattended GitHub runs; never execute their artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.slack_notify import notify
from scripts.dataset_mutation_authorization import GitHub

REPOSITORY = "SkyTruth/shared-datasets-1"
FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}
PUBLICATION_JOBS = ("Apply approved PR mutation plans", "Install reviewed feature-ID reset",
                    "Detect reviewed PR mutation plans", "Build and publish catalog web")


def alert_for_run(event: dict, *, jobs: list[dict] | None = None) -> dict | None:
    run = event["workflow_run"]
    # Maintenance and automatic follow-ups are unattended. CI's main-push
    # publication jobs share that coverage; other supervised checks stay in GitHub.
    if (
        not (run["event"] in {"schedule", "workflow_run"}
             or (run["event"] == "push" and run["name"] == "CI"))
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
        failed_delivery = [job for job in jobs or []
            if job["conclusion"] in FAILED_CONCLUSIONS
            and job["status"] == "completed"
            and job["name"].rsplit(" / ", 1)[-1].startswith(PUBLICATION_JOBS)]
        if not failed_delivery:
            return None
    name = str(run["name"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return {
        "title": "Unattended GitHub workflow failed",
        "body": f"{name} finished with {run['conclusion']}.\n"
                f"Trigger: {run['event']}.\n"
                f"<https://github.com/{REPOSITORY}/actions/runs/{run_id}|Open workflow run>",
        "status": "error",
    }


def ci_jobs_for_event(api: GitHub, run: dict) -> list[dict]:
    """Verify identity first; completed events superseded by reruns are no-ops."""
    run_id = run["id"]
    if type(run_id) is not int or run_id <= 0:
        raise ValueError("workflow run ID must be a positive integer")
    authoritative = api.get(f"repos/{REPOSITORY}/actions/runs/{run_id}")
    workflow = api.get(f"repos/{REPOSITORY}/actions/workflows/{authoritative['workflow_id']}")
    if (workflow["path"] != ".github/workflows/ci.yml"
            or authoritative["head_sha"] != run["head_sha"]
            or authoritative["event"] != "push" or authoritative["head_branch"] != "main"
            or authoritative["head_repository"]["full_name"] != REPOSITORY):
        raise ValueError("CI failure event does not identify a trusted main-push run")
    if authoritative["run_attempt"] > run["run_attempt"]:
        return []
    if (authoritative["run_attempt"] != run["run_attempt"]
            or authoritative["status"] != "completed"
            or authoritative["conclusion"] != run["conclusion"]):
        raise ValueError("CI failure event does not identify the completed source attempt")
    return api.pages(
        f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{run['run_attempt']}/jobs?per_page=100",
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
    if (run["name"] == "CI" and run["event"] == "push"
            and run["conclusion"] in FAILED_CONCLUSIONS
            and run["head_branch"] == "main"
            and run["head_repository"]["full_name"] == REPOSITORY
            and event["repository"]["full_name"] == REPOSITORY):
        jobs = ci_jobs_for_event(GitHub(), run)
    alert = alert_for_run(event, jobs=jobs)
    if alert is None:
        print("No unattended workflow failure to announce.")
    elif args.dry_run:
        print(json.dumps(alert, indent=2))
    else:
        notify(**alert, strict=True)


if __name__ == "__main__":
    main()
