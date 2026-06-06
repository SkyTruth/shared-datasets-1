"""Release-oriented vector feature model helpers.

These helpers keep feature identity, content hashes, metadata sidecars, and
release manifests explicit before any artifact is published to Cloud Storage.
They are intentionally local/serialization primitives; callers remain
responsible for source-specific normalization and GDAL artifact generation.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


RELEASE_FEATURE_MODEL_SCHEMA_VERSION = 1
RELEASE_MANIFEST_SCHEMA_VERSION = 1
METADATA_SIDECAR_SCHEMA_VERSION = 1
RELEASE_SCHEMA_SCHEMA_VERSION = 1
FEATURE_ID_ALGORITHM = "shared-datasets-feature-id:v1"
FEATURE_HASH_ALGORITHM = "sha256:canonical-feature-content:v1"
MAX_FIRESTORE_DOCUMENT_BYTES = 1_048_576
DEFAULT_MAX_SIDECAR_RECORD_BYTES = 900 * 1024
FEATURE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
EXT_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
FEATURE_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
REQUIRED_VECTOR_ARTIFACT_ROLES = ("fgb", "pmtiles", "metadata", "schema", "manifest")
ARTIFACT_HASH_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


class ReleaseFeatureModelError(ValueError):
    """Raised when release feature model payloads are invalid."""


@dataclass(frozen=True)
class FeatureRecord:
    feature_id: str
    feature_hash: str
    geometry: Mapping[str, Any] | None
    properties: Mapping[str, Any]
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class SidecarRecord:
    schema_version: int
    asset_slug: str
    release: str
    feature_id: str
    feature_hash: str
    properties: Mapping[str, Any]
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    feature_count: int
    duplicate_feature_ids: tuple[str, ...]
    oversized_feature_ids: tuple[str, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ReleaseSchemaField:
    name: str
    type: str
    nullable: bool = True
    projectable: bool = True


def canonical_json(value: Any) -> str:
    """Serialize a value into stable, compact JSON for hashing and manifests."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def normalize_feature_id_token(value: Any) -> str:
    """Normalize a provider/composite token into an API-safe feature-id fragment."""
    token = str(value).strip()
    if not token:
        raise ReleaseFeatureModelError("feature ID token must be non-empty")
    token = re.sub(r"\s+", "-", token)
    token = re.sub(r"[^A-Za-z0-9._:-]+", "-", token).strip("-")
    if not token:
        raise ReleaseFeatureModelError("feature ID token contains no usable characters")
    return token[:256]


def provider_feature_id(*, source_field: str, source_value: Any) -> str:
    """Build a stable feature_id from a verified provider/source identifier."""
    field = normalize_feature_id_token(source_field)
    value = normalize_feature_id_token(source_value)
    feature_id = f"src:{field}:{value}"
    validate_feature_id(feature_id)
    return feature_id


def composite_provider_feature_id(source_values: Mapping[str, Any]) -> str:
    """Build a stable feature_id from a curator-approved composite provider key."""
    if not source_values:
        raise ReleaseFeatureModelError("composite provider feature ID requires at least one field")
    normalized = {str(key): str(value).strip() for key, value in sorted(source_values.items())}
    if any(not key or not value for key, value in normalized.items()):
        raise ReleaseFeatureModelError("composite provider feature ID fields and values must be non-empty")
    digest = sha256_hex(canonical_json(normalized))[:24]
    feature_id = f"src:composite:{digest}"
    validate_feature_id(feature_id)
    return feature_id


def generated_feature_id(*, asset_slug: str, preimage: Mapping[str, Any], token_length: int = 24) -> str:
    """Build a curator-approved generated per-feature ID.

    The caller chooses the preimage after the provider-ID decision point. This
    function deliberately does not infer fields.
    """
    if token_length < 16:
        raise ReleaseFeatureModelError("generated feature ID token length must be at least 16")
    if not preimage:
        raise ReleaseFeatureModelError("generated feature ID preimage must be non-empty")
    digest = sha256_hex(canonical_json({"asset_slug": asset_slug, "preimage": preimage}))[:token_length]
    feature_id = f"gen:{digest}"
    validate_feature_id(feature_id)
    return feature_id


