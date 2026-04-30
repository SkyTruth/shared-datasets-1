from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.wdpa_monthly import run as wdpa


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


class FakeClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self._bucket = bucket

    def bucket(self, name: str) -> FakeBucket:
        return self._bucket


class WdpaMonthlyTests(unittest.TestCase):
    def test_default_run_date_uses_month_start(self):
        self.assertEqual(
            wdpa.default_run_date(dt.date(2026, 5, 7)),
            dt.date(2026, 5, 1),
        )

    def test_source_url_uses_month_token(self):
        run_date = dt.date(2026, 4, 29)
        self.assertEqual(
            wdpa.build_source_url("https://example/{month_token}/{run_date}", run_date),
            "https://example/Apr2026/2026-04-29",
        )

    def test_download_missing_monthly_source_is_source_unavailable(self):
        def unavailable(_request, *, timeout):
            raise wdpa.urllib.error.HTTPError(
                "https://example/missing.zip",
                404,
                "not found",
                hdrs=None,
                fp=None,
            )

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(wdpa.urllib.request, "urlopen", unavailable):
                with self.assertRaisesRegex(
                    wdpa.SourceNotAvailableError,
                    "not available yet",
                ):
                    wdpa.download_file(
                        "https://example/missing.zip",
                        Path(tmp) / "wdpa.zip",
                    )

    def test_run_skips_when_monthly_source_is_not_available_yet(self):
        bucket = FakeBucket()

        with (
            mock.patch.dict(wdpa.os.environ, {"RUN_DATE": "2026-05-01"}, clear=True),
            mock.patch.object(wdpa, "require_binary", lambda _binary: None),
            mock.patch.object(wdpa.storage, "Client", lambda project: FakeClient(bucket)),
            mock.patch.object(
                wdpa,
                "download_file",
                side_effect=wdpa.SourceNotAvailableError("source not ready"),
            ),
        ):
            records = wdpa.run()

        self.assertEqual(len(records), len(wdpa.ASSETS))
        self.assertEqual({record["status"] for record in records}, {"skipped"})
        self.assertEqual({record["reason"] for record in records}, {"source not ready"})
        self.assertFalse(any(blob.uploads for blob in bucket.blobs.values()))

    def test_prepare_source_datasets_extracts_nested_zips(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            inner_zip = tmp_path / "inner.zip"
            with zipfile.ZipFile(inner_zip, "w") as inner:
                inner.writestr("source.geojson", "{}")

            outer_zip = tmp_path / "outer.zip"
            with zipfile.ZipFile(outer_zip, "w") as outer:
                outer.write(inner_zip, "nested/inner.zip")
            inner_zip.unlink()

            sources = wdpa.prepare_source_datasets(outer_zip, tmp_path)

            self.assertEqual(len(sources), 1)
            self.assertTrue(sources[0].startswith("/vsizip/"))
            self.assertTrue((tmp_path / "source-zips" / "inner.zip").exists())
            self.assertFalse(outer_zip.exists())

    def test_realm_split_and_union_schema(self):
        layers = [
            wdpa.SourceLayer(
                name="points",
                geometry_type="Point",
                fields=(
                    wdpa.FieldSpec("REALM", "String"),
                    wdpa.FieldSpec("NAME", "String"),
                ),
            ),
            wdpa.SourceLayer(
                name="polygons",
                geometry_type="Polygon",
                fields=(
                    wdpa.FieldSpec("REALM", "String"),
                    wdpa.FieldSpec("NAME", "String"),
                    wdpa.FieldSpec("GIS_AREA", "Real"),
                ),
            ),
        ]

        self.assertEqual(wdpa.choose_split_field(layers), "REALM")
        self.assertEqual(
            wdpa.source_field_union(layers),
            (
                wdpa.FieldSpec("REALM", "String"),
                wdpa.FieldSpec("NAME", "String"),
                wdpa.FieldSpec("GIS_AREA", "Real"),
            ),
        )
        self.assertEqual(
            wdpa.asset_where_clause(wdpa.ASSETS[0], "REALM"),
            "REALM IN ('Marine', 'Coastal')",
        )
        self.assertEqual(
            wdpa.asset_where_clause(wdpa.ASSETS[1], "REALM"),
            "REALM = 'Terrestrial'",
        )

    def test_marine_split_uses_documented_numeric_values(self):
        self.assertEqual(
            wdpa.asset_where_clause(wdpa.ASSETS[0], "MARINE"),
            "MARINE IN ('1', '2')",
        )
        self.assertEqual(
            wdpa.asset_where_clause(wdpa.ASSETS[1], "MARINE"),
            "MARINE = '0'",
        )

    def test_sampled_where_clause_is_deterministic(self):
        sample = wdpa.SampleSpec(fraction=0.01, seed=123)
        self.assertEqual(
            wdpa.sampled_where_clause("REALM = 'Terrestrial'", sample),
            (
                "(REALM = 'Terrestrial') AND "
                "(((SITE_ID * 1103515245 + 123) % 1000000) < 10000)"
            ),
        )

    def test_sample_field_is_required_when_sampling(self):
        layers = [
            wdpa.SourceLayer(
                name="missing-site-id",
                geometry_type="Point",
                fields=(wdpa.FieldSpec("REALM", "String"),),
            )
        ]
        with self.assertRaisesRegex(RuntimeError, "sample field SITE_ID missing"):
            wdpa.assert_sample_field_available(layers, wdpa.SampleSpec(fraction=0.01, seed=1))

    def test_release_upload_uses_no_clobber(self):
        bucket = FakeBucket()
        publisher = wdpa.GcsPublisher(FakeClient(bucket), bucket.name)
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(b"data")
            tmp.flush()
            info = publisher.upload_new_object(
                local_path=Path(tmp.name),
                object_name="asset/releases/2026-04-29/asset.fgb",
                metadata={"asset_slug": "asset"},
            )
        blob = bucket.blob("asset/releases/2026-04-29/asset.fgb")
        self.assertEqual(blob.uploads[0][1], 0)
        self.assertEqual(info["generation"], blob.generation)

    def test_latest_replace_uses_observed_generation(self):
        bucket = FakeBucket()
        blob = bucket.blob("asset/latest/asset.fgb")
        blob.exists = True
        blob.generation = 7
        publisher = wdpa.GcsPublisher(FakeClient(bucket), bucket.name)
        with tempfile.NamedTemporaryFile() as tmp:
            tmp.write(b"data")
            tmp.flush()
            publisher.replace_latest_object(
                local_path=Path(tmp.name),
                object_name=blob.name,
                metadata={"asset_slug": "asset"},
            )
        self.assertEqual(blob.uploads[0][1], 7)

    def test_successful_run_record_is_detected(self):
        bucket = FakeBucket()
        asset = wdpa.ASSETS[0]
        record = bucket.blob(asset.run_record_object(dt.date(2026, 4, 29)))
        record.exists = True
        record.text = json.dumps({"status": "success"})
        publisher = wdpa.GcsPublisher(FakeClient(bucket), bucket.name)
        self.assertTrue(publisher.successful_run_record(asset, dt.date(2026, 4, 29)))

    def test_partial_release_blocks_publish(self):
        bucket = FakeBucket()
        asset = wdpa.ASSETS[0]
        partial = bucket.blob(asset.release_object(dt.date(2026, 4, 29), ".fgb"))
        partial.exists = True
        publisher = wdpa.GcsPublisher(FakeClient(bucket), bucket.name)
        with self.assertRaisesRegex(RuntimeError, "without a successful run record"):
            publisher.assert_no_partial_release(asset, dt.date(2026, 4, 29))


@unittest.skipUnless(
    shutil.which("ogrinfo")
    and shutil.which("ogr2ogr")
    and shutil.which("tippecanoe")
    and shutil.which("pmtiles"),
    "requires GDAL and Tippecanoe binaries",
)
class WdpaMonthlyIntegrationTests(unittest.TestCase):
    def test_fixture_zip_builds_mixed_geometry_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            geojson = tmp_path / "wdpa_fixture.geojson"
            geojson.write_text(
                json.dumps(
                    {
                        "type": "FeatureCollection",
                        "features": [
                            self._feature("0", "terrestrial-point", {"type": "Point", "coordinates": [0, 0]}),
                            self._feature("1", "coastal-point", {"type": "Point", "coordinates": [1, 1]}),
                            self._feature("2", "marine-point", {"type": "Point", "coordinates": [2, 2]}),
                            self._feature(
                                "0",
                                "terrestrial-poly",
                                {
                                    "type": "Polygon",
                                    "coordinates": [[[0, 0], [0, 1], [1, 1], [0, 0]]],
                                },
                            ),
                            self._feature(
                                "1",
                                "coastal-poly",
                                {
                                    "type": "Polygon",
                                    "coordinates": [[[1, 1], [1, 2], [2, 2], [1, 1]]],
                                },
                            ),
                            self._feature(
                                "2",
                                "marine-poly",
                                {
                                    "type": "Polygon",
                                    "coordinates": [[[2, 2], [2, 3], [3, 3], [2, 2]]],
                                },
                            ),
                        ],
                    }
                )
            )
            zip_path = tmp_path / "fixture.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.write(geojson, geojson.name)

            source = wdpa.source_dataset_path(zip_path)
            layers, split_field, source_fields = wdpa.discover_source_layers(source)
            outputs = wdpa.build_asset_outputs(
                source=source,
                source_layers=layers,
                source_fields=source_fields,
                asset=wdpa.ASSETS[0],
                where=wdpa.asset_where_clause(wdpa.ASSETS[0], split_field),
                workdir=tmp_path,
            )

            self.assertEqual(outputs.row_count, 4)
            self.assertTrue(outputs.fgb.exists())
            self.assertTrue(outputs.pmtiles.exists())

    @staticmethod
    def _feature(marine: str, name: str, geometry: dict) -> dict:
        return {
            "type": "Feature",
            "properties": {"MARINE": marine, "NAME": name},
            "geometry": geometry,
        }


if __name__ == "__main__":
    unittest.main()
