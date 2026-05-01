from __future__ import annotations

import unittest

from scripts import gcs_asset


CATEGORIES = {
    "100-geographic-reference": {"130-protected-areas"},
    "200-imagery-derived": {"250-weather-climate"},
}


class GcsAssetPathValidationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
