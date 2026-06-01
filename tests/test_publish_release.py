from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from google.api_core.exceptions import NotFound, PreconditionFailed
from typer.testing import CliRunner

from scripts import publish_release
from scripts import release_feature_model
from scripts.gcs_asset import app


CATALOG_HEADER = (
    "asset_slug,title,category,subcategory,status,owner,update_cadence,canonical_path,"
    "canonical_format,available_formats,metadata_paths,has_pmtiles,has_geojson,has_csv,"
    "source,license,citation,notes\n"
)


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
    def __init__(self, name: str = "test-bucket") -> None:
        self.name = name
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

    def bucket(self, _name: str) -> FakeBucket:
        return self._bucket


def write_catalog(tmp_path: Path, *, canonical_format: str = "fgb", available_formats: str = "fgb;pmtiles") -> Path:
    path = tmp_path / "catalog.csv"
    path.write_text(
        CATALOG_HEADER
        + (
            "example-asset,Example Asset,100-geographic-reference,110-boundaries,active,"
            "SkyTruth,manual,gs://test-bucket/100-geographic-reference/110-boundaries/"
            f"example-asset/latest/example-asset.{ 'tif' if canonical_format == 'cog' else canonical_format },"
            f"{canonical_format},{available_formats},README.md,true,false,false,"
            "Example source,Example license,Example citation,Example notes\n"
        )
    )
    return path


def write_artifact(tmp_path: Path, name: str, data: bytes = b"dataset bytes") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def write_vector_bundle(
    tmp_path: Path,
    *,
    asset_slug: str = "example-asset",
    release: str = "2026-05-01",
    bucket_name: str = "test-bucket",
    asset_root: str | None = None,
) -> dict[str, Path]:
    paths = {
        "fgb": write_artifact(tmp_path, f"{asset_slug}.fgb", b"fgb bytes"),
        "pmtiles": write_artifact(tmp_path, f"{asset_slug}.pmtiles", b"pmtiles bytes"),
    }
    feature = release_feature_model.FeatureRecord(
        feature_id="src:id:1",
        feature_hash="sha256:" + "a" * 64,
        geometry=None,
        properties={"name": "A"},
        provenance={"source": "fixture"},
    )
    sidecar = tmp_path / f"{asset_slug}.metadata.ndjson.gz"
    release_feature_model.write_metadata_sidecar(
        [release_feature_model.sidecar_record(asset_slug=asset_slug, release=release, feature=feature)],
        sidecar,
    )
    schema_payload = release_feature_model.build_release_schema(
        asset_slug=asset_slug,
        release=release,
        fields=[release_feature_model.ReleaseSchemaField("name", "String")],
    )
    schema = tmp_path / f"{asset_slug}.schema.json"
    schema.write_text(json.dumps(schema_payload, sort_keys=True) + "\n")
    paths["metadata"] = sidecar
    paths["schema"] = schema
    root = asset_root or f"100-geographic-reference/110-boundaries/{asset_slug}"
    release_base = f"gs://{bucket_name}/{root}/releases/{release}/{asset_slug}"
    artifacts = [
        {"role": "fgb", "path": f"{release_base}.fgb", "sha256": publish_release.sha256_file(paths["fgb"])},
        {"role": "pmtiles", "path": f"{release_base}.pmtiles", "sha256": publish_release.sha256_file(paths["pmtiles"])},
        {"role": "metadata", "path": f"{release_base}.metadata.ndjson.gz", "sha256": publish_release.sha256_file(sidecar)},
        {"role": "schema", "path": f"{release_base}.schema.json", "sha256": publish_release.sha256_file(schema)},
        {"role": "manifest", "path": f"{release_base}.manifest.json"},
    ]
    manifest = tmp_path / f"{asset_slug}.manifest.json"
    manifest.write_text(
        json.dumps(
            release_feature_model.build_release_manifest(
                asset_slug=asset_slug,
                release=release,
                source_inputs=[],
                artifacts=artifacts,
                schema=schema_payload,
                id_strategy={"strategy": "provider", "field": "id"},
                validation={"valid": True, "feature_count": 1},
            ),
            sort_keys=True,
        )
        + "\n"
    )
    paths["manifest"] = manifest
    return paths


def skip_schema_compatibility(**_kwargs):
    return None


