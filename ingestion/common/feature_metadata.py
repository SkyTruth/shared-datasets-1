"""Feature metadata bundle helpers for scheduled vector ingestion jobs."""

from __future__ import annotations

import gzip
import io
import json
import logging
import re
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from scripts import release_feature_model

LOGGER = logging.getLogger(__name__)

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


def content_hashes(
    *,
    geometry: Mapping[str, Any] | None,
    properties: Mapping[str, Any],
    exclude_properties: Sequence[str] = (),
) -> tuple[str, str]:
    return release_feature_model.content_hashes(
        geometry=geometry,
        properties=properties,
        exclude_properties=exclude_properties,
    )


IDENTITY_BASELINE_FIELDS = ("feature_id", "geometry_hash", "properties_hash", "identity_key")


def identity_baseline_records(
    records: Iterable[Mapping[str, Any]],
    *,
    exclude_properties: Sequence[str],
) -> list[dict[str, Any]]:
    """Project previous-release records down to the fields identity work needs.

    Identity comparison and feature_id assignment read only feature_id, the two
    content hashes, and the identity key. Dropping `properties` here keeps the
    previous-release baseline proportional to the identity data rather than to
    the full previous release, which for large assets is the difference between
    a few hundred megabytes and several gigabytes.

    When `exclude_properties` is set, properties_hash and the derived content
    identity key are recomputed from identity-only properties before the
    projection discards them.
    """

    baseline: list[dict[str, Any]] = []
    for record in records:
        payload = asdict(record) if is_dataclass(record) and not isinstance(record, type) else dict(record)
        properties = payload.get("properties")
        if exclude_properties and isinstance(properties, Mapping):
            properties_hash = release_feature_model.properties_hash(
                properties,
                exclude_properties=exclude_properties,
            )
            payload["properties_hash"] = properties_hash
            geometry_hash = str(payload.get("geometry_hash") or "")
            if geometry_hash:
                payload["identity_key"] = list(
                    release_feature_model.content_identity_key(
                        geometry_hash_value=geometry_hash,
                        properties_hash_value=properties_hash,
                    )
                )
        baseline.append({field: payload[field] for field in IDENTITY_BASELINE_FIELDS if field in payload})
    return baseline


class SchemaAccumulator:
    """Derive a release schema from sidecar records one record at a time."""

    def __init__(self) -> None:
        self._field_names: list[str] = []
        self._observed: dict[str, Any] = {}
        self._nullable: dict[str, bool] = {}

    def observe(self, record: Mapping[str, Any]) -> None:
        properties = record.get("properties") if isinstance(record.get("properties"), Mapping) else {}
        for name, value in properties.items():
            field_name = str(name)
            if field_name not in self._nullable:
                self._field_names.append(field_name)
                self._nullable[field_name] = False
            if value is None:
                self._nullable[field_name] = True
            elif field_name not in self._observed:
                self._observed[field_name] = value

    def payload(self, *, asset_slug: str, release: str) -> dict[str, Any]:
        return {
            "schema_version": RELEASE_SCHEMA_VERSION,
            "asset_slug": asset_slug,
            "release": release,
            "fields": [
                {
                    "name": name,
                    "type": schema_type(self._observed.get(name)),
                    "nullable": self._nullable.get(name, True),
                    "projectable": True,
                }
                for name in self._field_names
            ],
        }


