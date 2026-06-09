"""Feature metadata bundle helpers for scheduled vector ingestion jobs."""

from __future__ import annotations

import gzip
import io
import json
import re
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from scripts import release_feature_model

FEATURE_ID_COLUMN = "feature_id"
GEOMETRY_HASH_COLUMN = "geometry_hash"
PROPERTIES_HASH_COLUMN = "properties_hash"
FEATURE_IDENTITY_ALGORITHM = release_feature_model.FEATURE_ID_ALGORITHM
HASH_ALGORITHM = release_feature_model.HASH_ALGORITHM
RELEASE_SCHEMA_VERSION = release_feature_model.RELEASE_SCHEMA_SCHEMA_VERSION
SIDECAR_SCHEMA_VERSION = release_feature_model.METADATA_SIDECAR_SCHEMA_VERSION
MANIFEST_SCHEMA_VERSION = release_feature_model.RELEASE_MANIFEST_SCHEMA_VERSION
RELEASE_FEATURE_MODEL_SCHEMA_VERSION = release_feature_model.RELEASE_FEATURE_MODEL_SCHEMA_VERSION
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
    return release_feature_model.canonical_json(value)


def sha256_hex(value: bytes | str) -> str:
    return release_feature_model.sha256_hex(value)


def validate_feature_id(feature_id: str) -> None:
    try:
        release_feature_model.validate_feature_id(feature_id)
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise RuntimeError(str(exc)) from exc


def source_field_feature_id(field_name: str, value: Any) -> str:
    try:
        return release_feature_model.source_field_feature_id(source_field=field_name, source_value=value)
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise RuntimeError(str(exc)) from exc


def content_hashes(*, geometry: Mapping[str, Any] | None, properties: Mapping[str, Any]) -> tuple[str, str]:
    return release_feature_model.content_hashes(geometry=geometry, properties=properties)


def assign_generated_feature_ids(
    identity_keys: Iterable[Sequence[str]],
    *,
    previous_records: Iterable[Mapping[str, Any]] | None = None,
) -> dict[tuple[str, ...], str]:
    try:
        return release_feature_model.assign_generated_feature_ids(identity_keys, previous_records=previous_records)
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise RuntimeError(str(exc)) from exc


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


