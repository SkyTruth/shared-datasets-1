#!/usr/bin/env python3
"""Prepare and validate immutable, reviewed dataset mutation documents."""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
from typing import Any


DEFAULT_BUCKET = "skytruth-shared-datasets-1"
MAX_MUTATIONS = 50
MAX_RELEASE_INDEX_REBUILDS = 10
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


PLAN_DIRECTORY = ".github/dataset-plans"
FINALIZATION_VERSION = "finalize-promoted-release-v1"


def strict_json_loads(raw: str | bytes) -> Any:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise PlanValidationError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise PlanValidationError(f"non-finite JSON value: {value}")

    try:
        return json.loads(
            raw, object_pairs_hook=object_pairs, parse_constant=invalid_constant
        )
    except (ValueError, UnicodeError) as exc:
        raise PlanValidationError(f"invalid JSON: {exc}") from exc


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n"
    ).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def require_fields(value: Any, allowed: set[str], *, label: str) -> None:
    if not isinstance(value, dict):
        raise PlanValidationError(f"{label} must be an object")
    unknown = set(value) - allowed
    if unknown:
        raise PlanValidationError(
            f"{label} unknown fields: {', '.join(sorted(unknown))}"
        )


def unique_targets(values: list[str], *, label: str) -> None:
    if len(values) != len(set(values)):
        raise PlanValidationError(f"{label} contains duplicate destinations")


def find_fenced_json(body: str, fence_name: str) -> str | None:
    pattern = re.compile(
        rf"```(?:json\s+)?{re.escape(fence_name)}\s*\n(.*?)\n```",
        flags=re.DOTALL | re.IGNORECASE,
    )
    matches = pattern.findall(body)
    if len(matches) > 1:
        raise PlanValidationError(f"multiple {fence_name} fences are ambiguous")
    return matches[0] if matches else None


def extract_fenced_json(body: str, fence_name: str) -> dict[str, Any] | None:
    raw = find_fenced_json(body, fence_name)
    if raw is None:
        return None
    payload = strict_json_loads(raw)
    if not isinstance(payload, dict):
        raise PlanValidationError(f"{fence_name} must be a JSON object")
    return payload


def object_name_from_uri(uri: str, *, bucket: str, label: str) -> str:
    prefix = f"gs://{bucket}/"
    if not uri.startswith(prefix):
        raise PlanValidationError(f"{label} must be inside gs://{bucket}/")
    name = uri.removeprefix(prefix)
    if not name:
        raise PlanValidationError(f"{label} must be an object URI, not a bucket root")
    if name.endswith("/"):
        raise PlanValidationError(f"{label} must name an object, not a prefix")
    if (
        any(part in {"", ".", ".."} for part in name.split("/"))
        or "\\" in name
        or any(ord(c) < 32 for c in name)
    ):
        raise PlanValidationError(f"{label} has an invalid object path")
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
    asset_slug = plan.get("asset_slug", "")
    proposal_id = plan.get("proposal_id", "")
    if not isinstance(asset_slug, str) or not isinstance(proposal_id, str):
        raise PlanValidationError("asset_slug and proposal_id must be strings")
    if not SLUG_RE.fullmatch(asset_slug):
        raise PlanValidationError("asset_slug must be lowercase kebab-case")
    if not PROPOSAL_RE.fullmatch(proposal_id) or proposal_id in {".", ".."}:
        raise PlanValidationError(
            "proposal_id may contain only letters, digits, dots, underscores, and hyphens"
        )
    return asset_slug, proposal_id


def normalize_numeric_generation(value: Any, *, label: str, required: bool) -> str:
    if value is None or value == "":
        if required:
            raise PlanValidationError(f"{label} is required")
        return ""
    generation = str(value)
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", generation):
        raise PlanValidationError(f"{label} must be numeric")
    return generation


