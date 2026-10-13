#!/usr/bin/env python3
"""Validate preview database state detachment without destroying its data."""

from __future__ import annotations

import argparse
import json


DATABASES = {
    "preview": ("google_firestore_database.feature_preview", "feature-preview"),
}


def blocked_database_changes(plan: dict, database: str) -> list[str]:
    """Detach only the named database; never mutate a live resource."""
    database_address, database_name = DATABASES[database]
    blocked = []
    for resource in plan.get("resource_changes", []):
        address = resource["address"]
        change = resource["change"]
        actions = change["actions"]
        if address == database_address:
            if actions == ["forget"] and change["before"]["name"] == database_name:
                continue
        elif actions in (["no-op"], ["read"]):
            continue
        blocked.append(f"{'/'.join(actions)} {address}")
    return blocked


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan_json")
    parser.add_argument("--database-only", choices=DATABASES, required=True)
    args = parser.parse_args()
    with open(args.plan_json) as handle:
        plan = json.load(handle)
    blocked = blocked_database_changes(plan, args.database_only)
    if blocked:
        print("Refusing metadata retirement plan:")
        for item in blocked:
            print(f"- {item}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
