from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
from unittest import mock

import pytest

from ingestion.mpatlas_monthly import run as job
from ingestion.common import feature_metadata as fm
from tests.test_eamlis_monthly import FakeBucket
from scripts import slack_notify


DAY = dt.date(2026, 11, 2)


def feature(zone=1, geometry=None):
    return {"type": "Feature", "properties": {"zone_id": zone, "assessment_id": 100 + zone,
            "zone_name": "Example", "classes": {"fishing": ["none"]},
            "affiliated_reports": [], "assessment_projects": [], "formally_recognized": True,
            "assessment_created_date": "2026-10-16T12:34:56.123456+00:00", "notes": None},
            "geometry": geometry or {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]]}}


def source_file(directory, features):
    path = directory / "input.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}))
    return path


def sidecar(features, release="2026-10-16"):
    return fm.enrich_features_with_source_field_ids(features, asset_slug=job.ASSET.slug,
            release=release, id_field="zone_id", provenance={"source": job.SOURCE_URL})[1]


def test_fingerprint_tracks_geometry_attributes_and_identity_but_not_order_or_release():
    features = [feature(), feature(2)]
    before = sidecar(features)
    assert job.source_fingerprint(before).sha256 == job.source_fingerprint(sidecar(list(reversed(features)), "2026-11-02")).sha256
    for changed in ("geometry", "properties", "identity"):
        after = copy.deepcopy(features)
        if changed == "geometry":
            after[0]["geometry"]["coordinates"][0][1][0] = 3
        elif changed == "properties":
            after[0]["properties"]["zone_name"] = "Changed"
        else:
            after[0]["properties"]["zone_id"] = 42
        assert job.source_fingerprint(before).sha256 != job.source_fingerprint(sidecar(after)).sha256
    assert job.source_fingerprint(before).sha256 != job.source_fingerprint(before[:1]).sha256


@pytest.mark.parametrize("bad", ["duplicate", "missing_id", "invalid_id", "reserved", "unsupported", "empty_geometry", "nested"])
def test_source_boundary_rejects_invalid_identity_and_schema(tmp_path, bad):
    features = [feature()]
    if bad == "duplicate":
        features.append(feature())
    elif bad == "missing_id":
        features[0]["properties"].pop("zone_id")
    elif bad == "invalid_id":
        features[0]["properties"]["zone_id"] = "bad/id"
    elif bad == "reserved":
        features[0]["properties"]["feature_id"] = "1"
    elif bad == "unsupported":
        features[0]["geometry"] = {"type": "LineString", "coordinates": [[0, 0], [1, 1]]}
    elif bad == "empty_geometry":
        features[0]["geometry"]["coordinates"] = []
    else:
        features[0]["properties"]["surprise"] = {"x": 1}
    with pytest.raises(RuntimeError):
        job.prepare_source(source_file(tmp_path, features), tmp_path / "prepared.geojsonl")


def test_source_boundary_preserves_nested_properties_and_counts_exclusions(tmp_path):
    missing = feature(2)
    missing["geometry"] = None
    output = tmp_path / "prepared.geojsonl"
    counts = job.prepare_source(source_file(tmp_path, [feature(), missing]), output)
    assert counts == {"source_rows": 2, "published_rows": 1, "excluded_null_geometry": 1}
    prepared = list(fm.iter_geojsonseq(output))
    assert prepared[0]["properties"]["classes"] == '{"fishing":["none"]}'
    assert prepared[0]["properties"]["formally_recognized"] is True
    assert job.prepare_source(source_file(tmp_path, [missing]), output)["published_rows"] == 0
    assert job.prepare_source(source_file(tmp_path, []), output)["published_rows"] == 0


@pytest.mark.parametrize("kind", ["source", "content"])
def test_unchanged_ingest_skips_build_and_publish_writing_only_checkin(tmp_path, kind):
    raw = b'{"type":"FeatureCollection","features":[]}'
    records = sidecar([feature()])
    baseline = job.Baseline("2026-10-16", job.source_fingerprint(records).sha256,
                            hashlib.sha256(raw).hexdigest() if kind == "source" else "0" * 64, 1, frozenset({"1"}))
    publisher = mock.Mock(spec=job.GcsPublisher)
    publisher.bucket = FakeBucket()
    publisher.bucket.name = job.quality.BUCKET
    publisher.load_json.return_value = None
    def download(_url, destination, **kwargs):
        destination.write_bytes(raw)
    with mock.patch.object(job, "load_baseline", return_value=baseline), \
         mock.patch.object(job, "download_file", side_effect=download), \
         mock.patch.object(job, "prepare_metadata", return_value=(tmp_path / "enriched", records, {"published_rows": 1})) as prepare, \
         mock.patch.object(job, "build_outputs") as build, \
         mock.patch.object(job.vector_pipeline, "publish_vector_bundle") as publish:
        result = job.ingest(publisher, DAY, tmp_path)
    assert result["status"] == "skipped"
    assert result["source_fingerprint_hash"] == baseline.fingerprint_hash
    assert result["reason"] == "source fingerprint unchanged"
    # The check-in names the day it ran and the release that stays current.
    assert (result["run_date"], result["release_date"], result["row_count"]) == (DAY.isoformat(), "2026-10-16", 1)
    assert publisher.write_run_record.call_args.kwargs["run_date"] == DAY
    build.assert_not_called()
    publish.assert_not_called()
    publisher.write_run_record.assert_called_once()
    publisher.assert_no_partial_release.assert_called_once_with(job.ASSET, DAY)
    assert prepare.call_count == (0 if kind == "source" else 1)


