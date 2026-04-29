from __future__ import annotations

import datetime as dt
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.common.gcs import GcsPublisher
from ingestion.sea_ice_daily import run as sea_ice


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
        self.metadata = self.metadata
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


class SeaIceDailyTests(unittest.TestCase):
    def test_filename_uses_year_and_day_of_year(self):
        self.assertEqual(
            sea_ice.ims_filename_for_day(dt.date(2026, 4, 28)),
            "ims2026118_4km_GIS_v1.3.tif.gz",
        )

    def test_source_url_template_and_root_forms(self):
        source_date = dt.date(2026, 4, 28)
        self.assertEqual(
            sea_ice.source_url_for_day(
                "https://example.test/{yyyy}/{file_name}",
                source_date,
            ),
            "https://example.test/2026/ims2026118_4km_GIS_v1.3.tif.gz",
        )
        self.assertEqual(
            sea_ice.source_url_for_day("https://example.test/root", source_date),
            "https://example.test/root/2026/ims2026118_4km_GIS_v1.3.tif.gz",
        )

    def test_iter_source_days_is_newest_first(self):
        self.assertEqual(
            list(sea_ice.iter_source_days(dt.date(2026, 4, 29), 3)),
            [
                dt.date(2026, 4, 29),
                dt.date(2026, 4, 28),
                dt.date(2026, 4, 27),
            ],
        )

    def test_asset_paths(self):
        asset = sea_ice.ASSET
        run_date = dt.date(2026, 4, 28)
        self.assertEqual(
            asset.release_object(run_date, ".fgb"),
            (
                "200-imagery-derived/250-weather-climate/ims-sea-ice-extent/"
                "releases/2026-04-28/ims-sea-ice-extent.fgb"
            ),
        )
        self.assertEqual(
            asset.latest_object(".pmtiles"),
            (
                "200-imagery-derived/250-weather-climate/ims-sea-ice-extent/"
                "latest/ims-sea-ice-extent.pmtiles"
            ),
        )
        self.assertEqual(
            asset.run_record_object(run_date),
            (
                "200-imagery-derived/250-weather-climate/ims-sea-ice-extent/"
                "runs/2026-04-28.json"
            ),
        )

    def test_filename_date_and_documented_valid_date_are_distinct(self):
        source = sea_ice.AvailableSource(
            filename_date=dt.date(2026, 4, 28),
            source_url="https://example.test/source.tif.gz",
            source_filename="ims2026118_4km_GIS_v1.3.tif.gz",
        )
        self.assertEqual(source.filename_date, dt.date(2026, 4, 28))
        self.assertEqual(source.documented_valid_date, dt.date(2026, 4, 29))

    def test_parse_text_ogrinfo_output(self):
        text = """
Layer name: ims_sea_ice_extent
Geometry: Multi Polygon
Feature Count: 12
FID Column = fid
Geometry Column = geom
DN: Integer (0.0)
ice_date: String (0.0)
"""
        self.assertEqual(
            sea_ice.parse_text_feature_count(text, Path("example.fgb")),
            12,
        )
        self.assertEqual(
            sea_ice.parse_text_layer_fields(text),
            ("DN", "ice_date"),
        )

    def test_publish_uses_release_no_clobber_and_latest_generation(self):
        bucket = FakeBucket()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        asset = sea_ice.ASSET
        run_date = dt.date(2026, 4, 28)
        bucket.blob(asset.latest_object(".fgb")).exists = True
        bucket.blob(asset.latest_object(".fgb")).generation = 7
        bucket.blob(asset.latest_object(".pmtiles")).exists = True
        bucket.blob(asset.latest_object(".pmtiles")).generation = 11

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fgb = tmp_path / "out.fgb"
            pmtiles = tmp_path / "out.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")
            record = sea_ice.publish_outputs(
                publisher=publisher,
                asset=asset,
                outputs=sea_ice.AssetOutputs(
                    fgb=fgb,
                    pmtiles=pmtiles,
                    row_count=2,
                    sha256={"fgb": "fgbhash", "pmtiles": "pmhash"},
                ),
                source=sea_ice.AvailableSource(
                    filename_date=run_date,
                    source_url="https://example.test/ims.tif.gz",
                    source_filename="ims2026118_4km_GIS_v1.3.tif.gz",
                ),
            )

        release_fgb = bucket.blob(asset.release_object(run_date, ".fgb"))
        latest_fgb = bucket.blob(asset.latest_object(".fgb"))
        latest_pmtiles = bucket.blob(asset.latest_object(".pmtiles"))
        run_record = bucket.blob(asset.run_record_object(run_date))

        self.assertEqual(record["status"], "success")
        self.assertEqual(release_fgb.uploads[0][1], 0)
        self.assertEqual(latest_fgb.uploads[0][1], 7)
        self.assertEqual(latest_pmtiles.uploads[0][1], 11)
        self.assertEqual(json.loads(run_record.text)["rows"], 2)

    def test_publish_skips_existing_success_record(self):
        bucket = FakeBucket()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        asset = sea_ice.ASSET
        run_date = dt.date(2026, 4, 28)
        run_record = bucket.blob(asset.run_record_object(run_date))
        run_record.exists = True
        run_record.text = json.dumps({"status": "success"})

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            fgb = tmp_path / "out.fgb"
            pmtiles = tmp_path / "out.pmtiles"
            fgb.write_bytes(b"fgb")
            pmtiles.write_bytes(b"pmtiles")
            record = sea_ice.publish_outputs(
                publisher=publisher,
                asset=asset,
                outputs=sea_ice.AssetOutputs(
                    fgb=fgb,
                    pmtiles=pmtiles,
                    row_count=2,
                    sha256={"fgb": "fgbhash", "pmtiles": "pmhash"},
                ),
                source=sea_ice.AvailableSource(
                    filename_date=run_date,
                    source_url="https://example.test/ims.tif.gz",
                    source_filename="ims2026118_4km_GIS_v1.3.tif.gz",
                ),
            )

        self.assertEqual(record["status"], "skipped")
        self.assertFalse(bucket.blob(asset.release_object(run_date, ".fgb")).uploads)


@unittest.skipUnless(
    all(
        shutil.which(binary)
        for binary in (
            "gdal_create",
            "gdal_calc.py",
            "gdal_polygonize.py",
            "ogr2ogr",
            "ogrinfo",
            "tippecanoe",
            "pmtiles",
        )
    ),
    "requires GDAL, Tippecanoe, and PMTiles binaries",
)
class SeaIceDailyIntegrationTests(unittest.TestCase):
    def test_synthetic_raster_builds_fgb_and_pmtiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_tif = tmp_path / "source.tif"
            sea_ice.run_command(
                [
                    "gdal_create",
                    "-of",
                    "GTiff",
                    "-outsize",
                    "4",
                    "4",
                    "-bands",
                    "1",
                    "-burn",
                    "3",
                    "-ot",
                    "Byte",
                    "-a_srs",
                    "EPSG:3857",
                    "-a_ullr",
                    "-100000",
                    "100000",
                    "100000",
                    "-100000",
                    str(source_tif),
                ]
            )

            outputs = sea_ice.build_outputs(
                source_tif=source_tif,
                source_date=dt.date(2026, 4, 28),
                workdir=tmp_path,
            )

            self.assertGreater(outputs.row_count, 0)
            self.assertTrue(outputs.fgb.exists())
            self.assertTrue(outputs.pmtiles.exists())


if __name__ == "__main__":
    unittest.main()
