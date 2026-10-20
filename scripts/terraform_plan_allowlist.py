#!/usr/bin/env python3
"""Refuse a Terraform plan that changes resources outside an explicit allowlist.

Reads `terraform show -json` output and exits nonzero when any create, update,
replace, or delete touches a resource address that is not allowlisted. This is
the single owner of the resource-change allowlist rule used by the constrained
prod Terraform apply workflows; keep it stdlib-only so workflow steps can run
it before `uv sync`.

Image targets own a closed resource scope and pin the Cloud Run job to the
expected image. EAMLIS and sea ice permit only job updates; MPAtlas also permits
first installation and its scheduler, invoker and canonical deny-policy update.
Other callers retain their declared address/action policy.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

IGNORED_ACTIONS = ([], ["no-op"], ["read"])
JOB_IMAGE_TARGETS = {
    "mpatlas-monthly": "module.mpatlas_monthly_job.google_cloud_run_v2_job.this",
    "eamlis-monthly": "module.eamlis_monthly_job.google_cloud_run_v2_job.this",
    "sea-ice-daily": "module.sea_ice_daily_job.google_cloud_run_v2_job.this",
}


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def job_image_scope(target: str) -> set[str]:
    addresses = {JOB_IMAGE_TARGETS[target]}
    if target == "mpatlas-monthly":
        addresses.update({
            "google_cloud_run_v2_job_iam_member.mpatlas_scheduler_invoker",
            "module.mpatlas_monthly_scheduler.google_cloud_scheduler_job.this",
            "google_iam_deny_policy.canonical_destructive_actions[0]",
        })
    return addresses


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
            if actions != ["update"] and not (address == JOB_IMAGE_TARGETS["mpatlas-monthly"] and actions == ["create"]):
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
        help="Newline-separated resource addresses allowed to change; image targets supply their closed scope.",
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
        help="Require this job's closed resource scope and exact image; MPAtlas also permits first installation.",
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

    allowed_exact = set(split_lines(args.allowed_exact or ""))
    allowed_patterns = [re.compile(pattern) for pattern in split_lines(args.allowed_patterns)]
    job_image = None
    if args.job_image_target is not None:
        if not args.expected_image:
            parser.error("--job-image-target requires a nonempty --expected-image")
        address = JOB_IMAGE_TARGETS[args.job_image_target]
        scope = job_image_scope(args.job_image_target)
        if (args.allowed_exact is not None and allowed_exact != scope) or allowed_patterns:
            parser.error("--job-image-target requires its exact resource scope and no allowed patterns")
        if args.job_image_target == "mpatlas-monthly" and not args.block_deletes:
            parser.error("the MPAtlas image target requires --block-deletes")
        allowed_exact = scope
        job_image = (address, args.expected_image)
    elif args.expected_image is not None:
        parser.error("--expected-image requires --job-image-target")
    elif args.allowed_exact is None:
        parser.error("--allowed-exact is required without --job-image-target")

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
