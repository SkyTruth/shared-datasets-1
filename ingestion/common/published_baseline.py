"""Authenticate vector comparison metadata against its indexed publication."""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import json
from typing import Any

from google.api_core.exceptions import NotFound

from ingestion.common import release_index
from ingestion.common.source_fingerprint import latest_success_record
from scripts import release_feature_model as model


class MissingManifest(ValueError):
    """The latest published manifest needs reviewed repair."""


@dataclass(frozen=True)
class PublishedBaseline:
    record: dict[str, Any]
    manifest: dict[str, Any]
    records: tuple[dict[str, Any], ...]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def read(publisher, asset) -> PublishedBaseline:
    """Read indexed record → authenticated manifest → pinned, validated sidecar.

    Identity semantics belong to the job. A missing record/hash or mismatched
    publication fails; untrusted latest bytes never supply a fallback baseline.
    """
    blob = publisher.bucket.blob(asset.latest_object(".manifest.json"))
    try:
        blob.reload()
    except NotFound as exc:
        raise MissingManifest("published manifest is missing") from exc
    record = latest_success_record(publisher, asset)
    _require(record is not None, "baseline has no indexed successful run record")
    release = publisher.load_json(release_index.release_index_object(asset.slug))["latest_release"]
    _require((record.get("release_date") or record["run_date"]) == release["date"],
             "baseline record names another release")
    expected = model.validate_artifact_hash(record["sha256"]["manifest"], label="indexed manifest")
    raw = blob.download_as_bytes(if_generation_match=int(blob.generation))
    _require(hashlib.sha256(raw).hexdigest() == expected, "baseline manifest SHA-256 mismatch with indexed run record")
    manifest = json.loads(raw)
    artifacts = model.validate_release_manifest(manifest, expected_asset_slug=asset.slug,
        expected_release=release["date"], require_generations=True)

    def artifact(role, suffix):
        ref = artifacts[role]
        name = asset.release_object(dt.date.fromisoformat(release["date"]), suffix)
        _require(ref["path"] == f"gs://{publisher.bucket.name}/{name}",
                 f"baseline {role} must belong to its dated release")
        data = publisher.bucket.blob(name, generation=ref["generation"]).download_as_bytes(
            if_generation_match=ref["generation"])
        _require(hashlib.sha256(data).hexdigest() == ref["sha256"].removeprefix("sha256:"),
                 f"baseline {role} SHA-256 mismatch")
        return data

    schema = json.loads(artifact("schema", ".schema.json"))
    model.validate_release_schema(schema, expected_asset_slug=asset.slug, expected_release=release["date"])
    records = tuple(model.read_metadata_sidecar_bytes(artifact("metadata", ".metadata.ndjson.gz")))
    validation = model.validate_sidecar_records(records, expected_asset_slug=asset.slug, expected_release=release["date"])
    _require(validation.valid, f"invalid baseline metadata: {validation.errors}")
    _require(validation.feature_count == manifest["validation"]["feature_count"] == release["rows"],
             "baseline metadata count differs from the published manifest/index")
    return PublishedBaseline(record, manifest, records)
