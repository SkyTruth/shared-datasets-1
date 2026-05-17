#!/usr/bin/env python3
"""Generate deterministic shared-datasets generated identifiers for vectors.

This helper works on GeoJSON-like features. It does not publish or mutate
canonical Cloud Storage objects; use the reviewed dataset publish workflow for
any canonical artifact replacement.
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from xml.sax.saxutils import escape


ALGORITHM = "shared-datasets-group-id:v1"
ROW_ID_ALGORITHM = "shared-datasets-row-id:v1"
BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
COLLISION_PROBABILITY_THRESHOLD = 2e-10
DEFAULT_COLUMN = "shared_datasets_group_id"
DEFAULT_ROW_ID_COLUMN = "shared_datasets_row_id"
MIN_TOKEN_LENGTH = 8
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class GroupIdError(ValueError):
    """Raised when group ID inputs cannot produce valid IDs."""


@dataclass(frozen=True)
class GroupSummary:
    key: tuple[Any, ...]
    feature_indexes: tuple[int, ...]
    geometry_digests: tuple[str, ...]
    preimage: str
    token: str


@dataclass(frozen=True)
class GeometryAmbiguity:
    token: str
    geometry_digests: tuple[str, ...]
    group_keys: tuple[tuple[Any, ...], ...]
    feature_indexes: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class GroupIdResult:
    features: list[dict[str, Any]]
    token_length: int
    group_count: int
    blank_group_count: int
    column: str
    groups: tuple[GroupSummary, ...]
    identical_preimage_group_count: int = 0
    ambiguous_identical_geometry_groups: tuple[GeometryAmbiguity, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RowGroupIdResult:
    row_tokens: tuple[str, ...]
    token_length: int
    group_count: int
    blank_group_count: int
    column: str
    groups: tuple[GroupSummary, ...]
    identical_preimage_group_count: int = 0
    ambiguous_identical_geometry_groups: tuple[GeometryAmbiguity, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class RowIdResult:
    row_tokens: tuple[str, ...]
    token_length: int
    row_count: int
    column: str
    duplicate_geometry_digest_count: int = 0
    duplicate_geometry_row_count: int = 0
    warnings: tuple[str, ...] = field(default_factory=tuple)


def collision_probability(group_count: int, token_length: int, *, alphabet_size: int = len(BASE62_ALPHABET)) -> float:
    """Return the birthday-bound collision probability for the configured space."""
    if group_count < 0:
        raise GroupIdError("group_count must be non-negative")
    if token_length < 1:
        raise GroupIdError("token_length must be positive")
    if group_count < 2:
        return 0.0
    return group_count * (group_count - 1) / (2 * (alphabet_size**token_length))


def token_length_for_group_count(
    group_count: int,
    *,
    target_probability: float = COLLISION_PROBABILITY_THRESHOLD,
    min_length: int = MIN_TOKEN_LENGTH,
) -> int:
    """Return the shortest token length meeting the target birthday bound."""
    if group_count < 0:
        raise GroupIdError("group_count must be non-negative")
    if target_probability <= 0:
        raise GroupIdError("target_probability must be positive")
    token_length = min_length
    while collision_probability(group_count, token_length) > target_probability:
        token_length += 1
    return token_length


def base62_token(digest: bytes, token_length: int) -> str:
    """Encode a SHA digest into a fixed-length base62 token."""
    if token_length < 1:
        raise GroupIdError("token_length must be positive")
    value = int.from_bytes(digest, "big") % (len(BASE62_ALPHABET) ** token_length)
    chars: list[str] = []
    for _ in range(token_length):
        value, remainder = divmod(value, len(BASE62_ALPHABET))
        chars.append(BASE62_ALPHABET[remainder])
    return "".join(reversed(chars))


def normalize_group_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        normalized = " ".join(value.strip().split())
        return normalized or None
    return str(value)


def normalize_number(value: int | float) -> int | float:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if not math.isfinite(value):
        raise GroupIdError("geometry coordinates must be finite numbers")
    if value == 0:
        return 0
    return float(format(value, ".15g"))


def normalize_geometry(value: Any) -> Any:
    """Normalize GeoJSON geometry values for deterministic hashing."""
    if isinstance(value, dict):
        return {str(key): normalize_geometry(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [normalize_geometry(item) for item in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return normalize_number(value)
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def geometry_digest(geometry: Any) -> str:
    normalized = normalize_geometry(geometry)
    return hashlib.sha256(canonical_json(normalized).encode("utf-8")).hexdigest()


def group_preimage(*, asset_slug: str, geometry_digests: Sequence[str]) -> str:
    payload = {
        "algorithm": ALGORITHM,
        "asset_slug": asset_slug,
        "geometry_digests": sorted(geometry_digests),
    }
    return canonical_json(payload)


def row_preimage(*, asset_slug: str, geometry_digest_value: str, duplicate_ordinal: int = 0) -> str:
    payload = {
        "algorithm": ROW_ID_ALGORITHM,
        "asset_slug": asset_slug,
        "geometry_digest": geometry_digest_value,
        "geometry_duplicate_ordinal": duplicate_ordinal,
    }
    return canonical_json(payload)


def validate_row_id_inputs(asset_slug: str, column: str) -> None:
    if not SLUG_RE.fullmatch(asset_slug):
        raise GroupIdError(f"asset_slug must be lowercase kebab-case: {asset_slug!r}")
    if not column.strip():
        raise GroupIdError("column must be non-empty")


def validate_group_inputs(asset_slug: str, grouping_fields: Sequence[str], column: str) -> tuple[str, ...]:
    if not SLUG_RE.fullmatch(asset_slug):
        raise GroupIdError(f"asset_slug must be lowercase kebab-case: {asset_slug!r}")
    fields = tuple(field.strip() for field in grouping_fields if field.strip())
    if not fields:
        raise GroupIdError("at least one grouping field is required")
    if not column.strip():
        raise GroupIdError("column must be non-empty")
    return fields


def feature_properties(feature: dict[str, Any]) -> dict[str, Any]:
    properties = feature.setdefault("properties", {})
    if not isinstance(properties, dict):
        raise GroupIdError("feature properties must be a mapping")
    return properties


def group_key_for_feature(feature: dict[str, Any], grouping_fields: Sequence[str], index: int) -> tuple[tuple[Any, ...], bool]:
    properties = feature_properties(feature)
    values = tuple(normalize_group_value(properties.get(field)) for field in grouping_fields)
    if all(value is None for value in values):
        return ("__blank_feature__", index), True
    return ("__group__", values), False


def group_features(features: Sequence[dict[str, Any]], grouping_fields: Sequence[str]) -> tuple[dict[tuple[Any, ...], list[int]], int]:
    grouped: dict[tuple[Any, ...], list[int]] = {}
    blank_group_count = 0
    for index, feature in enumerate(features):
        key, is_blank = group_key_for_feature(feature, grouping_fields, index)
        if is_blank:
            blank_group_count += 1
        grouped.setdefault(key, []).append(index)
    return grouped, blank_group_count


def collect_group_inputs(
    features: Iterable[dict[str, Any]], grouping_fields: Sequence[str]
) -> tuple[int, list[str], dict[tuple[Any, ...], list[int]], int]:
    geometry_digests: list[str] = []
    grouped: dict[tuple[Any, ...], list[int]] = {}
    blank_group_count = 0
    row_count = 0
    for index, feature in enumerate(features):
        geometry_digests.append(geometry_digest(feature.get("geometry")))
        key, is_blank = group_key_for_feature(feature, grouping_fields, index)
        if is_blank:
            blank_group_count += 1
        grouped.setdefault(key, []).append(index)
        row_count += 1
    return row_count, geometry_digests, grouped, blank_group_count


def token_collisions(groups: Sequence[GroupSummary]) -> dict[str, set[str]]:
    by_token: dict[str, set[str]] = {}
    for group in groups:
        by_token.setdefault(group.token, set()).add(group.preimage)
    return {token: preimages for token, preimages in by_token.items() if len(preimages) > 1}


def row_token_collisions(tokens_and_preimages: Sequence[tuple[str, str]]) -> dict[str, set[str]]:
    by_token: dict[str, set[str]] = {}
    for token, preimage in tokens_and_preimages:
        by_token.setdefault(token, set()).add(preimage)
    return {token: preimages for token, preimages in by_token.items() if len(preimages) > 1}


def ambiguous_identical_geometry_groups(groups: Sequence[GroupSummary]) -> tuple[GeometryAmbiguity, ...]:
    by_preimage: dict[str, list[GroupSummary]] = {}
    for group in groups:
        by_preimage.setdefault(group.preimage, []).append(group)
    ambiguities: list[GeometryAmbiguity] = []
    for grouped in by_preimage.values():
        if len(grouped) < 2:
            continue
        first = grouped[0]
        ambiguities.append(
            GeometryAmbiguity(
                token=first.token,
                geometry_digests=first.geometry_digests,
                group_keys=tuple(group.key for group in grouped),
                feature_indexes=tuple(group.feature_indexes for group in grouped),
            )
        )
    return tuple(ambiguities)


def identical_preimage_count(ambiguities: Sequence[GeometryAmbiguity]) -> int:
    return sum(len(ambiguity.group_keys) for ambiguity in ambiguities)


def group_key_label(key: tuple[Any, ...]) -> str:
    if len(key) >= 2 and key[0] == "__group__":
        values = key[1]
        if isinstance(values, (list, tuple)):
            return " / ".join(str(value) for value in values)
    if len(key) >= 2 and key[0] == "__blank_feature__":
        return f"blank feature #{key[1]}"
    return canonical_json(key)


def ambiguity_summary(ambiguity: GeometryAmbiguity) -> str:
    labels = [group_key_label(key) for key in ambiguity.group_keys]
    visible = ", ".join(labels[:4])
    if len(labels) > 4:
        visible += f", ... +{len(labels) - 4} more"
    return f"token {ambiguity.token}: {visible}"


def build_group_summaries(
    *,
    grouped: dict[tuple[Any, ...], list[int]],
    geometry_digests: Sequence[str],
    asset_slug: str,
    token_length: int,
) -> tuple[GroupSummary, ...]:
    summaries: list[GroupSummary] = []
    for key in sorted(grouped, key=lambda item: canonical_json(item)):
        indexes = tuple(grouped[key])
        digests = tuple(geometry_digests[index] for index in indexes)
        preimage = group_preimage(asset_slug=asset_slug, geometry_digests=digests)
        token = base62_token(hashlib.sha256(preimage.encode("utf-8")).digest(), token_length)
        summaries.append(
            GroupSummary(
                key=key,
                feature_indexes=indexes,
                geometry_digests=tuple(sorted(digests)),
                preimage=preimage,
                token=token,
            )
        )
    return tuple(summaries)


def collect_row_id_inputs(features: Iterable[dict[str, Any]]) -> tuple[list[str], list[int], int, int]:
    geometry_digests: list[str] = []
    duplicate_ordinals: list[int] = []
    seen_by_digest: dict[str, int] = {}
    for feature in features:
        digest = geometry_digest(feature.get("geometry"))
        ordinal = seen_by_digest.get(digest, 0)
        seen_by_digest[digest] = ordinal + 1
        geometry_digests.append(digest)
        duplicate_ordinals.append(ordinal)
    duplicate_counts = [count for count in seen_by_digest.values() if count > 1]
    return geometry_digests, duplicate_ordinals, len(duplicate_counts), sum(duplicate_counts)


def row_tokens_for_length(
    *,
    asset_slug: str,
    geometry_digests: Sequence[str],
    duplicate_ordinals: Sequence[int],
    token_length: int,
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    tokens: list[str] = []
    tokens_and_preimages: list[tuple[str, str]] = []
    for digest, ordinal in zip(geometry_digests, duplicate_ordinals, strict=True):
        preimage = row_preimage(
            asset_slug=asset_slug,
            geometry_digest_value=digest,
            duplicate_ordinal=ordinal,
        )
        token = base62_token(hashlib.sha256(preimage.encode("utf-8")).digest(), token_length)
        tokens.append(token)
        tokens_and_preimages.append((token, preimage))
    return tuple(tokens), tuple(tokens_and_preimages)


def build_row_ids(
    features: Iterable[dict[str, Any]],
    *,
    asset_slug: str,
    column: str = DEFAULT_ROW_ID_COLUMN,
    token_length: int | None = None,
) -> RowIdResult:
    validate_row_id_inputs(asset_slug, column)
    geometry_digests, duplicate_ordinals, duplicate_digest_count, duplicate_row_count = collect_row_id_inputs(features)
    row_count = len(geometry_digests)
    resolved_length = token_length or token_length_for_group_count(row_count)
    if resolved_length < MIN_TOKEN_LENGTH:
        raise GroupIdError(f"token_length must be at least {MIN_TOKEN_LENGTH}")

    while True:
        row_tokens, tokens_and_preimages = row_tokens_for_length(
            asset_slug=asset_slug,
            geometry_digests=geometry_digests,
            duplicate_ordinals=duplicate_ordinals,
            token_length=resolved_length,
        )
        collisions = row_token_collisions(tokens_and_preimages)
        if not collisions:
            break
        if token_length is not None:
            raise GroupIdError(
                f"token_length {token_length} produced {len(collisions)} true hash-prefix collision(s); "
                "increase token_length"
            )
        resolved_length += 1

    warnings: list[str] = []
    if duplicate_row_count:
        warnings.append(
            f"{duplicate_row_count} row(s) across {duplicate_digest_count} duplicate geometry digest(s) "
            "received deterministic geometry_duplicate_ordinal disambiguators in source feature order"
        )

    return RowIdResult(
        row_tokens=row_tokens,
        token_length=resolved_length,
        row_count=row_count,
        column=column,
        duplicate_geometry_digest_count=duplicate_digest_count,
        duplicate_geometry_row_count=duplicate_row_count,
        warnings=tuple(warnings),
    )


def add_row_ids(
    features: Sequence[dict[str, Any]],
    *,
    asset_slug: str,
    column: str = DEFAULT_ROW_ID_COLUMN,
    token_length: int | None = None,
) -> tuple[list[dict[str, Any]], RowIdResult]:
    output_features = [copy.deepcopy(feature) for feature in features]
    result = build_row_ids(
        output_features,
        asset_slug=asset_slug,
        column=column,
        token_length=token_length,
    )
    for index, token in enumerate(result.row_tokens):
        feature_properties(output_features[index])[column] = token
    return output_features, result


def build_row_group_ids(
    features: Iterable[dict[str, Any]],
    *,
    asset_slug: str,
    grouping_fields: Sequence[str],
    column: str = DEFAULT_COLUMN,
    token_length: int | None = None,
    fail_on_ambiguous_geometry: bool = False,
) -> RowGroupIdResult:
    fields = validate_group_inputs(asset_slug, grouping_fields, column)
    row_count, geometry_digests, grouped, blank_group_count = collect_group_inputs(features, fields)
    group_count = len(grouped)
    resolved_length = token_length or token_length_for_group_count(group_count)
    if resolved_length < MIN_TOKEN_LENGTH:
        raise GroupIdError(f"token_length must be at least {MIN_TOKEN_LENGTH}")

    while True:
        groups = build_group_summaries(
            grouped=grouped,
            geometry_digests=geometry_digests,
            asset_slug=asset_slug,
            token_length=resolved_length,
        )
        collisions = token_collisions(groups)
        if not collisions:
            break
        if token_length is not None:
            raise GroupIdError(
                f"token_length {token_length} produced {len(collisions)} true hash-prefix collision(s); "
                "increase token_length"
            )
        resolved_length += 1

    row_tokens = [""] * row_count
    for group in groups:
        for index in group.feature_indexes:
            row_tokens[index] = group.token

    geometry_ambiguities = ambiguous_identical_geometry_groups(groups)
    identical_count = identical_preimage_count(geometry_ambiguities)
    if geometry_ambiguities and fail_on_ambiguous_geometry:
        examples = "; ".join(ambiguity_summary(ambiguity) for ambiguity in geometry_ambiguities[:3])
        raise GroupIdError(
            f"{identical_count} group(s) share identical collective geometry and would share generated IDs: {examples}"
        )

    warnings: list[str] = []
    if identical_count:
        warnings.append(
            f"{identical_count} group(s) share identical collective geometry and therefore share generated IDs; "
            f"review as potential aliases/duplicates: {ambiguity_summary(geometry_ambiguities[0])}"
        )
    if blank_group_count:
        warnings.append(f"{blank_group_count} blank/null grouping value(s) were assigned per-feature groups")

    return RowGroupIdResult(
        row_tokens=tuple(row_tokens),
        token_length=resolved_length,
        group_count=group_count,
        blank_group_count=blank_group_count,
        column=column,
        groups=groups,
        identical_preimage_group_count=identical_count,
        ambiguous_identical_geometry_groups=geometry_ambiguities,
        warnings=tuple(warnings),
    )


def add_group_ids(
    features: Sequence[dict[str, Any]],
    *,
    asset_slug: str,
    grouping_fields: Sequence[str],
    column: str = DEFAULT_COLUMN,
    token_length: int | None = None,
    fail_on_ambiguous_geometry: bool = False,
) -> GroupIdResult:
    """Return copied features with a generated group ID property added."""
    output_features = [copy.deepcopy(feature) for feature in features]
    row_result = build_row_group_ids(
        output_features,
        asset_slug=asset_slug,
        grouping_fields=grouping_fields,
        column=column,
        token_length=token_length,
        fail_on_ambiguous_geometry=fail_on_ambiguous_geometry,
    )
    for index, token in enumerate(row_result.row_tokens):
        feature_properties(output_features[index])[column] = token

    return GroupIdResult(
        features=output_features,
        token_length=row_result.token_length,
        group_count=row_result.group_count,
        blank_group_count=row_result.blank_group_count,
        column=column,
        groups=row_result.groups,
        identical_preimage_group_count=row_result.identical_preimage_group_count,
        ambiguous_identical_geometry_groups=row_result.ambiguous_identical_geometry_groups,
        warnings=row_result.warnings,
    )


def load_geojson_features(path: Path) -> tuple[list[dict[str, Any]], bool]:
    text = path.read_text()
    stripped = text.lstrip()
    if stripped.startswith("{"):
        payload = json.loads(text)
        if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
            raise GroupIdError("GeoJSON input must be a FeatureCollection")
        return payload["features"], True
    features = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not all(isinstance(feature, dict) and feature.get("type") == "Feature" for feature in features):
        raise GroupIdError("GeoJSONSeq input must contain one Feature object per line")
    return features, False


def write_geojson_features(path: Path, features: Sequence[dict[str, Any]], *, feature_collection: bool) -> None:
    if feature_collection:
        payload = {"type": "FeatureCollection", "features": list(features)}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        return
    path.write_text("\n".join(canonical_json(feature) for feature in features) + "\n")


def discover_ogr_layer_name(source: Path, *, ogrinfo_bin: str = "ogrinfo") -> str:
    completed = subprocess.run(
        [ogrinfo_bin, "-ro", "-so", str(source)],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if completed.returncode != 0:
        raise GroupIdError(f"could not inspect source layer with {ogrinfo_bin}: {completed.stdout.strip()}")
    match = re.search(r"^Layer name:\s*(.+?)\s*$", completed.stdout, flags=re.MULTILINE)
    if match:
        return match.group(1).strip()
    list_match = re.search(r"^\s*\d+:\s+(.+?)(?:\s+\(|$)", completed.stdout, flags=re.MULTILINE)
    if list_match:
        return list_match.group(1).strip()
    raise GroupIdError(f"could not determine source layer from {source}")


def iter_ogr_features(source: Path, *, source_layer: str | None = None, ogr2ogr_bin: str = "ogr2ogr") -> Iterator[dict[str, Any]]:
    command = [ogr2ogr_bin, "-f", "GeoJSONSeq", "-t_srs", "EPSG:4326", "/vsistdout/", str(source)]
    if source_layer:
        command.append(source_layer)
    process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if process.stdout is None:
        raise GroupIdError(f"could not stream source features with {ogr2ogr_bin}: missing stdout pipe")
    try:
        for line in process.stdout:
            if not line.strip():
                continue
            feature = json.loads(line)
            if not isinstance(feature, dict) or feature.get("type") != "Feature":
                raise GroupIdError("OGR GeoJSONSeq stream must contain one Feature object per line")
            yield feature
        stderr = process.stderr.read() if process.stderr is not None else ""
        returncode = process.wait()
        if returncode != 0:
            raise GroupIdError(f"could not stream source features with {ogr2ogr_bin}: {stderr.strip()}")
    except Exception:
        if process.poll() is None:
            process.kill()
            process.wait()
        raise


def load_ogr_features(source: Path, *, source_layer: str | None = None, ogr2ogr_bin: str = "ogr2ogr") -> list[dict[str, Any]]:
    return list(iter_ogr_features(source, source_layer=source_layer, ogr2ogr_bin=ogr2ogr_bin))


def write_group_id_map_csv(path: Path, result: RowGroupIdResult | RowIdResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rowid", result.column])
        for rowid, token in enumerate(result.row_tokens):
            writer.writerow([rowid, token])


def write_group_id_vrt(
    path: Path,
    *,
    source: Path,
    source_layer: str,
    map_path: Path,
    source_vrt_layer: str = "source",
    map_vrt_layer: str = "group_ids",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = f"""<OGRVRTDataSource>
  <OGRVRTLayer name="{escape(source_vrt_layer)}">
    <SrcDataSource relativeToVRT="0">{escape(str(source))}</SrcDataSource>
    <SrcLayer>{escape(source_layer)}</SrcLayer>
  </OGRVRTLayer>
  <OGRVRTLayer name="{escape(map_vrt_layer)}">
    <SrcDataSource relativeToVRT="0">{escape(str(map_path))}</SrcDataSource>
    <GeometryType>wkbNone</GeometryType>
  </OGRVRTLayer>
