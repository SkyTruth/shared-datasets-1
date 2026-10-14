#!/usr/bin/env python3
"""Select a PR-backed catalog refresh after an exact successful main CI run."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.ci_source_proof import select_plan
from scripts.dataset_mutation_authorization import (
    GitHub, document_from_files, discover_push_prs, require, sha,
)


CATALOG_INPUTS = {
    "catalog/shared-datasets-catalog.csv", "catalog/categories.yaml",
    "scripts/catalog_site.py", "scripts/catalog_csv.py", "scripts/catalog_web_publish.py",
    "scripts/collect_release_indexes.py", "scripts/catalog_drift_guard.py",
    "scripts/catalog_web_changes.py", ".github/workflows/catalog-web-deploy.yml",
}


def affects_catalog(path: str) -> bool:
    return path in CATALOG_INPUTS or path.startswith(("web/catalog/", "docs/assets/"))


def should_refresh(api: GitHub, event: dict, env: dict, plans: list[dict]) -> bool:
    """Consume the CI source already verified by deployment_source --bootstrap."""
    source = event["workflow_run"]
    repository = event["repository"]
    require(env["GITHUB_EVENT_NAME"] == "workflow_run" and env["GITHUB_REF"] == "refs/heads/main",
            "catalog selection requires a main completion event")
    require(repository["full_name"] == env["GITHUB_REPOSITORY"], "completion repository mismatch")
    run = api.get(f"repos/{repository['full_name']}/actions/runs/{source['id']}/attempts/{source['run_attempt']}")
    require(all(run[key] == source[key] for key in ("id", "run_attempt", "head_sha")),
            "catalog completion source changed")
    require(run["path"] == ".github/workflows/ci.yml" and run["event"] == "push"
            and run["head_branch"] == "main" and run["status"] == "completed"
            and run["conclusion"] == "success"
            and all(run[key]["id"] == repository["id"] and run[key]["full_name"] == repository["full_name"]
                    for key in ("repository", "head_repository")),
            "catalog refresh requires successful same-repository main CI")
    plan = select_plan(plans, run_id=str(run["id"]), attempt=run["run_attempt"])
    before, after = sha(plan["base"]), sha(plan["tested_sha"])
    require(after == run["head_sha"] and plan["head"] == after, "catalog plan revision mismatch")
    paths = subprocess.check_output(["git", "diff", "--name-only", before, after], text=True).splitlines()
    require(paths == plan["changed_paths"], "catalog plan does not describe its Git range")
    if not any(map(affects_catalog, paths)):
        return False
    # Reconstruct the verified CI comparison for the shared merged-PR enumerator.
    push = {"repository": repository, "before": before, "after": after,
            "ref": "refs/heads/main", "forced": False, "deleted": False}
    push_env = {**env, "GITHUB_SHA": after, "GITHUB_EVENT_NAME": "push"}
    numbers = discover_push_prs(api, push, push_env, documents_only=False)
    require(bool(numbers), "catalog changes require a merged same-repository PR")
    reviewed_paths = set()
    has_mutation = False
    for number in numbers:
        pr = api.get(f"repos/{repository['full_name']}/pulls/{number}")
        files = api.pages(f"repos/{repository['full_name']}/pulls/{number}/files?per_page=100")
        has_mutation |= document_from_files(api, pr, repository["full_name"], files,
                                            merged=True, check_body=False) is not None
        reviewed_paths.update(item["filename"] for item in files)
    require(all(path in reviewed_paths for path in paths if affects_catalog(path)),
            "catalog input changes are not covered by merged PRs")
    # Dataset publication already owns the catalog refresh for this range.
    return not has_mutation


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", type=Path, required=True)
    args = parser.parse_args()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    plans = [json.loads(path.read_text()) for path in args.plan_dir.rglob("plan.json")]
    selected = should_refresh(GitHub(), event, dict(os.environ), plans)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write(f"should_deploy={str(selected).lower()}\n")


if __name__ == "__main__":
    main()
