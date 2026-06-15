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
SCHEMA_COMPATIBILITY_BLOCKING_KINDS = {"removed", "renamed", "type_changed"}
WAIVER_REQUIRED_TEXT_FIELDS = ("rationale", "consumer_impact", "reviewer", "pr_reference", "migration_path")
NO_CACHE_CONTROL = "no-cache, max-age=0, must-revalidate"
GCLOUD_COMPOSITE_TEMP_PREFIX = "gcloud/tmp/parallel_composite_uploads/see_gcloud_storage_cp_help_for_details/"
BREAKING_CHANGE_CATEGORIES = {
    "path",
    "format",
    "artifact_set",
    "schema",
    "feature_identity",
    "pmtiles_lookup",
    "metadata_sidecar",
    "access",
    "catalog",
    "lifecycle_delete",
    "other",
}


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


def pr_api_payload_to_event(
    pr: dict[str, Any],
    *,
    repository: str,
    default_branch: str,
    allow_merged: bool = False,
) -> dict[str, Any]:
    errors = []
    is_open = pr.get("state") == "open"
    is_merged = pr.get("state") == "closed" and pr.get("merged") is True
    if not is_open and not (allow_merged and is_merged):
        if allow_merged:
            errors.append("PR must be open or merged")
        else:
            errors.append("PR must be open")
    if pr.get("head", {}).get("repo", {}).get("full_name") != repository:
        errors.append("PR head repository must match this repository")
    if pr.get("base", {}).get("repo", {}).get("full_name") != repository:
        errors.append("PR base repository must match this repository")
    if pr.get("base", {}).get("ref") != default_branch:
        errors.append(f"PR base branch must be {default_branch}")
    if errors:
        raise PlanValidationError("; ".join(errors))
    return {"pull_request": pr}


def first_workflow_run_pr_number(event: dict[str, Any]) -> str | None:
    pull_requests = event.get("workflow_run", {}).get("pull_requests") or []
    for pull_request in pull_requests:
        number = pull_request.get("number") if isinstance(pull_request, dict) else None
        if number is not None and str(number).isdigit():
            return str(number)
    return None


def repository_name_from_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    full_name = payload.get("full_name") or payload.get("nameWithOwner")
    if full_name:
        return str(full_name)
    name = payload.get("name")
    owner = payload.get("owner")
    if isinstance(owner, dict):
        owner_name = owner.get("login") or owner.get("name")
    else:
        owner_name = owner
    if owner_name and name:
        return f"{owner_name}/{name}"
    return ""


def pr_candidate_number(candidate: dict[str, Any]) -> str | None:
    number = candidate.get("number")
    if number is not None and str(number).isdigit():
        return str(number)
    return None


def pr_candidate_base_ref(candidate: dict[str, Any]) -> str:
    base = candidate.get("base") if isinstance(candidate.get("base"), dict) else {}
    return str(base.get("ref") or candidate.get("baseRefName") or "")


def pr_candidate_head_ref(candidate: dict[str, Any]) -> str:
    head = candidate.get("head") if isinstance(candidate.get("head"), dict) else {}
    return str(head.get("ref") or candidate.get("headRefName") or "")


def pr_candidate_head_sha(candidate: dict[str, Any]) -> str:
    head = candidate.get("head") if isinstance(candidate.get("head"), dict) else {}
    return str(head.get("sha") or candidate.get("headRefOid") or "")


def pr_candidate_merge_sha(candidate: dict[str, Any]) -> str:
    merge_commit = candidate.get("mergeCommit") if isinstance(candidate.get("mergeCommit"), dict) else {}
    return str(candidate.get("merge_commit_sha") or merge_commit.get("oid") or "")


def pr_candidate_head_repo(candidate: dict[str, Any]) -> str:
    head = candidate.get("head") if isinstance(candidate.get("head"), dict) else {}
    return repository_name_from_payload(head.get("repo") or candidate.get("headRepository"))


def pr_candidate_base_repo(candidate: dict[str, Any]) -> str:
    base = candidate.get("base") if isinstance(candidate.get("base"), dict) else {}
    return repository_name_from_payload(base.get("repo") or candidate.get("baseRepository"))


def pr_candidate_is_merged(candidate: dict[str, Any]) -> bool:
    state = str(candidate.get("state") or "").lower()
    return (
        candidate.get("merged") is True
        or bool(candidate.get("merged_at") or candidate.get("mergedAt"))
        or state == "merged"
    )


