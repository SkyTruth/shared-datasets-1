from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.eamlis_monthly import run as eamlis
from ingestion.common.gcs import GcsPublisher


def gdal_binaries_work() -> bool:
    if not (shutil.which("ogrinfo") and shutil.which("ogr2ogr") and shutil.which("tippecanoe")):
        return False
    for binary in ("ogrinfo", "ogr2ogr", "tippecanoe"):
        try:
            completed = subprocess.run(
                [binary, "--version"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        if completed.returncode != 0:
            return False
    return True


class FakeBlob:
    def __init__(self, name: str, *, exists: bool = False, generation: int = 1) -> None:
        self.name = name
        self.exists = exists
        self.generation = generation
        self.size = 0
        self.metadata = None
        self.content_type = None
        self.text = ""
        self.uploads = []

    def reload(self) -> None:
        if not self.exists:
            raise NotFound("not found")

    def download_as_text(self) -> str:
        self.reload()
        return self.text

    def upload_from_filename(self, filename, *, content_type=None, if_generation_match=None):
        self._check_generation(if_generation_match)
        self.exists = True
        self.generation += 1
        self.content_type = content_type
        self.size = Path(filename).stat().st_size
        self.uploads.append(("filename", if_generation_match, content_type))

    def upload_from_string(self, data, *, content_type=None, if_generation_match=None):
        self._check_generation(if_generation_match)
        self.exists = True
        self.generation += 1
        self.content_type = content_type
        self.text = data
        self.size = len(data.encode())
        self.uploads.append(("string", if_generation_match, content_type))

    def _check_generation(self, if_generation_match):
        if if_generation_match == 0 and self.exists:
            raise PreconditionFailed("exists")
        if if_generation_match not in (None, 0) and if_generation_match != self.generation:
            raise PreconditionFailed("generation mismatch")


class FakeBucket:
    def __init__(self) -> None:
        self.name = "test-bucket"
        self.blobs = {}

    def blob(self, name: str) -> FakeBlob:
        if name not in self.blobs:
            self.blobs[name] = FakeBlob(name)
        return self.blobs[name]

    def list_blobs(self, *, prefix: str):
        return [blob for name, blob in self.blobs.items() if name.startswith(prefix) and blob.exists]


class FakeClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self._bucket = bucket

    def bucket(self, _name: str) -> FakeBucket:
        return self._bucket


class FakeHttpResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = io.BytesIO(body)

    def read(self, size: int = -1) -> bytes:
        return self._body.read(size)

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> bool:
        return False


def sample_metadata(*, data_last_edit_date: int = 2000) -> dict:
    return {
        "serviceItemId": "service-1",
        "editingInfo": {
            "dataLastEditDate": data_last_edit_date,
            "schemaLastEditDate": 1000,
        },
        "fields": [
            {"name": "AMLIS_KEY", "type": "esriFieldTypeString", "alias": "AMLIS_KEY", "length": 11, "nullable": True},
            {"name": "LAT_DEG", "type": "esriFieldTypeSmallInteger", "alias": "LAT_DEG", "nullable": True},
            {"name": "DATE_REVISED", "type": "esriFieldTypeDate", "alias": "DATE_REVISED", "nullable": True},
            {"name": "OBJECTID", "type": "esriFieldTypeOID", "alias": "OBJECTID", "nullable": False},
        ],
    }


def sample_stats(feature_count: int = 2) -> eamlis.SourceStats:
    return eamlis.SourceStats(
        feature_count=feature_count,
        max_date_revised=1777546800000,
        max_objectid=feature_count,
    )


def sample_source_state(*, fingerprint_hash: str = "abc123", feature_count: int = 2) -> eamlis.SourceState:
    fields = eamlis.normalized_fields(sample_metadata())
    fingerprint = {
        "layer_url": "https://example.test/layer",
        "where": eamlis.DEFAULT_WHERE,
        "service_item_id": "service-1",
        "data_last_edit_date": 2000,
        "schema_last_edit_date": 1000,
        "field_schema_hash": eamlis.stable_hash(fields),
        "feature_count": feature_count,
        "max_date_revised": 1777546800000,
    }
    return eamlis.SourceState(
        layer_url="https://example.test/layer",
        where=eamlis.DEFAULT_WHERE,
        service_item_id="service-1",
        data_last_edit_date=2000,
        schema_last_edit_date=1000,
        fields=fields,
        field_schema_hash=eamlis.stable_hash(fields),
        stats=sample_stats(feature_count),
        fingerprint_hash=fingerprint_hash,
        fingerprint=fingerprint,
    )


class EamlisMonthlyTests(unittest.TestCase):
    def test_request_json_retries_transient_transport_failure(self):
        calls = []

        def flaky(_request, *, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    "https://example.test/layer",
                    500,
                    "server error",
                    hdrs=None,
                    fp=None,
                )
            return FakeHttpResponse(200, b'{"ok": true}')

        with (
            mock.patch.object(eamlis.urllib.request, "urlopen", flaky),
            mock.patch("ingestion.common.http.time.sleep"),
        ):
            payload = eamlis.request_json("https://example.test/layer", {"f": "json"})

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(len(calls), 2)

    def test_request_json_rejects_non_json_response(self):
        with mock.patch.object(
            eamlis.urllib.request,
            "urlopen",
            return_value=FakeHttpResponse(200, b"not json"),
        ):
            with self.assertRaisesRegex(RuntimeError, "not JSON"):
                eamlis.request_json("https://example.test/layer", {"f": "json"})

    def test_request_json_rejects_arcgis_application_error(self):
        with mock.patch.object(
            eamlis.urllib.request,
            "urlopen",
            return_value=FakeHttpResponse(200, b'{"error": {"message": "bad"}}'),
        ):
            with self.assertRaisesRegex(RuntimeError, "ArcGIS returned an error"):
                eamlis.request_json("https://example.test/layer", {"f": "json"})

    def test_source_fingerprint_includes_edit_dates_and_stats(self):
        metadata = sample_metadata(data_last_edit_date=1234)
        fields = eamlis.normalized_fields(metadata)
        with (
            mock.patch.object(eamlis, "request_json", side_effect=[metadata, {
                "features": [
                    {
                        "attributes": {
                            "feature_count": 7,
                            "max_date_revised": 1777546800000,
                            "max_objectid": 9,
                        }
                    }
                ]
            }]),
        ):
            state = eamlis.fetch_source_state("https://example.test/layer", eamlis.DEFAULT_WHERE)

        self.assertEqual(state.data_last_edit_date, 1234)
        self.assertEqual(state.stats.feature_count, 7)
        self.assertEqual(state.field_schema_hash, eamlis.stable_hash(fields))
        self.assertEqual(state.fingerprint["max_date_revised"], 1777546800000)

    def test_run_skips_when_latest_success_fingerprint_matches(self):
        bucket = FakeBucket()
        previous = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 4, 2)))
        previous.exists = True
        previous.text = json.dumps(
            {
                "run_date": "2026-04-02",
                "status": "success",
                "source_fingerprint_hash": "same",
                "release_path": "gs://test-bucket/release/",
                "latest_paths": [{"path": "gs://test-bucket/latest.fgb"}],
                "sha256": {"fgb": "old-sha"},
            }
        )
        release_index = bucket.blob(f"_catalog/releases/{eamlis.ASSET.slug}.json")
        release_index.exists = True
        release_index.text = json.dumps(
            {
                "asset_slug": eamlis.ASSET.slug,
                "latest_release": {
                    "date": "2026-04-02",
                    "run_record_path": f"gs://test-bucket/{previous.name}",
                },
            }
        )
        source = sample_source_state(fingerprint_hash="same")

        with (
            mock.patch.dict(eamlis.os.environ, {"RUN_DATE": "2026-05-02"}, clear=True),
            mock.patch.object(eamlis, "require_binary", lambda _binary: None),
            mock.patch.object(eamlis.storage, "Client", lambda project: FakeClient(bucket)),
            mock.patch.object(eamlis, "fetch_source_state", return_value=source),
            mock.patch.object(eamlis, "download_source_geojson") as download,
        ):
            records = eamlis.run()

        self.assertEqual(records[0]["status"], "skipped")
        self.assertEqual(records[0]["reason"], "source fingerprint unchanged")
        self.assertFalse(download.called)
        run_blob = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 5, 2)))
        self.assertEqual(run_blob.uploads[0][1], 0)

    def test_download_source_geojson_pages_until_expected_count(self):
        source = sample_source_state(feature_count=3)
        pages = [
            {
                "type": "FeatureCollection",
                "features": [
                    self._feature(1, 1777546800000),
                    self._feature(2, 1777460400000),
                ],
                "properties": {"exceededTransferLimit": True},
            },
            {
                "type": "FeatureCollection",
                "features": [self._feature(3, None)],
                "properties": {"exceededTransferLimit": False},
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "source.geojson"
            with mock.patch.object(eamlis, "request_json", side_effect=pages):
                extract = eamlis.download_source_geojson(source=source, dest=dest, page_size=2)
            payload = json.loads(dest.read_text())

        self.assertEqual(extract.row_count, 3)
        self.assertEqual(payload["features"][0]["properties"]["DATE_REVISED"], "2026-04-30")
        self.assertEqual(payload["features"][2]["properties"]["DATE_REVISED"], None)

    def test_publish_changed_asset_uses_safe_gcs_preconditions_and_run_record(self):
        bucket = FakeBucket()
        latest = bucket.blob(eamlis.ASSET.latest_object(".fgb"))
        latest.exists = True
        latest.generation = 7
        latest_pmtiles = bucket.blob(eamlis.ASSET.latest_object(".pmtiles"))
        publisher = GcsPublisher(
            FakeClient(bucket),
            bucket.name,
            release_suffixes=eamlis.RELEASE_SUFFIXES,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            fgb = tmp_path / "asset.fgb"
            pmtiles = tmp_path / "asset.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")
            output = eamlis.AssetOutput(
                fgb=fgb,
                pmtiles=pmtiles,
                row_count=2,
                sha256={"fgb": "new-sha", "pmtiles": "pmtiles-sha"},
            )
            record = eamlis.publish_changed_asset(
                publisher=publisher,
                run_date=dt.date(2026, 5, 2),
                source=sample_source_state(fingerprint_hash="new"),
                output=output,
            )

        release = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".fgb"))
        release_pmtiles = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".pmtiles"))
        run_blob = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 5, 2)))
        self.assertEqual(release.uploads[0][1], 0)
        self.assertEqual(release_pmtiles.uploads[0][1], 0)
        self.assertEqual(latest.uploads[0][1], 7)
        self.assertEqual(latest_pmtiles.uploads[0][1], 0)
        self.assertEqual(run_blob.uploads[0][1], 0)
        self.assertEqual(record["status"], "success")
        self.assertEqual(record["sha256"]["fgb"], "new-sha")
        self.assertEqual(record["sha256"]["pmtiles"], "pmtiles-sha")
        self.assertEqual(len(record["release_paths"]), 2)
        self.assertEqual(len(record["latest_paths"]), 2)

    def test_output_hash_unchanged_writes_skipped_record_without_publish(self):
        bucket = FakeBucket()
        previous = bucket.blob(eamlis.ASSET.run_record_object(dt.date(2026, 4, 2)))
        previous.exists = True
        previous.text = json.dumps(
            {
                "run_date": "2026-04-02",
                "status": "success",
                "source_fingerprint_hash": "old",
                "release_path": "gs://test-bucket/release/",
                "latest_paths": [{"path": "gs://test-bucket/latest.fgb"}],
                "sha256": {"fgb": "same-sha"},
            }
        )
        release_index = bucket.blob(f"_catalog/releases/{eamlis.ASSET.slug}.json")
        release_index.exists = True
        release_index.text = json.dumps(
            {
                "asset_slug": eamlis.ASSET.slug,
                "latest_release": {
                    "date": "2026-04-02",
                    "run_record_path": f"gs://test-bucket/{previous.name}",
                },
            }
        )
        source = sample_source_state(fingerprint_hash="new")
        output = eamlis.AssetOutput(
            fgb=Path("/tmp/nonexistent.fgb"),
            pmtiles=Path("/tmp/nonexistent.pmtiles"),
            row_count=2,
            sha256={"fgb": "same-sha"},
        )
        with (
            mock.patch.dict(eamlis.os.environ, {"RUN_DATE": "2026-05-02"}, clear=True),
            mock.patch.object(eamlis, "require_binary", lambda _binary: None),
            mock.patch.object(eamlis.storage, "Client", lambda project: FakeClient(bucket)),
            mock.patch.object(eamlis, "fetch_source_state", return_value=source),
            mock.patch.object(eamlis, "download_source_geojson", return_value=mock.Mock()),
            mock.patch.object(eamlis, "build_asset_output", return_value=output),
        ):
            records = eamlis.run()

        self.assertEqual(records[0]["status"], "skipped")
        self.assertEqual(records[0]["reason"], "generated FGB hash unchanged")
        release = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".fgb"))
        release_pmtiles = bucket.blob(eamlis.ASSET.release_object(dt.date(2026, 5, 2), ".pmtiles"))
        self.assertFalse(release.uploads)
        self.assertFalse(release_pmtiles.uploads)

    def test_parse_ogrinfo_summary_from_text_output(self):
        summary = eamlis.parse_ogrinfo_summary(
            """
INFO: Open of `example.fgb'
      using driver `FlatGeobuf' successful.

Layer name: eamlis_abandoned_mine_land_inventory
Geometry: Point
Feature Count: 2
Extent: (-150.000000, 40.000000) - (-149.000000, 41.000000)
Layer SRS WKT:
GEOGCRS["WGS 84"]
FID Column = fid
Geometry Column = geometry
AMLIS_KEY: String (0.0)
LAT_DEG: Integer (0.0)
DATE_REVISED: Date (0.0)
"""
        )

        self.assertEqual(summary["feature_count"], 2)
        self.assertEqual(summary["geometry_type"], "Point")
        self.assertEqual(summary["fields"], ["AMLIS_KEY", "LAT_DEG", "DATE_REVISED"])

    @staticmethod
    def _feature(objectid: int, date_revised: int | None) -> dict:
        return {
            "type": "Feature",
            "id": objectid,
            "geometry": {"type": "Point", "coordinates": [-100 - objectid, 40 + objectid]},
            "properties": {
                "AMLIS_KEY": f"AK{objectid:06d}",
                "LAT_DEG": 40 + objectid,
                "DATE_REVISED": date_revised,
                "OBJECTID": objectid,
            },
        }