def normalize_compatibility_waiver(
    raw: Any, *, asset_slug: str, label: str
) -> dict[str, Any] | None:
    if raw in (None, ""):
        return None
    if not isinstance(raw, dict):
        raise PlanValidationError(f"{label} must be an object")
    require_fields(
        raw,
        {"asset_slug", "blocked_changes", *WAIVER_REQUIRED_TEXT_FIELDS},
        label=label,
    )
    if raw.get("asset_slug") != asset_slug:
        raise PlanValidationError(f"{label}.asset_slug must match the publish plan asset_slug")

    normalized: dict[str, Any] = {"asset_slug": asset_slug}
    for key in WAIVER_REQUIRED_TEXT_FIELDS:
        if not isinstance(raw.get(key, ""), str):
            raise PlanValidationError(f"{label}.{key} must be a string")
        value = raw.get(key, "").strip()
        if not value:
            raise PlanValidationError(f"{label}.{key} is required")
        normalized[key] = value

    blocked_changes = raw.get("blocked_changes")
    if not isinstance(blocked_changes, list) or not blocked_changes:
        raise PlanValidationError(f"{label}.blocked_changes must be a nonempty list")
    normalized_changes: list[dict[str, str]] = []
    for index, change in enumerate(blocked_changes, start=1):
        if not isinstance(change, dict):
            raise PlanValidationError(
                f"{label}.blocked_changes[{index}] must be an object"
            )
        require_fields(
            change, {"kind", "field"}, label=f"{label}.blocked_changes[{index}]"
        )
        kind = str(change.get("kind", "")).strip()
        field = str(change.get("field", "")).strip()
        if kind not in SCHEMA_COMPATIBILITY_BLOCKING_KINDS:
            raise PlanValidationError(
                f"{label}.blocked_changes[{index}].kind is not a blocking schema change kind"
            )
        if not field:
            raise PlanValidationError(
                f"{label}.blocked_changes[{index}].field is required"
            )
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


def normalize_slug_list(raw: Any, *, label: str) -> list[str]:
    if raw in (None, "") or raw == []:
        return []
    values = normalize_string_list(raw, label=label)
    if len(values) > MAX_RELEASE_INDEX_REBUILDS:
        raise PlanValidationError(f"{label} may contain at most {MAX_RELEASE_INDEX_REBUILDS} asset slugs")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, value in enumerate(values, start=1):
        if not SLUG_RE.fullmatch(value):
            raise PlanValidationError(f"{label}[{index}] must be lowercase kebab-case")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return normalized


def normalize_breaking_changes(
    raw: Any, *, label: str = "breaking_changes"
) -> list[dict[str, Any]]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise PlanValidationError(f"{label} must be a list")
    normalized: list[dict[str, Any]] = []
    for index, change in enumerate(raw, start=1):
        item_label = f"{label}[{index}]"
        if not isinstance(change, dict):
            raise PlanValidationError(f"{item_label} must be an object")
        require_fields(
            change,
            {"category", "summary", "consumer_action", "affected_surfaces"},
            label=item_label,
        )
        category = str(change.get("category", "")).strip()
        if category not in BREAKING_CHANGE_CATEGORIES:
            allowed = ", ".join(sorted(BREAKING_CHANGE_CATEGORIES))
            raise PlanValidationError(
                f"{item_label}.category must be one of: {allowed}"
            )
        summary = str(change.get("summary", "")).strip()
        if not summary:
            raise PlanValidationError(f"{item_label}.summary is required")
        consumer_action = str(change.get("consumer_action", "")).strip()
        if not consumer_action:
            raise PlanValidationError(f"{item_label}.consumer_action is required")
        surfaces = normalize_string_list(
            change.get("affected_surfaces"), label=f"{item_label}.affected_surfaces"
        )
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


