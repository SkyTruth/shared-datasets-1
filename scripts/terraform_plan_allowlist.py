#!/usr/bin/env python3
"""Refuse a Terraform plan that changes resources outside an explicit allowlist.

Reads `terraform show -json` output and exits nonzero when any create, update,
replace, or delete touches a resource address that is not allowlisted. This is
the single owner of the resource-change allowlist rule used by the constrained
prod Terraform apply workflows; keep it stdlib-only so workflow steps can run
it before `uv sync`.

The EAMLIS and sea-ice image targets additionally permit only an update of their
single Cloud Run job to the expected image; other callers retain their declared
address/action policy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

IGNORED_ACTIONS = ([], ["no-op"], ["read"])
JOB_IMAGE_TARGETS = {
    "eamlis-monthly": "module.eamlis_monthly_job.google_cloud_run_v2_job.this",
    "sea-ice-daily": "module.sea_ice_daily_job.google_cloud_run_v2_job.this",
}


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def blocked_changes(
    plan: dict,
    *,
    allowed_exact: set[str],
    allowed_patterns: list[re.Pattern[str]],
    block_deletes: bool,
    job_image: tuple[str, str] | None = None,
) -> list[str]:
    blocked = []
    for resource in plan.get("resource_changes", []):
        actions = resource.get("change", {}).get("actions", [])
        if actions in IGNORED_ACTIONS:
            continue
        address = resource.get("address", "")
        if block_deletes and "delete" in actions:
            blocked.append(f"{'/'.join(actions)} {address}")
            continue
        if address not in allowed_exact and not any(pattern.match(address) for pattern in allowed_patterns):
            blocked.append(f"{'/'.join(actions)} {address}")
            continue
        if job_image is not None and address == job_image[0]:
            if actions != ["update"]:
                blocked.append(f"{'/'.join(actions)} {address}")
                continue
            after = resource.get("change", {}).get("after", {})
            containers = (
                after.get("template", [{}])[0]
                .get("template", [{}])[0]
                .get("containers", [{}])
            )
            image = containers[0].get("image") if containers else None
            if image != job_image[1]:
                blocked.append(f"unexpected image for {address}: {image!r}")
    return blocked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_json", help="Path to `terraform show -json` output for the plan.")
    parser.add_argument(
        "--allowed-exact",
        required=True,
        help="Newline-separated resource addresses allowed to change.",
    )
    parser.add_argument(
        "--allowed-patterns",
        default="",
        help="Newline-separated regexes; addresses matching any are allowed to change.",
    )
    parser.add_argument(
        "--block-deletes",
        action="store_true",
        help="Refuse deletes, including replaces, even for allowlisted addresses.",
    )
    parser.add_argument(
        "--job-image-target",
        choices=sorted(JOB_IMAGE_TARGETS),
        help="Require an update-only plan for this single Cloud Run job and its expected image.",
    )
    parser.add_argument(
        "--expected-image",
        help="Exact planned container image; required with --job-image-target.",
    )
    parser.add_argument(
        "--refusal-prefix",
        required=True,
        help="Message prefix printed before the blocked resource list.",
    )
    args = parser.parse_args(argv)

    allowed_exact = set(split_lines(args.allowed_exact))
    allowed_patterns = [re.compile(pattern) for pattern in split_lines(args.allowed_patterns)]
    job_image = None
    if args.job_image_target is not None:
        if not args.expected_image:
            parser.error("--job-image-target requires a nonempty --expected-image")
        address = JOB_IMAGE_TARGETS[args.job_image_target]
        if allowed_exact != {address} or allowed_patterns:
            parser.error("--job-image-target requires only its exact job address and no allowed patterns")
        job_image = (address, args.expected_image)
    elif args.expected_image is not None:
        parser.error("--expected-image requires --job-image-target")

    with open(args.plan_json) as file_obj:
        plan = json.load(file_obj)

    blocked = blocked_changes(
        plan,
        allowed_exact=allowed_exact,
        allowed_patterns=allowed_patterns,
        block_deletes=args.block_deletes,
        job_image=job_image,
    )
    if blocked:
        print(f"{args.refusal_prefix} because the Terraform plan changes non-allowlisted resources:")
        for item in blocked:
            print(f"- {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
