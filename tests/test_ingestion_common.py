from __future__ import annotations

import datetime as dt
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from google.api_core.exceptions import NotFound, PreconditionFailed

from ingestion.common import release_index
from ingestion.common.gcs import GcsPublisher
from scripts import release_feature_model


class FakeBlob:
    def __init__(self, name: str, *, exists: bool = False, generation: int = 1) -> None:
        self.name = name
        self.exists = exists
        self.generation = generation
        self.metageneration = 1
        self.size = 0
        self.metadata = None
        self.remote_metadata = None
        self.content_type = None
        self.content = b""
        self.text = ""
        self.uploads = []

    def reload(self) -> None:
        if not self.exists:
            raise NotFound("not found")
        if self.remote_metadata is not None:
            self.metadata = dict(self.remote_metadata)

    def download_as_text(self) -> str:
        self.reload()
        return self.text

    def download_as_bytes(self) -> bytes:
        self.reload()
        return self.content

    def upload_from_filename(self, filename, *, content_type=None, if_generation_match=None):
        self._check_generation(if_generation_match)
        self.exists = True
        self.generation += 1
        self.content_type = content_type
        self.size = Path(filename).stat().st_size
        self.remote_metadata = dict(self.metadata or {})
        self.uploads.append(("filename", if_generation_match, content_type))

    def upload_from_string(self, data, *, content_type=None, if_generation_match=None):
        self._check_generation(if_generation_match)
        self.exists = True
        self.generation += 1
        self.content_type = content_type
        self.text = data
        self.size = len(data.encode())
        self.remote_metadata = dict(self.metadata or {})
        self.uploads.append(("string", if_generation_match, content_type))

    def patch(self, **kwargs):
        if_metageneration_match = kwargs.get("if_metageneration_match")
        if not self.exists:
            raise NotFound("not found")
        if (
            if_metageneration_match is not None
            and if_metageneration_match != self.metageneration
        ):
            raise PreconditionFailed("metageneration mismatch")
        self.metageneration += 1
        self.remote_metadata = dict(self.metadata or {})
        self.uploads.append(("patch", if_metageneration_match, None))

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


def valid_metadata_contract_record(asset: FakeAsset, release: str) -> dict:
    release_prefix = f"asset/releases/{release}"
    return {
        "schema_version": 1,
        "asset_slug": asset.slug,
        "run_date": release,
        "release_date": release,
        "status": "success",
        "release_path": f"gs://test-bucket/{release_prefix}/",
        "release_paths": [
            {"path": f"gs://test-bucket/{release_prefix}/asset.fgb", "generation": 2},
            {"path": f"gs://test-bucket/{release_prefix}/asset.pmtiles", "generation": 3},
            {"path": f"gs://test-bucket/{release_prefix}/asset.metadata.ndjson.gz", "generation": 4},
            {"path": f"gs://test-bucket/{release_prefix}/asset.schema.json", "generation": 5},
            {"path": f"gs://test-bucket/{release_prefix}/asset.manifest.json", "generation": 6},
        ],
        "row_count": 1,
        "sha256": {
            "fgb": "a" * 64,
            "pmtiles": "b" * 64,
            "metadata": "c" * 64,
            "schema": "d" * 64,
            "manifest": "e" * 64,
        },
    }