def test_completed_run_is_idempotent_and_partial_run_is_not_bypassed(tmp_path):
    publisher = mock.Mock(spec=job.GcsPublisher)
    publisher.bucket = FakeBucket()
    publisher.bucket.name = job.quality.BUCKET
    publisher.load_json.return_value = {"status": "success"}
    with mock.patch.object(job, "download_file") as download:
        assert job.ingest(publisher, DAY, tmp_path) == {"status": "success"}
        download.assert_not_called()
        publisher.record_existing_successful_release.assert_called_once_with(job.ASSET, DAY)
    publisher.load_json.return_value = None
    publisher.assert_no_partial_release.side_effect = RuntimeError("partial release")
    with mock.patch.object(job, "load_baseline", return_value=job.Baseline("2026-10-16", "changed", None, 1, frozenset({"1"}))), \
         mock.patch.object(job, "download_file", side_effect=lambda url, path, **kw: path.write_bytes(b"changed")), \
         mock.patch.object(job, "prepare_metadata", return_value=(tmp_path / "enriched", sidecar([feature()]), {})), \
         mock.patch.object(job, "build_outputs") as build:
        with pytest.raises(RuntimeError, match="partial release"):
            job.ingest(publisher, DAY, tmp_path)
        build.assert_not_called()


@pytest.mark.parametrize("suffix", [".metadata.ndjson.gz", ".schema.json"])
def test_bad_baseline_checksum_fails_before_comparison(tmp_path, suffix):
    from tests.test_shared_hold_flow import seed
    from tests.test_source_loss import Client

    publisher, _ = seed(Client(), tmp_path / "baseline")
    publisher.bucket.blob(job.ASSET.release_object(dt.date(2026, 10, 16), suffix)).data = b"corrupted"
    with pytest.raises(RuntimeError, match="baseline.*reviewed repair required.*corrupt"):
        job.load_baseline(publisher, job.quality.baseline(publisher.bucket)["release"])


@pytest.mark.skipif(os.environ.get("RUN_GDAL_INTEGRATION_TESTS") != "1", reason="native geospatial fixture")
def test_mixed_geometry_bundle_has_source_ids_valid_tiles_and_stored_hashes():
    import geopandas

    root = Path(os.environ.get("SHARED_DATASETS_WORKDIR", str(Path(tempfile.gettempdir()) / "shared-datasets-1"))) / "_scratch" / "mpatlas-tests"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as directory:
        workdir = Path(directory)
        features = [feature(), feature(2, {"type": "Point", "coordinates": [5, 5]}),
                    feature(3, {"type": "Polygon", "coordinates": [[[8, 0], [10, 2], [10, 0], [8, 2], [8, 0]]]})]
        enriched, records, _ = job.prepare_metadata(source_file(workdir, features), workdir, DAY)
        assert records[0]["properties"]["assessment_created_date"] == features[0]["properties"]["assessment_created_date"]
        assert records[0]["properties"]["classes"] == fm.canonical_json(features[0]["properties"]["classes"])
        outputs = job.build_outputs(enriched, records, workdir, DAY)
        assert outputs.row_count == 3
        assert outputs.pmtiles.read_bytes()[:7] == b"PMTiles"
        # Read FGB attributes directly: GeoJSONSeq export heuristically parses
        # JSON-looking strings and would change the representation under test.
        stored_features = json.loads(geopandas.read_file(outputs.fgb).to_json(drop_id=True, na="null"))["features"]
        expected = {r["feature_id"]: (r["geometry_hash"], r["properties_hash"]) for r in records}
        for stored in stored_features:
            properties = stored["properties"]
            identity = properties.pop("feature_id")
            geometry_hash = properties.pop("geometry_hash")
            properties_hash = properties.pop("properties_hash")
            assert fm.content_hashes(geometry=stored["geometry"], properties=properties) == expected[identity]
            assert (geometry_hash, properties_hash) == expected[identity]


