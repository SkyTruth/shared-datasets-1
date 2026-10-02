"""Portable workspace v1 validation and direct generation-pinned fetching.

The normative specification is docs/standards/workspace-snapshot-v1.md.
Browser, TypeScript, and Python use the shared rejection corpus in tests/fixtures.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .catalog import (
    DEFAULT_BUCKET,
    DatasetRef,
    SharedDatasetsError,
    CatalogLoadError,
    _artifact_url,
    _fetch_ref,
    _unique_json_object,
)

MAX_BYTES = 1024 * 1024
FORMATS = {
    "fgb": r"\.fgb$",
    "csv": r"\.csv$",
    "geojson": r"\.geojson$",
    "ndgeojson": r"\.(ndgeojson|geojsonl)$",
    "cog": r"\.(tif|tiff)$",
    "pmtiles": r"\.pmtiles$",
    "metadata": r"\.metadata(?:\.[a-z]{2,3}(?:_[a-z0-9]{2,8})*)?\.ndjson\.gz$",
    "schema": r"\.schema\.json$",
}
PROVENANCE_KEYS = "title citation source source_url license notes status lifecycle_reason lifecycle_date successor_asset_slug consumer_guidance source_version release_index_uri release_index_generation release_index_updated_at identity_contract".split()
IDENTITY_KEYS = "strategy source_fields generated_id_type assignment_key feature_id_column geometry_hash_column properties_hash_column".split()


class SnapshotError(SharedDatasetsError, ValueError):
    """Malformed, unsafe, or unsupported portable snapshot."""


def _fail(message):
    raise SnapshotError(f"Invalid workspace: {message}")


def _object(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        _fail(f"{name} has unknown or missing fields")


def _text(value, name, nullable=False, maximum=16384):
    if nullable and value is None:
        return
    if (
        not isinstance(value, str)
        or len(value.encode("utf-16-le")) // 2 > maximum
        or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value)
    ):
        _fail(f"{name} must be bounded text")


def _locale(value):
    if value is not None and (
        not isinstance(value, str)
        or len(value) > 64
        or not re.fullmatch(r"[a-z]{2,3}(?:_[a-z0-9]{2,8})*", value)
    ):
        _fail("invalid locale")


def _generation(value, nullable=False):
    if nullable and value is None:
        return
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[1-9][0-9]{0,19}", value)
        or int(value) > 2**64 - 1
    ):
        _fail("generation must be a positive decimal string within uint64")


def _uri(value, bucket):
    _text(value, "gs_uri", maximum=2048)
    prefix = f"gs://{bucket}/"
    if (
        not value.startswith(prefix)
        or re.search(r"[?#%\\\s*\[\]]", value)
        or any(p in {"", ".", ".."} for p in value[len(prefix) :].split("/"))
    ):
        _fail("URI is outside the workspace bucket or has unsafe path components")
    return value[len(prefix) :]


def _number(value, minimum, maximum, name):
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        _fail(f"invalid {name}")


def validate_snapshot(snapshot: Any, *, bucket: str = DEFAULT_BUCKET) -> dict:
    """Validate the whole document, returning an independent JSON value.

    Bucket trust comes from the caller, never from the imported document.
    No remote access, release resolution, or signing occurs during validation.
    """
    try:
        if isinstance(snapshot, (str, bytes)):
            if (
                len(snapshot.encode() if isinstance(snapshot, str) else snapshot)
                > MAX_BYTES
            ):
                _fail("JSON exceeds 1 MiB")
            snapshot = json.loads(snapshot, object_pairs_hook=_unique_json_object)

        def depth(value, level=0):
            if level > 12 or (isinstance(value, float) and not math.isfinite(value)):
                _fail("JSON nesting exceeds 12")
            if isinstance(value, dict):
                for item in value.values():
                    depth(item, level + 1)
            elif isinstance(value, list):
                for item in value:
                    depth(item, level + 1)

        depth(snapshot)
        if (
            len(
                json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).encode()
            )
            > MAX_BYTES
        ):
            _fail("JSON exceeds 1 MiB")
        _object(
            snapshot,
            "kind schema_version bucket datasets presentation".split(),
            "snapshot",
        )
        if (
            snapshot["kind"] != "skytruth-workspace"
            or type(snapshot["schema_version"]) not in (int, float)
            or snapshot["schema_version"] != 1
        ):
            _fail("unrecognized kind or schema_version")
        if snapshot["bucket"] != bucket or not re.fullmatch(
            r"[a-z0-9][a-z0-9._-]{2,221}", bucket
        ):
            _fail("bucket is not allowed")
        datasets = snapshot["datasets"]
        if not isinstance(datasets, list) or not 1 <= len(datasets) <= 32:
            _fail("expected 1–32 datasets")
        slugs, uris = set(), set()
        for dataset in datasets:
            _object(
                dataset,
                "asset_slug release canonical_format access_tier artifacts provenance".split(),
                "dataset",
            )
            slug, date = dataset["asset_slug"], dataset["release"]
            if (
                not isinstance(slug, str)
                or len(slug) > 128
                or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug)
                or slug in slugs
            ):
                _fail("invalid or duplicate asset_slug")
            slugs.add(slug)
            if date is not None:
                if not isinstance(date, str) or not re.fullmatch(
                    r"\d{4}-\d{2}-\d{2}", date
                ):
                    _fail("invalid release date")
                dt.date.fromisoformat(date)
            if (
                dataset["access_tier"] not in {"public", "private", "internal"}
                or dataset["canonical_format"] not in FORMATS
                or dataset["canonical_format"] in {"metadata", "schema"}
            ):
                _fail("unsupported tier or canonical format")
            artifacts = dataset["artifacts"]
            if not isinstance(artifacts, list) or not 1 <= len(artifacts) <= 8:
                _fail("expected 1–8 artifacts")
            roles, roots, canonical = set(), set(), 0
            for artifact in artifacts:
                _object(
                    artifact,
                    "format role gs_uri generation size sha256 requested_locale resolved_locale".split(),
                    "artifact",
                )
                fmt, role = artifact["format"], artifact["role"]
                if fmt not in FORMATS or role not in {
                    "canonical",
                    "tiles",
                    "metadata",
                    "schema",
                }:
                    _fail("unsupported artifact format or role")
                path = _uri(artifact["gs_uri"], bucket)
                parts = path.split("/")
                location = ["latest"] if date is None else ["releases", date]
                if (
                    parts[-len(location) - 2 : -1] != [slug, *location]
                    or len(parts) < len(location) + 4
                    or not re.search(FORMATS[fmt], path)
                ):
                    _fail(
                        "artifact is outside the asset release or has the wrong extension"
                    )
                roots.add(re.split(r"/(?:latest|releases)/", artifact["gs_uri"])[0])
                _generation(artifact["generation"])
                size, checksum = artifact["size"], artifact["sha256"]
                if size is not None and (
                    type(size) not in (int, float)
                    or not float(size).is_integer()
                    or not 0 <= size <= 2**53 - 1
                ):
                    _fail("size must be a nonnegative safe integer or null")
                if checksum is not None and (
                    not isinstance(checksum, str)
                    or not re.fullmatch(r"[a-f0-9]{64}", checksum)
                ):
                    _fail("invalid SHA-256")
                _locale(artifact["requested_locale"])
                _locale(artifact["resolved_locale"])
                role_key = (role, artifact["resolved_locale"])
                if artifact["gs_uri"] in uris or role_key in roles:
                    _fail("duplicate artifact URI or role/locale")
                uris.add(artifact["gs_uri"])
                roles.add(role_key)
                if role == "canonical":
                    canonical += 1
                    if fmt != dataset["canonical_format"]:
                        _fail("canonical format mismatch")
                if (
                    role in {"tiles", "metadata", "schema"}
                    and fmt
                    != {"tiles": "pmtiles", "metadata": "metadata", "schema": "schema"}[
                        role
                    ]
                ):
                    _fail("role/format mismatch")
                if role != "metadata" and (
                    artifact["requested_locale"] is not None
                    or artifact["resolved_locale"] is not None
                ):
                    _fail("locale only applies to metadata")
                if role == "metadata":
                    locale = artifact["resolved_locale"]
                    suffix = (
                        f".metadata.{locale}.ndjson.gz"
                        if locale
                        else ".metadata.ndjson.gz"
                    )
                    if not path.endswith(suffix):
                        _fail("metadata locale/path mismatch")
            if canonical != 1 or len(roots) != 1:
                _fail(
                    "exactly one canonical artifact and one shared asset root required"
                )
            provenance = dataset["provenance"]
            _object(provenance, PROVENANCE_KEYS, "provenance")
            for key in set(PROVENANCE_KEYS) - {
                "identity_contract",
                "release_index_generation",
            }:
                _text(provenance[key], key, nullable=True)
            _generation(provenance["release_index_generation"], nullable=True)
            if (
                provenance["release_index_uri"] is not None
                and _uri(provenance["release_index_uri"], bucket)
                != f"_catalog/releases/{slug}.json"
            ):
                _fail("invalid release-index provenance")
            source = provenance["source_url"]
            if source is not None:
                publication = urlsplit(source)
                publication.port  # Reject malformed ports at the URL boundary.
                secret_key = r"token|access_token|id_token|signature|sig|credential|key|api_key|apikey|x-goog-.*|x-amz-.*"
                if (
                    publication.scheme not in {"http", "https"}
                    or not publication.hostname
                    or publication.username
                    or publication.password
                    or any(
                        re.fullmatch(secret_key, key, re.I)
                        for key, _ in parse_qsl(
                            publication.query, keep_blank_values=True
                        )
                    )
                ):
                    _fail("source_url must be a non-secret HTTP URL")
            identity = provenance["identity_contract"]
            if identity is not None:
                if not isinstance(identity, dict) or set(identity) - set(IDENTITY_KEYS):
                    _fail("invalid identity contract")
                for value in identity.values():
                    if isinstance(value, list):
                        if len(value) > 32:
                            _fail("identity fields exceed limit")
                        for entry in value:
                            _text(entry, "identity field", maximum=128)
                    else:
                        _text(value, "identity field", maximum=128)
        presentation = snapshot["presentation"]
        if presentation is not None:
            _object(
                presentation, "basemap viewport locale layers".split(), "presentation"
            )
            if presentation["basemap"] not in {"map", "satellite"}:
                _fail("unsupported basemap")
            _locale(presentation["locale"])
            layers = presentation["layers"]
            if not isinstance(layers, list) or len(layers) != len(slugs):
                _fail("presentation must name all datasets")
            seen = set()
            for layer in layers:
                _object(
                    layer,
                    "asset_slug visible source_layer color_field".split(),
                    "layer",
                )
                if (
                    layer["asset_slug"] not in slugs
                    or layer["asset_slug"] in seen
                    or layer["visible"] is not True
                ):
                    _fail("invalid, duplicate, or unsupported layer visibility")
                seen.add(layer["asset_slug"])
                _text(layer["source_layer"], "source_layer", nullable=True, maximum=128)
                _text(layer["color_field"], "color_field", nullable=True, maximum=128)
                if len(layers) > 1 and (
                    layer["source_layer"] is not None
                    or layer["color_field"] is not None
                ):
                    _fail("multi-dataset styling is unsupported")
            viewport = presentation["viewport"]
            if viewport is not None:
                _object(viewport, "center zoom bearing pitch".split(), "viewport")
                if (
                    not isinstance(viewport["center"], list)
                    or len(viewport["center"]) != 2
                ):
                    _fail("invalid center")
                for value, low, high, name in [
                    (viewport["center"][0], -180, 180, "longitude"),
                    (viewport["center"][1], -85.051129, 85.051129, "latitude"),
                    (viewport["zoom"], 0, 22, "zoom"),
                    (viewport["bearing"], -180, 180, "bearing"),
                    (viewport["pitch"], 0, 85, "pitch"),
                ]:
                    _number(value, low, high, name)
        return json.loads(json.dumps(snapshot))
    except SnapshotError:
        raise
    except (
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        RecursionError,
        CatalogLoadError,
    ) as exc:
        raise SnapshotError(f"Invalid workspace: {exc}") from exc


@dataclass(frozen=True)
class FetchedSnapshotArtifact:
    """Published expectations and verified local evidence remain distinct."""

    ref: DatasetRef
    published_sha256: str | None
    published_size: int | None

    @property
    def cache_path(self) -> Path:
        assert self.ref.cache_path is not None
        return self.ref.cache_path

    def lineage(self) -> dict:
        return {
            "gs_uri": self.ref.gs_uri,
            "generation": str(self.ref.generation),
            "release": self.ref.last_updated or None,
            "resolved_id": self.ref.resolved_id,
            "published_sha256": self.published_sha256,
            "published_size": self.published_size,
            "verified_sha256": self.ref.sha256,
            "verified_size": self.ref.size,
        }


def fetch_snapshot_artifact(
    snapshot,
    asset_slug: str,
    *,
    role="canonical",
    locale=None,
    bucket=DEFAULT_BUCKET,
    access="gcs",
    client=None,
    cache_dir=None,
    force=False,
    timeout=60.0,
) -> FetchedSnapshotArtifact:
    """Download captured bytes using ADC by default; never read a release index.

    Unknown/unavailable generations and integrity mismatches fail. The existing
    disposable cache is keyed by exact URI/generation and rehashed on reuse.
    """
    snapshot = validate_snapshot(snapshot, bucket=bucket)
    matches = [
        (d, a)
        for d in snapshot["datasets"]
        if d["asset_slug"] == asset_slug
        for a in d["artifacts"]
        if a["role"] == role and a["resolved_locale"] == locale
    ]
    if len(matches) != 1:
        raise SnapshotError("Requested snapshot artifact is missing or ambiguous")
    dataset, artifact = matches[0]
    generation = int(artifact["generation"])
    ref = DatasetRef(
        slug=asset_slug,
        title=dataset["provenance"]["title"] or asset_slug,
        format=artifact["format"],
        gs_uri=artifact["gs_uri"],
        url=_artifact_url(artifact["gs_uri"], generation),
        last_updated=dataset["release"] or "",
        generation=generation,
        access_tier=dataset["access_tier"],
        sha256=artifact["sha256"],
        size=artifact["size"],
        release_index_generation=(
            int(dataset["provenance"]["release_index_generation"])
            if dataset["provenance"]["release_index_generation"]
            else None
        ),
    )
    fetched = _fetch_ref(
        ref,
        access=access,
        client=client,
        cache_dir=cache_dir,
        force=force,
        timeout=timeout,
    )
    return FetchedSnapshotArtifact(fetched, artifact["sha256"], artifact["size"])