def pr_candidate_is_eligible(
    candidate: dict[str, Any],
    *,
    repository: str,
    default_branch: str,
) -> bool:
    if pr_candidate_number(candidate) is None:
        return False
    base_ref = pr_candidate_base_ref(candidate)
    if base_ref and base_ref != default_branch:
        return False
    for repo_name in (pr_candidate_head_repo(candidate), pr_candidate_base_repo(candidate)):
        if repo_name and repo_name != repository:
            return False
    return True


def select_workflow_run_pr_candidate(
    candidates: list[Any],
    *,
    repository: str,
    default_branch: str,
    head_sha: str,
    head_branch: str,
) -> dict[str, Any] | None:
    eligible = [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict)
        and pr_candidate_is_eligible(candidate, repository=repository, default_branch=default_branch)
    ]
    if not eligible:
        return None

    def score(candidate: dict[str, Any]) -> tuple[int, int, int, int]:
        sha_values = {pr_candidate_head_sha(candidate), pr_candidate_merge_sha(candidate)}
        sha_match = int(bool(head_sha and head_sha in sha_values))
        branch_match = int(bool(head_branch and pr_candidate_head_ref(candidate) == head_branch))
        merged = int(pr_candidate_is_merged(candidate))
        return (sha_match, branch_match, merged, int(pr_candidate_number(candidate) or 0))

    return max(eligible, key=score)


def resolve_workflow_run_pr_number(
    event: dict[str, Any],
    *,
    repository: str,
    default_branch: str,
    commit_prs: list[Any] | None = None,
    branch_prs: list[Any] | None = None,
) -> str | None:
    direct = first_workflow_run_pr_number(event)
    if direct is not None:
        return direct

    workflow_run = event.get("workflow_run", {})
    head_sha = str(workflow_run.get("head_sha") or "")
    head_branch = str(workflow_run.get("head_branch") or "")
    for candidates in (commit_prs or [], branch_prs or []):
        candidate = select_workflow_run_pr_candidate(
            candidates if isinstance(candidates, list) else [],
            repository=repository,
            default_branch=default_branch,
            head_sha=head_sha,
            head_branch=head_branch,
        )
        if candidate is not None:
            return pr_candidate_number(candidate)
    return None


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


def validate_delete_object_name(name: str, *, label: str) -> None:
    try:
        validate_canonical_object_name(name, label=label)
        return
    except PlanValidationError:
        pass
    if name.startswith(GCLOUD_COMPOSITE_TEMP_PREFIX) and "/" not in name.removeprefix(GCLOUD_COMPOSITE_TEMP_PREFIX):
        return
    raise PlanValidationError(f"{label} is outside approved delete prefixes")


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


def normalize_compatibility_waiver(raw: Any, *, asset_slug: str, label: str) -> dict[str, Any] | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict):
        raise PlanValidationError(f"{label} must be an object")
    if raw.get("asset_slug") != asset_slug:
        raise PlanValidationError(f"{label}.asset_slug must match the publish plan asset_slug")

    normalized: dict[str, Any] = {"asset_slug": asset_slug}
    for key in WAIVER_REQUIRED_TEXT_FIELDS:
        value = str(raw.get(key, "")).strip()
        if not value:
            raise PlanValidationError(f"{label}.{key} is required")
        normalized[key] = value

    blocked_changes = raw.get("blocked_changes")
    if not isinstance(blocked_changes, list) or not blocked_changes:
        raise PlanValidationError(f"{label}.blocked_changes must be a nonempty list")
    normalized_changes: list[dict[str, str]] = []
    for index, change in enumerate(blocked_changes, start=1):
        if not isinstance(change, dict):
            raise PlanValidationError(f"{label}.blocked_changes[{index}] must be an object")
        kind = str(change.get("kind", "")).strip()
        field = str(change.get("field", "")).strip()
        if kind not in SCHEMA_COMPATIBILITY_BLOCKING_KINDS:
            raise PlanValidationError(f"{label}.blocked_changes[{index}].kind is not a blocking schema change kind")
        if not field:
            raise PlanValidationError(f"{label}.blocked_changes[{index}].field is required")
        normalized_changes.append({"kind": kind, "field": field})

    normalized["blocked_changes"] = normalized_changes
    return normalized