@pytest.mark.parametrize("marker", ["current", "older", "missing"])
def test_byte_shortcut_is_bound_to_normalized_fingerprint_definition(tmp_path, marker):
    from tests.test_shared_hold_flow import seed
    from tests.test_source_loss import Client

    records = sidecar([feature()])
    fingerprint = job.source_fingerprint(records).sha256
    source = {"uri": job.SOURCE_URL, "sha256": "a" * 64}
    if marker != "missing":
        source["source_fingerprint_hash"] = fingerprint if marker == "current" else "b" * 64
    publisher, _ = seed(Client(), tmp_path / "baseline", records=records, source_inputs=[source])
    baseline = job.load_baseline(publisher, job.quality.baseline(publisher.bucket)["release"])
    assert baseline.feature_ids == frozenset({"1"}) and baseline.row_count == 1
    assert baseline.fingerprint_hash == source.get("source_fingerprint_hash", fingerprint)
    assert baseline.source_sha256 == ("a" * 64 if marker == "current" else None)


def test_interrupted_skip_retries_only_checkin_without_losing_run_pointer(tmp_path):
    from ingestion.common import release_index
    from tests.test_eamlis_monthly import FakeBucket, FakeClient
    bucket = FakeBucket()
    publisher = job.GcsPublisher(FakeClient(bucket), bucket.name)
    records = sidecar([feature()])
    baseline = job.Baseline("2026-10-16", job.source_fingerprint(records).sha256, "a" * 64, 1, frozenset({"1"}))
    def download(_url, destination, **kwargs):
        destination.write_text("downloaded source")
    with mock.patch.object(job, "load_baseline", return_value=baseline) as load, \
         mock.patch.object(job, "download_file", side_effect=download) as fetch, \
         mock.patch.object(job, "prepare_metadata", return_value=(tmp_path / "enriched", records, {})) as prepare, \
         mock.patch.object(job, "build_outputs") as build:
        with mock.patch.object(release_index, "record_latest_run", side_effect=RuntimeError("lost index write")):
            with pytest.raises(RuntimeError, match="lost index write"):
                job.ingest(publisher, DAY, tmp_path)
        result = job.ingest(publisher, DAY, tmp_path)
    assert result["status"] == "skipped"
    assert result["source_fingerprint_hash"] == baseline.fingerprint_hash
    assert load.call_count == fetch.call_count == prepare.call_count == 1
    build.assert_not_called()
    run = bucket.blob(job.ASSET.run_record_object(DAY))
    assert len(run.uploads) == 1
    index = json.loads(bucket.blob(f"_catalog/releases/{job.ASSET.slug}.json").text)
    assert index["latest_run"]["run_record_path"] == f"gs://{bucket.name}/{run.name}"
    assert index["latest_release"] is None


TOOLS = {"ogr2ogr": {"path": "/usr/bin/ogr2ogr", "version": "GDAL 3.6.2"}}


def changed_ingest(tmp_path, published_zones, source_zones):
    """Run a changed-source refresh; the baseline's rows and IDs come from the same published zones."""
    records = sidecar([feature(zone) for zone in source_zones], DAY.isoformat())
    published = frozenset(str(zone) for zone in published_zones)
    baseline = job.Baseline("2026-10-16", "0" * 64, None, len(published), published)
    publisher = mock.Mock(spec=job.GcsPublisher)
    publisher.bucket = FakeBucket()
    publisher.bucket.name = job.quality.BUCKET
    publisher.load_json.return_value = None
    bundle = mock.Mock(release_paths=[{"path": "release"}], latest_paths=[{"path": "latest"}], sha256={"fgb": "f" * 64})
    counts = {"published_rows": len(records)}
    with mock.patch.object(job, "load_baseline", return_value=baseline), \
         mock.patch.object(job, "download_file", side_effect=lambda url, path, **kw: path.write_bytes(b"changed")), \
         mock.patch.object(job, "prepare_metadata", return_value=(tmp_path / "enriched", records, counts)), \
         mock.patch.object(job, "build_outputs", side_effect=lambda *args: fixture_outputs(tmp_path, records)), \
         mock.patch.object(job, "toolchain", return_value=TOOLS), \
         mock.patch.object(job.vector_pipeline, "publish_vector_bundle", return_value=bundle) as publish, \
         mock.patch.object(slack_notify, "notify"):
        return job.ingest(publisher, DAY, tmp_path), publisher, publish


def test_changed_source_publishes_a_record_with_tools_and_lost_zone_count(tmp_path):
    # 40 published zones; zone 40 is gone and zone 41 is new: 2.5% lost, inside the 5% limit.
    result, publisher, publish = changed_ingest(tmp_path, range(1, 41), [*range(1, 40), 41])
    assert (result["status"], result["run_date"], result["release_date"]) == ("success", DAY.isoformat(), DAY.isoformat())
    assert (result["row_count"], result["lost_objects"], result["tools"]) == (40, 1, TOOLS)
    assert result["source_stats"] == {"published_rows": 40} and result["release_paths"] == [{"path": "release"}]
    assert result["release_path"] == f"gs://{job.quality.BUCKET}/{job.ASSET.release_prefix(DAY)}/"
    publish.assert_called_once()
    assert publish.call_args.kwargs["source_inputs"][0]["tools"] == TOOLS
    publisher.write_run_record.assert_called_once_with(asset=job.ASSET, run_date=DAY, payload=result)


