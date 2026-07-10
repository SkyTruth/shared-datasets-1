#!/usr/bin/env python3
"""Refuse a Terraform plan that changes resources outside an explicit allowlist.

Reads `terraform show -json` output and exits nonzero when any create, update,
replace, or delete touches a resource address that is not allowlisted. This is
the single owner of the resource-change allowlist rule used by the constrained
prod Terraform apply workflows; keep it stdlib-only so workflow steps can run
it before `uv sync`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

IGNORED_ACTIONS = ([], ["no-op"], ["read"])


def split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def blocked_changes(
    plan: dict,
    *,
    allowed_exact: set[str],
    allowed_patterns: list[re.Pattern[str]],
    block_deletes: bool,
) -> list[str]:
    blocked = []
    for resource in plan.get("resource_changes", []):
        actions = resource.get("change", {}).get("actions", [])
        if actions in IGNORED_ACTIONS:
            continue
        address = resource.get("address", "")
        if block_deletes and actions == ["delete"]:
            blocked.append(f"{'/'.join(actions)} {address}")
            continue
        if address in allowed_exact:
            continue
        if any(pattern.match(address) for pattern in allowed_patterns):
            continue
        blocked.append(f"{'/'.join(actions)} {address}")
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
        help="Refuse deletes even for allowlisted addresses.",
    )
    parser.add_argument(
        "--refusal-prefix",
        required=True,
        help="Message prefix printed before the blocked resource list.",
    )
    args = parser.parse_args(argv)

    with open(args.plan_json) as file_obj:
        plan = json.load(file_obj)
    allowed_exact = set(split_lines(args.allowed_exact))
    allowed_patterns = [re.compile(pattern) for pattern in split_lines(args.allowed_patterns)]

    blocked = blocked_changes(
        plan,
        allowed_exact=allowed_exact,
        allowed_patterns=allowed_patterns,
        block_deletes=args.block_deletes,
    )
    if blocked:
        print(f"{args.refusal_prefix} because the Terraform plan changes non-allowlisted resources:")
        for item in blocked:
            print(f"- {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
