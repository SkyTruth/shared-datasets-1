"""Require the exact merged executor's owner acceptance before credentials."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.dataset_mutation_authorization import (
    GitHub, effective_acceptance, repository_context, require, sha, validate_pr,
)


def authorize(api, repository, executor):
    executor = sha(executor)
    context = repository_context({"repository": api.get(f"repos/{repository}")})
    require(context["full_name"] == repository, "repository mismatch")
    candidates = api.pages(f"repos/{repository}/commits/{executor}/pulls?per_page=100")
    merged = [pr for pr in candidates if pr.get("merged_at") and pr.get("merge_commit_sha") == executor]
    require(len(merged) == 1, "executor must identify exactly one merged PR")
    number = merged[0]["number"]
    pr = api.get(f"repos/{repository}/pulls/{number}")
    validate_pr(pr, context, number, merged=True)
    require(pr["merge_commit_sha"] == executor, "merged PR executor mismatch")
    reviews = api.pages(f"repos/{repository}/pulls/{number}/reviews?per_page=100")
    acceptance = effective_acceptance(pr, reviews, repository)
    return {"executor_sha": executor, "pr_number": number, "acceptance": acceptance}


if __name__ == "__main__":
    print(json.dumps(authorize(GitHub(), os.environ["GITHUB_REPOSITORY"], os.environ["EXECUTOR_SHA"]), sort_keys=True))
