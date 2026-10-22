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
from scripts.deployment_revision import DeploymentError, GitHub, require, verify_ci, verify_context


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
        if run.get("head_sha") != sha or run.get("status") != "completed":
            continue
        try:
            verify_ci(api, repository, sha, run["id"], run["run_attempt"])
        except DeploymentError:
            continue
        return run["id"], run["run_attempt"]
    raise DeploymentError("no complete main CI attempt with passing ci-ready exists for executor")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--executor-sha", required=True)
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    try:
        api = GitHub()
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        args.source_run_id, args.source_run_attempt = resolve(api, os.environ["GITHUB_REPOSITORY"], args.executor_sha, event, int(os.environ["GITHUB_RUN_ID"]), int(os.environ["GITHUB_RUN_ATTEMPT"]))
        verify_context(args, api)
        if not args.bootstrap:
            require(subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == args.executor_sha, "executor checkout changed")
        with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
            stream.write(f"source_run_id={args.source_run_id}\nsource_run_attempt={args.source_run_attempt}\n")
    except DeploymentError as error:
        parser.exit(1, f"DEPLOYMENT_NOT_READY: {error}\n")


if __name__ == "__main__":
    main()
