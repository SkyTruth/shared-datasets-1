#!/usr/bin/env python3
"""Repair a proven post-publication PR receipt without replaying data or Slack."""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import dataset_mutation_authorization as auth
from scripts.catalog_refresh_authorization import verify_completed_mutation

REPOSITORY = "SkyTruth/shared-datasets-1"
REPAIR_STEP = "Repair verified publication receipt"


def receipt_from_log(log: str, pr_number: int, asset_slug: str) -> str:
    """Accept only the original publisher's exact failed comment command.

    Reaching post_pr_comment proves the Slack subprocess returned; its known
    best-effort failure diagnostic must also be absent. No send is repeated.
    """
    auth.require("Slack notification skipped:" not in log, "original Slack delivery failed; receipt-only recovery is ineligible")
    auth.require("gh: Resource not accessible by integration (HTTP 403)" in log
                 and "post_pr_comment(pr_number, body)" in log, "failure was not the known PR receipt permission boundary")
    commands = re.findall(r"subprocess\.CalledProcessError: Command '(.+)' returned non-zero exit status 1\.", log)
    auth.require(len(commands) == 1, "missing or ambiguous failed receipt command")
    command = ast.literal_eval(commands[0])
    expected = ["gh", "api", f"repos/{REPOSITORY}/issues/{pr_number}/comments", "-f"]
    auth.require(isinstance(command, list) and len(command) == 5 and command[:4] == expected
                 and isinstance(command[4], str), "failed command is not this PR's receipt")
    body = command[4].removeprefix("body=")
    pattern = (rf"<!-- shared-datasets-breaking-alert:live:publish:{re.escape(asset_slug)}:[0-9a-f]{{16}} -->\n"
               rf"Live breaking change Slack alert sent for `{re.escape(asset_slug)}` \(publish\)\.")
    auth.require(re.fullmatch(pattern, body), "failed receipt does not match the immutable publication")
    return body


def verify_source(api, pr_number: int, run_id: int, attempt: int) -> tuple[dict, dict, str]:
    repository = auth.repository_context({"repository": api.get(f"repos/{REPOSITORY}")})
    latest = api.get(f"repos/{REPOSITORY}/actions/runs/{auth.positive_int(run_id)}")
    auth.require(latest.get("run_attempt") == auth.positive_int(attempt), "source attempt was superseded")
    run = auth.workflow_run(api, repository, run_id, attempt, success=False, path=latest["path"])
    auth.require(run.get("status") == "completed" and run.get("conclusion") == "failure", "source must be a completed failed attempt")
    auth.require(run["event"] in {"push", "workflow_dispatch"}, "source must be a trusted main publication")
    envelope = auth.run_authorization(api, repository, run, pr_number=pr_number)
    auth.require(envelope["outcome"] == "mutation" and set(envelope["document"]) >= {"publish"}
                 and "delete" not in envelope["document"] and "identity_reset" not in envelope["document"]["publish"],
                 "receipt recovery supports completed publication only")
    verify_completed_mutation(api, envelope, run["head_sha"], pr_number)
    jobs = api.pages(f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", field="jobs")
    leaf = f"Apply approved PR mutation plans (PR #{pr_number})"
    matches = [job for job in jobs if job["name"] in {leaf, f"Publish PR #{pr_number} / {leaf}"}]
    auth.require(len(matches) == 1, "missing or ambiguous original publication job")
    job = matches[0]
    auth.require(job["status"] == "completed" and job["conclusion"] == "failure", "publication job did not fail")
    steps = {step["name"]: step for step in job["steps"]}
    required = ("Promote approved staged objects", "Finalize promoted release metadata", "Rebuild promoted release index", "Record completed publication")
    auth.require(all(steps.get(name, {}).get("conclusion") == "success" for name in required), "publication did not reach its completed boundary")
    failed = [step["name"] for step in job["steps"] if step.get("conclusion") not in {"success", "skipped"}]
    auth.require(failed == ["Send breaking change alert"], "failure extends beyond the PR receipt")
    log = subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/actions/jobs/{job['id']}/logs"], text=True)
    body = receipt_from_log(log, pr_number, envelope["document"]["publish"]["asset_slug"])
    return run, job, body


def repair(api, pr_number: int, run_id: int, attempt: int) -> dict:
    run, job, body = verify_source(api, pr_number, run_id, attempt)
    marker = body.splitlines()[0]
    comments = api.pages(f"repos/{REPOSITORY}/issues/{pr_number}/comments?per_page=100")
    matches = [comment for comment in comments if marker in str(comment.get("body", ""))
               and comment.get("user", {}).get("login") in {auth.REVIEWER, "github-actions[bot]"}]
    auth.require(len(matches) <= 1, "ambiguous existing publication receipts")
    if matches:
        comment = matches[0]
    else:
        source = f"https://github.com/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}"
        body += f"\n\nReceipt recovered from the [completed publication]({source}); data and Slack delivery were not replayed."
        raw = subprocess.check_output(["gh", "api", f"repos/{REPOSITORY}/issues/{pr_number}/comments", "-f", f"body={body}"], text=True)
        comment = json.loads(raw)
        auth.require(comment.get("body") == body, "GitHub did not acknowledge the repaired receipt")
    auth.positive_int(comment.get("id"))
    return {"pr_number": pr_number, "source_run_id": run["id"], "source_attempt": attempt,
            "source_job_id": job["id"], "receipt_comment_id": comment["id"], "receipt_body": comment["body"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pr-number", type=int, required=True)
    parser.add_argument("--source-run-id", type=int, required=True)
    parser.add_argument("--source-attempt", type=int, required=True)
    args = parser.parse_args()
    auth.require_production_context(dict(os.environ))
    auth.require(os.environ["GITHUB_EVENT_NAME"] == "workflow_dispatch" and os.environ["GITHUB_ACTOR"] == auth.REVIEWER,
                 "receipt recovery requires the repository owner on trusted main")
    auth.require(os.environ["GITHUB_SHA"] == os.environ["GITHUB_WORKFLOW_SHA"]
                 == subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(), "receipt recovery executor mismatch")
    api = auth.GitHub()
    # Capture proves current main CI, exact PR acceptance and immutable plan bytes.
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    envelope = auth.capture(api, event, dict(os.environ))
    auth.require(envelope["pr_number"] == args.pr_number, "recovery PR differs from dispatch")
    print(json.dumps(repair(api, args.pr_number, args.source_run_id, args.source_attempt), sort_keys=True))


if __name__ == "__main__":
    main()