def normalize_string_list(value: Any, *, label: str) -> list[str]:
    if isinstance(value, str):
        values = [value.strip()] if value.strip() else []
    elif isinstance(value, list):
        values = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, str) or not item.strip():
                raise PlanValidationError(f"{label}[{index}] must be a non-empty string")
            values.append(item.strip())
    else:
        raise PlanValidationError(f"{label} must be a non-empty string or list of strings")
    if not values:
        raise PlanValidationError(f"{label} must not be empty")
    return values


def normalize_breaking_changes(raw: Any, *, label: str = "breaking_changes") -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise PlanValidationError(f"{label} must be a list")
    normalized: list[dict[str, Any]] = []
    for index, change in enumerate(raw, start=1):
        item_label = f"{label}[{index}]"
        if not isinstance(change, dict):
            raise PlanValidationError(f"{item_label} must be an object")
        category = str(change.get("category", "")).strip()
        if category not in BREAKING_CHANGE_CATEGORIES:
            allowed = ", ".join(sorted(BREAKING_CHANGE_CATEGORIES))
            raise PlanValidationError(f"{item_label}.category must be one of: {allowed}")
        summary = str(change.get("summary", "")).strip()
        if not summary:
            raise PlanValidationError(f"{item_label}.summary is required")
        consumer_action = str(change.get("consumer_action", "")).strip()
        if not consumer_action:
            raise PlanValidationError(f"{item_label}.consumer_action is required")
        surfaces = normalize_string_list(change.get("affected_surfaces"), label=f"{item_label}.affected_surfaces")
        normalized.append(
            {
                "category": category,
                "summary": summary,
                "consumer_action": consumer_action,
                "affected_surfaces": surfaces,
            }
        )
    return normalized


