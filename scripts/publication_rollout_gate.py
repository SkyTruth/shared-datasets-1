#!/usr/bin/env python3
"""Fail deployment while publication adoption is on HOLD; stdlib only.

Version 1 has no permitting state or override. This fences the three deployment
workflows, not already deployed jobs, historical workflows, or runtime writers.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = REPO_ROOT / "catalog/publication-rollout.json"
CATALOG_PATH = REPO_ROOT / "catalog/shared-datasets-catalog.csv"
BUCKET = "skytruth-shared-datasets-1"
REQUIRED_ASSETS_BY_JOB = {
    "wdpa-monthly": frozenset({"wdpa-marine", "wdpa-terrestrial"}),
    "sea-ice-daily": frozenset({"ims-sea-ice-extent"}),
    "eamlis-monthly": frozenset({"eamlis-abandoned-mine-land-inventory"}),
}


def require(condition: Any, message: str) -> None:
    if not condition:
        raise ValueError(message)


def strict_json(data: str) -> Any:
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"duplicate policy field: {key}")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError(f"invalid JSON constant: {value}")

    return json.loads(data, object_pairs_hook=unique_fields, parse_constant=invalid_constant)


def validate_policy(policy: Any, catalog_path: Path) -> None:
    """Validate complete HOLD coverage; success here never authorizes deployment."""
    require(isinstance(policy, dict) and set(policy) == {"schema_version", "stage", "jobs"}, "policy fields must be schema_version, stage and jobs")
    require(type(policy["schema_version"]) is int and policy["schema_version"] == 1, "unsupported rollout policy version")
    require(policy["stage"] == "hold", "rollout policy v1 only supports stage hold")
    jobs = policy["jobs"]
    require(isinstance(jobs, dict) and set(jobs) == set(REQUIRED_ASSETS_BY_JOB), "policy must register exactly all three ingestion jobs")
    with catalog_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        require(len(fields) == len(set(fields)) and {"asset_slug", "category", "subcategory", "canonical_path"} <= set(fields), "catalog has missing/duplicate required columns")
        rows = list(reader)
    for job, expected_assets in REQUIRED_ASSETS_BY_JOB.items():
        assets = jobs[job]
        require(isinstance(assets, dict) and set(assets) == expected_assets, f"{job}: required asset registration is missing or incorrect")
        for slug, root in assets.items():
            matches = [row for row in rows if row["asset_slug"] == slug]
            require(len(matches) == 1, f"{slug}: catalog must contain exactly one row")
            row = matches[0]
            require(all(isinstance(row[key], str) and re.fullmatch(r"[0-9]{3}-[a-z0-9]+(?:-[a-z0-9]+)*", row[key]) for key in ("category", "subcategory")), f"{slug}: invalid catalog taxonomy root")
            expected_root = f"{row['category']}/{row['subcategory']}/{slug}"
            require(root == expected_root, f"{slug}: policy root differs from catalog")
            require(row["canonical_path"] == f"gs://{BUCKET}/{expected_root}/latest/{slug}.fgb", f"{slug}: catalog canonical path differs from registered root/bucket")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, choices=tuple(REQUIRED_ASSETS_BY_JOB))
    args = parser.parse_args(argv)
    try:
        validate_policy(strict_json(POLICY_PATH.read_text(encoding="utf-8")), CATALOG_PATH)
    except (OSError, ValueError) as exc:
        print(f"PUBLICATION_ROLLOUT_INVALID: {exc}", file=sys.stderr)
        return 2
    print(
        f"PUBLICATION_ROLLOUT_HOLD: {args.job} deployment is blocked. "
        "Publication writer integration and adoption require separate review; "
        "policy v1 has no permitting state or override.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
