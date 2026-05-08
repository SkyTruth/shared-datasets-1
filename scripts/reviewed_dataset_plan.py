#!/usr/bin/env python3
"""Validate reviewed dataset mutation plans embedded in PR bodies."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
from typing import Any


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
MAX_MUTATIONS = 50
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
PROPOSAL_RE = re.compile(r"^[A-Za-z0-9._-]+$")
WILDCARD_CHARS = set("*?[]{}")


class PlanValidationError(ValueError):
    """Raised when a reviewed mutation plan is malformed or unsafe."""


def find_fenced_json(body: str, fence_name: str) -> str | None:
    pattern = re.compile(
        rf"```(?:json\s+)?{re.escape(fence_name)}\s*\n(.*?)\n```",
        flags=re.DOTALL | re.IGNORECASE,
    )
    match = pattern.search(body)
    return match.group(1) if match else None


def extract_fenced_json(body: str, fence_name: str) -> dict[str, Any] | None:
    raw = find_fenced_json(body, fence_name)
    if raw is None:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PlanValidationError(f"{fence_name} JSON is invalid: {exc}") from exc
    if not isinstance(payload, dict):
        raise PlanValidationError(f"{fence_name} must be a JSON object")
    return payload


def event_body(event_path: str | os.PathLike[str]) -> str:
    event = json.loads(pathlib.Path(event_path).read_text())
    return event.get("pull_request", {}).get("body") or ""


def object_name_from_uri(uri: str, *, bucket: str, label: str) -> str:
    prefix = f"gs://{bucket}/"
    if not uri.startswith(prefix):
        raise PlanValidationError(f"{label} must be inside gs://{bucket}/")
    name = uri.removeprefix(prefix)
    if not name:
        raise PlanValidationError(f"{label} must be an object URI, not a bucket root")
    if name.endswith("/"):
        raise PlanValidationError(f"{label} must name an object, not a prefix")
    if any(char in name for char in WILDCARD_CHARS):
        raise PlanValidationError(f"{label} must not contain wildcard characters")
    return name


def validate_canonical_object_name(name: str, *, label: str) -> None:
    approved = name == "README.md" or name.startswith("_catalog/") or re.match(r"^[0-9]{3}-", name)
    blocked = name.startswith("_scratch/") or name.startswith("_deprecated/") or name.startswith("000-system/")
    if blocked or not approved:
        raise PlanValidationError(f"{label} is outside approved canonical mutation prefixes")


def require_slug_and_proposal(plan: dict[str, Any]) -> tuple[str, str]:
    asset_slug = str(plan.get("asset_slug", ""))
    proposal_id = str(plan.get("proposal_id", ""))
    if not SLUG_RE.fullmatch(asset_slug):
        raise PlanValidationError("asset_slug must be lowercase kebab-case")
    if not PROPOSAL_RE.fullmatch(proposal_id):
        raise PlanValidationError("proposal_id may contain only letters, digits, dots, underscores, and hyphens")
    return asset_slug, proposal_id


def normalize_numeric_generation(value: Any, *, label: str, required: bool) -> str:
    if value is None or value == "":
        if required:
            raise PlanValidationError(f"{label} is required")
        return ""
    generation = str(value)
    if not generation.isdigit():
        raise PlanValidationError(f"{label} must be numeric")
    return generation


def normalize_publish_plan(plan: dict[str, Any], *, bucket: str = DEFAULT_BUCKET) -> dict[str, Any]:
    asset_slug, proposal_id = require_slug_and_proposal(plan)
    promotions = plan.get("promotions")
    if not isinstance(promotions, list) or not promotions:
        raise PlanValidationError("promotions must be a nonempty list")
    if len(promotions) > MAX_MUTATIONS:
        raise PlanValidationError(f"promotions may contain at most {MAX_MUTATIONS} objects")

    expected_source_prefix = f"gs://{bucket}/_scratch/pending-publishes/{asset_slug}/{proposal_id}/"
    normalized: dict[str, Any] = {
        "asset_slug": asset_slug,
        "proposal_id": proposal_id,
        "promotions": [],
    }

    for index, raw in enumerate(promotions, start=1):
        if not isinstance(raw, dict):
            raise PlanValidationError(f"promotions[{index}] must be an object")
        source_uri = str(raw.get("source_uri", ""))
        destination_uri = str(raw.get("destination_uri", ""))
        if not source_uri.startswith(expected_source_prefix):
            raise PlanValidationError(f"promotions[{index}].source_uri must start with {expected_source_prefix}")
        object_name_from_uri(source_uri, bucket=bucket, label=f"promotions[{index}].source_uri")
        destination_name = object_name_from_uri(
            destination_uri,
            bucket=bucket,
            label=f"promotions[{index}].destination_uri",
        )
        validate_canonical_object_name(destination_name, label=f"promotions[{index}].destination_uri")
        source_generation = normalize_numeric_generation(
            raw.get("source_generation", ""),
            label=f"promotions[{index}].source_generation",
            required=True,
        )
        destination_generation = normalize_numeric_generation(
            raw.get("destination_generation", ""),
            label=f"promotions[{index}].destination_generation",
            required=False,
        )
        content_type = "" if raw.get("content_type") is None else str(raw.get("content_type", ""))
        cache_control = "" if raw.get("cache_control") is None else str(raw.get("cache_control", ""))
        if len(content_type) > 200:
            raise PlanValidationError(f"promotions[{index}].content_type is too long")
        if len(cache_control) > 500:
            raise PlanValidationError(f"promotions[{index}].cache_control is too long")

        normalized["promotions"].append(
            {
                "source_uri": source_uri,
                "destination_uri": destination_uri,
                "source_generation": source_generation,
                "destination_generation": destination_generation,
                "content_type": content_type,
                "cache_control": cache_control,
            }
        )

    return normalized


def normalize_delete_plan(plan: dict[str, Any], *, bucket: str = DEFAULT_BUCKET) -> dict[str, Any]:
    asset_slug, proposal_id = require_slug_and_proposal(plan)
    deletions = plan.get("deletions")
    if not isinstance(deletions, list) or not deletions:
        raise PlanValidationError("deletions must be a nonempty list")
    if len(deletions) > MAX_MUTATIONS:
        raise PlanValidationError(f"deletions may contain at most {MAX_MUTATIONS} objects")

    normalized: dict[str, Any] = {
        "asset_slug": asset_slug,
        "proposal_id": proposal_id,
        "deletions": [],
    }

    for index, raw in enumerate(deletions, start=1):
        if not isinstance(raw, dict):
            raise PlanValidationError(f"deletions[{index}] must be an object")
        uri = str(raw.get("uri", ""))
        object_name = object_name_from_uri(uri, bucket=bucket, label=f"deletions[{index}].uri")
        validate_canonical_object_name(object_name, label=f"deletions[{index}].uri")
        generation = normalize_numeric_generation(
            raw.get("generation", ""),
            label=f"deletions[{index}].generation",
            required=True,
        )
        reason = str(raw.get("reason", "")).strip()
        if len(reason) < 12:
            raise PlanValidationError(f"deletions[{index}].reason must explain why deletion is required")

        normalized["deletions"].append(
            {
                "uri": uri,
                "generation": generation,
                "reason": reason,
            }
        )

    return normalized


def command_detect(args: argparse.Namespace) -> int:
    body = event_body(args.event_path)
    result = {
        "has_publish_plan": find_fenced_json(body, "shared-datasets-publish-plan") is not None,
        "has_delete_plan": find_fenced_json(body, "shared-datasets-delete-plan") is not None,
    }
    if args.github_output:
        with pathlib.Path(args.github_output).open("a") as output:
            for key, value in result.items():
                output.write(f"{key}={str(value).lower()}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def command_extract(args: argparse.Namespace) -> int:
    body = event_body(args.event_path)
    if args.plan_type == "publish":
        plan = extract_fenced_json(body, "shared-datasets-publish-plan")
        if plan is None:
            raise PlanValidationError("PR body must contain a fenced shared-datasets-publish-plan JSON block")
        normalized = normalize_publish_plan(plan, bucket=args.bucket)
    else:
        plan = extract_fenced_json(body, "shared-datasets-delete-plan")
        if plan is None:
            raise PlanValidationError("PR body must contain a fenced shared-datasets-delete-plan JSON block")
        normalized = normalize_delete_plan(plan, bucket=args.bucket)

    payload = json.dumps(normalized, indent=2, sort_keys=True) + "\n"
    if args.output:
        pathlib.Path(args.output).write_text(payload)
    print(payload, end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect = subparsers.add_parser("detect", help="Detect publish/delete plan fences in a pull request event.")
    detect.add_argument("--event-path", required=True, help="Path to the GitHub event JSON payload.")
    detect.add_argument("--github-output", help="Optional GITHUB_OUTPUT path for job outputs.")
    detect.set_defaults(func=command_detect)

    extract = subparsers.add_parser("extract", help="Extract and validate a reviewed mutation plan.")
    extract.add_argument("plan_type", choices=("publish", "delete"))
    extract.add_argument("--event-path", required=True, help="Path to the GitHub event JSON payload.")
    extract.add_argument("--bucket", default=DEFAULT_BUCKET, help="Expected shared datasets bucket.")
    extract.add_argument("--output", help="Optional path for normalized plan JSON.")
    extract.set_defaults(func=command_extract)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PlanValidationError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
