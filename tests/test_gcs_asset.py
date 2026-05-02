from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
            "100-geographic-reference/130-protected-areas/wdpa-terrestrial/releases/2026-04-29/wdpa-terrestrial.pmtiles",
            "200-imagery-derived/250-weather-climate/example/previews/example-preview.png",
            "200-imagery-derived/250-weather-climate/example/runs/2026-04-29.json",
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
                "last_updated,source,license,notes\n"
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


if __name__ == "__main__":
    unittest.main()
