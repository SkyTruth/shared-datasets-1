"""Offline, evidence-bound candidates for reviewed generated-ID sequence migration.

This checks supplied evidence, not the authenticity or global completeness of
history. Only the protected publication review can accept those prerequisites.
It has no network client, remote writes, or runtime seed override.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import datetime as dt
import hashlib
import gzip
import re
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import release_feature_model as model


SCHEMA_VERSION = 1
REVIEW_REQUIREMENTS = (
    "Independently verify authentic evidence from the asset's first allocation through the cutoff.",
    "Verify uninterrupted journal/log coverage and retention, including overwritten/deleted generations and partial allocations.",
    "Resolve every historical ID reuse and every unaccounted reservation before approving canonical publication.",
    "Publish only the reviewed candidate bytes with the exact anchor generations; re-audit if the anchor changes.",
    "Generated publication must enforce exclusive ownership of this baseline before exposing IDs; a read preflight is not a lock.",
)


class SequenceAuditError(ValueError):
    pass


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n"
    ).encode()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SequenceAuditError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise SequenceAuditError(f"non-finite JSON value: {value}")


def _json_loads(value):
    return json.loads(
        value, object_pairs_hook=_unique_object, parse_constant=_reject_constant
    )


def read_json(path: Path) -> Any:
    return _json_loads(path.read_bytes())


def _sidecar_records(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                record = _json_loads(line)
                if not isinstance(record, dict):
                    raise SequenceAuditError("sidecar row must be a JSON object")
                yield record


def _instant(value: Any) -> str:
    if not isinstance(value, str):
        raise SequenceAuditError(
            "event/coverage time must be an ISO timestamp with timezone"
        )
    timestamp = dt.datetime.fromisoformat(value)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise SequenceAuditError("event/coverage time must include a timezone")
    return (
        timestamp.astimezone(dt.UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _normalized_inventory(value: Any) -> dict[str, Any]:
    """One strict boundary before sorting, hashing or interpreting evidence."""
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != SCHEMA_VERSION
    ):
        raise SequenceAuditError("inventory requires integer schema_version 1")
    for field in ("asset_slug", "asset_root"):
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise SequenceAuditError(f"inventory {field} must be a nonempty string")
    if not isinstance(value.get("coverage"), dict) or not isinstance(
        value.get("anchor"), dict
    ):
        raise SequenceAuditError("inventory coverage and anchor must be objects")
    for field in ("objects", "events", "decision_evidence"):
        entries = value.get(field, [])
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict) for entry in entries
        ):
            raise SequenceAuditError(f"inventory {field} must be an array of objects")
    evidence = value["coverage"].get("evidence", [])
    if not isinstance(evidence, list) or any(
        not isinstance(entry, dict) for entry in evidence
    ):
        raise SequenceAuditError("coverage evidence must be an array of objects")
    for entry in [
        *value.get("objects", []),
        *evidence,
        *value.get("decision_evidence", []),
    ]:
        if not isinstance(entry.get("local_path"), str) or not entry["local_path"]:
            raise SequenceAuditError("evidence requires a nonempty local_path")
        if (
            type(entry.get("size")) is not int
            or entry["size"] < 0
            or not isinstance(entry.get("sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
        ):
            raise SequenceAuditError(
                "evidence requires a nonnegative integer size and lowercase SHA-256"
            )
    for entry in [*value.get("objects", []), *value.get("events", [])]:
        _object_key(entry)
    for entry in value.get("objects", []):
        for field in ("kind", "publication", "release"):
            if not isinstance(entry.get(field), str) or not entry[field]:
                raise SequenceAuditError(f"object evidence requires {field}")
        dt.date.fromisoformat(entry["release"])
        if entry["kind"] == "allocation_artifact":
            if not isinstance(entry.get("allocation_evidence"), dict):
                raise SequenceAuditError(
                    "allocation artifact requires an exact sidecar evidence reference"
                )
            _object_key(entry["allocation_evidence"])
            if not isinstance(entry.get("mapping_evidence_sha256"), str):
                raise SequenceAuditError(
                    "allocation artifact requires hash-bound mapping evidence"
                )
    for entry in evidence:
        if (
            not isinstance(entry.get("provenance"), str)
            or not entry["provenance"].strip()
        ):
            raise SequenceAuditError("coverage provenance must be a nonempty string")
    for event in value.get("events", []):
        if not isinstance(event.get("operation"), str) or event["operation"] not in {
            "write",
            "delete",
        }:
            raise SequenceAuditError("event operation must be write or delete")
    for entry in value["anchor"].values():
        if not isinstance(entry, dict):
            raise SequenceAuditError("anchor references must be objects")
        _object_key(entry)
    normalized = deepcopy(value)
    for field in ("genesis", "cutoff"):
        normalized["coverage"][field] = _instant(value["coverage"].get(field))
    for event in normalized.get("events", []):
        event["observed_at"] = _instant(event.get("observed_at"))
    return normalized


def _descriptor(entry: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in entry.items() if key != "local_path"}


def _check_file(entry: dict[str, Any], directory: Path) -> Path:
    path = directory / entry["local_path"]
    if type(entry.get("size")) is not int or entry["size"] < 0:
        raise SequenceAuditError("evidence size must be a non-negative integer")
    if path.stat().st_size != entry["size"]:
        raise SequenceAuditError(f"evidence byte size mismatch: {path}")
    with path.open("rb") as handle:
        digest = hashlib.file_digest(handle, "sha256").hexdigest()
    if digest != entry.get("sha256"):
        raise SequenceAuditError(f"evidence SHA-256 mismatch: {path}")
    return path


def _object_key(value: dict[str, Any]) -> tuple[str, int]:
    path, generation = value.get("path"), value.get("generation")
    if not isinstance(path, str) or not path.startswith("gs://"):
        raise SequenceAuditError("object evidence requires a gs:// path")
    if type(generation) is not int or generation <= 0:
        raise SequenceAuditError(
            "object evidence requires a positive integer generation"
        )
    return path, generation


def _legacy_records(path: Path, *, asset_slug: str):
    for record in _sidecar_records(path):
        properties = record.get("properties", {})
        if not isinstance(properties, dict):
            raise SequenceAuditError("legacy properties must be an object")
        if (
            record.get("schema_version") != 1
            or record.get("asset_slug") not in (None, asset_slug)
            or not str(properties.get("SITE_PID") or "").strip()
        ):
            raise SequenceAuditError(
                "unsupported legacy metadata; explicit conversion evidence required"
            )
        yield {
            "feature_id": str(properties.get("ext_id", "")),
            "identity_key": [str(properties["SITE_PID"])],
        }


def audit(inventory: dict[str, Any], *, directory: Path) -> dict[str, Any]:
    """Validate a small normalized inventory; raw journal/log interpretation is review-owned.

    Events are an exported/reconciled inventory, never an assertion of global
    completeness. Their source evidence must be supplied, hashed and reviewed.
    """
    errors: list[str] = []
    try:
        inventory = _normalized_inventory(inventory)
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "not_ready",
            "next_feature_id": None,
            "errors": [str(exc)],
            "human_review_requirements": list(REVIEW_REQUIREMENTS),
        }
    canonical = deepcopy(inventory)
    objects = inventory.get("objects", [])
    coverage = inventory.get("coverage", {})
    root = inventory.get("asset_root", "")
    slug = inventory.get("asset_slug", "")
    highest = 0
    seen_ids: dict[str, tuple[str, ...]] = {}
    files: dict[tuple[str, int], tuple[dict[str, Any], Path]] = {}
    counts = {"records": 0, "objects": 0, "partial_objects": 0}
    try:
        if (
            inventory.get("schema_version") != SCHEMA_VERSION
            or not slug
            or not root.startswith("gs://")
        ):
            raise SequenceAuditError(
                "inventory requires schema_version 1, asset_slug and gs:// asset_root"
            )
        if (
            not coverage.get("genesis")
            or not coverage.get("cutoff")
            or coverage["genesis"] > coverage["cutoff"]
        ):
            raise SequenceAuditError("coverage requires an ordered genesis and cutoff")
        for field in ("genesis", "cutoff"):
            dt.datetime.fromisoformat(coverage[field])
        evidence = coverage.get("evidence", [])
        if not evidence:
            raise SequenceAuditError(
                "authoritative journal/event coverage evidence is missing; latest-only data is insufficient"
            )
        for entry in evidence:
            _check_file(entry, directory)
            if not entry.get("provenance"):
                raise SequenceAuditError(
                    "coverage evidence requires source export provenance for human verification"
                )
        for entry in objects:
            key = _object_key(entry)
            if not key[0].startswith(root.rstrip("/") + "/"):
                raise SequenceAuditError("object evidence is outside the asset root")
            if key in files:
                raise SequenceAuditError("duplicate object generation in inventory")
            if entry.get("publication") not in {"committed", "partial"}:
                raise SequenceAuditError(
                    "object publication must be committed or partial"
                )
            files[key] = (entry, _check_file(entry, directory))
        if not files:
            raise SequenceAuditError("no historical allocation objects supplied")
        events = inventory.get("events", [])
        writes = [event for event in events if event.get("operation") == "write"]
        if any(event.get("operation") not in {"write", "delete"} for event in events):
            raise SequenceAuditError("events must be write/delete inventory records")
        write_keys = [_object_key(event) for event in writes]
        if len(set(write_keys)) != len(write_keys) or set(write_keys) != set(files):
            raise SequenceAuditError(
                "write events and generation-specific evidence must match exactly"
            )
        for event in events:
            if _object_key(event) not in files:
                raise SequenceAuditError(
                    "event references missing generation-specific allocation evidence"
                )
            when = event.get("observed_at", "")
            dt.datetime.fromisoformat(when)
            if not coverage["genesis"] <= when <= coverage["cutoff"]:
                raise SequenceAuditError(
                    "event falls outside supplied coverage interval"
                )
        if (
            not writes
            or min(event["observed_at"] for event in writes) != coverage["genesis"]
        ):
            raise SequenceAuditError(
                "supplied writes do not reach the declared first allocation"
            )
        # Only exact reviewed reuse decisions may explain a historical key change.
        transitions: set[tuple[str, tuple[str, ...], tuple[str, ...], str]] = set()
        for entry in inventory.get("decision_evidence", []):
            decision_file = read_json(_check_file(entry, directory))
            if (
                not isinstance(decision_file, dict)
                or not isinstance(decision_file.get("decisions"), list)
                or any(
                    not isinstance(item, dict) for item in decision_file["decisions"]
                )
            ):
                raise SequenceAuditError(
                    "decision evidence must contain an array of decision objects"
                )
            for decision in decision_file["decisions"]:
                if decision.get("action") != "reuse_previous_feature_id":
                    continue
                if (
                    not decision.get("reviewer")
                    or not decision.get("pr_reference")
                    or not decision.get("rationale")
                ):
                    raise SequenceAuditError(
                        "reuse evidence requires reviewer, PR reference and rationale"
                    )
                old_key = decision.get("previous_identity_key")
                if not old_key:
                    raise SequenceAuditError(
                        "historical reuse evidence requires explicit previous_identity_key"
                    )
                transitions.add(
                    (
                        str(decision["reuse_feature_id"]),
                        tuple(old_key),
                        tuple(decision["new_identity_key"]),
                        decision["release"],
                    )
                )
        for event in sorted(
            writes, key=lambda value: (value["observed_at"], *_object_key(value))
        ):
            entry, path = files[_object_key(event)]
            counts["objects"] += 1
            counts["partial_objects"] += entry.get("publication") == "partial"
            kind = entry.get("kind")
            if kind == "manifest":
                manifest = read_json(path)
                if not isinstance(manifest, dict):
                    raise SequenceAuditError("manifest evidence must be a JSON object")
                if manifest.get("asset_slug") != slug:
                    raise SequenceAuditError("historical manifest asset mismatch")
                artifacts = model.validate_release_manifest(
                    manifest, expected_asset_slug=slug, require_generations=True
                )
                for role, artifact in artifacts.items():
                    if role == "manifest":
                        continue
                    artifact_key = _object_key(artifact)
                    if (
                        artifact_key not in files
                        or str(artifact["sha256"]).removeprefix("sha256:")
                        != files[artifact_key][0]["sha256"]
                    ):
                        raise SequenceAuditError(
                            f"manifest {role} write lacks exact generation/hash evidence"
                        )
                historical_metadata = artifacts["metadata"]
                metadata_key = _object_key(historical_metadata)
                if (
                    metadata_key not in files
                    or str(historical_metadata["sha256"]).removeprefix("sha256:")
                    != files[metadata_key][0]["sha256"]
                ):
                    raise SequenceAuditError(
                        "historical manifest references missing or mismatched metadata generation"
                    )
                metadata_entry, metadata_path = files[metadata_key]
                if (
                    metadata_entry["kind"] != "metadata"
                    or metadata_entry["release"] != manifest.get("release")
                    or entry["release"] != manifest.get("release")
                ):
                    raise SequenceAuditError(
                        "manifest and metadata evidence releases must match"
                    )
                validation = model.validate_sidecar_records(
                    _sidecar_records(metadata_path),
                    expected_asset_slug=slug,
                    expected_release=manifest["release"],
                )
                manifest_validation = manifest.get("validation")
                expected_count = (
                    manifest_validation.get("feature_count")
                    if isinstance(manifest_validation, dict)
                    else None
                )
                if (
                    not validation.valid
                    or type(expected_count) is not int
                    or expected_count != validation.feature_count
                ):
                    raise SequenceAuditError(
                        "manifest metadata release/count mismatch: "
                        + "; ".join(validation.errors)
                    )
                identity = manifest.get("identity", {})
                model.validate_identity_metadata(identity)
                next_id = identity.get("next_generated_feature_id_after_release")
                if next_id is not None:
                    highest = max(
                        highest, model.validate_generated_sequence(next_id) - 1
                    )
                continue
            if kind == "allocation_artifact":
                reference = entry["allocation_evidence"]
                metadata_key = _object_key(reference)
                if metadata_key not in files:
                    raise SequenceAuditError(
                        "allocation artifact lacks generation-specific sidecar evidence"
                    )
                metadata_entry, _ = files[metadata_key]
                if (
                    metadata_entry["kind"] not in {"metadata", "legacy_metadata"}
                    or metadata_entry["release"] != entry["release"]
                    or reference.get("sha256") != metadata_entry["sha256"]
                ):
                    raise SequenceAuditError(
                        "allocation artifact mapping mismatches sidecar release/hash"
                    )
                if entry["mapping_evidence_sha256"] not in {
                    item["sha256"] for item in evidence
                }:
                    raise SequenceAuditError(
                        "allocation artifact lacks supplied mapping provenance evidence"
                    )
                # The sidecar supplies numeric allocations. Review must verify
                # conversion lineage for these exact bytes, including partials.
                continue
            if kind not in {"metadata", "legacy_metadata"}:
                raise SequenceAuditError(
                    "unresolved allocation reservation or unsupported artifact; sidecar/allocation evidence required"
                )
            if kind == "metadata":
                validation = model.validate_sidecar_records(
                    _sidecar_records(path),
                    expected_asset_slug=slug,
                    expected_release=entry.get("release"),
                )
                if not validation.valid:
                    raise SequenceAuditError("; ".join(validation.errors))
                records = _sidecar_records(path)
            else:
                records = _legacy_records(path, asset_slug=slug)
            release_ids: set[str] = set()
            for record in records:
                feature_id = record["feature_id"]
                if not isinstance(
                    feature_id, str
                ) or not model.GENERATED_FEATURE_ID_RE.fullmatch(feature_id):
                    raise SequenceAuditError(
                        "historical generated ID is not a canonical positive decimal"
                    )
                if feature_id in release_ids:
                    raise SequenceAuditError(
                        "duplicate ID in historical allocation object"
                    )
                release_ids.add(feature_id)
                key = model.identity_key_from_record(record)
                old_key = seen_ids.get(feature_id)
                if (
                    old_key is not None
                    and old_key != key
                    and (feature_id, old_key, key, entry.get("release"))
                    not in transitions
                ):
                    raise SequenceAuditError(
                        f"unexplained historical feature-ID reuse: {feature_id}: {old_key} -> {key}"
                    )
                seen_ids[feature_id] = key
                highest = max(highest, int(feature_id))
                counts["records"] += 1
        anchor = inventory.get("anchor", {})
        for role in ("latest_manifest", "release_manifest", "metadata"):
            if _object_key(anchor.get(role, {})) not in files:
                raise SequenceAuditError(
                    f"migration anchor {role} lacks exact generation-specific evidence; legacy conversion may be required"
                )
        for role in ("latest_manifest", "release_manifest", "metadata"):
            entry, _ = files[_object_key(anchor[role])]
            if any(
                anchor[role].get(field) != entry.get(field)
                for field in ("path", "generation", "sha256", "size")
            ):
                raise SequenceAuditError(
                    "anchor descriptor must bind exact evidence path/generation/hash/size"
                )
        latest_entry, latest_path = files[_object_key(anchor["latest_manifest"])]
        release_entry, release_path = files[_object_key(anchor["release_manifest"])]
        metadata_entry, _ = files[_object_key(anchor["metadata"])]
        latest = read_json(latest_path)
        if latest_path.read_bytes() != release_path.read_bytes():
            raise SequenceAuditError(
                "latest/release manifest anchors differ; reconcile publication before migration"
            )
        artifacts = model.validate_release_manifest(
            latest, expected_asset_slug=slug, require_generations=True
        )
        if (
            artifacts["metadata"]["path"],
            artifacts["metadata"].get("generation"),
        ) != _object_key(metadata_entry):
            raise SequenceAuditError("anchor metadata does not match bound manifest")
        if (
            str(artifacts["metadata"]["sha256"]).removeprefix("sha256:")
            != metadata_entry["sha256"]
        ):
            raise SequenceAuditError("anchor metadata hash does not match manifest")
        if artifacts["manifest"]["path"] != release_entry["path"]:
            raise SequenceAuditError(
                "release manifest anchor does not match manifest self-path"
            )
        expected_latest = root.rstrip("/") + f"/latest/{slug}.manifest.json"
        if latest_entry["path"] != expected_latest:
            raise SequenceAuditError("latest manifest anchor does not match asset")
        if metadata_entry.get("kind") != "metadata":
            raise SequenceAuditError(
                "legacy anchor requires a reviewed converted release bundle before manifest preparation"
            )
    except (KeyError, TypeError, ValueError, OSError) as exc:
        errors.append(str(exc))
    # Locations of local files and ordering do not change the reviewed evidence.
    canonical["objects"] = sorted(
        (_descriptor(item) for item in objects),
        key=lambda item: (item.get("path", ""), item.get("generation", 0)),
    )
    canonical["events"] = sorted(
        inventory.get("events", []),
        key=lambda item: (
            item.get("observed_at", ""),
            item.get("path", ""),
            item.get("generation", 0),
            item.get("operation", ""),
        ),
    )
    canonical["coverage"] = {
        **coverage,
        "evidence": sorted(
            (_descriptor(item) for item in coverage.get("evidence", [])),
            key=lambda item: item.get("sha256", ""),
        ),
    }
    canonical["decision_evidence"] = sorted(
        (_descriptor(item) for item in inventory.get("decision_evidence", [])),
        key=lambda item: item.get("sha256", ""),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "not_ready" if errors else "prepared_for_review",
        "asset_slug": slug,
        "next_feature_id": None if errors else highest + 1,
        "anchor": inventory.get("anchor", {}),
        "inventory_sha256": model.sha256_hex(json_bytes(canonical)),
        "evidence": canonical,
        "counts": counts,
        "errors": errors,
        "human_review_requirements": list(REVIEW_REQUIREMENTS),
        "limitation": "Supplied evidence was checked; authenticity and global historical completeness were not independently established. This candidate is not allocation authority.",
    }


def prepare_manifest(
    inventory: dict[str, Any], seed: dict[str, Any], *, directory: Path
) -> dict[str, Any]:
    verified = audit(inventory, directory=directory)
    if verified != seed or seed.get("status") != "prepared_for_review":
        raise SequenceAuditError(
            "seed is not an exact reproducible review candidate for this evidence"
        )
    anchor = seed["anchor"]
    descriptor = next(
        item
        for item in inventory["objects"]
        if _object_key(item) == _object_key(anchor["latest_manifest"])
    )
    manifest = read_json(directory / descriptor["local_path"])
    identity = manifest["identity"]
    if not str(identity.get("strategy", "")).startswith("generated_sequence"):
        raise SequenceAuditError("migration requires a generated sequence identity")
    identity.update(
        {
            "sequence_state_version": model.GENERATED_SEQUENCE_STATE_VERSION,
            "next_generated_feature_id_before_release": seed["next_feature_id"],
            "next_generated_feature_id_after_release": seed["next_feature_id"],
            "sequence_migration": {
                "seed_sha256": model.sha256_hex(json_bytes(seed)),
                "anchor": anchor,
            },
        }
    )
    model.validate_release_manifest(
        manifest, expected_asset_slug=seed["asset_slug"], require_generations=True
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "prepared_for_review",
        "candidate_manifest": manifest,
        "candidate_sha256": model.sha256_hex(json_bytes(manifest)),
        "destination_preconditions": [
            anchor["release_manifest"],
            anchor["latest_manifest"],
        ],
        "human_review_requirements": list(REVIEW_REQUIREMENTS),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("audit", "prepare-manifest"))
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--seed", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        inventory = _normalized_inventory(read_json(args.inventory))
    except (KeyError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    directory = args.inventory.resolve().parent
    protected = [
        args.inventory,
        *(directory / item["local_path"] for item in inventory.get("objects", [])),
        *(
            directory / item["local_path"]
            for item in inventory.get("coverage", {}).get("evidence", [])
        ),
        *(
            directory / item["local_path"]
            for item in inventory.get("decision_evidence", [])
        ),
    ]
    if args.seed:
        protected.append(args.seed)
    if any(
        args.output.resolve() == path.resolve()
        or (args.output.exists() and path.exists() and args.output.samefile(path))
        for path in protected
    ):
        parser.error("output must not alias input evidence")
    if args.command == "prepare-manifest":
        if args.seed is None:
            parser.error("prepare-manifest requires --seed")
        result = prepare_manifest(inventory, read_json(args.seed), directory=directory)
    else:
        result = audit(inventory, directory=directory)
    args.output.write_bytes(json_bytes(result))
    return 2 if result["status"] == "not_ready" else 0


if __name__ == "__main__":
    raise SystemExit(main())
