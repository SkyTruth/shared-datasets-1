from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.common.gcs import GcsPublisher
from ingestion.sea_ice_daily import run as sea_ice

VALID_FGB_SHA = "a" * 64
VALID_PMTILES_SHA = "b" * 64
VALID_METADATA_SHA = "c" * 64
VALID_SCHEMA_SHA = "d" * 64


def runnable_command(command: list[str]) -> bool:
    executable = shutil.which(command[0])
    if not executable:
        return False
    try:
        completed = subprocess.run(
            [executable, *command[1:]],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


class FakeBlob:
    def __init__(self, name: str, *, exists: bool = False, generation: int = 1) -> None:
        self.name = name
        self.exists = exists
        self.generation = generation
        self.size = 0
        self.metadata = None
        self.content_type = None
        self.text = ""
        self.data = b""
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
        self.data = Path(filename).read_bytes()
        self.text = self.data.decode("utf-8", errors="replace")
        self.size = len(self.data)
        self.uploads.append(("filename", if_generation_match, content_type))

    def upload_from_string(self, data, *, content_type=None, if_generation_match=None):
        self._check_generation(if_generation_match)
        self.exists = True
        self.generation += 1
        self.content_type = content_type
        self.text = data
        self.data = data.encode()
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


def fake_asset_outputs(tmp_path: Path, *, release: str = "2026-04-28") -> sea_ice.AssetOutputs:
    fgb = tmp_path / "out.fgb"
    pmtiles = tmp_path / "out.pmtiles"
    metadata = tmp_path / "out.metadata.ndjson.gz"
    schema = tmp_path / "out.schema.json"
    manifest = tmp_path / "out.manifest.json"
    for path, data in (
        (fgb, b"fgb"),
        (pmtiles, b"pmtiles"),
        (metadata, b"metadata"),
        (schema, b'{"schema_version":1}\n'),
    ):
        path.write_bytes(data)
    schema_payload = {
        "schema_version": 1,
        "asset_slug": sea_ice.ASSET.slug,
        "release": release,
        "fields": [{"name": "ice_date", "type": "String", "nullable": False, "projectable": True}],
    }
    return sea_ice.AssetOutputs(
        fgb=fgb,
        pmtiles=pmtiles,
        metadata=metadata,
        schema=schema,
        manifest=manifest,
        row_count=2,
        sha256={
            "fgb": VALID_FGB_SHA,
            "pmtiles": VALID_PMTILES_SHA,
            "metadata": VALID_METADATA_SHA,
            "schema": VALID_SCHEMA_SHA,
        },
        schema_payload=schema_payload,
        sidecar_records=(),
    )


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

    def test_convert_geojsonseq_to_fgb_uses_geojsonseq_driver_prefix(self):
        with mock.patch.object(sea_ice, "remove_if_exists"), mock.patch.object(
            sea_ice,
            "run_command",
        ) as run_command:
            sea_ice.convert_geojsonseq_to_fgb(
                Path("features.geojsonseq"),
                Path("out.fgb"),
            )

        command = run_command.call_args.args[0]
        self.assertEqual(command[-1], "GeoJSONSeq:features.geojsonseq")

    def test_probe_source_uses_common_request_helper(self):
        source_url = "https://example.test/source.tif.gz"
        outcome = sea_ice.RequestOutcome(
            status=sea_ice.STATUS_SUCCESS,
            url=source_url,
            attempts=1,
            http_status=200,
        )
        with mock.patch.object(
            sea_ice,
            "request_with_retries",
            return_value=(outcome, None),
        ) as request_with_retries:
            result = sea_ice.probe_source(source_url)

        self.assertEqual(result, outcome)
        request_with_retries.assert_called_once()
        kwargs = request_with_retries.call_args.kwargs
        self.assertEqual(kwargs["not_ready_status_codes"], frozenset({404}))
        self.assertEqual(kwargs["timeout_seconds"], sea_ice.REQUEST_TIMEOUT_SECONDS)

    def test_find_latest_available_source_continues_after_transient_probe(self):
        anchor_day = dt.date(2026, 5, 12)

        def fake_probe(source_url: str) -> sea_ice.RequestOutcome:
            if "ims2026132" in source_url:
                return sea_ice.RequestOutcome(
                    status=sea_ice.STATUS_TRANSIENT,
                    url=source_url,
                    attempts=3,
                    http_status=500,
                    reason="HTTP 500",
                )
            if "ims2026131" in source_url:
                return sea_ice.RequestOutcome(
                    status=sea_ice.STATUS_SUCCESS,
                    url=source_url,
                    http_status=200,
                    attempts=1,
                )
            self.fail(f"unexpected probe URL: {source_url}")

        with mock.patch.object(sea_ice, "probe_source", side_effect=fake_probe):
            lookup = sea_ice.find_latest_available_source(
                "https://example.test/{yyyy}/{file_name}",
                anchor_day,
                2,
            )

        self.assertIsNotNone(lookup.source)
        self.assertEqual(lookup.source.filename_date, dt.date(2026, 5, 11))
        self.assertEqual(len(lookup.source_request_warnings), 1)
        self.assertEqual(lookup.source_request_warnings[0]["http_status"], 500)

    def test_find_latest_available_source_continues_after_missing_probe(self):
        anchor_day = dt.date(2026, 5, 12)

        def fake_probe(source_url: str) -> sea_ice.RequestOutcome:
            if "ims2026132" in source_url:
                return sea_ice.RequestOutcome(
                    status=sea_ice.STATUS_NOT_READY,
                    url=source_url,
                    http_status=404,
                    attempts=1,
                )
            if "ims2026131" in source_url:
                return sea_ice.RequestOutcome(
                    status=sea_ice.STATUS_SUCCESS,
                    url=source_url,
                    http_status=200,
                    attempts=1,
                )
            self.fail(f"unexpected probe URL: {source_url}")

        with mock.patch.object(sea_ice, "probe_source", side_effect=fake_probe):
            lookup = sea_ice.find_latest_available_source(
                "https://example.test/{yyyy}/{file_name}",
                anchor_day,
                2,
            )

        self.assertIsNotNone(lookup.source)
        self.assertEqual(lookup.source.filename_date, dt.date(2026, 5, 11))
        self.assertEqual(lookup.source_request_warnings, ())

    def test_transient_probe_to_published_source_skips_without_uploads(self):
        bucket = FakeBucket()
        asset = sea_ice.ASSET
        source_date = dt.date(2026, 5, 11)
        run_record = bucket.blob(asset.run_record_object(source_date))
        run_record.exists = True
        run_record.text = json.dumps(
            {
                "status": "success",
                "run_date": source_date.isoformat(),
                "source_version": sea_ice.ims_filename_for_day(source_date),
                "release_paths": [],
                "latest_paths": [],
            }
        )

        def fake_probe(source_url: str) -> sea_ice.RequestOutcome:
            if "ims2026132" in source_url:
                return sea_ice.RequestOutcome(
                    status=sea_ice.STATUS_TRANSIENT,
                    url=source_url,
                    attempts=3,
                    http_status=500,
                    reason="HTTP 500",
                )
            if "ims2026131" in source_url:
                return sea_ice.RequestOutcome(
                    status=sea_ice.STATUS_SUCCESS,
                    url=source_url,
                    http_status=200,
                    attempts=1,
                )
            self.fail(f"unexpected probe URL: {source_url}")

        with (
            mock.patch.dict(
                sea_ice.os.environ,
                {
                    "RUN_DATE": "2026-05-12",
                    "GOOGLE_CLOUD_PROJECT": "test-project",
                    "SHARED_DATASETS_BUCKET": bucket.name,
                    "SEA_ICE_SOURCE_URL_TEMPLATE": "https://example.test/{yyyy}/{file_name}",
                    "SEA_ICE_MAX_LOOKBACK_DAYS": "2",
                },
            ),
            mock.patch.object(sea_ice, "require_binary", return_value=None),
            mock.patch.object(sea_ice.storage, "Client", return_value=FakeClient(bucket)),
            mock.patch.object(sea_ice, "probe_source", side_effect=fake_probe),
            mock.patch.object(
                sea_ice,
                "download_source",
                side_effect=AssertionError("published source should not download"),
            ),
        ):
            record = sea_ice.run()

        self.assertEqual(record["status"], "skipped")
        self.assertEqual(record["source_filename_date"], "2026-05-11")
        self.assertEqual(record["source_request_warnings"][0]["http_status"], 500)
        self.assertFalse(bucket.blob(asset.release_object(source_date, ".fgb")).uploads)
        self.assertFalse(bucket.blob(asset.latest_object(".fgb")).uploads)

    def test_repeated_transient_probes_skip_without_release_uploads(self):
        bucket = FakeBucket()
        asset = sea_ice.ASSET

        def fake_probe(source_url: str) -> sea_ice.RequestOutcome:
            return sea_ice.RequestOutcome(
                status=sea_ice.STATUS_TRANSIENT,
                url=source_url,
                attempts=3,
                http_status=500,
                reason="HTTP 500",
            )

        with (
            mock.patch.dict(
                sea_ice.os.environ,
                {
                    "RUN_DATE": "2026-05-12",
                    "GOOGLE_CLOUD_PROJECT": "test-project",
                    "SHARED_DATASETS_BUCKET": bucket.name,
                    "SEA_ICE_SOURCE_URL_TEMPLATE": "https://example.test/{yyyy}/{file_name}",
                    "SEA_ICE_MAX_LOOKBACK_DAYS": "2",
                },
            ),
            mock.patch.object(sea_ice, "require_binary", return_value=None),
            mock.patch.object(sea_ice.storage, "Client", return_value=FakeClient(bucket)),
            mock.patch.object(sea_ice, "probe_source", side_effect=fake_probe),
            mock.patch.object(
                sea_ice,
                "download_source",
                side_effect=AssertionError("no source should not download"),
            ),
        ):
            record = sea_ice.run()

        self.assertEqual(record["status"], "skipped")
        self.assertEqual(len(record["source_request_warnings"]), 2)
        for source_date in (dt.date(2026, 5, 12), dt.date(2026, 5, 11)):
            self.assertFalse(bucket.blob(asset.release_object(source_date, ".fgb")).uploads)
            self.assertFalse(
                bucket.blob(asset.release_object(source_date, ".pmtiles")).uploads
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
            record = sea_ice.publish_outputs(
                publisher=publisher,
                asset=asset,
                outputs=fake_asset_outputs(tmp_path),
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
        self.assertEqual(json.loads(run_record.text)["row_count"], 2)
        self.assertEqual(len(json.loads(run_record.text)["release_paths"]), 5)
        manifest_blob = bucket.blob(asset.release_object(run_date, ".manifest.json"))
        manifest = json.loads(manifest_blob.text)
        self.assertEqual(manifest["id_strategy"], {"strategy": "generated", "preimage": ["geometry_digest"]})
        artifacts = {artifact["role"]: artifact for artifact in manifest["artifacts"]}
        release_by_role = dict(zip(("fgb", "pmtiles", "metadata", "schema"), record["release_paths"][:4], strict=True))
        latest_by_role = dict(zip(("fgb", "pmtiles", "metadata", "schema"), record["latest_paths"][:4], strict=True))
        for role in ("fgb", "pmtiles", "metadata", "schema"):
            self.assertEqual(artifacts[role]["path"], release_by_role[role]["path"])
            self.assertEqual(artifacts[role]["generation"], release_by_role[role]["generation"])
            self.assertEqual(artifacts[role]["latest_path"], latest_by_role[role]["path"])
            self.assertEqual(artifacts[role]["latest_generation"], latest_by_role[role]["generation"])
        self.assertNotIn("generation", artifacts["manifest"])
        self.assertNotIn("latest_generation", artifacts["manifest"])
        manifest_sha = hashlib.sha256(manifest_blob.data).hexdigest()
        self.assertEqual(record["sha256"]["manifest"], manifest_sha)
        self.assertEqual(json.loads(run_record.text)["sha256"]["manifest"], manifest_sha)

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
            record = sea_ice.publish_outputs(
                publisher=publisher,
                asset=asset,
                outputs=fake_asset_outputs(tmp_path),
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
        runnable_command(command)
        for command in (
            ["gdal_create", "--help-general"],
            ["gdal_calc.py", "--help"],
            ["gdal_polygonize.py", "--help"],
            ["ogr2ogr", "--version"],
            ["ogrinfo", "--version"],
            ["pmtiles", "version"],
        )
    )
    and shutil.which("tippecanoe-decode"),
    "requires runnable GDAL, tippecanoe-decode, and PMTiles binaries",
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
            self.assertTrue(outputs.metadata.exists())
            self.assertTrue(outputs.schema.exists())


if __name__ == "__main__":
    unittest.main()
