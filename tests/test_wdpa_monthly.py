from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

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
    def test_source_url_uses_month_token(self):
        run_date = dt.date(2026, 4, 29)
        self.assertEqual(
            wdpa.build_source_url("https://example/{month_token}/{run_date}", run_date),
            "https://example/Apr2026/2026-04-29",
        )

    def test_discover_layers_requires_identical_schemas(self):
        payload = {
            "layers": [
                {
                    "name": "points",
                    "geometryFields": [{"type": "Point"}],
                    "fields": [
                        {"name": "MARINE", "type": "String"},
                        {"name": "NAME", "type": "String"},
                    ],
                },
                {
                    "name": "polygons",
                    "geometryFields": [{"type": "Polygon"}],
                    "fields": [
                        {"name": "MARINE", "type": "String"},
                        {"name": "NAME", "type": "String"},
                    ],
                },
            ]
        }
        layers = [
            layer
            for layer in wdpa.parse_layers(payload)
            if any(field.name == "MARINE" for field in layer.fields)
        ]
        self.assertEqual([layer.name for layer in layers], ["points", "polygons"])
        self.assertEqual(layers[0].fields, layers[1].fields)

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
            layers = wdpa.discover_source_layers(source)
            outputs = wdpa.build_asset_outputs(
                source=source,
                source_layers=layers,
                source_fields=layers[0].fields,
                asset=wdpa.ASSETS[0],
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
