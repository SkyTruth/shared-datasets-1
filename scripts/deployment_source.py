#!/usr/bin/env python3
"""Resolve a passing main CI attempt for an exact deployment revision."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.deployment_revision import DeploymentError, DeploymentPrerequisitePending, GitHub, require, verify_ci, verify_context


def resolve(api, repository, sha, event, current_id, current_attempt):
    if event.get("workflow_run", {}).get("name") == "CI":
        source = event["workflow_run"]
        verify_ci(api, repository, sha, source["id"], source["run_attempt"])
        return source["id"], source["run_attempt"]
    if event.get("after") == sha:
        verify_ci(api, repository, sha, current_id, current_attempt)
        return current_id, current_attempt
    # Manual mutation recovery and index-rebuild follow-ups retain their original
    # executor. Overall CI can be red because publication failed after ci-ready.
    runs = api.pages(f"repos/{repository}/actions/workflows/ci.yml/runs?head_sha={sha}&event=push&branch=main&per_page=100", "workflow_runs")
    for run in sorted(runs, key=lambda item: (item["id"], item["run_attempt"]), reverse=True):
        if run.get("head_sha") != sha:
            continue
        # ci-ready is the authority boundary. Publication can still be running
        # or can fail after this exact revision passed all required validation.
        # Never replace a pending/unsuccessful latest attempt with older evidence.
        verify_ci(api, repository, sha, run["id"], run["run_attempt"])
        return run["id"], run["run_attempt"]
    raise DeploymentError("no main CI attempt exists for the exact executor revision")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--executor-sha", required=True)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--defer-pending", action="store_true",
                        help="Defer a scheduled catalog probe's exact pending CI prerequisite before mutation")
    args = parser.parse_args()
    try:
        api = GitHub()
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        require(not args.defer_pending or (args.bootstrap and args.workflow == "catalog-web-deploy.yml"
                and os.environ.get("GITHUB_EVENT_NAME") == "schedule"),
                "only a scheduled catalog bootstrap may defer pending CI")
        try:
            args.source_run_id, args.source_run_attempt = resolve(api, os.environ["GITHUB_REPOSITORY"], args.executor_sha, event, int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"]))
        except DeploymentPrerequisitePending as pending:
            if not args.defer_pending:
                raise
            args.source_run_id, args.source_run_attempt = pending.run_id, pending.attempt
        # Even a deferral must verify the caller, checkout and main provenance.
        # This repeats the exact gate check after those context checks and can
        # proceed if the gate completed while the context was being verified.
        verify_context(args, api)
        if not args.bootstrap:
            require(subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == args.executor_sha, "executor checkout changed")
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"ready=true\nsource_run_id={args.source_run_id}\nsource_run_attempt={args.source_run_attempt}\n")
    except DeploymentPrerequisitePending as error:
        if not args.defer_pending:
            parser.exit(1, f"DEPLOYMENT_NOT_READY: {error}\n")
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write("ready=false\n")
        print(f"::notice::Catalog deployment deferred: {error}. The scheduled probe will reconsider this exact revision.")
    except DeploymentError as error:
        parser.exit(1, f"DEPLOYMENT_NOT_READY: {error}\n")


if __name__ == "__main__":
    main()
