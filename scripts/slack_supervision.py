#!/usr/bin/env python3
"""Request accountable supervision through the trusted main notification workflow."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.deployment_revision import GitHub
from scripts.operational_policy import REPOSITORY, WORKFLOW, validate_supervision_request


def request_from_args(args):
    operation_id = args.operation_id or "deployment:" + args.target
    request = {"command": args.command.replace("-", "_"),
               "binding": {"operation_id": operation_id, "target": args.target,
                           "revision": args.revision, "pr_number": args.pr},
               "responsible_human": args.human, "conversation_ref": args.conversation,
               "current_action": args.current_action, "question_ref": args.question_ref,
               "handoff_ref": args.handoff_ref}
    validate_supervision_request(request)
    return request


def wait_for_request(api, request_id, *, started, timeout=120, clock=time.monotonic, sleep=time.sleep):
    """Observe this unique request, never substitute another run's success."""
    deadline = clock() + timeout
    title = "Supervision " + request_id
    workflow = WORKFLOW.rsplit("/", 1)[-1]
    while True:
        rows = api.pages(f"repos/{REPOSITORY}/actions/workflows/{workflow}/runs?event=workflow_dispatch&branch=main&created=%3E%3D{started}&per_page=100", "workflow_runs")
        matches = [row for row in rows if row.get("display_title") == title]
        if len(matches) > 1:
            raise ValueError("Supervision request has ambiguous workflow runs; inspect before proceeding")
        if matches:
            run = matches[0]
            if (run.get("event") != "workflow_dispatch" or run.get("head_branch") != "main"
                    or run.get("path") != WORKFLOW or run.get("head_repository", {}).get("full_name") != REPOSITORY):
                raise ValueError("Supervision request run provenance does not match trusted main")
            if run.get("status") == "completed":
                if run.get("conclusion") != "success":
                    raise ValueError(f"Supervision registration was not verified: {run.get('html_url', '')}")
                jobs = api.pages(f"repos/{REPOSITORY}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100", "jobs")
                workers = [job for job in jobs if job.get("name") == "Reconcile Slack incident status"]
                if (len(workers) != 1 or workers[0].get("status") != "completed" or workers[0].get("conclusion") != "success"
                        or not any(step.get("name") == "Verify durable incident checkpoints" and step.get("conclusion") == "success"
                                   for step in workers[0].get("steps", []))):
                    raise ValueError("Supervision request lacks a successful signed checkpoint verification; a skipped worker is not registration")
                return run["html_url"]
        if clock() >= deadline:
            raise TimeoutError("Supervision request is still pending; verify its exact workflow run before overseeing deployment")
        sleep(min(5, max(0, deadline - clock())))


def dispatch(request, *, wait=True, timeout=120, runner=subprocess.run, api=None):
    validate_supervision_request(request)
    request_id = str(uuid.uuid4())
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inputs = {"action": "supervision", "delivery_result": "none", "supervision_request_id": request_id,
              "supervision_request": json.dumps(request, separators=(",", ":"), sort_keys=True)}
    # Inputs are stdin JSON. Chat/question references never enter shell code,
    # command-line process listings, or an interpolated workflow run script.
    runner(["gh", "workflow", "run", WORKFLOW.rsplit("/", 1)[-1], "--repo", REPOSITORY,
            "--ref", "main", "--json"], input=json.dumps(inputs), text=True, check=True)
    if not wait:
        return {"request_id": request_id, "verified": False}
    url = wait_for_request(api or GitHub(), request_id, started=started, timeout=timeout)
    return {"request_id": request_id, "verified": True, "workflow_url": url}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["claim", "check-in", "clarification", "release", "supersede"])
    parser.add_argument("--target", required=True)
    parser.add_argument("--operation-id", help="Exact existing workflow/job/publication scope; defaults to deployment:TARGET")
    parser.add_argument("--revision", required=True, help="Exact tested revision, never a moving branch")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--human", required=True, help="Responsible human's authenticated GitHub login")
    parser.add_argument("--conversation", required=True, help="Originating conversation URL")
    parser.add_argument("--current-action", required=True)
    parser.add_argument("--question-ref")
    parser.add_argument("--handoff-ref")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-wait", action="store_true", help="Request only; does not prove registered ownership")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.timeout <= 600:
            raise ValueError("Supervision wait must be between 1 and 600 seconds")
        request = request_from_args(args)
        result = request if args.dry_run else dispatch(request, wait=not args.no_wait, timeout=args.timeout)
        print(json.dumps(result, indent=2, sort_keys=True))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"SUPERVISION_NOT_VERIFIED: {error}\n")


if __name__ == "__main__":
    main()
