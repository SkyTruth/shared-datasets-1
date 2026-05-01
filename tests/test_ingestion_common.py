from __future__ import annotations

import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.common import release_index
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

    def list_blobs(self, prefix: str = ""):
        return [blob for blob in self.blobs.values() if blob.exists and blob.name.startswith(prefix)]


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
        blob.text = json.dumps(
            {
                "asset_slug": asset.slug,
                "run_date": run_date.isoformat(),
                "status": "success",
                "release_path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/",
                "release_paths": [
                    {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.fgb"},
                ],
            }
        )

        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        info = publisher.write_run_record(
            asset=asset,
            run_date=run_date,
            payload=json.loads(blob.text),
        )

        self.assertEqual(info["generation"], blob.generation)
        self.assertEqual(blob.uploads, [])
        index = json.loads(bucket.blob("_catalog/releases/test-asset.json").text)
        self.assertEqual(index["latest_release"]["date"], "2026-04-29")

    def test_partial_release_blocks_publish_for_configured_suffixes(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        run_date = dt.date(2026, 4, 29)
        bucket.blob(asset.release_object(run_date, ".csv")).exists = True

        publisher = GcsPublisher(FakeClient(bucket), bucket.name, release_suffixes=(".csv",))

        with self.assertRaisesRegex(RuntimeError, "without a successful run record"):
            publisher.assert_no_partial_release(asset, run_date)

    def test_write_success_run_record_updates_release_index(self):
        bucket = FakeBucket()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        asset = FakeAsset()
        run_date = dt.date(2026, 5, 1)
        payload = {
            "asset_slug": asset.slug,
            "run_date": run_date.isoformat(),
            "status": "success",
            "source_version": "source-v1",
            "release_path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/",
            "release_paths": [
                {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.fgb", "generation": 2},
                {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.pmtiles", "generation": 3},
            ],
            "rows": 10,
            "sha256": {"fgb": "abc", "pmtiles": "def"},
        }

        publisher.write_run_record(asset=asset, run_date=run_date, payload=payload)

        index = json.loads(bucket.blob("_catalog/releases/test-asset.json").text)
        self.assertEqual(index["latest_release"]["date"], "2026-05-01")
        self.assertEqual(index["latest_run"]["status"], "success")
        self.assertEqual([item["format"] for item in index["latest_release"]["files"]], ["fgb", "pmtiles"])

    def test_skipped_run_record_updates_latest_run_only(self):
        bucket = FakeBucket()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        asset = FakeAsset()
        run_date = dt.date(2026, 5, 1)

        publisher.write_run_record(
            asset=asset,
            run_date=run_date,
            payload={
                "asset_slug": asset.slug,
                "run_date": run_date.isoformat(),
                "status": "skipped",
                "reason": "source unchanged",
            },
        )

        index = json.loads(bucket.blob("_catalog/releases/test-asset.json").text)
        self.assertEqual(index["releases"], [])
        self.assertEqual(index["latest_run"]["status"], "skipped")
        self.assertEqual(index["latest_run"]["reason"], "source unchanged")

    def test_release_index_merge_is_idempotent_by_date(self):
        entry = {
            "date": "2026-05-01",
            "release_path": "gs://test-bucket/asset/releases/2026-05-01/",
            "files": [{"path": "gs://test-bucket/asset/releases/2026-05-01/asset.fgb", "format": "fgb"}],
        }

        index = release_index.empty_release_index("test-asset")
        index = release_index.merge_successful_release(index, entry, updated_at="2026-05-01T00:00:00+00:00")
        index = release_index.merge_successful_release(index, entry, updated_at="2026-05-01T00:00:00+00:00")

        self.assertEqual(len(index["releases"]), 1)
        self.assertEqual(index["latest_release"]["date"], "2026-05-01")

    def test_release_index_update_retries_generation_mismatch(self):
        bucket = FakeBucket()
        attempts = []
        real_write = release_index.write_release_index

        def flaky_write(bucket_arg, asset_slug, payload, *, generation):
            attempts.append(generation)
            if len(attempts) == 1:
                raise PreconditionFailed("race")
            return real_write(bucket_arg, asset_slug, payload, generation=generation)

        with mock.patch("ingestion.common.release_index.write_release_index", side_effect=flaky_write):
            info = release_index.record_latest_run(
                bucket,
                "test-asset",
                {"asset_slug": "test-asset", "run_date": "2026-05-01", "status": "skipped"},
            )

        self.assertEqual(len(attempts), 2)
        self.assertEqual(info["path"], "gs://test-bucket/_catalog/releases/test-asset.json")


if __name__ == "__main__":
    unittest.main()
