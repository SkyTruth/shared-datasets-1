from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from scripts import vector_asset


class VectorAssetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_skip = os.environ.get("SHARED_DATASETS_SKIP_TOOL_VERSION_PROBES")
        os.environ["SHARED_DATASETS_SKIP_TOOL_VERSION_PROBES"] = "1"

    def tearDown(self) -> None:
        if self._old_skip is None:
            os.environ.pop("SHARED_DATASETS_SKIP_TOOL_VERSION_PROBES", None)
        else:
            os.environ["SHARED_DATASETS_SKIP_TOOL_VERSION_PROBES"] = self._old_skip

    def test_metadata_lookup_pmtiles_project_only_feature_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}')

            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                pmtiles_feature_id_property=vector_asset.FEATURE_ID_COLUMN,
                allow_repo_output=True,
            )

        self.assertEqual(plan.required_pmtiles_properties, (vector_asset.FEATURE_ID_COLUMN,))
        self.assertEqual(plan.exact_pmtiles_properties, (vector_asset.FEATURE_ID_COLUMN,))
        self.assertIn(vector_asset.GEOMETRY_HASH_COLUMN, plan.required_fgb_properties)
        self.assertIn(vector_asset.PROPERTIES_HASH_COLUMN, plan.required_fgb_properties)
        self.assertTrue(all("shared_dataset" not in " ".join(command.argv) for command in plan.commands))

    def test_metadata_lookup_requires_feature_id_property_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}')

            with self.assertRaisesRegex(ValueError, "feature_id"):
                vector_asset.build_plan(
                    source=source,
                    asset_slug="example-asset",
                    pmtiles_feature_id_property="source_id",
                    allow_repo_output=True,
                )

    def test_pmtiles_include_properties_uses_feature_id_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}')
            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                pmtiles_feature_id_property=vector_asset.FEATURE_ID_COLUMN,
                allow_repo_output=True,
            )

        self.assertEqual(vector_asset.pmtiles_include_properties(plan), (vector_asset.FEATURE_ID_COLUMN,))


if __name__ == "__main__":
    unittest.main()
