from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import gcs_asset


CATEGORIES = {
    "100-geographic-reference": {"130-protected-areas"},
    "200-imagery-derived": {"250-weather-climate"},
}


class GcsAssetPathValidationTests(unittest.TestCase):
    def test_catalog_web_content_types_are_inferred(self):
        expected = {
            "catalog.json": "application/json",
            "shared-datasets-catalog.csv": "text/csv",
            "example.metadata.ndjson.gz": "application/x-ndjson",
            "example.metadata.es.ndjson.gz": "application/x-ndjson",
            "index.html": "text/html",
            "styles.css": "text/css",
            "app.js": "application/javascript",
            "README.md": "text/markdown",
        }

        for name, content_type in expected.items():
            with self.subTest(name=name):
                self.assertEqual(gcs_asset.content_type_for(Path(name), None), content_type)

    def test_valid_asset_paths_pass(self):
        paths = [
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/README.md",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/latest/wdpa-terrestrial.fgb",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/latest/wdpa-terrestrial.metadata.ndjson.gz",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/latest/wdpa-terrestrial.metadata.es.ndjson.gz",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/latest/wdpa-terrestrial.schema.json",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/latest/wdpa-terrestrial.manifest.json",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/releases/2026-04-29/wdpa-terrestrial.pmtiles",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/releases/2026-04-29/wdpa-terrestrial.metadata.ndjson.gz",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/releases/2026-04-29/wdpa-terrestrial.metadata.fr.ndjson.gz",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/releases/2026-04-29/wdpa-terrestrial.schema.json",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/releases/2026-04-29/wdpa-terrestrial.manifest.json",
            "200-imagery-derived/250-weather-climate/example/previews/example-preview.png",
            "200-imagery-derived/250-weather-climate/example/runs/2026-04-29.json",
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/index-loads/2026-04-29/load-1.json",
        ]

        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(gcs_asset.validate_asset_object_name(path, CATEGORIES), [])

    def test_root_objects_are_rejected(self):
        errors = gcs_asset.validate_asset_object_name("AGENTS.md", CATEGORIES)

        self.assertIn("root-level bucket objects are noncanonical", errors[0])

    def test_root_readme_is_allowed(self):
        self.assertEqual(gcs_asset.validate_asset_object_name("README.md", CATEGORIES), [])

    def test_unknown_taxonomy_is_rejected(self):
        errors = gcs_asset.validate_asset_object_name(
            "900-new/910-example/example/latest/example.fgb",
            CATEGORIES,
        )

        self.assertIn("unknown top-level prefix", errors[0])

    def test_latest_nested_paths_are_rejected(self):
        errors = gcs_asset.validate_asset_object_name(
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/latest/nested/file.fgb",
            CATEGORIES,
        )

        self.assertTrue(any("latest/ should contain direct files only" in error for error in errors))

    def test_direct_script_invocation_can_import_repo_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            catalog = Path(tmp) / "catalog.csv"
            catalog.write_text(
                "asset_slug,title,category,subcategory,status,owner,update_cadence,canonical_path,"
                "canonical_format,available_formats,metadata_paths,has_pmtiles,has_geojson,has_csv,"
                "source,license,citation,notes\n"
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "gcs_asset.py"),
                    "release-index",
                    "rebuild",
                    "--asset-slug",
                    "missing-asset",
                    "--catalog",
                    str(catalog),
                    "--dry-run",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("asset slug is not in the catalog", result.stderr)
        self.assertNotIn("ModuleNotFoundError", result.stderr)

    def test_copy_can_set_destination_metadata_with_generation_precondition(self):
        class FakeBlob:
            def __init__(self, name: str) -> None:
                self.name = name
                self.generation = 10
                self.size = 42
                self.content_type = None
                self.cache_control = None
                self.patch_calls = []

            def patch(self, *, if_generation_match=None) -> None:
                self.patch_calls.append(if_generation_match)

        class FakeBucket:
            def __init__(self) -> None:
                self.blobs = {}
                self.copy_kwargs = None

            def blob(self, name: str) -> FakeBlob:
                if name not in self.blobs:
                    self.blobs[name] = FakeBlob(name)
                return self.blobs[name]

            def copy_blob(self, src_blob, dst_bucket, *, new_name: str, **kwargs):
                self.copy_kwargs = kwargs
                copied = dst_bucket.blob(new_name)
                copied.content_type = src_blob.content_type
                copied.cache_control = src_blob.cache_control
                return copied

        class FakeClient:
            def __init__(self, bucket: FakeBucket) -> None:
                self.bucket_obj = bucket

            def bucket(self, _name: str) -> FakeBucket:
                return self.bucket_obj

        bucket = FakeBucket()
        with (
            mock.patch("scripts.gcs_asset.get_client", return_value=FakeClient(bucket)),
            mock.patch.dict(gcs_asset.os.environ, {gcs_asset.ALLOW_CANONICAL_MUTATION_ENV: "1"}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            gcs_asset.copy_object(
                "gs://test-bucket/source.json",
                "gs://test-bucket/dest.json",
                unsafe_overwrite=False,
                source_generation=5,
                replace_generation=9,
                content_type="application/json",
                cache_control="no-cache, max-age=0, must-revalidate",
            )

        self.assertEqual(bucket.copy_kwargs["if_source_generation_match"], 5)
        self.assertEqual(bucket.copy_kwargs["if_generation_match"], 9)
        copied = bucket.blob("dest.json")
        self.assertEqual(copied.content_type, "application/json")
        self.assertEqual(copied.cache_control, "no-cache, max-age=0, must-revalidate")
        self.assertEqual(copied.patch_calls, [10])

    def test_non_scratch_mutation_requires_explicit_publisher_runtime(self):
        with self.assertRaisesRegex(Exception, gcs_asset.ALLOW_CANONICAL_MUTATION_ENV):
            gcs_asset.require_mutation_allowed(
                "gs://test-bucket/100-geographic-reference/130-protected-areas/example/latest/example.fgb",
                operation="upload",
            )

    def test_scratch_mutation_is_allowed_without_publisher_runtime(self):
        gcs_asset.require_mutation_allowed(
            "gs://test-bucket/_scratch/pending-publishes/example/pr-1/example.fgb",
            operation="upload",
        )

    def test_unsafe_overwrite_is_rejected_outside_scratch_even_with_publisher_runtime(self):
        with mock.patch.dict(gcs_asset.os.environ, {gcs_asset.ALLOW_CANONICAL_MUTATION_ENV: "1"}):
            with self.assertRaisesRegex(Exception, "only allowed for _scratch"):
                gcs_asset.require_mutation_allowed(
                    "gs://test-bucket/100-geographic-reference/130-protected-areas/example/latest/example.fgb",
                    operation="upload",
                    unsafe_overwrite=True,
                )


if __name__ == "__main__":
    unittest.main()