def normalize_publish_plan(
    plan: dict[str, Any], *, bucket: str = DEFAULT_BUCKET
) -> dict[str, Any]:
    require_fields(
        plan,
        {
            "asset_slug",
            "proposal_id",
            "promotions",
            "breaking_changes",
            "release_index_asset_slugs",
        },
        label="publish plan",
    )
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
        "release_index_asset_slugs": normalize_slug_list(
            plan.get("release_index_asset_slugs"),
            label="release_index_asset_slugs",
        ),
    }

    for index, raw in enumerate(promotions, start=1):
        if not isinstance(raw, dict):
            raise PlanValidationError(f"promotions[{index}] must be an object")
        require_fields(
            raw,
            {
                "source_uri",
                "source_generation",
                "destination_uri",
                "destination_generation",
                "content_type",
                "cache_control",
                "compatibility_waiver",
            },
            label=f"promotions[{index}]",
        )
        source_uri = str(raw.get("source_uri", ""))
        destination_uri = str(raw.get("destination_uri", ""))
        if not source_uri.startswith(expected_source_prefix):
            raise PlanValidationError(
                f"promotions[{index}].source_uri must start with {expected_source_prefix}"
            )
        object_name_from_uri(
            source_uri, bucket=bucket, label=f"promotions[{index}].source_uri"
        )
        destination_name = object_name_from_uri(
            destination_uri,
            bucket=bucket,
            label=f"promotions[{index}].destination_uri",
        )
        validate_canonical_object_name(
            destination_name, label=f"promotions[{index}].destination_uri"
        )
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
        if any(raw.get(key) is not None and not isinstance(raw[key], str) for key in ("content_type", "cache_control")):
            raise PlanValidationError(f"promotions[{index}] metadata must be strings")
        content_type = raw.get("content_type") or ""
        cache_control = raw.get("cache_control") or ""
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

    unique_targets(
        [item["destination_uri"] for item in normalized["promotions"]],
        label="promotions",
    )
    return normalized


def normalize_delete_plan(
    plan: dict[str, Any], *, bucket: str = DEFAULT_BUCKET
) -> dict[str, Any]:
    require_fields(
        plan,
        {"asset_slug", "proposal_id", "deletions", "breaking_changes"},
        label="delete plan",
    )
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
        require_fields(
            raw, {"uri", "generation", "reason"}, label=f"deletions[{index}]"
        )
        uri = str(raw.get("uri", ""))
        object_name = object_name_from_uri(
            uri, bucket=bucket, label=f"deletions[{index}].uri"
        )
        validate_delete_object_name(object_name, label=f"deletions[{index}].uri")
        generation = normalize_numeric_generation(
            raw.get("generation", ""),
            label=f"deletions[{index}].generation",
            required=True,
        )
        reason = str(raw.get("reason", "")).strip()
        if len(reason) < 12:
            raise PlanValidationError(
                f"deletions[{index}].reason must explain why deletion is required"
            )

        normalized["deletions"].append(
            {
                "uri": uri,
                "generation": generation,
                "reason": reason,
            }
        )

    unique_targets([item["uri"] for item in normalized["deletions"]], label="deletions")
    return normalized


def normalize_document(document: Any) -> dict[str, Any]:
    require_fields(
        document,
        {"plan_version", "publish", "delete", "finalization_version"},
        label="plan document",
    )
    if type(document.get("plan_version")) is not int or document["plan_version"] != 1:
        raise PlanValidationError("plan_version must be 1")
    if document.get("finalization_version") != FINALIZATION_VERSION:
        raise PlanValidationError("unsupported finalization_version")
    normalized = {"plan_version": 1, "finalization_version": FINALIZATION_VERSION}
    for kind, normalize in (
        ("publish", normalize_publish_plan),
        ("delete", normalize_delete_plan),
    ):
        if kind in document:
            normalized[kind] = normalize(document[kind])
    payloads = [
        normalized[kind] for kind in ("publish", "delete") if kind in normalized
    ]
    if not payloads:
        raise PlanValidationError("plan document needs publish and/or delete")
    if len({(p["asset_slug"], p["proposal_id"]) for p in payloads}) != 1:
        raise PlanValidationError(
            "publish and delete must identify the same asset/proposal"
        )
    promoted = {
        p["destination_uri"]
        for p in normalized.get("publish", {}).get("promotions", [])
    }
    deleted = {p["uri"] for p in normalized.get("delete", {}).get("deletions", [])}
    if promoted & deleted:
        raise PlanValidationError("publish and delete destinations conflict")
    return normalized


