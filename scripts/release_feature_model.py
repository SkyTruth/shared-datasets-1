"""Release-oriented vector feature model helpers.

The release feature model has one addressable feature identity:
``feature_id``. Source-backed IDs are used directly when a source field is
unique, nonblank, index-like, and URL-friendly. Otherwise shared-datasets
assigns monotonic decimal-string IDs from a persisted release mapping.
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


RELEASE_FEATURE_MODEL_SCHEMA_VERSION = 2
RELEASE_MANIFEST_SCHEMA_VERSION = 2
METADATA_SIDECAR_SCHEMA_VERSION = 2
RELEASE_SCHEMA_SCHEMA_VERSION = 2
FEATURE_IDENTITY_SCHEMA_VERSION = 1
FEATURE_ID_ALGORITHM = "shared-datasets-feature-identity:v2"
HASH_ALGORITHM = "sha256"
GEOMETRY_HASH_ALGORITHM = "sha256:canonical-geometry:v1"
PROPERTIES_HASH_ALGORITHM = "sha256:canonical-feature-properties:v1"
FEATURE_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")
SHA256_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ARTIFACT_HASH_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
REQUIRED_VECTOR_ARTIFACT_ROLES = ("fgb", "pmtiles", "metadata", "schema", "manifest")
MAX_FIRESTORE_DOCUMENT_BYTES = 1_048_576
DEFAULT_MAX_SIDECAR_RECORD_BYTES = 900 * 1024
HASH_EXCLUDED_PROPERTIES = frozenset(
    {
        "feature_id",
        "geometry_hash",
        "properties_hash",
        "provenance",
        "run_id",
        "run_timestamp",
        "updated_at",
        "created_at",
    }
)


class ReleaseFeatureModelError(ValueError):
    """Raised when release feature model payloads are invalid."""


@dataclass(frozen=True)
class FeatureRecord:
    feature_id: str
    geometry_hash: str
    properties_hash: str
    geometry: Mapping[str, Any] | None
    properties: Mapping[str, Any]
    provenance: Mapping[str, Any]


@dataclass(frozen=True)
class SidecarRecord:
    schema_version: int
    asset_slug: str
    release: str
    feature_id: str
    geometry_hash: str
    properties_hash: str
    properties: Mapping[str, Any]
    provenance: Mapping[str, Any]
    identity_key: Sequence[str] | None = None


@dataclass(frozen=True)
class IdentityAmbiguity:
    identity_key: tuple[str, ...]
    geometry_hash: str
    properties_hash: str
    matching_geometry_feature_ids: tuple[str, ...]
    matching_properties_feature_ids: tuple[str, ...]


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    feature_count: int
    duplicate_feature_ids: tuple[str, ...]
    duplicate_identity_keys: tuple[tuple[str, ...], ...]
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


def normalize_number(value: int | float) -> int | float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if value == 0:
        return 0
    return float(format(value, ".15g"))


def normalize_geometry(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): normalize_geometry(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [normalize_geometry(item) for item in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return normalize_number(value)
    return value


def geometry_hash(geometry: Mapping[str, Any] | None) -> str:
    return "sha256:" + sha256_hex(canonical_json(normalize_geometry(geometry)))


def properties_hash(properties: Mapping[str, Any], *, exclude_properties: Sequence[str] = ()) -> str:
    excluded = HASH_EXCLUDED_PROPERTIES | set(exclude_properties)
    payload = {key: properties[key] for key in sorted(properties) if key not in excluded}
    return "sha256:" + sha256_hex(canonical_json(payload))


def validate_feature_id(feature_id: str) -> None:
    if not FEATURE_ID_RE.fullmatch(feature_id):
        raise ReleaseFeatureModelError("feature_id must be 1-64 alphanumeric characters")


def validate_hash(value: str, *, label: str) -> None:
    if not SHA256_HASH_RE.fullmatch(value):
        raise ReleaseFeatureModelError(f"{label} must be sha256: followed by 64 lowercase hex characters")


def source_field_feature_id(*, source_field: str, source_value: Any) -> str:
    """Return a feature ID copied directly from a source field value."""
    if source_field is None or not str(source_field).strip():
        raise ReleaseFeatureModelError("source field must be non-empty")
    if source_value is None:
        raise ReleaseFeatureModelError("feature_id must be 1-64 alphanumeric characters")
    feature_id = str(source_value).strip()
    validate_feature_id(feature_id)
    return feature_id


def source_fields_identity_key(properties: Mapping[str, Any], source_fields: Sequence[str]) -> tuple[str, ...]:
    fields = tuple(str(field).strip() for field in source_fields if str(field).strip())
    if not fields:
        raise ReleaseFeatureModelError("source-field identity requires at least one source field")
    if len(fields) > 2:
        raise ReleaseFeatureModelError("source-field identity accepts at most two source fields")
    values: list[str] = []
    for field in fields:
        raw_value = properties.get(field)
        value = "" if raw_value is None else str(raw_value).strip()
        if not value:
            raise ReleaseFeatureModelError(f"source identity field {field!r} is blank")
        values.append(value)
    return tuple(values)


def content_identity_key(*, geometry_hash_value: str, properties_hash_value: str) -> tuple[str, str]:
    validate_hash(geometry_hash_value, label="geometry_hash")
    validate_hash(properties_hash_value, label="properties_hash")
    return (geometry_hash_value, properties_hash_value)


def identity_key_from_record(record: Mapping[str, Any]) -> tuple[str, ...]:
    raw_key = record.get("identity_key")
    if isinstance(raw_key, Sequence) and not isinstance(raw_key, (str, bytes, bytearray)):
        key = tuple(str(part) for part in raw_key)
        if key:
            return key
    geometry_hash_value = str(record.get("geometry_hash") or "")
    properties_hash_value = str(record.get("properties_hash") or "")
    if geometry_hash_value and properties_hash_value:
        return content_identity_key(
            geometry_hash_value=geometry_hash_value,
            properties_hash_value=properties_hash_value,
        )
    feature_id = str(record.get("feature_id") or "")
    if feature_id:
        return (feature_id,)
    raise ReleaseFeatureModelError("record does not contain an identity key")


def previous_feature_id_mapping(records: Iterable[SidecarRecord | Mapping[str, Any]]) -> dict[tuple[str, ...], str]:
    mapping: dict[tuple[str, ...], str] = {}
    for index, record in enumerate(records, start=1):
        payload = asdict(record) if isinstance(record, SidecarRecord) else dict(record)
        feature_id = str(payload.get("feature_id") or "").strip()
        validate_feature_id(feature_id)
        key = identity_key_from_record(payload)
        if key in mapping and mapping[key] != feature_id:
            raise ReleaseFeatureModelError(f"duplicate previous identity key at record {index}: {key}")
        mapping[key] = feature_id
    return mapping


def assign_generated_feature_ids(
    identity_keys: Iterable[Sequence[str]],
    *,
    previous_records: Iterable[SidecarRecord | Mapping[str, Any]] | None = None,
) -> dict[tuple[str, ...], str]:
    """Assign monotonic decimal feature IDs while preserving prior mappings."""
    previous = previous_feature_id_mapping(previous_records or ())
    assigned: dict[tuple[str, ...], str] = {}
    used_feature_ids = set(previous.values())
    numeric_feature_ids = [int(feature_id) for feature_id in used_feature_ids if feature_id.isdigit()]
    next_sequence = max(numeric_feature_ids, default=0) + 1
    for raw_key in identity_keys:
        key = tuple(str(part) for part in raw_key)
        if not key:
            raise ReleaseFeatureModelError("generated feature ID requires a non-empty identity key")
        if key in assigned:
            raise ReleaseFeatureModelError(f"duplicate identity key while assigning feature_id: {key}")
        if key in previous:
            assigned[key] = previous[key]
            continue
        while str(next_sequence) in used_feature_ids:
            next_sequence += 1
        feature_id = str(next_sequence)
        validate_feature_id(feature_id)
        assigned[key] = feature_id
        used_feature_ids.add(feature_id)
        next_sequence += 1
    return assigned


def find_identity_ambiguities(
    new_records: Iterable[Mapping[str, Any]],
    *,
    previous_records: Iterable[SidecarRecord | Mapping[str, Any]],
) -> tuple[IdentityAmbiguity, ...]:
    """Find partial hash matches that require maintainer resolution."""
    by_geometry: dict[str, list[str]] = {}
    by_properties: dict[str, list[str]] = {}
    for record in previous_records:
        payload = asdict(record) if isinstance(record, SidecarRecord) else dict(record)
        feature_id = str(payload.get("feature_id") or "").strip()
        geometry_hash_value = str(payload.get("geometry_hash") or "").strip()
        properties_hash_value = str(payload.get("properties_hash") or "").strip()
        if feature_id and geometry_hash_value:
            by_geometry.setdefault(geometry_hash_value, []).append(feature_id)
        if feature_id and properties_hash_value:
            by_properties.setdefault(properties_hash_value, []).append(feature_id)

    ambiguities: list[IdentityAmbiguity] = []
    for record in new_records:
        geometry_hash_value = str(record.get("geometry_hash") or "").strip()
        properties_hash_value = str(record.get("properties_hash") or "").strip()
        geometry_matches = tuple(sorted(set(by_geometry.get(geometry_hash_value, ()))))
        properties_matches = tuple(sorted(set(by_properties.get(properties_hash_value, ()))))
        if not geometry_matches and not properties_matches:
            continue
        if geometry_matches == properties_matches and len(geometry_matches) == 1:
            continue
        ambiguities.append(
            IdentityAmbiguity(
                identity_key=identity_key_from_record(record),
                geometry_hash=geometry_hash_value,
                properties_hash=properties_hash_value,
                matching_geometry_feature_ids=geometry_matches,
                matching_properties_feature_ids=properties_matches,
            )
        )
    return tuple(ambiguities)


def content_hashes(
    *,
    geometry: Mapping[str, Any] | None,
    properties: Mapping[str, Any],
    exclude_properties: Sequence[str] = (),
) -> tuple[str, str]:
    return (
        geometry_hash(geometry),
        properties_hash(properties, exclude_properties=exclude_properties),
    )


def sidecar_record(
    *,
    asset_slug: str,
    release: str,
    feature: FeatureRecord,
    identity_key: Sequence[str] | None = None,
) -> SidecarRecord:
    validate_feature_id(feature.feature_id)
    validate_hash(feature.geometry_hash, label="geometry_hash")
    validate_hash(feature.properties_hash, label="properties_hash")
    return SidecarRecord(
        schema_version=METADATA_SIDECAR_SCHEMA_VERSION,
        asset_slug=asset_slug,
        release=release,
        feature_id=feature.feature_id,
        geometry_hash=feature.geometry_hash,
        properties_hash=feature.properties_hash,
        properties=dict(feature.properties),
        provenance=dict(feature.provenance),
        identity_key=tuple(identity_key) if identity_key else None,
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
    identity_keys: dict[tuple[str, ...], str] = {}
    duplicate_identity_keys: set[tuple[str, ...]] = set()
    oversized: list[str] = []
    errors: list[str] = []
    count = 0
    for record in records:
        payload = asdict(record) if isinstance(record, SidecarRecord) else dict(record)
        count += 1
        feature_id = str(payload.get("feature_id") or "")
        geometry_hash_value = str(payload.get("geometry_hash") or "")
        properties_hash_value = str(payload.get("properties_hash") or "")
        try:
            validate_feature_id(feature_id)
        except ReleaseFeatureModelError as exc:
            errors.append(f"invalid feature_id at record {count}: {exc}")
        try:
            validate_hash(geometry_hash_value, label="geometry_hash")
        except ReleaseFeatureModelError as exc:
            errors.append(f"invalid geometry_hash at record {count}: {exc}")
        try:
            validate_hash(properties_hash_value, label="properties_hash")
        except ReleaseFeatureModelError as exc:
            errors.append(f"invalid properties_hash at record {count}: {exc}")
        if feature_id in seen:
            duplicates.add(feature_id)
        seen.add(feature_id)
        try:
            identity_key = identity_key_from_record(payload)
            if identity_key in identity_keys and identity_keys[identity_key] != feature_id:
                duplicate_identity_keys.add(identity_key)
            else:
                identity_keys[identity_key] = feature_id
        except ReleaseFeatureModelError as exc:
            errors.append(f"invalid identity key at record {count}: {exc}")
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
    if duplicate_identity_keys:
        errors.append("duplicate identity keys: " + ", ".join(str(key) for key in sorted(duplicate_identity_keys)))
    if oversized:
        errors.append(
            "metadata sidecar record(s) exceed the configured serving document size: "
            + ", ".join(sorted(oversized))
        )
    return ValidationResult(
        valid=not errors,
        feature_count=count,
        duplicate_feature_ids=tuple(sorted(duplicates)),
        duplicate_identity_keys=tuple(sorted(duplicate_identity_keys)),
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


def read_metadata_sidecar_bytes(payload: bytes, *, label: str = "<sidecar>") -> Iterator[dict[str, Any]]:
    try:
        text = gzip.decompress(payload).decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReleaseFeatureModelError(f"{label}: sidecar is not valid gzip NDJSON") from exc
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReleaseFeatureModelError(f"{label}:{line_number}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise ReleaseFeatureModelError(f"{label}:{line_number}: sidecar row must be a JSON object")
        yield record


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
    return {
        "schema_version": RELEASE_SCHEMA_SCHEMA_VERSION,
        "asset_slug": asset_slug,
        "release": release,
        "fields": [asdict(field) if isinstance(field, ReleaseSchemaField) else dict(field) for field in fields],
    }


def validate_release_schema(
    schema: Mapping[str, Any],
    *,
    expected_asset_slug: str | None = None,
    expected_release: str | None = None,
) -> dict[str, ReleaseSchemaField]:
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
        if not isinstance(nullable, bool) or not isinstance(projectable, bool):
            errors.append(f"schema field {name!r} nullable/projectable must be boolean")
            continue
        if projectable:
            fields[name] = ReleaseSchemaField(name=name, type=field_type, nullable=nullable, projectable=projectable)
    if errors:
        raise ReleaseFeatureModelError("; ".join(errors))
    return fields


def validate_artifact_hash(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not ARTIFACT_HASH_RE.fullmatch(value):
        raise ReleaseFeatureModelError(f"{label} sha256 must be 64 lowercase hex characters, optionally prefixed by sha256:")
    return value.split(":", 1)[1] if value.startswith("sha256:") else value


def build_identity_metadata(
    *,
    strategy: str,
    source_fields: Sequence[str] = (),
    assignment_key: Sequence[str] = (),
    previous_release: str | None = None,
    next_generated_feature_id_after_release: int | None = None,
) -> dict[str, Any]:
    clean_source_fields = [str(field) for field in source_fields]
    clean_assignment_key = [str(part) for part in assignment_key]
    if strategy == "generated_sequence_source_fields" and clean_source_fields and not clean_assignment_key:
        clean_assignment_key = list(clean_source_fields)
    if strategy == "generated_sequence_content_hash" and not clean_assignment_key:
        clean_assignment_key = ["geometry_hash", "properties_hash"]
    identity: dict[str, Any] = {
        "schema_version": FEATURE_IDENTITY_SCHEMA_VERSION,
        "strategy": strategy,
        "source_fields": clean_source_fields,
        "feature_id_regex": FEATURE_ID_RE.pattern,
        "hash_algorithm": HASH_ALGORITHM,
        "canonicalization": FEATURE_ID_ALGORITHM,
    }
    if strategy.startswith("generated_sequence"):
        identity["generated_id_type"] = "monotonic_integer_string"
        identity["assignment_key"] = clean_assignment_key
        identity["previous_release"] = previous_release
        identity["next_generated_feature_id_after_release"] = next_generated_feature_id_after_release
    validate_identity_metadata(identity)
    return identity


def validate_identity_metadata(identity: Any) -> None:
    if not isinstance(identity, Mapping):
        raise ReleaseFeatureModelError("manifest identity must be an object")
    if identity.get("schema_version") != FEATURE_IDENTITY_SCHEMA_VERSION:
        raise ReleaseFeatureModelError("manifest identity has unsupported schema_version")
    strategy = identity.get("strategy")
    if strategy not in {"source_field", "generated_sequence_source_fields", "generated_sequence_content_hash"}:
        raise ReleaseFeatureModelError("manifest identity strategy is unsupported")
    source_fields = identity.get("source_fields")
    if not isinstance(source_fields, Sequence) or isinstance(source_fields, (str, bytes, bytearray)):
        raise ReleaseFeatureModelError("manifest identity source_fields must be an array")
    clean_source_fields = [str(field).strip() for field in source_fields if str(field).strip()]
    if clean_source_fields != list(source_fields):
        raise ReleaseFeatureModelError("manifest identity source_fields must be non-empty strings")
    if strategy == "source_field" and len(clean_source_fields) != 1:
        raise ReleaseFeatureModelError("source_field identity requires exactly one source field")
    if strategy == "generated_sequence_source_fields" and not (1 <= len(clean_source_fields) <= 2):
        raise ReleaseFeatureModelError("generated_sequence_source_fields identity requires one or two source fields")
    if strategy == "generated_sequence_content_hash" and clean_source_fields:
        raise ReleaseFeatureModelError("generated_sequence_content_hash identity must not include source fields")
    assignment_key = identity.get("assignment_key", [])
    if strategy.startswith("generated_sequence"):
        if not isinstance(assignment_key, Sequence) or isinstance(assignment_key, (str, bytes, bytearray)):
            raise ReleaseFeatureModelError("manifest identity assignment_key must be an array")
        clean_assignment_key = [str(part).strip() for part in assignment_key if str(part).strip()]
        if clean_assignment_key != list(assignment_key):
            raise ReleaseFeatureModelError("manifest identity assignment_key must contain non-empty strings")
        if strategy == "generated_sequence_source_fields" and clean_assignment_key != clean_source_fields:
            raise ReleaseFeatureModelError("generated_sequence_source_fields assignment_key must match source_fields")
        if strategy == "generated_sequence_content_hash" and clean_assignment_key != ["geometry_hash", "properties_hash"]:
            raise ReleaseFeatureModelError("generated_sequence_content_hash assignment_key must be geometry_hash and properties_hash")
    if identity.get("feature_id_regex") != FEATURE_ID_RE.pattern:
        raise ReleaseFeatureModelError("manifest identity feature_id_regex is unsupported")
    if identity.get("hash_algorithm") != HASH_ALGORITHM:
        raise ReleaseFeatureModelError("manifest identity hash_algorithm is unsupported")
    if identity.get("canonicalization") != FEATURE_ID_ALGORITHM:
        raise ReleaseFeatureModelError("manifest identity canonicalization is unsupported")


def validate_release_manifest(
    manifest: Mapping[str, Any],
    *,
    expected_asset_slug: str | None = None,
    expected_release: str | None = None,
    require_generations: bool = False,
) -> dict[str, Mapping[str, Any]]:
    errors: list[str] = []
    if manifest.get("schema_version") != RELEASE_MANIFEST_SCHEMA_VERSION:
        errors.append("manifest has unsupported schema_version")
    if expected_asset_slug is not None and manifest.get("asset_slug") != expected_asset_slug:
        errors.append(f"manifest asset_slug does not match {expected_asset_slug!r}")
    if expected_release is not None and manifest.get("release") != expected_release:
        errors.append(f"manifest release does not match {expected_release!r}")
    if manifest.get("release_feature_model_schema_version") != RELEASE_FEATURE_MODEL_SCHEMA_VERSION:
        errors.append("manifest release_feature_model_schema_version is unsupported")
    try:
        validate_identity_metadata(manifest.get("identity"))
    except ReleaseFeatureModelError as exc:
        errors.append(str(exc))
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
    if manifest.get("index_load_status") != "Firestore metadata serving is inactive":
        errors.append("manifest index_load_status must mark Firestore serving inactive")
    policy = manifest.get("index_status_policy")
    if (
        not isinstance(policy, Mapping)
        or policy.get("mode") != "inactive_firestore_serving"
        or policy.get("path") is not None
    ):
        errors.append("manifest index_status_policy must mark Firestore serving inactive with a null path")
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
    identity: Mapping[str, Any],
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
        "identity": dict(identity),
        "hashes": {
            "geometry_hash_algorithm": GEOMETRY_HASH_ALGORITHM,
            "properties_hash_algorithm": PROPERTIES_HASH_ALGORITHM,
        },
        "validation": dict(validation),
        "index_load_status": "Firestore metadata serving is inactive",
        "index_status_policy": {
            "mode": "inactive_firestore_serving",
            "path": None,
        },
    }


def index_load_record_name(asset_root: str, release: str, load_id: str) -> str:
    safe_load_id = re.sub(r"[^A-Za-z0-9]+", "", str(load_id))
    if not safe_load_id:
        raise ReleaseFeatureModelError("load_id contains no usable alphanumeric characters")
    return f"{asset_root.rstrip('/')}/index-loads/{release}/{safe_load_id}.json"
