#!/usr/bin/env python3
"""Require a completed mutation job before allocating catalog write credentials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dataset_mutation_authorization import GitHub, require


def verify_completed_mutation(api: GitHub, envelope: dict, executor_sha: str, pr_number: int) -> None:
    """Consume an already verified envelope and independently prove job success."""
    require(envelope["trusted_executor_sha"] == executor_sha, "catalog executor differs from authorization")
    require(envelope["pr_number"] == pr_number, "catalog PR differs from authorization")
    require(envelope["outcome"] == "mutation", "catalog refresh requires a mutation")
    source = envelope["source_run"]
    repo = envelope["repository"]["full_name"]
    jobs = api.pages(
        f"repos/{repo}/actions/runs/{source['id']}/attempts/{source['run_attempt']}/jobs?per_page=100",
        field="jobs",
    )
    leaf = f"Apply approved PR mutation plans (PR #{pr_number})"
    matches = [job for job in jobs if job["name"] == leaf or job["name"].endswith(" / " + leaf)]
    require(len(matches) == 1, "missing or ambiguous authorized mutation job")
    require(matches[0]["status"] == "completed" and matches[0]["conclusion"] == "success",
            "authorized mutation job has not completed successfully")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--executor-sha", required=True)
    parser.add_argument("--pr-number", type=int, required=True)
    args = parser.parse_args()
    verify_completed_mutation(GitHub(), json.loads(args.authorization.read_text()), args.executor_sha, args.pr_number)


if __name__ == "__main__":
    main()
