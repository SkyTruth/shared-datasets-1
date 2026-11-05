"""Check owner acceptance before credentials."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts.dataset_mutation_authorization import GitHub, effective_acceptance, repository_context, require, sha, validate_pr


if __name__ == "__main__":
    api, repository = GitHub(), os.environ["GITHUB_REPOSITORY"]
    executor = sha(os.environ["EXECUTOR_SHA"])
    context = repository_context({"repository": api.get(f"repos/{repository}")})
    require(context["full_name"] == repository, "repository mismatch")
    merged = len(sys.argv) == 1
    if merged:
        matches = [pr for pr in api.pages(f"repos/{repository}/commits/{executor}/pulls?per_page=100")
                   if pr.get("merged_at") and pr.get("merge_commit_sha") == executor]
        require(len(matches) == 1, "expected one merged executor PR")
        number = matches[0]["number"]
    else:
        number = int(sys.argv[1])
    pr = api.get(f"repos/{repository}/pulls/{number}")
    validate_pr(pr, context, number, merged=merged)
    require((pr["merge_commit_sha"] if merged else pr["head"]["sha"]) == executor, "PR executor mismatch")
    reviews = api.pages(f"repos/{repository}/pulls/{number}/reviews?per_page=100")
    print(json.dumps({"executor_sha": executor, "pr_number": number,
                      "acceptance": effective_acceptance(pr, reviews, repository)}))
