"""Publish MPAtlas zone snapshots only when canonical feature content changes."""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Mapping

from google.api_core.exceptions import NotFound
from google.cloud import storage

from ingestion.common import feature_metadata as fm, vector_pipeline, source_fingerprint as fingerprints
from ingestion.common.gcs import GcsPublisher
from ingestion.common.runtime import (
    configure_logging, download_file, parse_run_date, require_binary,
    run_command, run_job_main, sha256_file,
)
from scripts import release_feature_model as model

LOGGER = logging.getLogger("mpatlas_monthly")
SOURCE_URL = "https://guide.mpatlas.org/api/public/v4/zone/geojson/"
LAYER = "mpatlas_zone_assessments"
NESTED_FIELDS = frozenset({"affiliated_reports", "assessment_projects", "classes"})


@dataclass(frozen=True)
class AssetSpec(vector_pipeline.AssetPaths):
    slug: str = "mpatlas-zone-assessments"
    parent: str = "100-geographic-reference/130-protected-areas"


ASSET = AssetSpec()


@dataclass(frozen=True)
class Baseline:
    release: str
    fingerprint_hash: str
    source_sha256: str | None
    row_count: int


@dataclass(frozen=True)
class Outputs:
    fgb: Path
    pmtiles: Path
    metadata: Path
    schema: Path
    manifest: Path
    row_count: int
    sha256: dict[str, str]
    schema_payload: dict[str, Any]


def source_fingerprint(records: Iterable[Mapping[str, Any]]) -> fingerprints.Fingerprint:
    """Hash published identities and normalized content, without row/date order."""
    return fingerprints.Fingerprint("mpatlas-content-v1", {
        "source": SOURCE_URL,
        "features": fingerprints.unordered_digest(
            {key: row[key] for key in ("feature_id", "geometry_hash", "properties_hash")}
            for row in records
        ),
    })


def load_baseline(publisher: GcsPublisher) -> Baseline:
    """Use the generation-bound published manifest and verified metadata bytes."""
    blob = publisher.bucket.blob(ASSET.latest_object(".manifest.json"))
    try:
        blob.reload()
    except NotFound as exc:
        raise RuntimeError("MPAtlas refresh requires a published manifest; reviewed repair required") from exc
    manifest = json.loads(blob.download_as_bytes(if_generation_match=int(blob.generation)))
    release = manifest["release"]
    artifacts = model.validate_release_manifest(
        manifest, expected_asset_slug=ASSET.slug, expected_release=release, require_generations=True,
    )
    if manifest["identity"]["strategy"] != "source_field" or manifest["identity"]["source_fields"] != ["zone_id"]:
        raise RuntimeError("Published MPAtlas identity must remain source_field:zone_id")
    metadata = artifacts["metadata"]
    name = ASSET.release_object(dt.date.fromisoformat(release), ".metadata.ndjson.gz")
    if metadata["path"] != f"gs://{publisher.bucket.name}/{name}":
        raise RuntimeError("MPAtlas baseline metadata must belong to its dated release")
    raw = publisher.bucket.blob(name, generation=metadata["generation"]).download_as_bytes(
        if_generation_match=metadata["generation"],
    )
    if hashlib.sha256(raw).hexdigest() != metadata["sha256"].removeprefix("sha256:"):
        raise RuntimeError("MPAtlas baseline metadata SHA-256 mismatch")
    records = list(model.read_metadata_sidecar_bytes(raw, label=name))
    validation = model.validate_sidecar_records(records, expected_asset_slug=ASSET.slug, expected_release=release)
    if not validation.valid or validation.feature_count != manifest["validation"]["feature_count"]:
        raise RuntimeError(f"Invalid MPAtlas baseline metadata: {validation.errors}")
    fingerprint = source_fingerprint(records)
    sources = [item for item in manifest["source_inputs"] if item.get("uri") == SOURCE_URL]
    stored_hashes = [item["source_fingerprint_hash"] for item in sources if "source_fingerprint_hash" in item]
    # A byte shortcut also binds the normalized-hash definition. Legacy manual
    # releases use their verified published sidecar for the first comparison.
    source_hashes = [item["sha256"] for item in sources
                     if "sha256" in item and item.get("source_fingerprint_hash") == fingerprint.sha256]
    return Baseline(release, stored_hashes[0] if stored_hashes else fingerprint.sha256,
                    source_hashes[0] if source_hashes else None, len(records))


