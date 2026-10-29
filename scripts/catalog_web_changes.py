#!/usr/bin/env python3
"""Select reviewed catalog changes inside the existing post-CI main push job."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.dataset_mutation_authorization import (
    GitHub, document_from_files, discover_push_prs, require, sha,
)


CATALOG_INPUTS = {
    "catalog/shared-datasets-catalog.csv", "catalog/categories.yaml",
    "scripts/catalog_site.py", "scripts/catalog_csv.py", "scripts/catalog_web_publish.py",
    "scripts/collect_release_indexes.py", "scripts/catalog_drift_guard.py",
    "scripts/catalog_web_changes.py", ".github/workflows/catalog-web-deploy.yml",
    "scripts/catalog_refresh_check.py",
}


def affects_catalog(path: str) -> bool:
    return path in CATALOG_INPUTS or path.startswith(("web/catalog/", "docs/assets/"))


def should_refresh(api: GitHub, event: dict, env: dict) -> bool:
    """Select only; the publisher independently proves this run's passing CI."""
    repository = event["repository"]
    before, after = sha(event["before"]), sha(event["after"])
    require(env["GITHUB_EVENT_NAME"] == "push" and env["GITHUB_REF"] == event["ref"] == "refs/heads/main"
            and env["GITHUB_SHA"] == after and before != "0" * 40
            and event["forced"] is False and event["deleted"] is False,
            "catalog selection requires an ordinary main push")
    require(repository["full_name"] == env["GITHUB_REPOSITORY"]
            and env["GITHUB_WORKFLOW_REF"] == f"{repository['full_name']}/.github/workflows/ci.yml@refs/heads/main",
            "catalog selection requires the trusted main CI workflow")
    require(subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == after,
            "catalog checkout differs from its main push")
    subprocess.run(["git", "merge-base", "--is-ancestor", before, after], check=True)
    changed = subprocess.check_output(["git", "diff", "--name-only", "-z", before, after], text=True)
    paths = [path for path in changed.split("\0") if path]
    if not any(map(affects_catalog, paths)):
        return False
    numbers = discover_push_prs(api, event, env, documents_only=False)
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
    parser.parse_args()
    event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    selected = should_refresh(GitHub(), event, dict(os.environ))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
        stream.write(f"should_deploy={str(selected).lower()}\n")


if __name__ == "__main__":
    main()