@unittest.skipUnless(
    bool(os.environ.get("RUN_GDAL_INTEGRATION_TESTS")) and gdal_binaries_work(),
    "requires RUN_GDAL_INTEGRATION_TESTS=1 and runnable GDAL binaries",
)
class EamlisMonthlyIntegrationTests(unittest.TestCase):
    def test_fixture_geojson_builds_stable_fgb_output(self):
        source = sample_source_state(feature_count=2)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            geojson = tmp_path / "source.geojson"
            geojson.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            EamlisMonthlyTests._feature(1, 1777546800000),
                            EamlisMonthlyTests._feature(2, 1777460400000),
                        ],
                    }
                )
            )
            extract = eamlis.SourceExtract(
                geojson=geojson,
                row_count=2,
                null_geometry_count=0,
            )
            output_one = eamlis.build_asset_output(
                source=source,
                extract=extract,
                workdir=tmp_path,
            )
            first_hash = output_one.sha256["fgb"]
            output_one.fgb.unlink()
            output_two = eamlis.build_asset_output(
                source=source,
                extract=extract,
                workdir=tmp_path,
            )

        self.assertEqual(output_two.row_count, 2)
        self.assertEqual(output_two.sha256["fgb"], first_hash)


if __name__ == "__main__":
    unittest.main()