def prepare_source(source: Path, output: Path) -> dict[str, int]:
    """Validate upstream identity/shape once, retaining all source properties."""
    features = list(fm.iter_geojson_features(source))
    seen: set[str] = set()
    counts = {"source_rows": len(features), "excluded_null_geometry": 0, "published_rows": 0}
    with output.open("w", encoding="utf-8") as handle:
        for feature in features:
            properties = feature.get("properties")
            if not isinstance(properties, dict):
                raise RuntimeError("MPAtlas source properties must be an object")
            feature_id = fm.source_field_feature_id("zone_id", properties.get("zone_id"))
            if feature_id in seen:
                raise RuntimeError(f"Duplicate MPAtlas zone_id: {feature_id}")
            seen.add(feature_id)
            if set(properties) & {fm.FEATURE_ID_COLUMN, fm.GEOMETRY_HASH_COLUMN, fm.PROPERTIES_HASH_COLUMN}:
                raise RuntimeError("MPAtlas source contains reserved release columns")
            geometry = feature.get("geometry")
            if geometry is None:
                counts["excluded_null_geometry"] += 1
                continue
            if not isinstance(geometry, dict) or geometry.get("type") not in {"Polygon", "MultiPolygon", "Point", "MultiPoint"}:
                raise RuntimeError(f"Unsupported MPAtlas geometry for zone {feature_id}")
            if not geometry.get("coordinates"):
                raise RuntimeError(f"Empty MPAtlas geometry for zone {feature_id}")
            normalized = {}
            for key, value in properties.items():
                if key in NESTED_FIELDS and value is not None:
                    value = fm.canonical_json(value)
                elif isinstance(value, (list, dict)):
                    raise RuntimeError(f"Unexpected nested MPAtlas field {key}; source schema review required")
                normalized[key] = value
            handle.write(fm.canonical_json({"type": "Feature", "properties": normalized, "geometry": geometry}) + "\n")
            counts["published_rows"] += 1
    if not counts["published_rows"]:
        raise RuntimeError("MPAtlas source contains no publishable geometries")
    return counts


