"""Feature metadata bundle helpers for scheduled vector ingestion jobs."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from scripts import release_feature_model

FEATURE_ID_COLUMN = "feature_id"
EXT_ID_COLUMN = "ext_id"
FEATURE_HASH_COLUMN = "feature_hash"
EXT_ID_RE = release_feature_model.EXT_ID_RE
FEATURE_ID_ALGORITHM = "shared-datasets-feature-id:v1"
FEATURE_HASH_ALGORITHM = "sha256:canonical-feature-content:v1"
RELEASE_SCHEMA_VERSION = 1
SIDECAR_SCHEMA_VERSION = 1
MANIFEST_SCHEMA_VERSION = 1
RELEASE_FEATURE_MODEL_SCHEMA_VERSION = 1
VECTOR_BUNDLE_SUFFIXES = (
    ".fgb",
    ".pmtiles",
    ".metadata.ndjson.gz",
    ".schema.json",
    ".manifest.json",
)
ROLE_SUFFIXES = {
    "fgb": ".fgb",
    "pmtiles": ".pmtiles",
    "metadata": ".metadata.ndjson.gz",
    "schema": ".schema.json",
    "manifest": ".manifest.json",
}
NON_MANIFEST_ROLES = ("fgb", "pmtiles", "metadata", "schema")
ARTIFACT_HASH_RE = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_hex(value: bytes | str) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(payload).hexdigest()


def normalize_token(value: Any) -> str:
    if value is None:
        raise RuntimeError("feature ID token must be non-empty")
    try:
        return release_feature_model.normalize_feature_id_token(value)
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise RuntimeError(str(exc)) from exc


def provider_feature_id(field_name: str, value: Any) -> str:
    normalize_token(field_name)
    normalize_token(value)
    try:
        return release_feature_model.provider_feature_id(source_field=field_name, source_value=value)
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise RuntimeError(str(exc)) from exc


def generated_feature_id(asset_slug: str, preimage: Mapping[str, Any]) -> str:
    try:
        return release_feature_model.generated_feature_id(asset_slug=asset_slug, preimage=preimage)
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise RuntimeError(str(exc)) from exc


def validate_ext_id(ext_id: str) -> None:
    try:
        release_feature_model.validate_ext_id(ext_id)
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise RuntimeError(str(exc)) from exc


def assign_sequence_ext_ids(
    feature_ids: Iterable[str],
    *,
    previous_records: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, str]:
    try:
        return release_feature_model.assign_sequence_ext_ids(feature_ids, previous_records=previous_records)
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise RuntimeError(str(exc)) from exc


def content_feature_hash(*, geometry: Mapping[str, Any] | None, properties: Mapping[str, Any]) -> str:
    return "sha256:" + sha256_hex(canonical_json({"geometry": geometry, "properties": dict(sorted(properties.items()))}))


def resolve_ext_id(
    properties: Mapping[str, Any],
    *,
    feature_id: str,
    ext_id_field: str | None = None,
    generated_ext_id: str | None = None,
) -> str:
    if ext_id_field:
        value = str(properties.get(ext_id_field) or "").strip()
        if not value:
            raise RuntimeError(f"selected ext_id field {ext_id_field!r} is blank")
        validate_ext_id(value)
        return value
    if generated_ext_id:
        validate_ext_id(generated_ext_id)
        return generated_ext_id
    raise RuntimeError("ext_id requires a selected URL-safe field or generated sequence value")


def iter_geojsonseq(path: Path):
    with path.open(encoding="utf-8") as file_obj:
        for line_number, line in enumerate(file_obj, start=1):
            line = line.lstrip("\x1e").strip()
            if not line:
                continue
            try:
                feature = json.loads(line)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"{path}:{line_number}: invalid GeoJSONSeq feature") from exc
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise RuntimeError(f"{path}:{line_number}: expected GeoJSON Feature")
            yield feature


def iter_geojson_features(path: Path):
    payload = json.loads(path.read_text())
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise RuntimeError(f"expected GeoJSON FeatureCollection: {path}")
    features = payload.get("features")
    if not isinstance(features, list):
        raise RuntimeError(f"GeoJSON features must be an array: {path}")
    for index, feature in enumerate(features, start=1):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise RuntimeError(f"{path}: feature {index} is not a GeoJSON Feature")
        yield feature


def write_geojsonseq(features: Iterable[Mapping[str, Any]], path: Path) -> None:
    with path.open("w", encoding="utf-8") as file_obj:
        for feature in features:
            file_obj.write(canonical_json(dict(feature)) + "\n")


def enrich_features_with_provider_ids(
    features: Iterable[Mapping[str, Any]],
    *,
    asset_slug: str,
    release: str,
    id_field: str,
    provenance: Mapping[str, Any],
    ext_id_field: str | None = None,
    previous_records: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prepared: list[tuple[int, Mapping[str, Any], dict[str, Any], str]] = []
    seen: set[str] = set()
    for index, feature in enumerate(features, start=1):
        source_properties = dict(feature.get("properties") or {})
        raw_id = source_properties.get(id_field)
        feature_id = provider_feature_id(id_field, raw_id)
        if feature_id in seen:
            raise RuntimeError(f"duplicate {id_field} feature ID in {asset_slug}: {raw_id}")
        seen.add(feature_id)
        prepared.append((index, feature, source_properties, feature_id))
    generated_ext_ids = {} if ext_id_field else assign_sequence_ext_ids((item[3] for item in prepared), previous_records=previous_records)
    enriched: list[dict[str, Any]] = []
    sidecar_records: list[dict[str, Any]] = []
    seen_ext_ids: set[str] = set()
    for index, feature, source_properties, feature_id in prepared:
        ext_id = resolve_ext_id(
            source_properties,
            feature_id=feature_id,
            ext_id_field=ext_id_field,
            generated_ext_id=generated_ext_ids.get(feature_id),
        )
        if ext_id in seen_ext_ids:
            raise RuntimeError(f"duplicate ext_id in {asset_slug}: {ext_id}")
        seen_ext_ids.add(ext_id)
        metadata_properties = {**source_properties, EXT_ID_COLUMN: ext_id}
        feature_hash = content_feature_hash(geometry=feature.get("geometry"), properties=metadata_properties)
        published_properties = {
            **metadata_properties,
            FEATURE_ID_COLUMN: feature_id,
            FEATURE_HASH_COLUMN: feature_hash,
        }
        enriched_feature = {
            "type": "Feature",
            "id": feature_id,
            "properties": published_properties,
            "geometry": feature.get("geometry"),
        }
        enriched.append(enriched_feature)
        sidecar_records.append(
            sidecar_record(
                asset_slug=asset_slug,
                release=release,
                feature_id=feature_id,
                feature_hash=feature_hash,
                properties=metadata_properties,
                provenance={
                    **dict(provenance),
                    "source_row_number": index,
                    "id_field": id_field,
                    "ext_id_field": ext_id_field or "generated_sequence",
                },
            )
        )
    if not sidecar_records:
        raise RuntimeError(f"{asset_slug} metadata sidecar would be empty")
    return enriched, sidecar_records


def enrich_features_with_generated_ids(
    features: Iterable[Mapping[str, Any]],
    *,
    asset_slug: str,
    release: str,
    provenance: Mapping[str, Any],
    ext_id_field: str | None = None,
    previous_records: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prepared: list[tuple[int, Mapping[str, Any], dict[str, Any], str, Mapping[str, Any] | None, str]] = []
    seen: set[str] = set()
    for ordinal, feature in enumerate(features, start=1):
        source_properties = dict(feature.get("properties") or {})
        geometry = feature.get("geometry")
        geometry_digest = sha256_hex(canonical_json(geometry))
        feature_id = generated_feature_id(
            asset_slug,
            {"geometry_digest": geometry_digest},
        )
        if feature_id in seen:
            raise RuntimeError(f"duplicate generated feature_id in {asset_slug}: geometry_digest {geometry_digest}")
        seen.add(feature_id)
        prepared.append((ordinal, feature, source_properties, feature_id, geometry, geometry_digest))
    generated_ext_ids = {} if ext_id_field else assign_sequence_ext_ids((item[3] for item in prepared), previous_records=previous_records)
    enriched: list[dict[str, Any]] = []
    sidecar_records: list[dict[str, Any]] = []
    seen_ext_ids: set[str] = set()
    for ordinal, feature, source_properties, feature_id, geometry, geometry_digest in prepared:
        ext_id = resolve_ext_id(
            source_properties,
            feature_id=feature_id,
            ext_id_field=ext_id_field,
            generated_ext_id=generated_ext_ids.get(feature_id),
        )
        if ext_id in seen_ext_ids:
            raise RuntimeError(f"duplicate ext_id in {asset_slug}: {ext_id}")
        seen_ext_ids.add(ext_id)
        metadata_properties = {**source_properties, EXT_ID_COLUMN: ext_id}
        feature_hash = content_feature_hash(geometry=geometry, properties=metadata_properties)
        published_properties = {
            **metadata_properties,
            FEATURE_ID_COLUMN: feature_id,
            FEATURE_HASH_COLUMN: feature_hash,
        }
        enriched.append(
            {
                "type": "Feature",
                "id": feature_id,
                "properties": published_properties,
                "geometry": geometry,
            }
        )
        sidecar_records.append(
            sidecar_record(
                asset_slug=asset_slug,
                release=release,
                feature_id=feature_id,
                feature_hash=feature_hash,
                properties=metadata_properties,
                provenance={
                    **dict(provenance),
                    "geometry_digest": geometry_digest,
                    "source_row_number": ordinal,
                    "ext_id_field": ext_id_field or "generated_sequence",
                },
            )
        )
    if not sidecar_records:
        raise RuntimeError(f"{asset_slug} metadata sidecar would be empty")
    return enriched, sidecar_records


def sidecar_record(
    *,
    asset_slug: str,
    release: str,
    feature_id: str,
    feature_hash: str,
    properties: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "asset_slug": asset_slug,
        "release": release,
        "feature_id": feature_id,
        "feature_hash": feature_hash,
        "properties": dict(properties),
        "provenance": dict(provenance),
    }


def write_sidecar(records: Sequence[Mapping[str, Any]], path: Path) -> None:
    with path.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as gzip_file:
            with io.TextIOWrapper(gzip_file, encoding="utf-8", newline="\n") as file_obj:
                for record in records:
                    payload = asdict(record) if is_dataclass(record) else dict(record)
                    file_obj.write(canonical_json(payload) + "\n")


def schema_type(value: Any) -> str:
    if isinstance(value, bool):
        return "Boolean"
    if isinstance(value, int):
        return "Integer"
    if isinstance(value, float):
        return "Real"
    if isinstance(value, (list, dict)):
        return "JSON"
    return "String"


def schema_from_records(*, asset_slug: str, release: str, records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    field_names: list[str] = []
    observed: dict[str, Any] = {}
    nullable: dict[str, bool] = {}
    for record in records:
        properties = record.get("properties") if isinstance(record.get("properties"), Mapping) else {}
        for name, value in properties.items():
            if name not in field_names:
                field_names.append(str(name))
                nullable[str(name)] = False
            if value is None:
                nullable[str(name)] = True
            elif str(name) not in observed:
                observed[str(name)] = value
    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "asset_slug": asset_slug,
        "release": release,
        "fields": [
            {
                "name": name,
                "type": schema_type(observed.get(name)),
                "nullable": nullable.get(name, True),
                "projectable": True,
            }
            for name in field_names
        ],
    }


def write_schema(schema: Mapping[str, Any], path: Path) -> None:
    path.write_text(json.dumps(dict(schema), indent=2, sort_keys=True) + "\n")


def manifest_payload(
    *,
    asset_slug: str,
    release: str,
    bucket_name: str,
    asset_root: str,
    sha256_by_role: Mapping[str, str],
    schema: Mapping[str, Any],
    source_inputs: Sequence[Mapping[str, Any]],
    id_strategy: Mapping[str, Any],
    feature_count: int,
) -> dict[str, Any]:
    release_base = f"gs://{bucket_name}/{asset_root}/releases/{release}/{asset_slug}"
    artifacts = []
    for role, suffix in ROLE_SUFFIXES.items():
        entry: dict[str, Any] = {
            "role": role,
            "format": role,
            "path": f"{release_base}{suffix}",
        }
        if role != "manifest":
            entry["sha256"] = sha256_by_role[role]
        artifacts.append(entry)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "asset_slug": asset_slug,
        "release": release,
        "release_feature_model_schema_version": RELEASE_FEATURE_MODEL_SCHEMA_VERSION,
        "source_inputs": list(source_inputs),
        "artifacts": artifacts,
        "schema": dict(schema),
        "id_strategy": dict(id_strategy),
        "feature_hash_algorithm": FEATURE_HASH_ALGORITHM,
        "validation": {"valid": True, "feature_count": feature_count},
        "index_load_status": "tracked in index-loads/",
        "index_status_policy": {
            "mode": "external_index_load_records",
            "path": f"gs://{bucket_name}/{asset_root}/index-loads/{release}/",
        },
    }


def _generation_from_blob_info(info: Mapping[str, Any], *, label: str) -> int:
    generation = info.get("generation")
    if isinstance(generation, bool) or not isinstance(generation, int):
        raise RuntimeError(f"{label} must include an integer generation")
    return generation


def _path_from_blob_info(info: Mapping[str, Any], *, label: str) -> str:
    path = info.get("path")
    if not isinstance(path, str) or not path.startswith("gs://"):
        raise RuntimeError(f"{label} must include a gs:// path")
    return path


def validate_final_manifest_payload(
    payload: Mapping[str, Any],
    *,
    expected_asset_slug: str,
    expected_release: str,
) -> None:
    errors: list[str] = []
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append("manifest has unsupported schema_version")
    if payload.get("asset_slug") != expected_asset_slug:
        errors.append(f"manifest asset_slug does not match {expected_asset_slug!r}")
    if payload.get("release") != expected_release:
        errors.append(f"manifest release does not match {expected_release!r}")
    if payload.get("release_feature_model_schema_version") != RELEASE_FEATURE_MODEL_SCHEMA_VERSION:
        errors.append("manifest release_feature_model_schema_version is unsupported")
    if payload.get("feature_hash_algorithm") != FEATURE_HASH_ALGORITHM:
        errors.append("manifest feature_hash_algorithm is unsupported")
    policy = payload.get("index_status_policy")
    if not isinstance(policy, Mapping) or policy.get("mode") != "external_index_load_records":
        errors.append("manifest index_status_policy must point to external index-load records")
    schema = payload.get("schema")
    if not isinstance(schema, Mapping):
        errors.append("manifest schema must be an object")
    else:
        if schema.get("asset_slug") != expected_asset_slug:
            errors.append("manifest schema asset_slug does not match release")
        if schema.get("release") != expected_release:
            errors.append("manifest schema release does not match release")
        if not isinstance(schema.get("fields"), list):
            errors.append("manifest schema fields must be an array")

    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list):
        errors.append("manifest artifacts must be an array")
        artifacts = []
    artifacts_by_role: dict[str, Mapping[str, Any]] = {}
    for index, artifact in enumerate(artifacts, start=1):
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
        if role == "manifest":
            if artifact.get("generation") is not None:
                errors.append("manifest artifact must not embed its own object generation")
        else:
            if not isinstance(artifact.get("generation"), int):
                errors.append(f"manifest artifact {role!r} must include destination generation")
            sha = artifact.get("sha256")
            if not isinstance(sha, str) or not ARTIFACT_HASH_RE.fullmatch(sha):
                errors.append(
                    f"manifest artifact {role!r} sha256 must be 64 lowercase hex characters, optionally prefixed by sha256:"
                )
    missing = [role for role in ROLE_SUFFIXES if role not in artifacts_by_role]
    if missing:
        errors.append("manifest is missing required vector artifact role(s): " + ", ".join(missing))
    if errors:
        raise RuntimeError("; ".join(errors))


def final_manifest_payload(
    *,
    asset_slug: str,
    release: str,
    bucket_name: str,
    asset_root: str,
    sha256_by_role: Mapping[str, str],
    schema: Mapping[str, Any],
    source_inputs: Sequence[Mapping[str, Any]],
    id_strategy: Mapping[str, Any],
    feature_count: int,
    release_blob_info_by_role: Mapping[str, Mapping[str, Any]],
    latest_blob_info_by_role: Mapping[str, Mapping[str, Any]] | None,
    manifest_release_path: str,
    manifest_latest_path: str | None = None,
) -> dict[str, Any]:
    payload = manifest_payload(
        asset_slug=asset_slug,
        release=release,
        bucket_name=bucket_name,
        asset_root=asset_root,
        sha256_by_role=sha256_by_role,
        schema=schema,
        source_inputs=source_inputs,
        id_strategy=id_strategy,
        feature_count=feature_count,
    )
    latest_blob_info_by_role = latest_blob_info_by_role or {}
    artifacts_by_role = {str(artifact["role"]): dict(artifact) for artifact in payload["artifacts"]}

    for role in NON_MANIFEST_ROLES:
        release_info = release_blob_info_by_role.get(role)
        if not isinstance(release_info, Mapping):
            raise RuntimeError(f"release blob info is missing {role!r}")
        release_path = _path_from_blob_info(release_info, label=f"release {role} blob info")
        artifact = artifacts_by_role[role]
        if artifact["path"] != release_path:
            raise RuntimeError(f"release {role} path does not match manifest target")
        artifact["generation"] = _generation_from_blob_info(release_info, label=f"release {role} blob info")
        if release_info.get("size") is not None:
            artifact["size"] = int(release_info["size"])

        latest_info = latest_blob_info_by_role.get(role)
        if latest_info is not None:
            if not isinstance(latest_info, Mapping):
                raise RuntimeError(f"latest blob info for {role!r} must be an object")
            artifact["latest_path"] = _path_from_blob_info(latest_info, label=f"latest {role} blob info")
            if latest_info.get("generation") is not None:
                artifact["latest_generation"] = _generation_from_blob_info(
                    latest_info,
                    label=f"latest {role} blob info",
                )

    manifest_artifact = artifacts_by_role["manifest"]
    manifest_artifact["path"] = manifest_release_path
    if manifest_latest_path:
        manifest_artifact["latest_path"] = manifest_latest_path
    manifest_artifact.pop("generation", None)
    manifest_artifact.pop("latest_generation", None)
    payload["artifacts"] = [artifacts_by_role[role] for role in ROLE_SUFFIXES]
    validate_final_manifest_payload(
        payload,
        expected_asset_slug=asset_slug,
        expected_release=release,
    )
    return payload


def write_manifest(payload: Mapping[str, Any], path: Path) -> None:
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