</OGRVRTDataSource>
"""
    path.write_text(text)


def result_summary(result: RowGroupIdResult | GroupIdResult | RowIdResult) -> dict[str, Any]:
    if isinstance(result, RowIdResult):
        return {
            "algorithm": ROW_ID_ALGORITHM,
            "column": result.column,
            "row_count": result.row_count,
            "duplicate_geometry_digest_count": result.duplicate_geometry_digest_count,
            "duplicate_geometry_row_count": result.duplicate_geometry_row_count,
            "token_length": result.token_length,
            "collision_probability": collision_probability(result.row_count, result.token_length),
            "warnings": list(result.warnings),
            "stability": (
                "Per-row geometry-addressed fallback ID. The preimage is asset_slug, canonical OGR EPSG:4326 "
                "geometry digest, and geometry_duplicate_ordinal for duplicate geometries in source feature order."
            ),
        }
    return {
        "algorithm": ALGORITHM,
        "column": result.column,
        "group_count": result.group_count,
        "blank_group_count": result.blank_group_count,
        "identical_preimage_group_count": result.identical_preimage_group_count,
        "ambiguous_identical_geometry_groups": [
            {
                "token": ambiguity.token,
                "group_keys": ambiguity.group_keys,
                "feature_indexes": ambiguity.feature_indexes,
            }
            for ambiguity in result.ambiguous_identical_geometry_groups
        ],
        "token_length": result.token_length,
        "collision_probability": collision_probability(result.group_count, result.token_length),
        "warnings": list(result.warnings),
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Add deterministic shared-datasets group IDs, or last-resort per-row geometry IDs, "
            "to GeoJSON features or OGR-readable sources."
        )
    )
    parser.add_argument("source", type=Path, help="GeoJSON features or an OGR-readable source when --ogr-source is set.")
    parser.add_argument("--asset-slug", required=True)
    parser.add_argument("--grouping-field", action="append", help="Grouping field; repeat for composite groups.")
    parser.add_argument(
        "--row-id",
        action="store_true",
        help=(
            "Generate one shared_datasets_row_id per feature from canonical geometry. "
            "Use only as the explicit fallback when no provider ID or grouping field is suitable."
        ),
    )
    parser.add_argument("--column", help="Output column. Defaults to shared_datasets_group_id or shared_datasets_row_id by mode.")
    parser.add_argument("--token-length", type=int)
    parser.add_argument(
        "--fail-on-ambiguous-geometry",
        action="store_true",
        help="Fail when multiple grouping values share identical collective geometry and would receive the same ID.",
    )
    parser.add_argument("--out", type=Path, help="Output GeoJSON/GeoJSONSeq path.")
    parser.add_argument(
        "--ogr-source",
        action="store_true",
        help="Read an OGR source by streaming GeoJSONSeq and write a rowid group-ID map instead of full GeoJSON.",
    )
    parser.add_argument("--source-layer", help="Source layer name for OGR input; defaults to the first discovered layer.")
    parser.add_argument("--ogr2ogr-bin", default="ogr2ogr")
    parser.add_argument("--ogrinfo-bin", default="ogrinfo")
    parser.add_argument("--out-map", type=Path, help="Output rowid-to-group-ID CSV path for --ogr-source.")
    parser.add_argument("--out-vrt", type=Path, help="Optional OGR VRT joining the source and --out-map.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    grouping_fields = tuple(args.grouping_field or ())
    if args.row_id and grouping_fields:
        raise GroupIdError("--row-id cannot be combined with --grouping-field")
    if not args.row_id and not grouping_fields:
        raise GroupIdError("--grouping-field is required unless --row-id is set")
    column = args.column or (DEFAULT_ROW_ID_COLUMN if args.row_id else DEFAULT_COLUMN)
    if args.ogr_source:
        if args.out_map is None:
            raise GroupIdError("--out-map is required with --ogr-source")
        source_layer = args.source_layer or discover_ogr_layer_name(args.source, ogrinfo_bin=args.ogrinfo_bin)
        features = iter_ogr_features(args.source, source_layer=source_layer, ogr2ogr_bin=args.ogr2ogr_bin)
        if args.row_id:
            result = build_row_ids(
                features,
                asset_slug=args.asset_slug,
                column=column,
                token_length=args.token_length,
            )
        else:
            result = build_row_group_ids(
                features,
                asset_slug=args.asset_slug,
                grouping_fields=grouping_fields,
                column=column,
                token_length=args.token_length,
                fail_on_ambiguous_geometry=args.fail_on_ambiguous_geometry,
            )
        write_group_id_map_csv(args.out_map, result)
        summary = result_summary(result)
        summary["row_count"] = len(result.row_tokens)
        summary["map_path"] = str(args.out_map)
        summary["source_layer"] = source_layer
        if args.out_vrt is not None:
            write_group_id_vrt(args.out_vrt, source=args.source, source_layer=source_layer, map_path=args.out_map)
            summary["vrt_path"] = str(args.out_vrt)
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    if args.out is None:
        raise GroupIdError("--out is required unless --ogr-source is set")
    features, feature_collection = load_geojson_features(args.source)
    if args.row_id:
        output_features, result = add_row_ids(
            features,
            asset_slug=args.asset_slug,
            column=column,
            token_length=args.token_length,
        )
        write_geojson_features(args.out, output_features, feature_collection=feature_collection)
    else:
        result = add_group_ids(
            features,
            asset_slug=args.asset_slug,
            grouping_fields=grouping_fields,
            column=column,
            token_length=args.token_length,
            fail_on_ambiguous_geometry=args.fail_on_ambiguous_geometry,
        )
        write_geojson_features(args.out, result.features, feature_collection=feature_collection)
    print(json.dumps(result_summary(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
