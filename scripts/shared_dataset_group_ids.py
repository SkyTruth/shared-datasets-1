#!/usr/bin/env python3
"""Generate deterministic shared-datasets group identifiers for vector features.

This helper works on GeoJSON-like features. It does not publish or mutate
canonical Cloud Storage objects; use the reviewed dataset publish workflow for
any canonical artifact replacement.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence


ALGORITHM = "shared-datasets-group-id:v1"
BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
COLLISION_PROBABILITY_THRESHOLD = 2e-10
DEFAULT_COLUMN = "shared_datasets_group_id"
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


def group_features(features: Sequence[dict[str, Any]], grouping_fields: Sequence[str]) -> tuple[dict[tuple[Any, ...], list[int]], int]:
    grouped: dict[tuple[Any, ...], list[int]] = {}
    blank_group_count = 0
    for index, feature in enumerate(features):
        properties = feature_properties(feature)
        values = tuple(normalize_group_value(properties.get(field)) for field in grouping_fields)
        if all(value is None for value in values):
            key: tuple[Any, ...] = ("__blank_feature__", index)
            blank_group_count += 1
        else:
            key = ("__group__", values)
        grouped.setdefault(key, []).append(index)
    return grouped, blank_group_count


def token_collisions(groups: Sequence[GroupSummary]) -> dict[str, set[str]]:
    by_token: dict[str, set[str]] = {}
    for group in groups:
        by_token.setdefault(group.token, set()).add(group.preimage)
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
    features: Sequence[dict[str, Any]],
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
    fields = validate_group_inputs(asset_slug, grouping_fields, column)
    output_features = [copy.deepcopy(feature) for feature in features]
    geometry_digests = [geometry_digest(feature.get("geometry")) for feature in output_features]
    grouped, blank_group_count = group_features(output_features, fields)
    group_count = len(grouped)
    resolved_length = token_length or token_length_for_group_count(group_count)
    if resolved_length < MIN_TOKEN_LENGTH:
        raise GroupIdError(f"token_length must be at least {MIN_TOKEN_LENGTH}")

    while True:
        groups = build_group_summaries(
            features=output_features,
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

    for group in groups:
        for index in group.feature_indexes:
            feature_properties(output_features[index])[column] = group.token

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

    return GroupIdResult(
        features=output_features,
        token_length=resolved_length,
        group_count=group_count,
        blank_group_count=blank_group_count,
        column=column,
        groups=groups,
        identical_preimage_group_count=identical_count,
        ambiguous_identical_geometry_groups=geometry_ambiguities,
        warnings=tuple(warnings),
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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add deterministic shared-datasets group IDs to GeoJSON features.")
    parser.add_argument("source", type=Path, help="GeoJSON FeatureCollection or newline-delimited GeoJSON features.")
    parser.add_argument("--asset-slug", required=True)
    parser.add_argument("--grouping-field", action="append", required=True, help="Grouping field; repeat for composite groups.")
    parser.add_argument("--column", default=DEFAULT_COLUMN)
    parser.add_argument("--token-length", type=int)
    parser.add_argument(
        "--fail-on-ambiguous-geometry",
        action="store_true",
        help="Fail when multiple grouping values share identical collective geometry and would receive the same ID.",
    )
    parser.add_argument("--out", type=Path, required=True, help="Output GeoJSON/GeoJSONSeq path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    features, feature_collection = load_geojson_features(args.source)
    result = add_group_ids(
        features,
        asset_slug=args.asset_slug,
        grouping_fields=args.grouping_field,
        column=args.column,
        token_length=args.token_length,
        fail_on_ambiguous_geometry=args.fail_on_ambiguous_geometry,
    )
    write_geojson_features(args.out, result.features, feature_collection=feature_collection)
    print(
        json.dumps(
            {
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
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