def require_cache_sensitive_metadata(
    *,
    destination_name: str,
    content_type: str,
    cache_control: str,
    label: str,
) -> None:
    if destination_name.endswith(".pmtiles"):
        if cache_control != NO_CACHE_CONTROL:
            raise PlanValidationError(f"{label}.cache_control must be {NO_CACHE_CONTROL!r} for PMTiles objects")
        if content_type and content_type != "application/vnd.pmtiles":
            raise PlanValidationError(f"{label}.content_type must be 'application/vnd.pmtiles' for PMTiles objects")
    if destination_name == "_catalog/web/catalog.json":
        if cache_control != NO_CACHE_CONTROL:
            raise PlanValidationError(f"{label}.cache_control must be {NO_CACHE_CONTROL!r} for _catalog/web/catalog.json")
        if content_type and content_type != "application/json":
            raise PlanValidationError(f"{label}.content_type must be 'application/json' for _catalog/web/catalog.json")


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
        "breaking_changes": normalize_breaking_changes(plan.get("breaking_changes")),
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
        require_cache_sensitive_metadata(
            destination_name=destination_name,
            content_type=content_type,
            cache_control=cache_control,
            label=f"promotions[{index}]",
        )
        compatibility_waiver = normalize_compatibility_waiver(
            raw.get("compatibility_waiver"),
            asset_slug=asset_slug,
            label=f"promotions[{index}].compatibility_waiver",
        )

        normalized["promotions"].append(
            {
                "source_uri": source_uri,
                "destination_uri": destination_uri,
                "source_generation": source_generation,
                "destination_generation": destination_generation,
                "content_type": content_type,
                "cache_control": cache_control,
                "compatibility_waiver": compatibility_waiver,
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
        "breaking_changes": normalize_breaking_changes(plan.get("breaking_changes")),
    }

    for index, raw in enumerate(deletions, start=1):
        if not isinstance(raw, dict):
            raise PlanValidationError(f"deletions[{index}] must be an object")
        uri = str(raw.get("uri", ""))
        object_name = object_name_from_uri(uri, bucket=bucket, label=f"deletions[{index}].uri")
        validate_delete_object_name(object_name, label=f"deletions[{index}].uri")
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


def compact_plan_summary(plan_type: str, normalized: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "asset_slug": normalized.get("asset_slug", ""),
        "proposal_id": normalized.get("proposal_id", ""),
        "plan_type": plan_type,
    }
    if plan_type == "publish":
        promotions = normalized.get("promotions", [])
        summary.update(
            {
                "promotion_count": len(promotions),
                "new_destination_count": sum(1 for item in promotions if not item.get("destination_generation")),
                "replacement_count": sum(1 for item in promotions if item.get("destination_generation")),
                "compatibility_waiver_count": sum(1 for item in promotions if item.get("compatibility_waiver")),
                "breaking_change_count": len(normalized.get("breaking_changes", [])),
            }
        )
    else:
        summary["deletion_count"] = len(normalized.get("deletions", []))
        summary["breaking_change_count"] = len(normalized.get("breaking_changes", []))
    return summary


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
    if args.quiet:
        return 0
    if args.print_plan or not args.output:
        print(payload, end="")
    else:
        print(json.dumps(compact_plan_summary(args.plan_type, normalized), indent=2, sort_keys=True))
    return 0


def command_event_from_pr(args: argparse.Namespace) -> int:
    pr = json.loads(pathlib.Path(args.pr_json).read_text())
    if not isinstance(pr, dict):
        raise PlanValidationError("PR API payload must be a JSON object")
    event = pr_api_payload_to_event(
        pr,
        repository=args.repository,
        default_branch=args.default_branch,
        allow_merged=args.allow_merged,
    )
    payload = json.dumps(event, indent=2, sort_keys=True) + "\n"
    if args.output:
        pathlib.Path(args.output).write_text(payload)
    print(payload, end="")
    return 0


def load_json_list(path: str | None, *, label: str) -> list[Any]:
    if not path:
        return []
    payload = json.loads(pathlib.Path(path).read_text())
    if not isinstance(payload, list):
        raise PlanValidationError(f"{label} must be a JSON list")
    return payload


def command_resolve_workflow_run_pr(args: argparse.Namespace) -> int:
    event = json.loads(pathlib.Path(args.event_path).read_text())
    if not isinstance(event, dict):
        raise PlanValidationError("workflow_run event payload must be a JSON object")
    pr_number = resolve_workflow_run_pr_number(
        event,
        repository=args.repository,
        default_branch=args.default_branch,
        commit_prs=load_json_list(args.commit_prs_json, label="commit PR candidates"),
        branch_prs=load_json_list(args.branch_prs_json, label="branch PR candidates"),
    )
    if pr_number:
        print(pr_number)
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
    extract.add_argument(
        "--print-plan",
        action="store_true",
        help="Print the full normalized plan even when --output is set. By default --output prints only a compact summary.",
    )
    extract.add_argument("--quiet", action="store_true", help="Write --output without printing anything.")
    extract.set_defaults(func=command_extract)

    event_from_pr = subparsers.add_parser(
        "event-from-pr",
        help="Validate a same-repo PR API payload and wrap it like a pull_request event.",
    )
    event_from_pr.add_argument("--pr-json", required=True, help="Path to a GitHub REST pulls/{number} response.")
    event_from_pr.add_argument("--repository", required=True, help="Expected owner/name repository.")
    event_from_pr.add_argument("--default-branch", required=True, help="Expected base branch.")
    event_from_pr.add_argument(
        "--allow-merged",
        action="store_true",
        help="Accept already-merged same-repo PRs for approved self-authored fallback dispatch.",
    )
    event_from_pr.add_argument("--output", help="Optional path for wrapped event JSON.")
    event_from_pr.set_defaults(func=command_event_from_pr)

    resolve_workflow_run_pr = subparsers.add_parser(
        "resolve-workflow-run-pr",
        help="Resolve the reviewed PR number for a workflow_run event using optional GitHub PR candidates.",
    )
    resolve_workflow_run_pr.add_argument("--event-path", required=True, help="Path to the workflow_run event JSON.")
    resolve_workflow_run_pr.add_argument("--repository", required=True, help="Expected owner/name repository.")
    resolve_workflow_run_pr.add_argument("--default-branch", required=True, help="Expected base branch.")
    resolve_workflow_run_pr.add_argument(
        "--commit-prs-json",
        help="Optional JSON list from the GitHub commit-associated PR endpoint.",
    )
    resolve_workflow_run_pr.add_argument(
        "--branch-prs-json",
        help="Optional JSON list from gh pr list for the workflow_run head branch.",
    )
    resolve_workflow_run_pr.set_defaults(func=command_resolve_workflow_run_pr)

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