def assign_generated_feature_ids(
    identity_keys: Iterable[Sequence[str]],
    *,
    previous_records: Iterable[Mapping[str, Any]] | None = None,
    feature_id_overrides: Mapping[Sequence[str], str] | None = None,
    force_new_identity_keys: Iterable[Sequence[str]] = (),
) -> dict[tuple[str, ...], str]:
    try:
        return release_feature_model.assign_generated_feature_ids(
            identity_keys,
            previous_records=previous_records,
            feature_id_overrides=feature_id_overrides,
            force_new_identity_keys=force_new_identity_keys,
        )
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
    identity_excluded_properties: Sequence[str] = (),
    content_hash_pair: tuple[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if content_hash_pair is None:
        geometry_hash, properties_hash = content_hashes(
            geometry=geometry,
            properties=source_properties,
            exclude_properties=identity_excluded_properties,
        )
    else:
        geometry_hash, properties_hash = content_hash_pair
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


@dataclass(frozen=True)
class _PlannedFeature:
    """Identity decision for one emitted release row, without its payload."""

    ordinal: int
    identity_key: tuple[str, ...]
    geometry_hash: str
    properties_hash: str
    duplicate_source_row_numbers: tuple[int, ...]


@dataclass(frozen=True)
class GeneratedIdentityRelease:
    """Result of a written generated-identity release.

    Holds aggregates only. A release large enough to matter must never be
    represented in memory as a list of features, so this deliberately exposes
    no record collections.
    """

    feature_count: int
    schema_payload: dict[str, Any]
    next_generated_feature_id: int
    identity_decisions: dict[str, Any]


def _plan_generated_identities(
    features: Iterable[Mapping[str, Any]],
    *,
    asset_slug: str,
    source_fields: Sequence[str],
    identity_excluded_properties: Sequence[str],
) -> list[_PlannedFeature]:
    """First pass: derive identity for every source row, retaining no payloads."""

    planned: list[_PlannedFeature] = []
    duplicates_by_key: dict[tuple[str, ...], list[int]] = {}
    index_by_key: dict[tuple[str, ...], int] = {}
    for ordinal, feature in enumerate(features, start=1):
        source_properties = feature.get("properties") or {}
        geometry_hash, properties_hash = content_hashes(
            geometry=feature.get("geometry"),
            properties=source_properties,
            exclude_properties=identity_excluded_properties,
        )
        if source_fields:
            identity_key = release_feature_model.source_fields_identity_key(source_properties, source_fields)
        else:
            identity_key = release_feature_model.content_identity_key(
                geometry_hash_value=geometry_hash,
                properties_hash_value=properties_hash,
            )
        existing_index = index_by_key.get(identity_key)
        if existing_index is not None:
            first = planned[existing_index]
            if first.geometry_hash != geometry_hash or first.properties_hash != properties_hash:
                raise RuntimeError(
                    f"duplicate generated identity key with different content in {asset_slug}: "
                    f"rows {first.ordinal} and {ordinal}"
                )
            duplicates_by_key.setdefault(identity_key, []).append(ordinal)
            continue
        index_by_key[identity_key] = len(planned)
        planned.append(
            _PlannedFeature(
                ordinal=ordinal,
                identity_key=identity_key,
                geometry_hash=geometry_hash,
                properties_hash=properties_hash,
                duplicate_source_row_numbers=(),
            )
        )
    if not duplicates_by_key:
        return planned
    return [
        (
            row
            if row.identity_key not in duplicates_by_key
            else _PlannedFeature(
                ordinal=row.ordinal,
                identity_key=row.identity_key,
                geometry_hash=row.geometry_hash,
                properties_hash=row.properties_hash,
                duplicate_source_row_numbers=tuple(duplicates_by_key[row.identity_key]),
            )
        )
        for row in planned
    ]


def write_generated_id_release(
    *,
    open_features: Callable[[], Iterable[Mapping[str, Any]]],
    asset_slug: str,
    release: str,
    provenance: Mapping[str, Any],
    enriched_features_path: Path,
    sidecar_path: Path,
    source_fields: Sequence[str] = (),
    previous_records: Iterable[Mapping[str, Any]] | None = None,
    identity_resolution_decisions: Iterable[Mapping[str, Any]] | None = None,
    identity_excluded_properties: Sequence[str] = (),
    identity_ambiguity_match_properties: bool = True,
    sidecar_sink: Callable[[Mapping[str, Any]], None] | None = None,
) -> GeneratedIdentityRelease:
    """Write an enriched GeoJSONSeq and metadata sidecar with bounded memory.

    Two passes over `open_features()`:

    1. Derive each row's identity key and content hashes, keeping only those
       (no geometry, no properties), then resolve ambiguities and assign every
       feature_id. Nothing is written, so an unresolved ambiguity aborts before
       any output exists.
    2. Re-read the source and stream each enriched feature and sidecar record
       straight to disk, accumulating only the release schema, the feature
       count, and the highest assigned feature_id.

    Peak memory is therefore proportional to the identity data (a key and two
    hashes per feature) rather than to the release payload, which is what makes
    large assets publishable at all.

    Raises if any ambiguity is left unresolved by `identity_resolution_decisions`.
    """

    identity_baseline = identity_baseline_records(
        previous_records or (),
        exclude_properties=identity_excluded_properties,
    )
    planned = _plan_generated_identities(
        open_features(),
        asset_slug=asset_slug,
        source_fields=source_fields,
        identity_excluded_properties=identity_excluded_properties,
    )
    if not planned:
        raise RuntimeError(f"{asset_slug} metadata sidecar would be empty")

    scan = release_feature_model.find_identity_ambiguities(
        (
            {
                "geometry_hash": row.geometry_hash,
                "properties_hash": row.properties_hash,
                "identity_key": row.identity_key,
            }
            for row in planned
        ),
        previous_records=identity_baseline,
        match_properties=identity_ambiguity_match_properties,
    )
    try:
        resolutions = release_feature_model.validate_identity_resolutions(
            release=release,
            ambiguities=scan.ambiguities,
            decisions=identity_resolution_decisions or (),
        )
    except release_feature_model.ReleaseFeatureModelError as exc:
        raise RuntimeError(str(exc)) from exc
    unresolved = release_feature_model.unresolved_identity_ambiguities(scan.ambiguities, resolutions)
    if unresolved:
        raise_unresolved_identity_ambiguities(
            asset_slug=asset_slug,
            release=release,
            ambiguities=unresolved,
        )
    ids_by_key = assign_generated_feature_ids(
        (row.identity_key for row in planned),
        previous_records=identity_baseline,
        feature_id_overrides=release_feature_model.resolved_feature_id_overrides(resolutions),
        force_new_identity_keys=release_feature_model.resolved_force_new_identity_keys(resolutions),
    )
    # The baseline is only needed to decide identity. Release it before the
    # write pass so the previous release's hashes do not sit alongside the
    # current release's buffers.
    del identity_baseline

    schema = SchemaAccumulator()
    highest_feature_id = 0

    def _release_records() -> Iterator[Mapping[str, Any]]:
        nonlocal highest_feature_id
        with enriched_features_path.open("w", encoding="utf-8") as enriched_file:
            plan_index = 0
            for ordinal, feature in enumerate(open_features(), start=1):
                if plan_index >= len(planned):
                    break
                row = planned[plan_index]
                if row.ordinal != ordinal:
                    # Collapsed duplicate of an already-emitted identity key.
                    continue
                plan_index += 1
                feature_id = ids_by_key[row.identity_key]
                provenance_payload = {
                    **dict(provenance),
                    "source_row_number": row.ordinal,
                    "identity_key": list(row.identity_key),
                }
                if row.duplicate_source_row_numbers:
                    provenance_payload["duplicate_source_row_numbers"] = list(row.duplicate_source_row_numbers)
                enriched_feature, sidecar = _feature_record(
                    asset_slug=asset_slug,
                    release=release,
                    feature_id=feature_id,
                    geometry=feature.get("geometry"),
                    source_properties=feature.get("properties") or {},
                    provenance=provenance_payload,
                    identity_key=row.identity_key,
                    identity_excluded_properties=identity_excluded_properties,
                    content_hash_pair=(row.geometry_hash, row.properties_hash),
                )
                enriched_file.write(canonical_json(enriched_feature) + "\n")
                schema.observe(sidecar)
                if feature_id.isdigit():
                    highest_feature_id = max(highest_feature_id, int(feature_id))
                if sidecar_sink is not None:
                    sidecar_sink(sidecar)
                yield sidecar
            if plan_index != len(planned):
                raise RuntimeError(
                    f"{asset_slug} source changed between identity and write passes: "
                    f"wrote {plan_index} of {len(planned)} planned features"
                )

    written = write_sidecar(_release_records(), sidecar_path)
    if written != len(planned):
        raise RuntimeError(
            f"{asset_slug} sidecar wrote {written} records for {len(planned)} planned features"
        )
    return GeneratedIdentityRelease(
        feature_count=written,
        schema_payload=schema.payload(asset_slug=asset_slug, release=release),
        next_generated_feature_id=highest_feature_id + 1,
        identity_decisions=release_feature_model.build_identity_decisions(
            ambiguities_detected=len(scan.ambiguities),
            key_corroborated=scan.key_corroborated_count,
            resolutions=resolutions,
        ),
    )


AMBIGUITY_TYPE_EXPLANATIONS = {
    "same_geometry_changed_properties": (
        "same footprint as an existing feature, but its attributes changed"
    ),
    "same_properties_changed_geometry": (
        "same attributes as an existing feature, but its footprint moved"
    ),
    "conflicting_partial_matches": (
        "partially matches more than one existing feature"
    ),
}


def identity_ambiguity_alert_body(
    *,
    asset_slug: str,
    release: str,
    ambiguities: Sequence[release_feature_model.IdentityAmbiguity],
    limit: int = 5,
) -> str:
    """Write the Slack body for a release paused on a maintainer decision.

    This is a request for a decision, not an outage report. The wording says so
    up front, states what is and is not affected, and gives the exact next step,
    because an alert that reads like a broken pipeline gets escalated or
    ignored rather than acted on.
    """

    visible = list(ambiguities[:limit])
    count = len(ambiguities)
    noun = "feature" if count == 1 else "features"
    lines = [
        f"*{asset_slug}* is ready to publish release `{release}`, but {count} {noun} "
        "need a maintainer to confirm identity before it can go out.",
        "",
        "*Nothing is broken and nothing is at risk.* The published dataset is unchanged, "
        "the job stopped before writing anything, and it will keep retrying on its normal "
        "schedule. This release simply waits until someone decides.",
        "",
        "*Why this happens:* shared-datasets keeps a stable `feature_id` for each feature "
        "across releases. When the source changes a record in a way that makes its identity "
        "genuinely uncertain, the job asks rather than guesses, because a wrong guess "
        "silently breaks every saved reference to that feature.",
        "",
        "*What to do:* add reviewed decisions to "
        f"`catalog/feature-identity-resolutions/{asset_slug}.json` and merge them. "
        "The job picks them up on its next run. See "
        "`catalog/feature-identity-resolutions/README.md`.",
    ]
    if visible:
        lines.append("")
        lines.append(f"*{'Case' if count == 1 else 'Cases'} needing a decision:*")
    for index, ambiguity in enumerate(visible, start=1):
        explanation = AMBIGUITY_TYPE_EXPLANATIONS.get(ambiguity.ambiguity_type, ambiguity.ambiguity_type)
        lines.extend(
            [
                f"{index}. Source key `{'/'.join(ambiguity.identity_key)}` — {explanation}.",
                f"    Existing feature(s) it resembles: "
                f"`{', '.join(ambiguity.matching_geometry_feature_ids + ambiguity.matching_properties_feature_ids) or 'none'}`",
            ]
        )
    if count > len(visible):
        lines.append(
            f"\n_{count - len(visible)} more of the same kind. Full evidence for every case is in the "
            "job logs, one line each, searchable for `identity ambiguity evidence`._"
        )
    return "\n".join(lines)


def notify_identity_ambiguities(
    *,
    asset_slug: str,
    release: str,
    ambiguities: Sequence[release_feature_model.IdentityAmbiguity],
) -> bool:
    from scripts.slack_notify import notify

    count = len(ambiguities)
    return notify(
        title=f"Decision needed: {asset_slug} release {release}",
        body=identity_ambiguity_alert_body(asset_slug=asset_slug, release=release, ambiguities=ambiguities),
        status="decision",
        fields={
            "asset_slug": asset_slug,
            "release": release,
            "awaiting decisions": str(count),
            "published data": "unchanged",
        },
        strict=False,
    )


IDENTITY_AMBIGUITY_MESSAGE_LIMIT = 10

# Greppable marker for a release that stopped to ask, so log-based alerting can
# tell it apart from a failure without parsing prose.
RELEASE_BLOCKED_MARKER = "shared_datasets_release_blocked=identity_decision_required"


class IdentityDecisionRequired(RuntimeError):
    """A release stopped because a maintainer must confirm feature identity.

    Distinct from the errors that mean something broke. The pipeline is working
    as designed: it declined to guess an identity, wrote nothing, and is waiting
    for a reviewed decision. Jobs report this as a paused outcome rather than a
    failed execution so it does not read as an outage.
    """

    def __init__(
        self,
        message: str,
        *,
        asset_slug: str,
        release: str,
        ambiguity_count: int,
    ) -> None:
        super().__init__(message)
        self.asset_slug = asset_slug
        self.release = release
        self.ambiguity_count = ambiguity_count

    def blocked_record(self) -> dict[str, Any]:
        return {
            "asset_slug": self.asset_slug,
            "release": self.release,
            "status": "blocked",
            "reason": "identity_decision_required",
            "awaiting_decisions": self.ambiguity_count,
            "next_step": (
                "Add reviewed decisions to "
                f"catalog/feature-identity-resolutions/{self.asset_slug}.json and merge them."
            ),
        }


def raise_unresolved_identity_ambiguities(
    *,
    asset_slug: str,
    release: str,
    ambiguities: Sequence[release_feature_model.IdentityAmbiguity],
) -> None:
    if not ambiguities:
        return
    notify_identity_ambiguities(asset_slug=asset_slug, release=release, ambiguities=ambiguities)
    # One log line per ambiguity: a single entry holding all evidence can
    # exceed the Cloud Logging entry size limit and get truncated, which
    # leaves maintainers without the evidence the resolutions file needs.
    payloads = release_feature_model.identity_ambiguities_to_dicts(ambiguities)
    total = len(payloads)
    for index, payload in enumerate(payloads, start=1):
        LOGGER.error(
            "identity ambiguity evidence %s %s %d/%d: %s",
            asset_slug,
            release,
            index,
            total,
            json.dumps(payload, sort_keys=True),
        )
    visible = payloads[:IDENTITY_AMBIGUITY_MESSAGE_LIMIT]
    suffix = (
        ""
        if total <= len(visible)
        else (
            f" ... {total - len(visible)} more; full evidence is in the "
            "per-ambiguity 'identity ambiguity evidence' log lines."
        )
    )
    raise IdentityDecisionRequired(
        f"{asset_slug} release {release} is waiting on {total} maintainer identity decision(s); "
        "nothing was published: "
        + json.dumps(visible, sort_keys=True)
        + suffix,
        asset_slug=asset_slug,
        release=release,
        ambiguity_count=total,
    )


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


def write_sidecar(records: Iterable[Mapping[str, Any]], path: Path) -> int:
    """Write a metadata sidecar, validating records as they stream past.

    Validation and writing share one pass so callers never need the whole
    release in memory. `validate_sidecar_records` already accumulates only
    small per-release state (seen feature_ids and identity keys), so the
    written file is validated in full. An invalid release raises after the
    partial file is written; every caller writes to a scratch workdir and the
    raise prevents the file from being used or uploaded.
    """

    written = 0

    def _write_through(source: Iterable[Mapping[str, Any]], file_obj: io.TextIOWrapper) -> Iterator[Mapping[str, Any]]:
        nonlocal written
        for record in source:
            payload = asdict(record) if is_dataclass(record) and not isinstance(record, type) else dict(record)
            file_obj.write(canonical_json(payload) + "\n")
            written += 1
            yield payload

    with path.open("wb") as raw_file:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as gzip_file:
            with io.TextIOWrapper(gzip_file, encoding="utf-8", newline="\n") as file_obj:
                validation = release_feature_model.validate_sidecar_records(_write_through(records, file_obj))
    if not validation.valid:
        raise RuntimeError("metadata sidecar validation failed: " + "; ".join(validation.errors))
    return written


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


def schema_from_records(*, asset_slug: str, release: str, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    accumulator = SchemaAccumulator()
    for record in records:
        accumulator.observe(record)
    return accumulator.payload(asset_slug=asset_slug, release=release)


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