def seed_valid_metadata_contract(bucket: FakeBucket, asset: FakeAsset, release: str) -> dict:
    record = valid_metadata_contract_record(asset, release)
    metadata = bucket.blob(f"asset/releases/{release}/asset.metadata.ndjson.gz")
    metadata.exists = True
    metadata.content = gzip.compress(
        json.dumps(
            {
                "schema_version": release_feature_model.METADATA_SIDECAR_SCHEMA_VERSION,
                "asset_slug": asset.slug,
                "release": release,
                "feature_id": "1",
                "geometry_hash": "sha256:" + "a" * 64,
                "properties_hash": "sha256:" + "b" * 64,
                "identity_key": ["1"],
                "properties": {"OBJECTID": 1},
                "provenance": {},
            },
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )

    schema_payload = {
        "schema_version": release_feature_model.RELEASE_SCHEMA_SCHEMA_VERSION,
        "asset_slug": asset.slug,
        "release": release,
        "fields": [
            {
                "name": "OBJECTID",
                "type": "Integer",
                "nullable": False,
                "projectable": True,
            }
        ],
    }
    schema = bucket.blob(f"asset/releases/{release}/asset.schema.json")
    schema.exists = True
    schema.text = json.dumps(schema_payload)

    manifest = bucket.blob(f"asset/releases/{release}/asset.manifest.json")
    manifest.exists = True
    manifest.text = json.dumps(
        {
            "schema_version": release_feature_model.RELEASE_MANIFEST_SCHEMA_VERSION,
            "asset_slug": asset.slug,
            "release": release,
            "release_feature_model_schema_version": release_feature_model.RELEASE_FEATURE_MODEL_SCHEMA_VERSION,
            "source_inputs": [],
            "artifacts": [
                {
                    "role": "fgb",
                    "path": f"gs://test-bucket/asset/releases/{release}/asset.fgb",
                    "sha256": "a" * 64,
                    "generation": 2,
                },
                {
                    "role": "pmtiles",
                    "path": f"gs://test-bucket/asset/releases/{release}/asset.pmtiles",
                    "sha256": "b" * 64,
                    "generation": 3,
                },
                {
                    "role": "metadata",
                    "path": f"gs://test-bucket/asset/releases/{release}/asset.metadata.ndjson.gz",
                    "sha256": "c" * 64,
                    "generation": 4,
                },
                {
                    "role": "schema",
                    "path": f"gs://test-bucket/asset/releases/{release}/asset.schema.json",
                    "sha256": "d" * 64,
                    "generation": 5,
                },
                {
                    "role": "manifest",
                    "path": f"gs://test-bucket/asset/releases/{release}/asset.manifest.json",
                },
            ],
            "schema": schema_payload,
            "identity": release_feature_model.build_identity_metadata(
                strategy="source_field",
                source_fields=["OBJECTID"],
            ),
            "hashes": {},
            "validation": {},
            "index_load_status": "Firestore metadata serving is inactive",
            "index_status_policy": {"mode": "inactive_firestore_serving", "path": None},
        }
    )
    return record


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
        blob.remote_metadata = {"run_date": "old"}
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
        self.assertEqual(blob.remote_metadata, {"asset_slug": "asset"})

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
                "schema_version": 1,
                "asset_slug": asset.slug,
                "run_date": run_date.isoformat(),
                "release_date": run_date.isoformat(),
                "status": "success",
                "release_path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/",
                "release_paths": [
                    {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.fgb"},
                ],
                "row_count": 10,
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

    def test_record_existing_successful_release_refreshes_index(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        run_date = dt.date(2026, 5, 1)
        run_record = bucket.blob(asset.run_record_object(run_date))
        run_record.exists = True
        run_record.generation = 12
        run_record.text = json.dumps(
            {
                "schema_version": 1,
                "asset_slug": asset.slug,
                "run_date": run_date.isoformat(),
                "release_date": run_date.isoformat(),
                "status": "success",
                "source_version": "source-v1",
                "release_path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/",
                "release_paths": [
                    {
                        "path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.fgb",
                        "generation": 3,
                    },
                ],
                "row_count": 10,
            }
        )
        stale_index = bucket.blob("_catalog/releases/test-asset.json")
        stale_index.exists = True
        stale_index.generation = 4
        stale_index.text = json.dumps(
            {
                "schema_version": 1,
                "asset_slug": asset.slug,
                "updated_at": "2026-04-01T00:00:00+00:00",
                "latest_release": None,
                "latest_run": None,
                "releases": [],
            }
        )

        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        info = publisher.record_existing_successful_release(asset, run_date)

        self.assertEqual(info["generation"], stale_index.generation)
        index = json.loads(stale_index.text)
        self.assertEqual(index["latest_release"]["date"], "2026-05-01")
        self.assertEqual(index["latest_release"]["run_record_path"], "gs://test-bucket/asset/runs/2026-05-01.json")
        self.assertEqual(index["latest_run"]["status"], "success")

    def test_replace_latest_metadata_from_run_record_uses_metageneration(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        run_date = dt.date(2026, 5, 1)
        latest = bucket.blob(asset.latest_object(".fgb"))
        latest.exists = True
        latest.generation = 9
        latest.metageneration = 3
        latest.size = 27
        latest.remote_metadata = {"run_date": "old"}
        run_record = bucket.blob(asset.run_record_object(run_date))
        run_record.exists = True
        run_record.text = json.dumps(
            {
                "schema_version": 1,
                "asset_slug": asset.slug,
                "run_date": run_date.isoformat(),
                "release_date": run_date.isoformat(),
                "status": "success",
                "latest_paths": [
                    {
                        "path": f"gs://{bucket.name}/{latest.name}",
                        "generation": 9,
                    },
                ],
            }
        )

        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        refreshed = publisher.replace_latest_metadata_from_run_record(
            asset,
            run_date,
            {"asset_slug": asset.slug, "run_date": run_date.isoformat()},
        )

        self.assertEqual(refreshed[0]["metageneration"], 4)
        self.assertEqual(latest.uploads, [("patch", 3, None)])
        self.assertEqual(
            latest.remote_metadata,
            {"asset_slug": asset.slug, "run_date": "2026-05-01"},
        )

    def test_partial_release_blocks_publish_for_configured_suffixes(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        run_date = dt.date(2026, 4, 29)
        bucket.blob(asset.release_object(run_date, ".csv")).exists = True

        publisher = GcsPublisher(FakeClient(bucket), bucket.name, release_suffixes=(".csv",))

        with self.assertRaisesRegex(RuntimeError, "without a successful run record"):
            publisher.assert_no_partial_release(asset, run_date)

    def test_missing_latest_metadata_records_returns_none(self):
        bucket = FakeBucket()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)

        self.assertIsNone(publisher.load_latest_metadata_records(FakeAsset()))

    def test_invalid_latest_metadata_records_block_generated_id_reset(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        latest = bucket.blob(asset.latest_object(".metadata.ndjson.gz"))
        latest.exists = True
        latest.content = gzip.compress(
            json.dumps(
                {
                    "schema_version": 2,
                    "asset_slug": asset.slug,
                    "release": "2026-05-01",
                    "feature_id": "bad-id",
                    "geometry_hash": "sha256:" + "a" * 64,
                    "properties_hash": "sha256:" + "b" * 64,
                    "identity_key": ["key"],
                    "properties": {},
                    "provenance": {},
                }
            ).encode("utf-8")
            + b"\n"
        )
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)

        with self.assertRaisesRegex(RuntimeError, "refusing to reset generated sequence feature_id values"):
            publisher.load_latest_metadata_records(asset)

    def test_release_metadata_contract_issue_accepts_valid_contract(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        record = seed_valid_metadata_contract(bucket, asset, "2026-05-01")
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)

        self.assertIsNone(publisher.release_metadata_contract_issue(asset, record))

    def test_release_metadata_contract_issue_reports_invalid_schema(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        record = seed_valid_metadata_contract(bucket, asset, "2026-05-01")
        bucket.blob("asset/releases/2026-05-01/asset.schema.json").text = json.dumps(
            {
                "schema_version": 1,
                "asset_slug": asset.slug,
                "release": "2026-05-01",
                "fields": [],
            }
        )
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)

        issue = publisher.release_metadata_contract_issue(asset, record)

        self.assertIsNotNone(issue)
        self.assertIn("unsupported schema_version", issue)

    def test_write_success_run_record_updates_release_index(self):
        bucket = FakeBucket()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        asset = FakeAsset()
        run_date = dt.date(2026, 5, 1)
        payload = {
            "schema_version": 1,
            "asset_slug": asset.slug,
            "run_date": run_date.isoformat(),
            "release_date": run_date.isoformat(),
            "status": "success",
            "source_version": "source-v1",
            "release_path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/",
            "release_paths": [
                {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.fgb", "generation": 2},
                {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.pmtiles", "generation": 3},
            ],
            "row_count": 10,
            "sha256": {"fgb": "abc", "pmtiles": "def"},
        }

        publisher.write_run_record(asset=asset, run_date=run_date, payload=payload)

        index = json.loads(bucket.blob("_catalog/releases/test-asset.json").text)
        self.assertEqual(index["latest_release"]["date"], "2026-05-01")
        self.assertEqual(index["latest_run"]["status"], "success")
        self.assertEqual([item["format"] for item in index["latest_release"]["files"]], ["fgb", "pmtiles"])
        self.assertNotIn("index_status_policy", index["latest_release"])

    def test_write_metadata_bundle_run_record_adds_index_status_policy(self):
        bucket = FakeBucket()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        asset = FakeAsset()
        run_date = dt.date(2026, 5, 1)

        publisher.write_run_record(
            asset=asset,
            run_date=run_date,
            payload={
                "schema_version": 1,
                "asset_slug": asset.slug,
                "run_date": run_date.isoformat(),
                "release_date": run_date.isoformat(),
                "status": "success",
                "source_version": "source-v1",
                "release_path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/",
                "release_paths": [
                    {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.fgb", "generation": 2},
                    {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.metadata.ndjson.gz", "generation": 3},
                    {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.schema.json", "generation": 4},
                    {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.manifest.json", "generation": 5},
                ],
                "row_count": 10,
            },
        )

        release = json.loads(bucket.blob("_catalog/releases/test-asset.json").text)["latest_release"]
        self.assertEqual(release["index_load_status"], "Firestore metadata serving is inactive")
        self.assertEqual(
            release["index_status_policy"],
            {
                "mode": "inactive_firestore_serving",
                "path": None,
            },
        )

    def test_skipped_run_record_updates_latest_run_only(self):
        bucket = FakeBucket()
        publisher = GcsPublisher(FakeClient(bucket), bucket.name)
        asset = FakeAsset()
        run_date = dt.date(2026, 5, 1)

        publisher.write_run_record(
            asset=asset,
            run_date=run_date,
            payload={
                "schema_version": 1,
                "asset_slug": asset.slug,
                "run_date": run_date.isoformat(),
                "release_date": run_date.isoformat(),
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

    def test_successful_run_record_requires_modern_schema_fields(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        run_date = dt.date(2026, 5, 1)

        with self.assertRaisesRegex(release_index.ReleaseIndexError, "schema_version"):
            release_index.record_successful_release(
                bucket,
                asset.slug,
                {
                    "asset_slug": asset.slug,
                    "run_date": run_date.isoformat(),
                    "status": "success",
                    "release_path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/",
                    "release_paths": [
                        {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.fgb"},
                    ],
                    "rows": 10,
                },
            )

        with self.assertRaisesRegex(release_index.ReleaseIndexError, "release_date"):
            release_index.record_successful_release(
                bucket,
                asset.slug,
                {
                    "schema_version": 1,
                    "asset_slug": asset.slug,
                    "run_date": run_date.isoformat(),
                    "status": "success",
                    "release_path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/",
                    "release_paths": [
                        {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.fgb"},
                    ],
                    "row_count": 10,
                },
            )

        with self.assertRaisesRegex(release_index.ReleaseIndexError, "row_count"):
            release_index.record_successful_release(
                bucket,
                asset.slug,
                {
                    "schema_version": 1,
                    "asset_slug": asset.slug,
                    "run_date": run_date.isoformat(),
                    "release_date": run_date.isoformat(),
                    "status": "success",
                    "release_path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/",
                    "release_paths": [
                        {"path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.fgb"},
                    ],
                },
            )

    def test_release_paths_must_use_path_entries_not_legacy_aliases(self):
        bucket = FakeBucket()
        asset = FakeAsset()
        run_date = dt.date(2026, 5, 1)

        with self.assertRaisesRegex(release_index.ReleaseIndexError, "missing path"):
            release_index.record_successful_release(
                bucket,
                asset.slug,
                {
                    "schema_version": 1,
                    "asset_slug": asset.slug,
                    "run_date": run_date.isoformat(),
                    "release_date": run_date.isoformat(),
                    "status": "success",
                    "release_path": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/",
                    "release_paths": [
                        {"uri": f"gs://{bucket.name}/asset/releases/{run_date.isoformat()}/asset.fgb"},
                    ],
                    "row_count": 10,
                },
            )

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
                {
                    "schema_version": 1,
                    "asset_slug": "test-asset",
                    "run_date": "2026-05-01",
                    "release_date": "2026-05-01",
                    "status": "skipped",
                },
            )

        self.assertEqual(len(attempts), 2)
        self.assertEqual(info["path"], "gs://test-bucket/_catalog/releases/test-asset.json")

    def test_rebuild_index_from_bucket_uses_releases_without_run_records(self):
        bucket = FakeBucket()
        first = bucket.blob("asset/releases/2026-05-01/asset.fgb")
        first.exists = True
        first.generation = 8
        first.size = 13
        second = bucket.blob("asset/releases/2026-04-01/asset.fgb")
        second.exists = True
        second.generation = 6
        second.size = 21

        index = release_index.rebuild_index_from_bucket(
            bucket,
            {
                "asset_slug": "test-asset",
                "canonical_path": "gs://test-bucket/asset/latest/asset.fgb",
            },
        )

        self.assertEqual([item["date"] for item in index["releases"]], ["2026-05-01", "2026-04-01"])
        self.assertEqual(index["latest_release"]["files"][0]["path"], "gs://test-bucket/asset/releases/2026-05-01/asset.fgb")
        self.assertEqual(index["latest_release"]["files"][0]["generation"], 8)
        self.assertEqual(index["latest_run"]["status"], "success")
        self.assertNotIn("run_record_path", index["latest_run"])

    def test_rebuild_index_marks_localized_metadata_sidecars_with_locale(self):
        bucket = FakeBucket()
        canonical = bucket.blob("asset/releases/2026-05-01/asset.fgb")
        canonical.exists = True
        canonical.generation = 8
        canonical.size = 13
        source_sidecar = bucket.blob("asset/releases/2026-05-01/asset.metadata.ndjson.gz")
        source_sidecar.exists = True
        source_sidecar.generation = 9
        source_sidecar.size = 21
        localized_sidecar = bucket.blob("asset/releases/2026-05-01/asset.metadata.es.ndjson.gz")
        localized_sidecar.exists = True
        localized_sidecar.generation = 10
        localized_sidecar.size = 34

        index = release_index.rebuild_index_from_bucket(
            bucket,
            {
                "asset_slug": "test-asset",
                "canonical_format": "fgb",
                "canonical_path": "gs://test-bucket/asset/latest/asset.fgb",
            },
        )

        files = {item["path"]: item for item in index["latest_release"]["files"]}
        self.assertEqual(files["gs://test-bucket/asset/releases/2026-05-01/asset.metadata.ndjson.gz"]["format"], "metadata")
        self.assertNotIn("locale", files["gs://test-bucket/asset/releases/2026-05-01/asset.metadata.ndjson.gz"])
        self.assertEqual(files["gs://test-bucket/asset/releases/2026-05-01/asset.metadata.es.ndjson.gz"]["format"], "metadata")
        self.assertEqual(files["gs://test-bucket/asset/releases/2026-05-01/asset.metadata.es.ndjson.gz"]["role"], "metadata")
        self.assertEqual(files["gs://test-bucket/asset/releases/2026-05-01/asset.metadata.es.ndjson.gz"]["locale"], "es")

    def test_rebuild_index_adds_post_run_localized_metadata_sidecars(self):
        bucket = FakeBucket()
        for name, generation, size in (
            ("asset/releases/2026-05-01/asset.fgb", 8, 13),
            ("asset/releases/2026-05-01/asset.metadata.ndjson.gz", 9, 21),
            ("asset/releases/2026-05-01/asset.metadata.es.ndjson.gz", 10, 34),
            ("asset/releases/2026-05-01/asset.metadata.fr.ndjson.gz", 11, 35),
        ):
            blob = bucket.blob(name)
            blob.exists = True
            blob.generation = generation
            blob.size = size
        run_record = bucket.blob("asset/runs/2026-05-01.json")
        run_record.exists = True
        run_record.text = json.dumps(
            {
                "schema_version": 1,
                "asset_slug": "test-asset",
                "run_date": "2026-05-01",
                "release_date": "2026-05-01",
                "status": "success",
                "release_path": "gs://test-bucket/asset/releases/2026-05-01/",
                "release_paths": [
                    "gs://test-bucket/asset/releases/2026-05-01/asset.fgb",
                    "gs://test-bucket/asset/releases/2026-05-01/asset.metadata.ndjson.gz",
                    "gs://test-bucket/asset/releases/2026-05-01/asset.metadata.es.ndjson.gz",
                ],
                "row_count": 10,
            }
        )

        index = release_index.rebuild_index_from_bucket(
            bucket,
            {
                "asset_slug": "test-asset",
                "canonical_format": "fgb",
                "canonical_path": "gs://test-bucket/asset/latest/asset.fgb",
            },
        )

        files = {item["path"]: item for item in index["latest_release"]["files"]}
        fr_path = "gs://test-bucket/asset/releases/2026-05-01/asset.metadata.fr.ndjson.gz"
        self.assertEqual(files[fr_path]["format"], "metadata")
        self.assertEqual(files[fr_path]["role"], "metadata")
        self.assertEqual(files[fr_path]["locale"], "fr")
        self.assertEqual(files[fr_path]["generation"], 11)
        self.assertEqual(files[fr_path]["size"], 35)

    def test_rebuild_index_uses_artifact_path_hashes_for_localized_metadata(self):
        bucket = FakeBucket()
        for name, generation, size in (
            ("asset/releases/2026-05-01/asset.fgb", 8, 13),
            ("asset/releases/2026-05-01/asset.metadata.ndjson.gz", 9, 21),
            ("asset/releases/2026-05-01/asset.metadata-translations.csv", 10, 55),
            ("asset/releases/2026-05-01/asset.metadata.es.ndjson.gz", 11, 34),
        ):
            blob = bucket.blob(name)
            blob.exists = True
            blob.generation = generation
            blob.size = size
        run_record = bucket.blob("asset/runs/2026-05-01.json")
        run_record.exists = True
        run_record.text = json.dumps(
            {
                "schema_version": 1,
                "asset_slug": "test-asset",
                "run_date": "2026-05-01",
                "release_date": "2026-05-01",
                "status": "success",
                "release_path": "gs://test-bucket/asset/releases/2026-05-01/",
                "release_paths": [
                    "gs://test-bucket/asset/releases/2026-05-01/asset.fgb",
                    "gs://test-bucket/asset/releases/2026-05-01/asset.metadata.ndjson.gz",
                    "gs://test-bucket/asset/releases/2026-05-01/asset.metadata-translations.csv",
                    "gs://test-bucket/asset/releases/2026-05-01/asset.metadata.es.ndjson.gz",
                ],
                "row_count": 10,
                "sha256": {
                    "fgb": "f" * 64,
                    "metadata": "c" * 64,
                    "metadata_es": "e" * 64,
                },
                "artifacts": [
                    {
                        "path": "gs://test-bucket/asset/releases/2026-05-01/asset.metadata.ndjson.gz",
                        "sha256": "c" * 64,
                    },
                    {
                        "path": "gs://test-bucket/asset/releases/2026-05-01/asset.metadata-translations.csv",
                        "sha256": "a" * 64,
                    },
                    {
                        "path": "gs://test-bucket/asset/releases/2026-05-01/asset.metadata.es.ndjson.gz",
                        "sha256": "e" * 64,
                    },
                ],
            }
        )

        index = release_index.rebuild_index_from_bucket(
            bucket,
            {
                "asset_slug": "test-asset",
                "canonical_format": "fgb",
                "canonical_path": "gs://test-bucket/asset/latest/asset.fgb",
            },
        )

        files = {item["path"]: item for item in index["latest_release"]["files"]}
        self.assertEqual(
            files["gs://test-bucket/asset/releases/2026-05-01/asset.metadata.ndjson.gz"]["sha256"],
            "c" * 64,
        )
        self.assertEqual(
            files["gs://test-bucket/asset/releases/2026-05-01/asset.metadata-translations.csv"]["sha256"],
            "a" * 64,
        )
        self.assertEqual(
            files["gs://test-bucket/asset/releases/2026-05-01/asset.metadata.es.ndjson.gz"]["sha256"],
            "e" * 64,
        )

    def test_rebuild_index_ignores_display_only_release_blobs_without_canonical_format(self):
        bucket = FakeBucket()
        canonical = bucket.blob("asset/releases/2026-05-01/asset.fgb")
        canonical.exists = True
        canonical.generation = 8
        canonical.size = 13
        display_only = bucket.blob("asset/releases/2026-05-04/asset.pmtiles")
        display_only.exists = True
        display_only.generation = 42
        display_only.size = 99

        index = release_index.rebuild_index_from_bucket(
            bucket,
            {
                "asset_slug": "test-asset",
                "canonical_format": "fgb",
                "canonical_path": "gs://test-bucket/asset/latest/asset.fgb",
            },
        )

        self.assertEqual([item["date"] for item in index["releases"]], ["2026-05-01"])
        self.assertEqual(index["latest_release"]["date"], "2026-05-01")
        self.assertEqual(index["latest_release"]["files"][0]["format"], "fgb")

    def test_rebuild_index_prefers_live_release_blob_metadata_over_stale_run_record(self):
        bucket = FakeBucket()
        fgb = bucket.blob("asset/releases/2026-05-01/asset.fgb")
        fgb.exists = True
        fgb.generation = 8
        fgb.size = 13
        pmtiles = bucket.blob("asset/releases/2026-05-01/asset.pmtiles")
        pmtiles.exists = True
        pmtiles.generation = 42
        pmtiles.size = 99
        pmtiles.metadata = {"pmtiles_sha256": "newpmtiles"}
        run_record = bucket.blob("asset/runs/2026-05-01.json")
        run_record.exists = True
        run_record.text = json.dumps(
            {
                "asset_slug": "test-asset",
                "run_date": "2026-05-01",
                "schema_version": 1,
                "release_date": "2026-05-01",
                "status": "success",
                "release_path": "gs://test-bucket/asset/releases/2026-05-01/",
                "release_paths": [
                    {
                        "path": "gs://test-bucket/asset/releases/2026-05-01/asset.fgb",
                        "generation": 1,
                        "size": 2,
                    },
                    {
                        "path": "gs://test-bucket/asset/releases/2026-05-01/asset.pmtiles",
                        "generation": 1,
                        "size": 2,
                    },
                ],
                "row_count": 10,
                "sha256": {"fgb": "fgbsha", "pmtiles": "oldpmtiles"},
            }
        )

        index = release_index.rebuild_index_from_bucket(
            bucket,
            {
                "asset_slug": "test-asset",
                "canonical_path": "gs://test-bucket/asset/latest/asset.fgb",
            },
        )

        files = {entry["format"]: entry for entry in index["latest_release"]["files"]}
        self.assertEqual(files["fgb"]["generation"], 8)
        self.assertEqual(files["fgb"]["sha256"], "fgbsha")
        self.assertEqual(files["pmtiles"]["generation"], 42)
        self.assertEqual(files["pmtiles"]["size"], 99)
        self.assertEqual(files["pmtiles"]["sha256"], "newpmtiles")

    def test_rebuild_index_backfills_legacy_successful_run_records(self):
        bucket = FakeBucket()
        fgb = bucket.blob("asset/releases/2026-06-03/asset.fgb")
        fgb.exists = True
        fgb.generation = 8
        fgb.size = 13
        pmtiles = bucket.blob("asset/releases/2026-06-03/asset.pmtiles")
        pmtiles.exists = True
        pmtiles.generation = 42
        pmtiles.size = 99
        metadata = bucket.blob("asset/releases/2026-06-03/asset.metadata.ndjson.gz")
        metadata.exists = True
        metadata.generation = 43
        metadata.size = 100
        schema = bucket.blob("asset/releases/2026-06-03/asset.schema.json")
        schema.exists = True
        schema.generation = 44
        schema.size = 101
        manifest = bucket.blob("asset/releases/2026-06-03/asset.manifest.json")
        manifest.exists = True
        manifest.generation = 45
        manifest.size = 102
        run_record = bucket.blob("asset/runs/2026-06-03.json")
        run_record.exists = True
        run_record.generation = 77
        run_record.size = 123
        run_record.text = json.dumps(
            {
                "asset_slug": "test-asset",
                "record_version": 1,
                "run_date": "2026-06-03",
                "status": "success",
                "release_path": "gs://test-bucket/asset/releases/2026-06-03/",
                "release_paths": [
                    {
                        "path": "gs://test-bucket/asset/releases/2026-06-03/asset.fgb",
                        "generation": 1,
                        "size": 2,
                    },
                    {
                        "path": "gs://test-bucket/asset/releases/2026-06-03/asset.pmtiles",
                        "generation": 1,
                        "size": 2,
                    },
                    {
                        "path": "gs://test-bucket/asset/releases/2026-06-03/asset.metadata.ndjson.gz",
                        "generation": 1,
                        "size": 2,
                    },
                    {
                        "path": "gs://test-bucket/asset/releases/2026-06-03/asset.schema.json",
                        "generation": 1,
                        "size": 2,
                    },
                    {
                        "path": "gs://test-bucket/asset/releases/2026-06-03/asset.manifest.json",
                        "generation": 1,
                        "size": 2,
                    },
                ],
                "rows": 1755,
                "sha256": {
                    "fgb": "f" * 64,
                    "pmtiles": "b" * 64,
                    "metadata": "c" * 64,
                    "schema": "d" * 64,
                    "manifest": "e" * 64,
                },
                "source_filename": "ims2026154_4km_GIS_v1.3.tif.gz",
            }
        )

        index = release_index.rebuild_index_from_bucket(
            bucket,
            {
                "asset_slug": "test-asset",
                "canonical_path": "gs://test-bucket/asset/latest/asset.fgb",
            },
        )

        release = index["latest_release"]
        self.assertEqual(release["date"], "2026-06-03")
        self.assertEqual(release["rows"], 1755)
        self.assertEqual(release["source_version"], "ims2026154_4km_GIS_v1.3.tif.gz")
        self.assertEqual(release["run_record_path"], "gs://test-bucket/asset/runs/2026-06-03.json")
        files = {entry["format"]: entry for entry in release["files"]}
        self.assertEqual(files["fgb"]["generation"], 8)
        self.assertEqual(files["fgb"]["size"], 13)
        self.assertEqual(files["fgb"]["sha256"], "f" * 64)
        self.assertEqual(files["pmtiles"]["generation"], 42)
        self.assertEqual(files["pmtiles"]["size"], 99)
        self.assertEqual(files["pmtiles"]["sha256"], "b" * 64)
        self.assertEqual(
            release["index_status_policy"]["path"],
            None,
        )


if __name__ == "__main__":
    unittest.main()