def document_path(document: dict[str, Any]) -> str:
    normalized = normalize_document(document)
    plan = normalized.get("publish") or normalized["delete"]
    return f"{PLAN_DIRECTORY}/{plan['asset_slug']}/{plan['proposal_id']}/{sha256(canonical_bytes(normalized))}.json"


def read_document(raw: bytes, *, path: str) -> dict[str, Any]:
    document = normalize_document(strict_json_loads(raw))
    if raw != canonical_bytes(document):
        raise PlanValidationError(
            "checked-in plan must contain exact canonical bytes; run prepare"
        )
    if path != document_path(document):
        raise PlanValidationError(
            "checked-in plan path does not match its digest/asset/proposal"
        )
    return document


def write_document(
    document: dict[str, Any], *, repo_root: pathlib.Path
) -> pathlib.Path:
    document = normalize_document(document)
    path = repo_root / document_path(document)
    if not path.parent.resolve().is_relative_to(repo_root.resolve()):
        raise PlanValidationError("plan directory escapes repository")
    if path.exists():
        if path.is_symlink() or path.read_bytes() != canonical_bytes(document):
            raise PlanValidationError(
                f"refusing to replace altered immutable plan: {path}"
            )
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as output:
            output.write(canonical_bytes(document))
    return path


def render_document(document: dict[str, Any]) -> str:
    document = normalize_document(document)
    result = f"Checked-in plan: `{document_path(document)}`\n\nSHA-256: `{sha256(canonical_bytes(document))}`\n"
    for kind in ("publish", "delete"):
        if kind in document:
            result += f"\n```shared-datasets-{kind}-plan\n{canonical_bytes(document[kind]).decode()}```\n"
    return result


def check_rendered_body(body: str, document: dict[str, Any]) -> None:
    for kind, normalize in (
        ("publish", normalize_publish_plan),
        ("delete", normalize_delete_plan),
    ):
        payload = extract_fenced_json(body, f"shared-datasets-{kind}-plan")
        if (normalize(payload) if payload is not None else None) != document.get(kind):
            raise PlanValidationError(
                f"{kind} fence must match checked-in plan; regenerate the PR body"
            )


def command_prepare(args: argparse.Namespace) -> int:
    document = {"plan_version": 1, "finalization_version": FINALIZATION_VERSION}
    for kind in ("publish", "delete"):
        path = getattr(args, kind)
        if path:
            document[kind] = strict_json_loads(pathlib.Path(path).read_bytes())
    if args.body:
        if args.publish or args.delete:
            raise PlanValidationError("use --body or payload files, not both")
        body = pathlib.Path(args.body).read_text()
        for kind in ("publish", "delete"):
            payload = extract_fenced_json(body, f"shared-datasets-{kind}-plan")
            if payload is not None:
                document[kind] = payload
    write_document(document, repo_root=pathlib.Path(args.repo_root))
    print(render_document(document), end="")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare",
        help="Write a checked-in immutable plan and render its PR fences; grants no approval.",
    )
    prepare.add_argument("--publish", help="Publish payload JSON path.")
    prepare.add_argument("--delete", help="Delete payload JSON path.")
    prepare.add_argument(
        "--body", help="Migrate a local legacy PR body into a new unapproved document."
    )
    prepare.add_argument("--repo-root", default=".")
    prepare.set_defaults(func=command_prepare)

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
