"""Authenticate vector comparison metadata against the claimed release index."""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import json
from typing import Any
import zlib

from google.api_core.exceptions import GoogleAPIError

from ingestion.common import release_index, source_quality
from scripts import release_feature_model as model


class BaselineContractError(ValueError):
    """Published baseline bytes or references fail their persisted contract."""


class MissingManifest(BaselineContractError):
    """The indexed publication has no dated manifest."""


@dataclass(frozen=True)
class PublishedBaseline:
    manifest: dict[str, Any]
    records: tuple[dict[str, Any], ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _generation(generation):
    _require(type(generation) in {int, str} and str(generation).isdigit() and int(generation) > 0,
             "baseline reference needs a positive generation")
    return int(generation)


def _reference(ref):
    """Normalize a manifest artifact reference before storage I/O."""
    _require(type(ref["size"]) is int and ref["size"] >= 0, "baseline reference needs a byte size")
    return {"path": ref["path"], "generation": _generation(ref["generation"]), "size": ref["size"],
            "sha256": release_index.normalize_sha256(ref["sha256"], label=ref["path"])}


def read(publisher, asset, release) -> PublishedBaseline:
    """Read the Guard's dated manifest generation, then its pinned artifacts.

    The generation identifies the manifest bytes; the manifest pins dated schema
    and metadata by generation and hash. Replacing metadata, a latest manifest
    and a run-record hash cannot substitute that chain. Index hashes can be stale
    after reviewed metadata updates and supply no authority. No run record or
    latest object is consulted.
    """
    try:
        if release is None:
            raise MissingManifest("published manifest is missing")
        day = dt.date.fromisoformat(release["date"])
        files = release["files"]
        _require(isinstance(files, list) and all(isinstance(ref, dict) for ref in files),
                 "baseline index files must be objects")
        _require(type(release["rows"]) is int and release["rows"] > 0, "baseline index needs a positive row count")

        def dated_path(suffix):
            return f"gs://{publisher.bucket.name}/{asset.release_object(day, suffix)}"

        refs = [ref for ref in files if ref.get("path") == dated_path(".manifest.json")]
        if not refs:
            raise MissingManifest("published manifest is missing from the index")
        _require(len(refs) == 1, "baseline index must pin exactly one .manifest.json object")
        ref = refs[0]
        generation = _generation(ref["generation"])
        raw = publisher.bucket.blob(asset.release_object(day, ".manifest.json"), generation=generation).download_as_bytes(
            if_generation_match=generation)
        if "size" in ref:
            _require(type(ref["size"]) is int and ref["size"] >= 0, "baseline manifest needs a byte size")
            _require(len(raw) == ref["size"], "baseline manifest size differs from the index")
        manifest = json.loads(raw)
        _require(isinstance(manifest, dict), "baseline manifest must be an object")
        artifacts = model.validate_release_manifest(manifest, expected_asset_slug=asset.slug,
            expected_release=release["date"], require_generations=True)

        def artifact(role, suffix):
            ref = _reference(artifacts[role])
            _require(ref["path"] == dated_path(suffix), f"baseline {role} must name its dated release object")
            return source_quality.pinned(publisher.bucket.client, ref)

        schema = json.loads(artifact("schema", ".schema.json"))
        _require(isinstance(schema, dict), "baseline schema must be an object")
        model.validate_release_schema(schema, expected_asset_slug=asset.slug, expected_release=release["date"])
        records = tuple(model.read_metadata_sidecar_bytes(artifact("metadata", ".metadata.ndjson.gz")))
        validation = model.validate_sidecar_records(records, expected_asset_slug=asset.slug, expected_release=release["date"])
        _require(validation.valid, f"invalid baseline metadata: {validation.errors}")
        _require(validation.feature_count == manifest["validation"]["feature_count"] == release["rows"],
                 "baseline metadata count differs from the published manifest/index")
        return PublishedBaseline(manifest, records)
    except MissingManifest:
        raise
    except (GoogleAPIError, source_quality.SourceLossError, release_index.ReleaseIndexError, ValueError, KeyError, TypeError,
            AttributeError, IndexError, OSError, EOFError, zlib.error) as exc:
        raise BaselineContractError(f"invalid indexed baseline: {exc}") from exc
