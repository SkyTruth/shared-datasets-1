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


def test_content_hash_tracks_geometry_attributes_and_identity_but_not_order_or_release():
    features = [feature(), feature(2)]
    before = sidecar(features)
    assert job.content_hash(before) == job.content_hash(sidecar(list(reversed(features)), "2026-11-02"))
    for changed in ("geometry", "properties", "identity"):
        after = copy.deepcopy(features)
        if changed == "geometry":
            after[0]["geometry"]["coordinates"][0][1][0] = 3
        elif changed == "properties":
            after[0]["properties"]["zone_name"] = "Changed"
        else:
            after[0]["properties"]["zone_id"] = 42
        assert job.content_hash(before) != job.content_hash(sidecar(after))
    assert job.content_hash(before) != job.content_hash(before[:1])


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
    with pytest.raises(RuntimeError, match="no publishable"):
        job.prepare_source(source_file(tmp_path, [missing]), output)
    with pytest.raises(RuntimeError, match="no publishable"):
        job.prepare_source(source_file(tmp_path, []), output)


@pytest.mark.parametrize("kind", ["source", "content"])
def test_unchanged_ingest_skips_build_and_publish_writing_only_checkin(tmp_path, kind):
    raw = b'{"type":"FeatureCollection","features":[]}'
    records = sidecar([feature()])
    baseline = job.Baseline("2026-10-16", job.content_hash(records), hashlib.sha256(raw).hexdigest() if kind == "source" else "0" * 64, 1)
    publisher = mock.Mock(spec=job.GcsPublisher)
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
    assert result["content_hash"] == baseline.content_hash
    build.assert_not_called()
    publish.assert_not_called()
    publisher.write_run_record.assert_called_once()
    publisher.assert_no_partial_release.assert_called_once_with(job.ASSET, DAY)
    assert prepare.call_count == (0 if kind == "source" else 1)


def test_completed_run_is_idempotent_and_partial_run_is_not_bypassed(tmp_path):
    publisher = mock.Mock(spec=job.GcsPublisher)
    publisher.load_json.return_value = {"status": "success"}
    with mock.patch.object(job, "download_file") as download:
        assert job.ingest(publisher, DAY, tmp_path) == {"status": "success"}
        download.assert_not_called()
    publisher.load_json.return_value = None
    publisher.assert_no_partial_release.side_effect = RuntimeError("partial release")
    with mock.patch.object(job, "load_baseline", return_value=job.Baseline("2026-10-16", "changed", None, 1)), \
         mock.patch.object(job, "download_file", side_effect=lambda url, path, **kw: path.write_bytes(b"changed")), \
         mock.patch.object(job, "prepare_metadata", return_value=(tmp_path / "enriched", sidecar([feature()]), {})), \
         mock.patch.object(job, "build_outputs") as build:
        with pytest.raises(RuntimeError, match="partial release"):
            job.ingest(publisher, DAY, tmp_path)
        build.assert_not_called()


def test_bad_baseline_checksum_fails_before_comparison():
    publisher = mock.Mock(spec=job.GcsPublisher)
    publisher.bucket = mock.Mock()
    publisher.bucket.name = "skytruth-shared-datasets-1"
    publisher.bucket.blob.return_value.generation = 1
    publisher.bucket.blob.return_value.download_as_bytes.side_effect = [json.dumps({
        "release": "2026-10-16", "identity": {"strategy": "source_field", "source_fields": ["zone_id"]},
    }).encode(), b"corrupted"]
    with mock.patch.object(job.model, "validate_release_manifest", return_value={"metadata": {
        "path": f"gs://skytruth-shared-datasets-1/{job.ASSET.release_object(dt.date(2026, 10, 16), '.metadata.ndjson.gz')}",
        "generation": 2, "sha256": "0" * 64,
    }}):
        with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
            job.load_baseline(publisher)


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