def write_manifest(payload: Mapping[str, Any], path: Path) -> None:
    path.write_text(json.dumps(dict(payload), sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _feature_record(
    *,
    asset_slug: str,
    release: str,
    feature_id: str,
    geometry: Mapping[str, Any] | None,
    source_properties: Mapping[str, Any],
    provenance: Mapping[str, Any],
    identity_key: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    geometry_hash, properties_hash = content_hashes(geometry=geometry, properties=source_properties)
    metadata_properties = dict(source_properties)
    published_properties = {
        **metadata_properties,
        FEATURE_ID_COLUMN: feature_id,
        GEOMETRY_HASH_COLUMN: geometry_hash,
        PROPERTIES_HASH_COLUMN: properties_hash,
    }
    enriched_feature = {
        "type": "Feature",
        "properties": published_properties,
        "geometry": geometry,
    }
    sidecar = sidecar_record(
        asset_slug=asset_slug,
        release=release,
        feature_id=feature_id,
        geometry_hash=geometry_hash,
        properties_hash=properties_hash,
        properties=metadata_properties,
        provenance=provenance,
        identity_key=identity_key,
    )
    return enriched_feature, sidecar


def enrich_features_with_source_field_ids(
    features: Iterable[Mapping[str, Any]],
    *,
    asset_slug: str,
    release: str,
    id_field: str,
    provenance: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    enriched: list[dict[str, Any]] = []
    sidecar_records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, feature in enumerate(features, start=1):
        source_properties = dict(feature.get("properties") or {})
        feature_id = source_field_feature_id(id_field, source_properties.get(id_field))
        if feature_id in seen:
            raise RuntimeError(f"duplicate {id_field} feature_id in {asset_slug}: {feature_id}")
        seen.add(feature_id)
        next_feature, sidecar = _feature_record(
            asset_slug=asset_slug,
            release=release,
            feature_id=feature_id,
            geometry=feature.get("geometry"),
            source_properties=source_properties,
            provenance={**dict(provenance), "source_row_number": index, "source_id_field": id_field},
            identity_key=(feature_id,),
        )
        enriched.append(next_feature)
        sidecar_records.append(sidecar)
    if not sidecar_records:
        raise RuntimeError(f"{asset_slug} metadata sidecar would be empty")
    return enriched, sidecar_records


def enrich_features_with_generated_ids(
    features: Iterable[Mapping[str, Any]],
    *,
    asset_slug: str,
    release: str,
    provenance: Mapping[str, Any],
    source_fields: Sequence[str] = (),
    previous_records: Iterable[Mapping[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[release_feature_model.IdentityAmbiguity, ...]]:
    prepared: list[dict[str, Any]] = []
    seen_identity_keys: dict[tuple[str, ...], dict[str, Any]] = {}
    for ordinal, feature in enumerate(features, start=1):
        source_properties = dict(feature.get("properties") or {})
        geometry = feature.get("geometry")
        geometry_hash, properties_hash = content_hashes(geometry=geometry, properties=source_properties)
        if source_fields:
            identity_key = release_feature_model.source_fields_identity_key(source_properties, source_fields)
        else:
            identity_key = release_feature_model.content_identity_key(
                geometry_hash_value=geometry_hash,
                properties_hash_value=properties_hash,
            )
        if identity_key in seen_identity_keys:
            first = seen_identity_keys[identity_key]
            if first["geometry_hash"] == geometry_hash and first["properties_hash"] == properties_hash:
                first["duplicate_source_row_numbers"].append(ordinal)
                continue
            previous_index = first["ordinal"]
            raise RuntimeError(f"duplicate generated identity key with different content in {asset_slug}: rows {previous_index} and {ordinal}")
        prepared_item = {
            "ordinal": ordinal,
            "feature": feature,
            "source_properties": source_properties,
            "geometry": geometry,
            "identity_key": identity_key,
            "geometry_hash": geometry_hash,
            "properties_hash": properties_hash,
            "duplicate_source_row_numbers": [],
        }
        seen_identity_keys[identity_key] = prepared_item
        prepared.append(prepared_item)

    ids_by_key = assign_generated_feature_ids((item["identity_key"] for item in prepared), previous_records=previous_records)
    provisional_records = [
        {
            "feature_id": ids_by_key[item["identity_key"]],
            "geometry_hash": item["geometry_hash"],
            "properties_hash": item["properties_hash"],
            "identity_key": item["identity_key"],
            "properties": item["source_properties"],
        }
        for item in prepared
    ]
    ambiguities = release_feature_model.find_identity_ambiguities(
        provisional_records,
        previous_records=previous_records or (),
    )
    enriched: list[dict[str, Any]] = []
    sidecar_records: list[dict[str, Any]] = []
    for item in prepared:
        ordinal = int(item["ordinal"])
        identity_key = item["identity_key"]
        feature_id = ids_by_key[identity_key]
        provenance_payload = {**dict(provenance), "source_row_number": ordinal, "identity_key": list(identity_key)}
        duplicate_source_row_numbers = item["duplicate_source_row_numbers"]
        if duplicate_source_row_numbers:
            provenance_payload["duplicate_source_row_numbers"] = list(duplicate_source_row_numbers)
        next_feature, sidecar = _feature_record(
            asset_slug=asset_slug,
            release=release,
            feature_id=feature_id,
            geometry=item["geometry"],
            source_properties=item["source_properties"],
            provenance=provenance_payload,
            identity_key=identity_key,
        )
        enriched.append(next_feature)
        sidecar_records.append(sidecar)
    if not sidecar_records:
        raise RuntimeError(f"{asset_slug} metadata sidecar would be empty")
    return enriched, sidecar_records, ambiguities


def sidecar_record(
    *,
    asset_slug: str,
    release: str,
    feature_id: str,
    geometry_hash: str,
    properties_hash: str,
    properties: Mapping[str, Any],
    provenance: Mapping[str, Any],
    identity_key: Sequence[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SIDECAR_SCHEMA_VERSION,
        "asset_slug": asset_slug,
        "release": release,
        "feature_id": feature_id,
        "geometry_hash": geometry_hash,
        "properties_hash": properties_hash,
        "identity_key": list(identity_key or ()),
        "properties": dict(properties),
        "provenance": dict(provenance),
    }


def write_sidecar(records: Sequence[Mapping[str, Any]], path: Path) -> None:
    validation = release_feature_model.validate_sidecar_records(records)
    if not validation.valid:
        raise RuntimeError("metadata sidecar validation failed: " + "; ".join(validation.errors))
    with path.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as gzip_file:
            with io.TextIOWrapper(gzip_file, encoding="utf-8", newline="\n") as file_obj:
                for record in records:
                    payload = asdict(record) if is_dataclass(record) else dict(record)
                    file_obj.write(canonical_json(payload) + "\n")


def validate_release_vector_contract(
    *,
    fgb_path: Path,
    pmtiles_path: Path,
    pmtiles_bin: str = "pmtiles",
    decode_zoom: int = 0,
) -> None:
    from scripts import vector_asset

    result = vector_asset.validate_metadata_lookup_bundle(
        fgb_path,
        pmtiles_path,
        pmtiles_bin=pmtiles_bin,
        decode_zoom=decode_zoom,
    )
    if not result.valid:
        raise RuntimeError("release vector metadata contract validation failed: " + "; ".join(result.errors))


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


def next_generated_feature_id(records: Sequence[Mapping[str, Any]]) -> int:
    numeric_ids = [
        int(str(record.get("feature_id") or ""))
        for record in records
        if str(record.get("feature_id") or "").isdigit()
    ]
    return max(numeric_ids, default=0) + 1


def manifest_payload(
    *,
    asset_slug: str,
    release: str,
    bucket_name: str,
    asset_root: str,
    sha256_by_role: Mapping[str, str],
    schema: Mapping[str, Any],
    source_inputs: Sequence[Mapping[str, Any]],
    identity: Mapping[str, Any],
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
    return release_feature_model.build_release_manifest(
        asset_slug=asset_slug,
        release=release,
        source_inputs=source_inputs,
        artifacts=artifacts,
        schema=schema,
        identity=identity,
        validation={"valid": True, "feature_count": feature_count},
    )


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
    try:
        release_feature_model.validate_release_manifest(
            payload,
            expected_asset_slug=expected_asset_slug,
            expected_release=expected_release,
            require_generations=True,
        )
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise RuntimeError(str(exc)) from exc


def final_manifest_payload(
    *,
    asset_slug: str,
    release: str,
    bucket_name: str,
    asset_root: str,
    sha256_by_role: Mapping[str, str],
    schema: Mapping[str, Any],
    source_inputs: Sequence[Mapping[str, Any]],
    identity: Mapping[str, Any],
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
        identity=identity,
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
    validate_final_manifest_payload(payload, expected_asset_slug=asset_slug, expected_release=release)
    return payload
