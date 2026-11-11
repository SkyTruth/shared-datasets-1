"""Publish MPAtlas zone snapshots only when canonical feature content changes."""
from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Iterable, Mapping

from google.api_core.exceptions import GoogleAPIError
from google.cloud import storage

from ingestion.common import feature_metadata as fm, vector_pipeline, source_fingerprint as fingerprints, publication_readiness
from ingestion.common import lifecycle, published_baseline
from ingestion.common.source_quality import MPATLAS as quality, SourceLossError
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
    feature_ids: frozenset[str]


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


def load_baseline(publisher: GcsPublisher, release=...) -> Baseline:
    """Read the claimed release; standalone schedule checks capture their own index."""
    try:
        if release is ...:
            before = quality.baseline(publisher.bucket)
            release = before["release"] if before else None
        verified = published_baseline.read(publisher, ASSET, release)
        manifest, records = verified.manifest, verified.records
        release = manifest["release"]
        if manifest["identity"]["strategy"] != "source_field" or manifest["identity"]["source_fields"] != ["zone_id"]:
            raise ValueError("Published MPAtlas identity must remain source_field:zone_id")
        fingerprint = source_fingerprint(records)
        sources = [item for item in manifest["source_inputs"] if item.get("uri") == SOURCE_URL]
        stored_hashes = [item["source_fingerprint_hash"] for item in sources if "source_fingerprint_hash" in item]
        # A byte shortcut also binds the normalized-hash definition. Legacy manual
        # releases use their verified published sidecar for the first comparison.
        source_hashes = [item["sha256"] for item in sources
                         if "sha256" in item and item.get("source_fingerprint_hash") == fingerprint.sha256]
        return Baseline(release, stored_hashes[0] if stored_hashes else fingerprint.sha256,
                        source_hashes[0] if source_hashes else None, len(records),
                        frozenset(row["feature_id"] for row in records))
    except published_baseline.MissingManifest as exc:
        raise RuntimeError("MPAtlas refresh requires a published manifest; reviewed repair required") from exc
    except (GoogleAPIError, SourceLossError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise RuntimeError(f"MPAtlas baseline failed its contract; reviewed repair required: {exc}") from exc


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
    return counts


def prepare_metadata(source: Path, workdir: Path, run_date: dt.date):
    from ingestion.common.stage_checkpoints import cached_build, native_tools
    return cached_build("mpatlas-normalized", {"asset_slug": ASSET.slug, "release": run_date.isoformat(),
        "tools": native_tools(("ogr2ogr",))}, workdir,
        lambda directory: _prepare_metadata(source, directory, run_date),
        files={"source": source}, result_type=None,
        code=("ingestion/mpatlas_monthly/run.py", "ingestion/common/feature_metadata.py",
            "ingestion/common/publication_readiness.py", "scripts/release_feature_model.py"))


def _prepare_metadata(source: Path, workdir: Path, run_date: dt.date):
    """Repair/promote geometry before hashing, so hashes describe stored shapes."""
    normalized = workdir / "source.geojsonl"
    def prepare(directory):
        return prepare_source(source, directory / "source.geojsonl")
    discovery = publication_readiness.discover_batch([
        publication_readiness.Target(ASSET.slug, run_date.isoformat(),
            {"source_sha256": sha256_file(source), "producer": sha256_file(Path(__file__)),
             "identity_strategy": "source_field", "source_fields": ["zone_id"]}, prepare)
    ], workdir / "analysis")
    publication_readiness.require_ready(discovery)
    analyzed = discovery["assets"][ASSET.slug]
    counts = analyzed["payload"]
    shutil.copyfile(Path(analyzed["analysis"]) / "source.geojsonl", normalized)
    # Geometry conversion must not coerce timestamps or reinterpret JSON text.
    # Only the identity crosses GDAL; source attributes are reattached verbatim.
    properties_by_id = {fm.source_field_feature_id("zone_id", f["properties"]["zone_id"]): f["properties"]
                        for f in fm.iter_geojsonseq(normalized)}
    if not counts["published_rows"]:
        enriched = workdir / "enriched.geojson"
        enriched.write_text(fm.canonical_json({"type": "FeatureCollection", "features": []}), encoding="utf-8")
        return enriched, [], counts
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
    from ingestion.common.stage_checkpoints import cached_build, digest, native_tools
    inputs = {"asset_slug": ASSET.slug, "release": run_date.isoformat(), "records_sha256": digest(records),
        "tools": native_tools(("ogr2ogr", "ogrinfo", "tippecanoe", "tippecanoe-decode", "pmtiles"))}
    return cached_build("mpatlas-artifacts", inputs, workdir,
        lambda directory: _build_outputs(enriched, records, directory, run_date),
        files={"source": enriched}, result_type=Outputs,
        code=("ingestion/mpatlas_monthly/run.py", "ingestion/common/feature_metadata.py",
            "ingestion/common/runtime.py", "scripts/pmtiles_zoom.py", "scripts/release_feature_model.py",
            "scripts/vector_asset.py"))


def _build_outputs(enriched: Path, records: list[dict[str, Any]], workdir: Path, run_date: dt.date) -> Outputs:
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


class Refresh:
    """The MPAtlas stages of `lifecycle.run`; the order and rules live there."""
    slug = ASSET.slug

    def __init__(self, publisher: GcsPublisher, workdir: Path):
        self.publisher, self.workdir = publisher, workdir
        self.loss_limit = lifecycle.loss_limit(ASSET.slug)

    def resume(self, attempt: dt.date) -> dict[str, Any] | None:
        existing = self.publisher.load_json(ASSET.run_record_object(attempt))
        if existing is None:
            self.publisher.assert_no_partial_release(ASSET, attempt)
            return None
        if self.gate.held:
            # A hold raised while this date's run was already recorded has no
            # later stage to announce it; the request is sent at most once.
            self.gate.notify()
            return self.gate.record()
        if existing.get("status") not in {"success", "skipped"}:
            raise RuntimeError("Existing MPAtlas run requires investigation before retry")
        if existing["status"] == "success":
            self.publisher.record_existing_successful_release(ASSET, attempt)
        else:
            self.publisher.update_latest_run_index(asset=ASSET, payload=existing,
                run_record_info={"path": f"gs://{self.publisher.bucket.name}/{ASSET.run_record_object(attempt)}"})
        return existing

    def baseline(self) -> lifecycle.Baseline:
        self.published = load_baseline(self.publisher, self.gate.before["release"] if self.gate.before else None)
        return lifecycle.Baseline(dt.date.fromisoformat(self.published.release), self.published.fingerprint_hash,
                                  self.published.row_count,
                                  self.gate.state["hold"]["reason"] if self.gate.held else None)

    def acquire(self, attempt: dt.date) -> lifecycle.Source:
        if attempt.isoformat() <= self.published.release:
            raise RuntimeError("MPAtlas RUN_DATE must be newer than the latest published release")
        self.source = self.workdir / "source.geojson"
        download_file(SOURCE_URL, self.source, user_agent="shared-datasets-1-mpatlas-monthly/1.0", timeout_seconds=300)
        source_hash = sha256_file(self.source)
        return lifecycle.Source(attempt, SOURCE_URL, source_hash, {"source_sha256": source_hash})

    def describe(self, source: lifecycle.Source, baseline: lifecycle.Baseline) -> lifecycle.Content:
        if not self.gate.held and source.version == self.published.source_sha256:
            return lifecycle.Content({"source_fingerprint_hash": self.published.fingerprint_hash},
                                     self.published.row_count, lost=0)
        self.enriched, self.records, counts = prepare_metadata(self.source, self.workdir, source.release)
        lost = len(self.published.feature_ids - {row["feature_id"] for row in self.records})
        return lifecycle.Content(source_fingerprint(self.records).record_fields(), len(self.records), lost,
                                 extra={"source_stats": counts})

    def build(self, source: lifecycle.Source, content: lifecycle.Content, baseline: lifecycle.Baseline) -> lifecycle.Built:
        self.outputs = build_outputs(self.enriched, self.records, self.workdir, source.release)
        return lifecycle.Built(self.outputs.row_count, toolchain(),
                               f"gs://{self.publisher.bucket.name}/{ASSET.release_prefix(source.release)}/")

    def publish(self, built: lifecycle.Built, record: dict[str, Any]) -> dict[str, Any]:
        self.gate.check_publication()
        release = dt.date.fromisoformat(record["release_date"])
        bundle = vector_pipeline.publish_vector_bundle(
            publisher=self.publisher, asset=ASSET, run_date=release, outputs=self.outputs,
            object_metadata={"asset_slug": ASSET.slug, "run_date": release.isoformat(),
                             "source_sha256": record["source_sha256"],
                             "source_fingerprint_hash": record["source_fingerprint_hash"]},
            source_inputs=[{"uri": SOURCE_URL, "sha256": record["source_sha256"], "size": self.source.stat().st_size,
                            "source_fingerprint_hash": record["source_fingerprint_hash"], "tools": record["tools"]}],
            identity=model.build_identity_metadata(strategy="source_field", source_fields=["zone_id"]),
        )
        record.update(release_paths=bundle.release_paths, latest_paths=bundle.latest_paths, sha256=bundle.sha256)
        return self.check_in(record)

    def check_in(self, record: dict[str, Any]) -> dict[str, Any]:
        self.publisher.write_run_record(asset=ASSET, run_date=dt.date.fromisoformat(record["run_date"]), payload=record)
        return record

    def hold(self, reason: str, source: lifecycle.Source, content: lifecycle.Content, built) -> dict[str, Any]:
        self.gate.block(reason, {"geometry_count": content.rows, "lost_objects": content.lost,
            "maximum_lost_object_percent": self.loss_limit, "checked_at": quality.now()})
        if built is not None:
            record = lifecycle.published(self.slug, attempt=source.release, source=source, content=content,
                                         built=built, lost=content.lost)
            quality.stage_candidate(self.gate, output=self.outputs,
                fingerprint=content.fingerprint["source_fingerprint"], release=source.release.isoformat(),
                source_inputs=[{"uri": source.uri, "sha256": source.version, "size": self.source.stat().st_size,
                    "source_fingerprint_hash": content.fingerprint["source_fingerprint_hash"], "tools": built.tools}],
                identity=model.build_identity_metadata(strategy="source_field", source_fields=["zone_id"]),
                geometry_hash_algorithm=model.GEOMETRY_HASH_ALGORITHM,
                record=record, source_input=self.source,
                # The retained source, manifest and record carry the raw bytes'
                # hash; an equivalent response with other bytes is another candidate.
                evidence={"source_sha256": source.version})
        self.gate.notify()
        return self.gate.record()


def ingest(publisher: GcsPublisher, run_date: dt.date, workdir: Path) -> dict[str, Any]:
    refresh = Refresh(publisher, workdir)
    with quality.Guard(publisher.bucket) as refresh.gate:
        return lifecycle.run(refresh, run_date)


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
