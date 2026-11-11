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


class MissingManifest(ValueError):
    """The indexed publication has no dated manifest."""


@dataclass(frozen=True)
class PublishedBaseline:
    manifest: dict[str, Any]
    records: tuple[dict[str, Any], ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _reference(ref, *, require_hash=True):
    """Normalize the persisted reference once before comparison or storage I/O."""
    generation = ref["generation"]
    _require(type(generation) in {int, str} and str(generation).isdigit() and int(generation) > 0,
             "baseline reference needs a positive generation")
    _require(type(ref["size"]) is int and ref["size"] >= 0, "baseline reference needs a byte size")
    value = {"path": ref["path"], "generation": int(generation), "size": ref["size"]}
    if require_hash or "sha256" in ref:
        value["sha256"] = release_index.normalize_sha256(ref["sha256"], label=ref["path"])
    return value


def read(publisher, asset, release) -> PublishedBaseline:
    """Read the Guard's dated manifest, schema and metadata, pinned by its index.

    Older manifests may be pinned by generation and size alone. Canonical
    metadata and schema require hashes in both the index and manifest. No run
    record or latest object supplies publication authority.
    """
    try:
        if release is None:
            raise MissingManifest("published manifest is missing")
        day = dt.date.fromisoformat(release["date"])
        files = release["files"]
        _require(isinstance(files, list) and all(isinstance(ref, dict) for ref in files),
                 "baseline index files must be objects")
        _require(type(release["rows"]) is int and release["rows"] > 0, "baseline index needs a positive row count")

        def indexed(suffix, *, require_hash=True):
            path = f"gs://{publisher.bucket.name}/{asset.release_object(day, suffix)}"
            refs = [ref for ref in files if ref.get("path") == path]
            if not refs and suffix == ".manifest.json":
                raise MissingManifest("published manifest is missing from the index")
            _require(len(refs) == 1, f"baseline index must pin exactly one {suffix} object")
            return _reference(refs[0], require_hash=require_hash)

        ref = indexed(".manifest.json", require_hash=False)
        if "sha256" in ref:
            raw = source_quality.pinned(publisher.bucket.client, ref)
        else:
            name = ref["path"].split("/", 3)[3]
            raw = publisher.bucket.blob(name, generation=ref["generation"]).download_as_bytes(
                if_generation_match=ref["generation"])
            _require(len(raw) == ref["size"], "baseline manifest size differs from the index")
        manifest = json.loads(raw)
        _require(isinstance(manifest, dict), "baseline manifest must be an object")
        artifacts = model.validate_release_manifest(manifest, expected_asset_slug=asset.slug,
            expected_release=release["date"], require_generations=True)

        def artifact(role, suffix):
            ref = indexed(suffix)
            _require(_reference(artifacts[role]) == ref, f"baseline {role} differs from its indexed object")
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
    except (GoogleAPIError, source_quality.SourceLossError, ValueError, KeyError, TypeError,
            AttributeError, IndexError, OSError, EOFError, zlib.error) as exc:
        raise ValueError(f"invalid indexed baseline: {exc}") from exc
