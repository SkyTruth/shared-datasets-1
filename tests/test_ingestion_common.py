from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.common.gcs import GcsPublisher


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


class FakeAsset:
    slug = "test-asset"

    def release_object(self, run_date: dt.date, suffix: str) -> str:
        return f"asset/releases/{run_date.isoformat()}/asset{suffix}"

    def latest_object(self, suffix: str) -> str:
        return f"asset/latest/asset{suffix}"

    def run_record_object(self, run_date: dt.date) -> str:
        return f"asset/runs/{run_date.isoformat()}.json"


class GcsPublisherTests(unittest.TestCase):
    def test_release_upload_uses_no_clobber(self):
        bucket = FakeBucket()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        with tempfile.NamedTemporaryFile(suffix=".fgb") as tmp:
            tmp.write(b"data")
            tmp.flush()
            info = publisher.upload_new_object(
                local_path=Path(tmp.name),
                object_name="asset/releases/2026-04-29/asset.fgb",
                metadata={"asset_slug": "asset"},
            )

        blob = bucket.blob("asset/releases/2026-04-29/asset.fgb")
        self.assertEqual(blob.uploads[0][1], 0)
        self.assertEqual(blob.content_type, "application/octet-stream")
        self.assertEqual(info["generation"], blob.generation)

    def test_cog_release_upload_uses_cog_content_type(self):
        bucket = FakeBucket()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
            tmp.write(b"data")
            tmp.flush()
            publisher.upload_new_object(
                local_path=Path(tmp.name),
                object_name="asset/releases/2026-04-29/asset.tif",
                metadata={"asset_slug": "asset"},
            )

        blob = bucket.blob("asset/releases/2026-04-29/asset.tif")
        self.assertEqual(blob.uploads[0][1], 0)
        self.assertEqual(
            blob.content_type,
            "image/tiff; application=geotiff; profile=cloud-optimized",
        )

    def test_latest_replace_uses_observed_generation(self):
        bucket = FakeBucket()
        blob = bucket.blob("asset/latest/asset.pmtiles")
        blob.exists = True
        blob.generation = 7
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)

        with tempfile.NamedTemporaryFile(suffix=".pmtiles") as tmp:
            tmp.write(b"data")
            tmp.flush()
            publisher.replace_latest_object(
                local_path=Path(tmp.name),
                object_name=blob.name,
                metadata={"asset_slug": "asset"},
            )

        self.assertEqual(blob.uploads[0][1], 7)
        self.assertEqual(blob.content_type, "application/vnd.pmtiles")

    def test_latest_manifest_replace_uses_observed_generation(self):
        bucket = FakeBucket()
        blob = bucket.blob("asset/latest/manifest.json")
        blob.exists = True
        blob.generation = 9
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)

        with tempfile.NamedTemporaryFile(suffix=".json") as tmp:
            tmp.write(b'{"release_path":"gs://bucket/asset/releases/2026-04-29/asset.zarr/"}')
            tmp.flush()
            publisher.replace_latest_object(
                local_path=Path(tmp.name),
                object_name=blob.name,
                metadata={"asset_slug": "asset"},
            )

        self.assertEqual(blob.uploads[0][1], 9)
        self.assertEqual(blob.content_type, "application/json")

    def test_successful_existing_run_record_is_idempotent(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        run_date = dt.date(2026, 4, 29)
        blob = bucket.blob(asset.run_record_object(run_date))
        blob.exists = True
        blob.text = json.dumps({"status": "success"})

        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        info = publisher.write_run_record(
            asset=asset,
            run_date=run_date,
            payload={"status": "success"},
        )

        self.assertEqual(info["generation"], blob.generation)
        self.assertEqual(blob.uploads, [])

    def test_partial_release_blocks_publish_for_configured_suffixes(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        run_date = dt.date(2026, 4, 29)
        bucket.blob(asset.release_object(run_date, ".csv")).exists = True

        publisher = GcsPublisher(FakeClient(bucket), bucket.name, release_suffixes=(".csv",))

        with self.assertRaisesRegex(RuntimeError, "without a successful run record"):
            publisher.assert_no_partial_release(asset, run_date)


if __name__ == "__main__":
    unittest.main()