def content_feature_hash(
    *,
    geometry: Mapping[str, Any] | None,
    properties: Mapping[str, Any],
    exclude_properties: Sequence[str] = (),
) -> str:
    """Hash normalized geometry and nonvolatile published properties."""
    excluded = set(exclude_properties)
    content = {
        "geometry": geometry,
        "properties": {key: properties[key] for key in sorted(properties) if key not in excluded},
    }
    return "sha256:" + sha256_hex(canonical_json(content))


def validate_feature_id(feature_id: str) -> None:
    if not FEATURE_ID_RE.fullmatch(feature_id):
        raise ReleaseFeatureModelError(
            "feature_id must be 1-256 chars and contain only letters, numbers, dot, underscore, colon, or dash"
        )


def validate_ext_id(ext_id: str) -> None:
    if not EXT_ID_RE.fullmatch(ext_id):
        raise ReleaseFeatureModelError("ext_id must be 1-64 URL-safe alphanumeric characters")


def validate_feature_hash(feature_hash: str) -> None:
    if not FEATURE_HASH_RE.fullmatch(feature_hash):
        raise ReleaseFeatureModelError("feature_hash must be sha256: followed by 64 lowercase hex characters")


def ext_id_from_record(record: Mapping[str, Any]) -> str:
    properties = record.get("properties") if isinstance(record.get("properties"), Mapping) else {}
    return str(record.get("ext_id") or properties.get("ext_id") or "").strip()