def prepare_metadata(source: Path, workdir: Path, run_date: dt.date):
    """Repair/promote geometry before hashing, so hashes describe stored shapes."""
    normalized = workdir / "source.geojsonl"
    counts = prepare_source(source, normalized)
    # Geometry conversion must not coerce timestamps or reinterpret JSON text.
    # Only the identity crosses GDAL; source attributes are reattached verbatim.
    properties_by_id = {fm.source_field_feature_id("zone_id", f["properties"]["zone_id"]): f["properties"]
                        for f in fm.iter_geojsonseq(normalized)}
    repaired = workdir / "repaired.geojsonl"
    run_command(["ogr2ogr", "-f", "GeoJSONSeq", "-select", "zone_id", "-makevalid", "-nlt", "PROMOTE_TO_MULTI",
                 "-t_srs", "EPSG:4326", "-lco", "SIGNIFICANT_FIGURES=17",
                 str(repaired), f"GeoJSONSeq:{normalized}"])
    repaired_features = (
        {**f, "properties": properties_by_id[fm.source_field_feature_id("zone_id", f["properties"]["zone_id"])]}
        for f in fm.iter_geojsonseq(repaired)
    )
    features, records = fm.enrich_features_with_source_field_ids(
        repaired_features, asset_slug=ASSET.slug, release=run_date.isoformat(),
        id_field="zone_id", provenance={"source": SOURCE_URL},
    )
    if len(records) != counts["published_rows"] or any(
        not f["geometry"] or f["geometry"]["type"] not in {"MultiPolygon", "MultiPoint"}
        or not f["geometry"].get("coordinates") for f in features
    ):
        raise RuntimeError("GDAL repair changed the MPAtlas row count or lost geometry")
    enriched = workdir / "enriched.geojson"
    enriched.write_text(fm.canonical_json({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    normalized.unlink()
    repaired.unlink()
    return enriched, records, counts


def build_outputs(enriched: Path, records: list[dict[str, Any]], workdir: Path, run_date: dt.date) -> Outputs:
    paths = {role: workdir / (ASSET.slug + suffix) for role, suffix in fm.ROLE_SUFFIXES.items()}
    fm.write_sidecar(records, paths["metadata"])
    schema = fm.schema_from_records(asset_slug=ASSET.slug, release=run_date.isoformat(), records=records)
    fm.write_schema(schema, paths["schema"])
    run_command(["ogr2ogr", "-oo", "DATE_AS_STRING=YES",
                 "-f", "FlatGeobuf", "-nln", LAYER, "-nlt", "GEOMETRY",
                 "-lco", "SPATIAL_INDEX=YES", str(paths["fgb"]), f"GeoJSON:{enriched}"])
    mbtiles = workdir / "display.mbtiles"
    run_command(["tippecanoe", "--force", "--output", str(mbtiles), "--layer", LAYER,
                 "--minimum-zoom=0", "--maximum-zoom=10", "--include=feature_id",
                 "--no-feature-limit", "--no-tile-size-limit", str(enriched)])
    run_command(["pmtiles", "convert", str(mbtiles), str(paths["pmtiles"])])
    mbtiles.unlink()
    # Both native archive validity and decoded compact properties are required.
    fm.validate_release_vector_contract(fgb_path=paths["fgb"], pmtiles_path=paths["pmtiles"], decode_zoom=0)
    return Outputs(**paths, row_count=len(records), schema_payload=schema,
                   sha256={role: sha256_file(paths[role]) for role in vector_pipeline.CORE_BUNDLE_ROLES})


def write_record(publisher: GcsPublisher, run_date: dt.date, record: dict[str, Any]) -> dict[str, Any]:
    publisher.write_run_record(asset=ASSET, run_date=run_date, payload=record)
    return record


def ingest(publisher: GcsPublisher, run_date: dt.date, workdir: Path) -> dict[str, Any]:
    existing = publisher.load_json(ASSET.run_record_object(run_date))
    if existing is not None:
        if existing.get("status") not in {"success", "skipped"}:
            raise RuntimeError("Existing MPAtlas run requires investigation before retry")
        if existing["status"] == "success":
            publisher.record_existing_successful_release(ASSET, run_date)
        else:
            publisher.update_latest_run_index(asset=ASSET, payload=existing,
                run_record_info={"path": f"gs://{publisher.bucket.name}/{ASSET.run_record_object(run_date)}"})
        return existing
    publisher.assert_no_partial_release(ASSET, run_date)
    baseline = load_baseline(publisher)
    if run_date.isoformat() <= baseline.release:
        raise RuntimeError("MPAtlas RUN_DATE must be newer than the latest published release")
    source = workdir / "source.geojson"
    download_file(SOURCE_URL, source, user_agent="shared-datasets-1-mpatlas-monthly/1.0", timeout_seconds=300)
    source_hash = sha256_file(source)
    record = {"schema_version": 1, "asset_slug": ASSET.slug, "run_date": run_date.isoformat(),
              "release_date": run_date.isoformat(), "source": SOURCE_URL, "source_version": source_hash,
              "source_sha256": source_hash}
    if source_hash == baseline.source_sha256:
        record.update(status="skipped", reason="source fingerprint unchanged", row_count=baseline.row_count,
                      source_fingerprint_hash=baseline.fingerprint_hash)
        return write_record(publisher, run_date, record)
    enriched, records, counts = prepare_metadata(source, workdir, run_date)
    fingerprint = source_fingerprint(records)
    record.update(**fingerprint.record_fields(), source_stats=counts, row_count=len(records))
    if fingerprint.sha256 == baseline.fingerprint_hash:
        record.update(status="skipped", reason="source fingerprint unchanged")
        return write_record(publisher, run_date, record)
    outputs = build_outputs(enriched, records, workdir, run_date)
    tools = toolchain()
    record["tools"] = tools
    bundle = vector_pipeline.publish_vector_bundle(
        publisher=publisher, asset=ASSET, run_date=run_date, outputs=outputs,
        object_metadata={"asset_slug": ASSET.slug, "run_date": run_date.isoformat(),
                         "source_sha256": source_hash, "source_fingerprint_hash": fingerprint.sha256},
        source_inputs=[{"uri": SOURCE_URL, "sha256": source_hash, "size": source.stat().st_size,
                        "source_fingerprint_hash": fingerprint.sha256, "tools": tools}],
        identity=model.build_identity_metadata(strategy="source_field", source_fields=["zone_id"]),
    )
    record.update(status="success", release_path=f"gs://{publisher.bucket.name}/{ASSET.release_prefix(run_date)}/",
                  release_paths=bundle.release_paths, latest_paths=bundle.latest_paths, sha256=bundle.sha256)
    return write_record(publisher, run_date, record)


def toolchain() -> dict[str, dict[str, str]]:
    tools = {}
    for name, arg in (("ogr2ogr", "--version"), ("tippecanoe", "--version"), ("pmtiles", "version")):
        path = str(Path(shutil.which(name)).resolve())
        completed = subprocess.run([path, arg], check=True, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        version = completed.stdout.strip()
        if not version:
            raise RuntimeError(f"Native tool returned no version: {path}")
        tools[name] = {"path": path, "version": version}
    return tools


def run() -> dict[str, Any]:
    configure_logging()
    for binary in ("ogr2ogr", "ogrinfo", "tippecanoe", "pmtiles", "tippecanoe-decode"):
        require_binary(binary)
    publisher = GcsPublisher(storage.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "shared-datasets-1")),
                             os.environ.get("SHARED_DATASETS_BUCKET", "skytruth-shared-datasets-1"))
    root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", str(Path(tempfile.gettempdir()) / "shared-datasets-1")))
    root = root / "vector-assets" / ASSET.slug
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="monthly-", dir=root) as directory:
        return ingest(publisher, parse_run_date(os.environ.get("RUN_DATE")), Path(directory))


def main() -> None:
    run_job_main(run, logger=LOGGER, failure_message="MPAtlas monthly job failed")


if __name__ == "__main__":
    main()
