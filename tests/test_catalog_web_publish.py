from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import catalog_web_publish


class FakeBlob:
    def __init__(self, generation=None) -> None:
        self.generation = generation
        self.size = 12
        self.content_type = "application/json"
        self.cache_control = None
        self.uploads = []

    def reload(self):
        if self.generation is None:
            from google.api_core.exceptions import NotFound

            raise NotFound("missing")

    def upload_from_filename(self, source, *, content_type, **kwargs):
        self.uploads.append((source, content_type, kwargs, self.cache_control))
        self.content_type = content_type
        self.generation = 456


class CatalogWebPublishTests(unittest.TestCase):
    def test_generated_files_require_root_bundle_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "index.html").write_text("")

            with self.assertRaisesRegex(catalog_web_publish.CatalogWebPublishError, "missing required"):
                catalog_web_publish.generated_files(root)

    def test_publish_file_replaces_existing_generation_with_no_cache(self):
        blob = FakeBlob(generation=123)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "catalog.json"
            source.write_text("{}")

            with mock.patch("scripts.catalog_web_publish.gcs_asset.require_mutation_allowed"), mock.patch(
                "scripts.catalog_web_publish.gcs_asset.get_blob",
                return_value=blob,
            ):
                result = catalog_web_publish.publish_file(
                    source,
                    "gs://skytruth-shared-datasets-1/_catalog/web/catalog.json",
                    cache_control="no-cache, max-age=0, must-revalidate",
                    dry_run=False,
                )

        self.assertEqual(blob.uploads[0][2]["if_generation_match"], 123)
        self.assertEqual(blob.uploads[0][3], "no-cache, max-age=0, must-revalidate")
        self.assertEqual(result["generation"], 456)

    def test_publish_file_uses_no_clobber_precondition_for_new_object(self):
        blob = FakeBlob(generation=None)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "app.js"
            source.write_text("console.log('ok');")

            with mock.patch("scripts.catalog_web_publish.gcs_asset.require_mutation_allowed"), mock.patch(
                "scripts.catalog_web_publish.gcs_asset.get_blob",
                return_value=blob,
            ):
                catalog_web_publish.publish_file(
                    source,
                    "gs://skytruth-shared-datasets-1/_catalog/web/app.js",
                    cache_control="no-cache, max-age=0, must-revalidate",
                    dry_run=False,
                )

        self.assertEqual(blob.uploads[0][2]["if_generation_match"], 0)


if __name__ == "__main__":
    unittest.main()