def ext_id_mapping_from_records(records: Iterable[SidecarRecord | Mapping[str, Any]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    seen_ext_ids: dict[str, str] = {}
    for index, record in enumerate(records, start=1):
        payload = asdict(record) if isinstance(record, SidecarRecord) else dict(record)
        feature_id = str(payload.get("feature_id") or "").strip()
        ext_id = ext_id_from_record(payload)
        try:
            validate_feature_id(feature_id)
            validate_ext_id(ext_id)
        except ReleaseFeatureModelError as exc:
            raise ReleaseFeatureModelError(f"invalid ext_id mapping at record {index}: {exc}") from exc
        if feature_id in mapping:
            raise ReleaseFeatureModelError(f"duplicate feature_id in ext_id mapping: {feature_id}")
        if ext_id in seen_ext_ids:
            raise ReleaseFeatureModelError(
                f"duplicate ext_id in ext_id mapping: {ext_id} for {seen_ext_ids[ext_id]} and {feature_id}"
            )
        mapping[feature_id] = ext_id
        seen_ext_ids[ext_id] = feature_id
    return mapping


def assign_sequence_ext_ids(
    feature_ids: Iterable[str],
    *,
    previous_records: Iterable[SidecarRecord | Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    previous = ext_id_mapping_from_records(previous_records or ())
    assigned: dict[str, str] = {}
    used_ext_ids = set(previous.values())
    numeric_ext_ids = [int(ext_id) for ext_id in used_ext_ids if ext_id.isdigit()]
    next_sequence = max(numeric_ext_ids, default=0) + 1
    for feature_id in feature_ids:
        feature_id = str(feature_id).strip()
        validate_feature_id(feature_id)
        if feature_id in assigned:
            raise ReleaseFeatureModelError(f"duplicate feature_id while assigning ext_id: {feature_id}")
        if feature_id in previous:
            assigned[feature_id] = previous[feature_id]
            continue
        while str(next_sequence) in used_ext_ids:
            next_sequence += 1
        ext_id = str(next_sequence)
        validate_ext_id(ext_id)
        assigned[feature_id] = ext_id
        used_ext_ids.add(ext_id)
        next_sequence += 1
    return assigned


def sidecar_record(
    *,
    asset_slug: str,
    release: str,
    feature: FeatureRecord,
) -> SidecarRecord:
    validate_feature_id(feature.feature_id)
    validate_feature_hash(feature.feature_hash)
    validate_ext_id(ext_id_from_record({"properties": feature.properties}))
    return SidecarRecord(
        schema_version=METADATA_SIDECAR_SCHEMA_VERSION,
        asset_slug=asset_slug,
        release=release,
        feature_id=feature.feature_id,
        feature_hash=feature.feature_hash,
        properties=dict(feature.properties),
        provenance=dict(feature.provenance),
    )


def sidecar_record_bytes(record: SidecarRecord | Mapping[str, Any]) -> bytes:
    payload = asdict(record) if isinstance(record, SidecarRecord) else dict(record)
    return (canonical_json(payload) + "\n").encode("utf-8")


def validate_sidecar_records(
    records: Iterable[SidecarRecord | Mapping[str, Any]],
    *,
    expected_asset_slug: str | None = None,
    expected_release: str | None = None,
    max_record_bytes: int = DEFAULT_MAX_SIDECAR_RECORD_BYTES,
) -> ValidationResult:
    seen: set[str] = set()
    duplicates: set[str] = set()
    seen_ext_ids: dict[str, str] = {}
    duplicate_ext_ids: set[str] = set()
    oversized: list[str] = []
    errors: list[str] = []
    count = 0
    for record in records:
        payload = asdict(record) if isinstance(record, SidecarRecord) else dict(record)
        count += 1
        feature_id = str(payload.get("feature_id") or "")
        feature_hash = str(payload.get("feature_hash") or "")
        ext_id = ext_id_from_record(payload)
        try:
            validate_feature_id(feature_id)
        except ReleaseFeatureModelError as exc:
            errors.append(f"invalid feature_id at record {count}: {exc}")
        try:
            validate_feature_hash(feature_hash)
        except ReleaseFeatureModelError as exc:
            errors.append(f"invalid feature_hash at record {count}: {exc}")
        try:
            validate_ext_id(ext_id)
        except ReleaseFeatureModelError as exc:
            errors.append(f"invalid ext_id at record {count}: {exc}")
        if feature_id in seen:
            duplicates.add(feature_id)
        seen.add(feature_id)
        if ext_id in seen_ext_ids:
            duplicate_ext_ids.add(ext_id)
        elif ext_id:
            seen_ext_ids[ext_id] = feature_id
        if len(sidecar_record_bytes(payload)) > max_record_bytes:
            oversized.append(feature_id or f"record-{count}")
        if payload.get("schema_version") != METADATA_SIDECAR_SCHEMA_VERSION:
            errors.append(f"record {count} has unsupported schema_version")
        if expected_asset_slug is not None and payload.get("asset_slug") != expected_asset_slug:
            errors.append(f"record {count} asset_slug does not match {expected_asset_slug!r}")
        if expected_release is not None and payload.get("release") != expected_release:
            errors.append(f"record {count} release does not match {expected_release!r}")
        if not isinstance(payload.get("properties"), Mapping):
            errors.append(f"record {count} properties must be an object")
        if not isinstance(payload.get("provenance"), Mapping):
            errors.append(f"record {count} provenance must be an object")
    if duplicates:
        errors.append("duplicate feature_id values: " + ", ".join(sorted(duplicates)))
    if duplicate_ext_ids:
        errors.append("duplicate ext_id values: " + ", ".join(sorted(duplicate_ext_ids)))
    if oversized:
        errors.append(
            "metadata sidecar record(s) exceed the configured serving document size: "
            + ", ".join(sorted(oversized))
        )
    return ValidationResult(
        valid=not errors,
        feature_count=count,
        duplicate_feature_ids=tuple(sorted(duplicates)),
        oversized_feature_ids=tuple(sorted(oversized)),
        errors=tuple(errors),
    )


def write_metadata_sidecar(records: Iterable[SidecarRecord | Mapping[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as gzip_file:
            with io.TextIOWrapper(gzip_file, encoding="utf-8", newline="\n") as handle:
                for record in records:
                    payload = asdict(record) if isinstance(record, SidecarRecord) else dict(record)
                    handle.write(canonical_json(payload) + "\n")


def read_metadata_sidecar(path: Path) -> Iterator[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ReleaseFeatureModelError(f"{path}:{line_number}: invalid JSON") from exc
            if not isinstance(payload, dict):
                raise ReleaseFeatureModelError(f"{path}:{line_number}: sidecar row must be a JSON object")
            yield payload


def read_metadata_sidecar_bytes(payload: bytes, *, label: str = "metadata sidecar") -> Iterator[dict[str, Any]]:
    with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as gzip_file:
        with io.TextIOWrapper(gzip_file, encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ReleaseFeatureModelError(f"{label}:{line_number}: invalid JSON") from exc
                if not isinstance(row, dict):
                    raise ReleaseFeatureModelError(f"{label}:{line_number}: sidecar row must be a JSON object")
                yield row


def release_artifact_name(asset_slug: str, role: str) -> str:
    suffixes = {
        "fgb": ".fgb",
        "pmtiles": ".pmtiles",
        "metadata": ".metadata.ndjson.gz",
        "schema": ".schema.json",
        "manifest": ".manifest.json",
    }
    suffix = suffixes.get(role)
    if suffix is None:
        raise ReleaseFeatureModelError(f"unsupported release artifact role: {role}")
    return f"{asset_slug}{suffix}"


def build_release_schema(
    *,
    asset_slug: str,
    release: str,
    fields: Sequence[ReleaseSchemaField | Mapping[str, Any]],
) -> dict[str, Any]:
    """Build the metadata sidecar projection schema used by the serving API."""
    return {
        "schema_version": RELEASE_SCHEMA_SCHEMA_VERSION,
        "asset_slug": asset_slug,
        "release": release,
        "fields": [
            asdict(field) if isinstance(field, ReleaseSchemaField) else dict(field)
            for field in fields
        ],
    }


def validate_release_schema(
    schema: Mapping[str, Any],
    *,
    expected_asset_slug: str | None = None,
    expected_release: str | None = None,
) -> dict[str, ReleaseSchemaField]:
    """Validate a release schema and return projectable fields by name."""
    errors: list[str] = []
    if schema.get("schema_version") != RELEASE_SCHEMA_SCHEMA_VERSION:
        errors.append("schema has unsupported schema_version")
    if expected_asset_slug is not None and schema.get("asset_slug") != expected_asset_slug:
        errors.append(f"schema asset_slug does not match {expected_asset_slug!r}")
    if expected_release is not None and schema.get("release") != expected_release:
        errors.append(f"schema release does not match {expected_release!r}")
    raw_fields = schema.get("fields")
    if not isinstance(raw_fields, Sequence) or isinstance(raw_fields, (str, bytes, bytearray)):
        errors.append("schema fields must be an array")
        raw_fields = []
    fields: dict[str, ReleaseSchemaField] = {}
    for index, raw_field in enumerate(raw_fields, start=1):
        if not isinstance(raw_field, Mapping):
            errors.append(f"schema fields[{index}] must be an object")
            continue
        name = raw_field.get("name")
        field_type = raw_field.get("type")
        if not isinstance(name, str) or not name:
            errors.append(f"schema fields[{index}].name must be a non-empty string")
            continue
        if name in fields:
            errors.append(f"schema has duplicate field name: {name}")
            continue
        if not isinstance(field_type, str) or not field_type:
            errors.append(f"schema field {name!r} type must be a non-empty string")
            continue
        nullable = raw_field.get("nullable", True)
        projectable = raw_field.get("projectable", True)
        if not isinstance(nullable, bool):
            errors.append(f"schema field {name!r} nullable must be boolean")
            continue
        if not isinstance(projectable, bool):
            errors.append(f"schema field {name!r} projectable must be boolean")
            continue
        if projectable:
            fields[name] = ReleaseSchemaField(
                name=name,
                type=field_type,
                nullable=nullable,
                projectable=projectable,
            )
    if errors:
        raise ReleaseFeatureModelError("; ".join(errors))
    return fields


def validate_artifact_hash(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not ARTIFACT_HASH_RE.fullmatch(value):
        raise ReleaseFeatureModelError(f"{label} sha256 must be 64 lowercase hex characters, optionally prefixed by sha256:")
    return value.split(":", 1)[1] if value.startswith("sha256:") else value


def validate_release_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_asset_slug: str | None = None,
    expected_release: str | None = None,
    require_generations: bool = False,
) -> dict[str, Mapping[str, Any]]:
    """Validate the durable release manifest and return artifact entries by role."""
    errors: list[str] = []
    if manifest.get("schema_version") != RELEASE_MANIFEST_SCHEMA_VERSION:
        errors.append("manifest has unsupported schema_version")
    if expected_asset_slug is not None and manifest.get("asset_slug") != expected_asset_slug:
        errors.append(f"manifest asset_slug does not match {expected_asset_slug!r}")
    if expected_release is not None and manifest.get("release") != expected_release:
        errors.append(f"manifest release does not match {expected_release!r}")
    if manifest.get("release_feature_model_schema_version") != RELEASE_FEATURE_MODEL_SCHEMA_VERSION:
        errors.append("manifest release_feature_model_schema_version is unsupported")
    if manifest.get("feature_hash_algorithm") != FEATURE_HASH_ALGORITHM:
        errors.append("manifest feature_hash_algorithm is unsupported")
    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, Sequence) or isinstance(raw_artifacts, (str, bytes, bytearray)):
        errors.append("manifest artifacts must be an array")
        raw_artifacts = []
    artifacts_by_role: dict[str, Mapping[str, Any]] = {}
    for index, artifact in enumerate(raw_artifacts, start=1):
        if not isinstance(artifact, Mapping):
            errors.append(f"manifest artifacts[{index}] must be an object")
            continue
        role = str(artifact.get("role") or artifact.get("format") or "")
        if not role:
            errors.append(f"manifest artifacts[{index}] is missing role")
            continue
        if role in artifacts_by_role:
            errors.append(f"manifest has duplicate artifact role: {role}")
            continue
        artifacts_by_role[role] = artifact
        path = artifact.get("path")
        if not isinstance(path, str) or not path.startswith("gs://"):
            errors.append(f"manifest artifact {role!r} path must be a gs:// URI")
        if role != "manifest":
            try:
                validate_artifact_hash(artifact.get("sha256"), label=f"manifest artifact {role!r}")
            except ReleaseFeatureModelError as exc:
                errors.append(str(exc))
        generation = artifact.get("generation")
        if role == "manifest" and generation is not None:
            errors.append("manifest artifact must not embed its own object generation")
        elif require_generations and role != "manifest" and not isinstance(generation, int):
            errors.append(f"manifest artifact {role!r} must include destination generation")
    missing = [role for role in REQUIRED_VECTOR_ARTIFACT_ROLES if role not in artifacts_by_role]
    if missing:
        errors.append("manifest is missing required vector artifact role(s): " + ", ".join(missing))
    policy = manifest.get("index_status_policy")
    if not isinstance(policy, Mapping) or policy.get("mode") != "external_index_load_records":
        errors.append("manifest index_status_policy must point to external index-load records")
    try:
        validate_release_schema(
            manifest.get("schema") if isinstance(manifest.get("schema"), Mapping) else {},
            expected_asset_slug=expected_asset_slug,
            expected_release=expected_release,
        )
    except ReleaseFeatureModelError as exc:
        errors.append(f"manifest schema is invalid: {exc}")
    if errors:
        raise ReleaseFeatureModelError("; ".join(errors))
    return artifacts_by_role


def build_release_manifest(
    *,
    asset_slug: str,
    release: str,
    source_inputs: Sequence[Mapping[str, Any]],
    artifacts: Sequence[Mapping[str, Any]],
    schema: Mapping[str, Any],
    id_strategy: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": RELEASE_MANIFEST_SCHEMA_VERSION,
        "asset_slug": asset_slug,
        "release": release,
        "release_feature_model_schema_version": RELEASE_FEATURE_MODEL_SCHEMA_VERSION,
        "source_inputs": list(source_inputs),
        "artifacts": list(artifacts),
        "schema": dict(schema),
        "id_strategy": dict(id_strategy),
        "feature_hash_algorithm": FEATURE_HASH_ALGORITHM,
        "validation": dict(validation),
        "index_load_status": "tracked in index-loads/",
        "index_status_policy": {
            "mode": "external_index_load_records",
            "path": f"index-loads/{release}/",
        },
    }


def index_load_record_name(asset_root: str, release: str, load_id: str) -> str:
    safe_load_id = normalize_feature_id_token(load_id)
    return f"{asset_root.rstrip('/')}/index-loads/{release}/{safe_load_id}.json"
