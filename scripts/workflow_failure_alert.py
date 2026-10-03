#!/usr/bin/env python3
"""Notify only for failed unattended GitHub runs; never execute their artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.slack_notify import notify

REPOSITORY = "SkyTruth/shared-datasets-1"
FAILED_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required", "startup_failure", "stale"}


def alert_for_run(event: dict) -> dict | None:
    run = event["workflow_run"]
    # The workflow subscribes only to maintenance and automatic deployment
    # workflows. PRs, pushes, and manual dispatches remain in GitHub.
    if (
        run["event"] not in {"schedule", "workflow_run"}
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
    name = str(run["name"]).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return {
        "title": "Unattended GitHub workflow failed",
        "body": f"{name} finished with {run['conclusion']}.\n"
                f"Trigger: {run['event']}.\n"
                f"<https://github.com/{REPOSITORY}/actions/runs/{run_id}|Open workflow run>",
        "status": "error",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-path", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    alert = alert_for_run(json.loads(args.event_path.read_text()))
    if alert is None:
        print("No unattended workflow failure to announce.")
    elif args.dry_run:
        print(json.dumps(alert, indent=2))
    else:
        notify(**alert, strict=True)


if __name__ == "__main__":
    main()
