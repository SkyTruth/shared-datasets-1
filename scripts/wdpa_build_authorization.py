#!/usr/bin/env python3
"""Verify the exact-head PR approving an immutable owned WDPA build bundle."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import dataset_mutation_authorization as auth
from scripts import reviewed_dataset_plan as plans
from scripts import wdpa_processing_gate as gate

REPOSITORY = "SkyTruth/shared-datasets-1"


def verify(api, evidence, *, root=gate.ROOT):
    errors = gate.check(evidence)
    auth.require(not errors, "WDPA acceptance is incomplete: " + "; ".join(errors))
    number = evidence["promotion_pr"]
    repository = auth.repository_context({"repository": api.get(f"repos/{REPOSITORY}")})
    auth.require(
        repository["full_name"] == REPOSITORY, "unexpected promotion repository"
    )
    pr = api.get(f"repos/{REPOSITORY}/pulls/{number}")
    auth.validate_pr(pr, repository, number, merged=True)
    acceptance = auth.effective_acceptance(
        pr,
        api.pages(f"repos/{REPOSITORY}/pulls/{number}/reviews?per_page=100"),
        REPOSITORY,
    )
    files = api.pages(f"repos/{REPOSITORY}/pulls/{number}/files?per_page=100")
    auth.require(
        len(files) == pr["changed_files"] and len(files) < 3000,
        "incomplete PR file enumeration",
    )
    candidates = [
        item
        for item in files
        if item["filename"].startswith(plans.PLAN_DIRECTORY + "/")
        and item["filename"].endswith(".json")
    ]
    path = evidence["promotion_plan"]
    auth.require(
        len(candidates) == 1
        and candidates[0]["filename"] == path
        and candidates[0]["status"] == "added",
        "promotion PR must add exactly this immutable plan",
    )
    raw, blob = auth.git_file(api, REPOSITORY, pr["head"]["sha"], path)
    auth.require(blob == candidates[0]["sha"], "plan blob differs from PR head")
    auth.require(
        auth.git_file(api, REPOSITORY, pr["merge_commit_sha"], path) == (raw, blob),
        "plan differs between reviewed head and merge",
    )
    auth.require(
        raw == (root / path).read_bytes(),
        "current plan differs from the reviewed bytes",
    )
    document = plans.strict_json_loads(raw)
    auth.require(
        plans.extract_fenced_json(pr.get("body") or "", "shared-datasets-publish-plan")
        == document,
        "readable publish fence differs from the immutable WDPA plan",
    )
    subprocess.run(
        ["git", "merge-base", "--is-ancestor", pr["merge_commit_sha"], "HEAD"],
        cwd=root,
        check=True,
    )
    return acceptance


def main():
    evidence = json.loads(gate.EVIDENCE.read_text())
    print(json.dumps(verify(auth.GitHub(), evidence), sort_keys=True))


if __name__ == "__main__":
    main()