class PublishReleaseTests(unittest.TestCase):
    def test_plan_derives_release_and_latest_paths_from_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path)
            publish_dir = tmp_path / "publish"
            publish_dir.mkdir()
            write_vector_bundle(publish_dir)
            bucket = FakeBucket()
            bucket.blob("100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb").exists = True
            bucket.blob("100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb").generation = 7

            plan = publish_release.build_publish_plan(
                asset_slug="example-asset",
                release_date="2026-05-01",
                publish_dir=publish_dir,
                catalog_path=catalog,
                client=FakeClient(bucket),
                schema_reader=lambda _path: [{"name": "id", "type": "Integer"}],
                schema_compatibility_checker=skip_schema_compatibility,
            )

        self.assertEqual(plan.asset_root, "100-geographic-reference/110-boundaries/example-asset")
        self.assertEqual(plan.release_path, "gs://test-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/")
        self.assertEqual([artifact.format for artifact in plan.artifacts], ["fgb", "pmtiles", "metadata", "schema", "manifest"])
        self.assertEqual(
            plan.remote_generations[
                "gs://test-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb"
            ],
            7,
        )

    def test_plan_includes_release_metadata_sidecar_schema_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path, available_formats="fgb;pmtiles")
            publish_dir = tmp_path / "publish"
            publish_dir.mkdir()
            write_vector_bundle(publish_dir)

            plan = publish_release.build_publish_plan(
                asset_slug="example-asset",
                release_date="2026-05-01",
                publish_dir=publish_dir,
                catalog_path=catalog,
                client=FakeClient(FakeBucket()),
                schema_reader=lambda _path: [],
                schema_compatibility_checker=skip_schema_compatibility,
            )

        self.assertEqual(
            [artifact.format for artifact in plan.artifacts],
            ["fgb", "pmtiles", "metadata", "schema", "manifest"],
        )
        sidecar = next(artifact for artifact in plan.artifacts if artifact.format == "metadata")
        self.assertEqual(
            sidecar.release_uri,
            "gs://test-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.metadata.ndjson.gz",
        )
        self.assertEqual(
            sidecar.latest_uri,
            "gs://test-bucket/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.metadata.ndjson.gz",
        )

    def test_companion_formats_must_be_explicitly_allowed_to_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path)
            artifact = write_artifact(tmp_path, "example-asset.fgb")

            with self.assertRaisesRegex(publish_release.PublishReleaseError, "complete feature metadata bundle"):
                publish_release.build_publish_plan(
                    asset_slug="example-asset",
                    release_date="2026-05-01",
                    publish_dir=None,
                    artifact_overrides={"fgb": artifact},
                    catalog_path=catalog,
                    client=FakeClient(FakeBucket()),
                    schema_reader=lambda _path: [],
                    schema_compatibility_checker=skip_schema_compatibility,
                )

    def test_partial_release_blocks_publish_before_latest_uploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path, available_formats="fgb")
            artifacts = write_vector_bundle(tmp_path)
            bucket = FakeBucket()
            bucket.blob("100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb").exists = True

            with self.assertRaisesRegex(publish_release.PublishReleaseError, "release object already exists"):
                publish_release.build_publish_plan(
                    asset_slug="example-asset",
                    release_date="2026-05-01",
                    publish_dir=None,
                    artifact_overrides=artifacts,
                    catalog_path=catalog,
                    client=FakeClient(bucket),
                    schema_reader=lambda _path: [],
                    schema_compatibility_checker=skip_schema_compatibility,
                )

    def test_execute_uploads_release_then_latest_with_generation_and_writes_run_record(self):
        def pass_schema_compatibility(**_kwargs):
            return {"blocked_diffs": [], "warning_diffs": []}

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path, available_formats="fgb")
            artifacts = write_vector_bundle(tmp_path)
            bucket = FakeBucket()
            latest = bucket.blob("100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb")
            latest.exists = True
            latest.generation = 12
            plan = publish_release.build_publish_plan(
                asset_slug="example-asset",
                release_date="2026-05-01",
                publish_dir=None,
                artifact_overrides=artifacts,
                catalog_path=catalog,
                client=FakeClient(bucket),
                schema_reader=lambda _path: [],
                schema_compatibility_checker=pass_schema_compatibility,
            )
            schema_updates = []
            notifications = []

            result = publish_release.execute_publish_plan(
                plan,
                client=FakeClient(bucket),
                source_version="source-v1",
                row_count=3,
                notes="published in test",
                schema_reader=lambda _path: [],
                schema_compatibility_checker=pass_schema_compatibility,
                schema_updater=lambda slug, path: schema_updates.append((slug, path.name)),
                notifier=lambda publish_plan, count: notifications.append((publish_plan.asset_slug, count)),
            )

        release = bucket.blob("100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb")
        release_manifest = bucket.blob("100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.manifest.json")
        run_record = bucket.blob("100-geographic-reference/110-boundaries/example-asset/runs/2026-05-01.json")
        self.assertEqual(release.uploads[0][1], 0)
        self.assertEqual(release_manifest.uploads[0][0], "string")
        self.assertEqual(latest.uploads[0][1], 12)
        self.assertEqual(run_record.uploads[0][1], 0)
        payload = json.loads(run_record.text)
        self.assertEqual(payload["source_version"], "source-v1")
        self.assertEqual(payload["row_count"], 3)
        release_index_blob = bucket.blob("_catalog/releases/example-asset.json")
        self.assertTrue(release_index_blob.exists)
        release_index_payload = json.loads(release_index_blob.text)
        self.assertEqual(release_index_payload["latest_release"]["date"], "2026-05-01")
        self.assertEqual(result.run_record["release_index"]["path"], "gs://test-bucket/_catalog/releases/example-asset.json")
        self.assertEqual(schema_updates, [("example-asset", "example-asset.fgb")])
        self.assertEqual(notifications, [("example-asset", 3)])
        self.assertEqual(result.warnings, ())
        manifest_payload = json.loads(release_manifest.text)
        manifest_entry = next(item for item in manifest_payload["artifacts"] if item["role"] == "manifest")
        self.assertNotIn("generation", manifest_entry)

    def test_metadata_generation_mismatch_blocks_success_run_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path, available_formats="fgb")
            artifacts = write_vector_bundle(tmp_path)
            readme = write_artifact(tmp_path, "README.md", b"# Updated docs\n")
            bucket = FakeBucket()
            latest = bucket.blob("100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb")
            latest.exists = True
            latest.generation = 12
            remote_readme = bucket.blob("100-geographic-reference/110-boundaries/example-asset/README.md")
            remote_readme.exists = True
            remote_readme.generation = 5
            plan = publish_release.build_publish_plan(
                asset_slug="example-asset",
                release_date="2026-05-01",
                publish_dir=None,
                artifact_overrides=artifacts,
                catalog_path=catalog,
                client=FakeClient(bucket),
                readme_path=readme,
                schema_reader=lambda _path: [],
                schema_compatibility_checker=skip_schema_compatibility,
            )
            remote_readme.generation = 6

            with self.assertRaisesRegex(publish_release.PublishReleaseError, "metadata object generation changed"):
                publish_release.execute_publish_plan(
                    plan,
                    client=FakeClient(bucket),
                    schema_updater=lambda _slug, _path: None,
                    notifier=lambda _plan, _count: None,
                )

        release = bucket.blob("100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb")
        run_record = bucket.blob("100-geographic-reference/110-boundaries/example-asset/runs/2026-05-01.json")
        self.assertEqual(release.uploads[0][1], 0)
        self.assertFalse(run_record.exists)
        self.assertEqual(run_record.uploads, [])

    def test_schema_compatibility_blocks_plan_before_uploads(self):
        def fail_schema_compatibility(**_kwargs):
            raise RuntimeError("blocked incompatible schema change")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path, available_formats="fgb")
            artifacts = write_vector_bundle(tmp_path)
            bucket = FakeBucket()

            with self.assertRaisesRegex(publish_release.PublishReleaseError, "schema compatibility check failed"):
                publish_release.build_publish_plan(
                    asset_slug="example-asset",
                    release_date="2026-05-01",
                    publish_dir=None,
                    artifact_overrides=artifacts,
                    catalog_path=catalog,
                    client=FakeClient(bucket),
                    schema_reader=lambda _path: [{"name": "id", "type": "Integer"}],
                    schema_compatibility_checker=fail_schema_compatibility,
                )

        release = bucket.blob("100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb")
        latest = bucket.blob("100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb")
        self.assertEqual(release.uploads, [])
        self.assertEqual(latest.uploads, [])

    def test_schema_compatibility_recheck_blocks_execute_before_uploads(self):
        def pass_schema_compatibility(**_kwargs):
            return {"blocked_diffs": [], "warning_diffs": []}

        def fail_schema_compatibility(**_kwargs):
            raise RuntimeError("snapshot changed before publish")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path, available_formats="fgb")
            artifacts = write_vector_bundle(tmp_path)
            bucket = FakeBucket()
            plan = publish_release.build_publish_plan(
                asset_slug="example-asset",
                release_date="2026-05-01",
                publish_dir=None,
                artifact_overrides=artifacts,
                catalog_path=catalog,
                client=FakeClient(bucket),
                schema_reader=lambda _path: [{"name": "id", "type": "Integer"}],
                schema_compatibility_checker=pass_schema_compatibility,
            )

            with self.assertRaisesRegex(publish_release.PublishReleaseError, "schema compatibility check failed"):
                publish_release.execute_publish_plan(
                    plan,
                    client=FakeClient(bucket),
                    schema_reader=lambda _path: [{"name": "id", "type": "Integer"}],
                    schema_compatibility_checker=fail_schema_compatibility,
                    schema_updater=lambda _slug, _path: None,
                    notifier=lambda _plan, _count: None,
                )

        release = bucket.blob("100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb")
        latest = bucket.blob("100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb")
        self.assertEqual(release.uploads, [])
        self.assertEqual(latest.uploads, [])

    def test_cog_validation_failure_blocks_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path, canonical_format="cog", available_formats="cog")
            artifact = write_artifact(tmp_path, "example-asset.tif")

            with self.assertRaisesRegex(publish_release.PublishReleaseError, "COG validation failed"):
                publish_release.build_publish_plan(
                    asset_slug="example-asset",
                    release_date="2026-05-01",
                    publish_dir=None,
                    artifact_overrides={"cog": artifact},
                    catalog_path=catalog,
                    client=FakeClient(FakeBucket()),
                    cog_validator=lambda _path: SimpleNamespace(valid=False, errors=("not a COG",)),
                )

    def test_cli_dry_run_prints_json_plan_for_explicit_artifact(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            artifacts = write_vector_bundle(
                Path(tmp),
                asset_slug="gfw-fixed-infrastructure",
                bucket_name="skytruth-shared-datasets-1",
                asset_root="300-infrastructure-industrial/330-offshore-platforms/gfw-fixed-infrastructure",
            )
            bucket = FakeBucket("skytruth-shared-datasets-1")
            with (
                mock.patch("scripts.gcs_asset.get_client", return_value=FakeClient(bucket)),
                mock.patch("scripts.publish_release.default_schema_reader", return_value=[]),
                mock.patch("scripts.publish_release.default_schema_compatibility_checker", return_value=None),
            ):
                result = runner.invoke(
                    app,
                    [
                        "publish-release",
                        "--asset-slug",
                        "gfw-fixed-infrastructure",
                        "--release-date",
                        "2026-05-01",
                        "--artifact",
                        f"fgb={artifacts['fgb']}",
                        "--artifact",
                        f"pmtiles={artifacts['pmtiles']}",
                        "--artifact",
                        f"metadata={artifacts['metadata']}",
                        "--artifact",
                        f"schema={artifacts['schema']}",
                        "--artifact",
                        f"manifest={artifacts['manifest']}",
                        "--dry-run",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["asset_slug"], "gfw-fixed-infrastructure")
        self.assertEqual(payload["stale_formats"], [])

    def test_cli_release_index_rebuild_dry_run_reads_remote_runs_and_releases(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path, available_formats="fgb")
            bucket = FakeBucket()
            release = bucket.blob(
                "100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb"
            )
            release.exists = True
            release.generation = 8
            release.size = 13
            run_record = bucket.blob(
                "100-geographic-reference/110-boundaries/example-asset/runs/2026-05-01.json"
            )
            run_record.exists = True
            run_record.text = json.dumps(
                {
                    "asset_slug": "example-asset",
                    "run_date": "2026-05-01",
                    "status": "success",
                    "release_path": "gs://test-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/",
                    "release_paths": [
                        "gs://test-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb"
                    ],
                }
            )
            with mock.patch("scripts.gcs_asset.get_client", return_value=FakeClient(bucket)):
                result = runner.invoke(
                    app,
                    [
                        "release-index",
                        "rebuild",
                        "--asset-slug",
                        "example-asset",
                        "--catalog",
                        str(catalog),
                        "--dry-run",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertTrue(payload["dry_run"])
        self.assertEqual(payload["payload"]["latest_release"]["date"], "2026-05-01")
        self.assertEqual(payload["payload"]["latest_release"]["files"][0]["generation"], 8)
        self.assertFalse(bucket.blob("_catalog/releases/example-asset.json").exists)

    def test_cli_release_index_rebuild_dry_run_reads_releases_without_runs(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            catalog = write_catalog(tmp_path, available_formats="fgb")
            bucket = FakeBucket()
            release = bucket.blob(
                "100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb"
            )
            release.exists = True
            release.generation = 8
            release.size = 13
            with mock.patch("scripts.gcs_asset.get_client", return_value=FakeClient(bucket)):
                result = runner.invoke(
                    app,
                    [
                        "release-index",
                        "rebuild",
                        "--asset-slug",
                        "example-asset",
                        "--catalog",
                        str(catalog),
                        "--dry-run",
                    ],
                )

        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.output)
        self.assertEqual(payload["payload"]["latest_release"]["date"], "2026-05-01")
        self.assertEqual(
            payload["payload"]["latest_release"]["files"][0]["path"],
            "gs://test-bucket/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-01/example-asset.fgb",
        )
        self.assertEqual(payload["payload"]["latest_run"]["status"], "success")
        self.assertFalse(bucket.blob("_catalog/releases/example-asset.json").exists)

    def test_invalid_release_date_is_rejected(self):
        with self.assertRaisesRegex(publish_release.PublishReleaseError, "YYYY-MM-DD"):
            publish_release.parse_release_date("2026-5-1")


if __name__ == "__main__":
    unittest.main()
