#!/usr/bin/env python3
"""Validate a local release metadata bundle without credentials or remote writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import release_feature_model as model


class MetadataValidationError(ValueError):
    """The local bundle does not satisfy the release metadata contract."""


@dataclass(frozen=True)
class ValidationResult:
    asset_slug: str
    release: str
    feature_count: int


def read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise MetadataValidationError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise MetadataValidationError(f"{label} must be a JSON object: {path}")
    return payload


def validate_artifact_generation(
    artifacts: Mapping[str, Mapping[str, Any]],
    *,
    role: str,
    uri: str | None,
    generation: int | None,
) -> None:
    artifact = artifacts[role]
    if uri is not None and artifact.get("path") != uri:
        raise MetadataValidationError(f"manifest {role} path does not match {uri}")
    # A manifest cannot embed its own generation. Its expected generation is
    # enforced when downloading it, before this local validation boundary.
    if (
        role != "manifest"
        and generation is not None
        and artifact.get("generation") != generation
    ):
        raise MetadataValidationError(
            f"manifest {role} generation does not match {generation}"
        )


def validate_sidecar_schema_projection(
    *, sidecar_path: Path, schema_fields: Mapping[str, Any]
) -> None:
    allowed = set(schema_fields)
    for line_number, record in enumerate(
        model.read_metadata_sidecar(sidecar_path), start=1
    ):
        extra = sorted(set(record["properties"]) - allowed)
        if extra:
            raise MetadataValidationError(
                f"record {line_number} has properties outside the release schema: {', '.join(extra)}"
            )


def validate_bundle(
    *,
    sidecar_path: Path,
    schema_path: Path,
    manifest_path: Path,
    asset_slug: str,
    release: str,
    sidecar_uri: str | None = None,
    sidecar_generation: int | None = None,
    schema_uri: str | None = None,
    schema_generation: int | None = None,
    manifest_uri: str | None = None,
) -> ValidationResult:
    schema = read_json_object(schema_path, label="release schema")
    manifest = read_json_object(manifest_path, label="release manifest")
    fields = model.validate_release_schema(
        schema, expected_asset_slug=asset_slug, expected_release=release
    )
    artifacts = model.validate_release_manifest(
        manifest,
        expected_asset_slug=asset_slug,
        expected_release=release,
        require_generations=True,
    )
    for role, path, uri, generation in (
        ("metadata", sidecar_path, sidecar_uri, sidecar_generation),
        ("schema", schema_path, schema_uri, schema_generation),
        ("manifest", manifest_path, manifest_uri, None),
    ):
        validate_artifact_generation(
            artifacts, role=role, uri=uri, generation=generation
        )
        if role != "manifest":
            expected_hash = model.validate_artifact_hash(
                artifacts[role]["sha256"], label=role
            )
            with path.open("rb") as handle:
                actual_hash = hashlib.file_digest(handle, "sha256").hexdigest()
            if actual_hash != expected_hash:
                raise MetadataValidationError(
                    f"manifest {role} checksum does not match local bytes"
                )
    validation = model.validate_sidecar_records(
        model.read_metadata_sidecar(sidecar_path),
        expected_asset_slug=asset_slug,
        expected_release=release,
    )
    if not validation.valid:
        raise MetadataValidationError("; ".join(validation.errors))
    if validation.feature_count <= 0:
        raise MetadataValidationError(
            "metadata sidecar must contain at least one record"
        )
    assertions = manifest.get("validation")
    if not isinstance(assertions, Mapping):
        raise MetadataValidationError("manifest validation must be an object")
    declared_count = assertions.get("feature_count")
    if declared_count is not None and declared_count != validation.feature_count:
        raise MetadataValidationError(
            "manifest feature_count does not match metadata sidecar"
        )
    validate_sidecar_schema_projection(sidecar_path=sidecar_path, schema_fields=fields)
    return ValidationResult(asset_slug, release, validation.feature_count)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("sidecar", "schema", "manifest"):
        parser.add_argument(f"--{name}", required=True, type=Path)
        parser.add_argument(f"--{name}-uri")
    parser.add_argument("--sidecar-generation", type=int)
    parser.add_argument("--schema-generation", type=int)
    parser.add_argument("--asset-slug", required=True)
    parser.add_argument("--release", required=True)
    args = parser.parse_args(argv)
    try:
        result = validate_bundle(
            sidecar_path=args.sidecar,
            schema_path=args.schema,
            manifest_path=args.manifest,
            asset_slug=args.asset_slug,
            release=args.release,
            sidecar_uri=args.sidecar_uri,
            sidecar_generation=args.sidecar_generation,
            schema_uri=args.schema_uri,
            schema_generation=args.schema_generation,
            manifest_uri=args.manifest_uri,
        )
    except (MetadataValidationError, model.ReleaseFeatureModelError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
