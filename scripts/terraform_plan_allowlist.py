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
    required_action: str | None = None,
) -> list[str]:
    blocked = []
    for resource in plan.get("resource_changes", []):
        actions = resource.get("change", {}).get("actions", [])
        if actions in IGNORED_ACTIONS:
            continue
        address = resource.get("address", "")
        if required_action is not None and actions != [required_action]:
            blocked.append(f"{'/'.join(actions)} {address}")
            continue
        if block_deletes and "delete" in actions:
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
        action="append",
        default=[],
        help="Newline-separated resource addresses allowed to change.",
    )
    parser.add_argument(
        "--allowed-pattern",
        "--allowed-patterns",
        dest="allowed_patterns",
        action="append",
        default=[],
        help="Newline-separated regexes; addresses matching any are allowed to change.",
    )
    parser.add_argument(
        "--require-action",
        choices=("create", "update", "delete", "forget"),
        help="Refuse every non-noop change whose action list is not exactly this one action.",
    )
    parser.add_argument(
        "--block-deletes",
        action="store_true",
        help="Refuse deletes, including replaces, even for allowlisted addresses.",
    )
    parser.add_argument(
        "--refusal-prefix",
        required=True,
        help="Message prefix printed before the blocked resource list.",
    )
    args = parser.parse_args(argv)

    with open(args.plan_json) as file_obj:
        plan = json.load(file_obj)
    allowed_exact = {
        address
        for value in args.allowed_exact
        for address in split_lines(value)
    }
    allowed_patterns = [
        re.compile(pattern)
        for value in args.allowed_patterns
        for pattern in split_lines(value)
    ]

    blocked = blocked_changes(
        plan,
        allowed_exact=allowed_exact,
        allowed_patterns=allowed_patterns,
        block_deletes=args.block_deletes,
        required_action=args.require_action,
    )
    if blocked:
        print(f"{args.refusal_prefix} because the Terraform plan changes non-allowlisted resources:")
        for item in blocked:
            print(f"- {item}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