def test_lost_zones_beyond_the_limit_stop_before_any_publication(tmp_path):
    # 40 published zones; three are replaced by three new ones. Net change is zero; 7.5% are lost.
    result, publisher, publish = changed_ingest(tmp_path, range(1, 41), [*range(1, 38), 41, 42, 43])
    assert result["status"] == "held" and result["reason"] == "objects_lost"
    assert result["hold"]["candidate"] is not None
    assert result["hold"]["observation"]["lost_objects"] == 3
    publish.assert_not_called()
    publisher.write_run_record.assert_not_called()


def fixture_outputs(directory, records):
    """Replace native builders only; use real sidecar, schema and manifest serialization."""
    paths = {role: directory / (job.ASSET.slug + suffix) for role, suffix in fm.ROLE_SUFFIXES.items()}
    paths["fgb"].write_bytes(b"external-native-fgb")
    paths["pmtiles"].write_bytes((Path(__file__).parent / "browser/fixtures/old.pmtiles").read_bytes())
    fm.write_sidecar(records, paths["metadata"])
    schema = fm.schema_from_records(asset_slug=job.ASSET.slug, release=records[0]["release"], records=records)
    fm.write_schema(schema, paths["schema"])
    return job.Outputs(**paths, row_count=len(records), schema_payload=schema,
                       sha256={r: job.sha256_file(paths[r]) for r in job.vector_pipeline.CORE_BUNDLE_ROLES})


@pytest.mark.parametrize("broken", ["replacement_pair", "replacement_triple", "missing_manifest_generation"])
def test_indexed_publication_authenticates_baseline_before_download(tmp_path, broken):
    from tests.test_shared_hold_flow import seed
    from tests.test_source_loss import Client, replace_baseline_sidecar

    publisher, _ = seed(Client(), tmp_path / "baseline")
    authority_paths = (job.quality.INDEX, job.ASSET.release_object(dt.date(2026, 10, 16), ".manifest.json"))
    authority = {name: (publisher.bucket.blob(name).generation, publisher.bucket.blob(name).data) for name in authority_paths}
    if broken.startswith("replacement"):
        records = sidecar([feature(n) for n in range(41, 81)])
        replace_baseline_sidecar(publisher.bucket, job.ASSET, records, tmp_path)
        if broken == "replacement_triple":
            prior = dt.date(2026, 10, 16)
            record_blob = publisher.bucket.blob(job.ASSET.run_record_object(prior))
            record = json.loads(record_blob.data)
            record["sha256"]["manifest"] = hashlib.sha256(
                publisher.bucket.blob(job.ASSET.latest_object(".manifest.json")).data).hexdigest()
            record_blob.upload_from_string(json.dumps(record), if_generation_match=record_blob.generation)
            assert {name: (publisher.bucket.blob(name).generation, publisher.bucket.blob(name).data)
                    for name in authority_paths} == authority
    else:
        blob = publisher.bucket.blob(job.quality.INDEX)
        index = json.loads(blob.data)
        ref = next(ref for ref in index["latest_release"]["files"] if ref["path"].endswith(".manifest.json"))
        del ref["generation"]
        blob.upload_from_string(json.dumps(index), if_generation_match=blob.generation)
    features = [feature(n) for n in (range(41, 81) if broken.startswith("replacement") else range(1, 41))]
    records = sidecar(features, DAY.isoformat())
    with mock.patch.object(job, "download_file", side_effect=lambda url, path, **kw: path.write_text(json.dumps(features))) as download, \
         mock.patch.object(job, "prepare_metadata", return_value=(tmp_path / "enriched", records, {"published_rows": 40})), \
         mock.patch.object(job, "build_outputs", side_effect=lambda *args: fixture_outputs(tmp_path, records)) as build, \
         mock.patch.object(job, "toolchain", return_value=TOOLS), \
         pytest.raises(RuntimeError, match="baseline.*reviewed repair required"):
        job.ingest(publisher, DAY, tmp_path)
    download.assert_not_called()
    build.assert_not_called()


def test_missing_manifest_retains_reviewed_repair_error(tmp_path):
    from tests.test_source_loss import Client
    publisher = job.GcsPublisher(Client(), job.quality.BUCKET)
    with pytest.raises(RuntimeError, match="^MPAtlas refresh requires a published manifest; reviewed repair required$"):
        job.load_baseline(publisher, None)
