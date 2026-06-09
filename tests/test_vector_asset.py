from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_metadata_lookup_flag_enables_release_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.geojson"
            source.write_text('{"type":"FeatureCollection","features":[]}')

            plan = vector_asset.build_plan(
                source=source,
                asset_slug="example-asset",
                metadata_lookup=True,
                allow_repo_output=True,
            )

        self.assertEqual(plan.pmtiles_feature_id_property, vector_asset.FEATURE_ID_COLUMN)
        self.assertEqual(plan.required_fgb_properties, vector_asset.RELEASE_VECTOR_FGB_PROPERTIES)
        self.assertEqual(plan.required_pmtiles_properties, vector_asset.PMTILES_METADATA_COLUMNS)
        self.assertEqual(plan.exact_pmtiles_properties, vector_asset.PMTILES_METADATA_COLUMNS)

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

    def test_validate_metadata_lookup_bundle_uses_exact_pmtiles_properties(self):
        with mock.patch("scripts.vector_asset.validate_outputs") as validate_outputs:
            validate_outputs.return_value = vector_asset.VectorValidationResult(
                fgb_path="example.fgb",
                pmtiles_path="example.pmtiles",
                valid=True,
                errors=(),
            )

            result = vector_asset.validate_metadata_lookup_bundle(
                Path("example.fgb"),
                Path("example.pmtiles"),
            )

        self.assertTrue(result.valid)
        self.assertEqual(validate_outputs.call_args.kwargs["required_fgb_properties"], vector_asset.RELEASE_VECTOR_FGB_PROPERTIES)
        self.assertEqual(validate_outputs.call_args.kwargs["required_pmtiles_properties"], vector_asset.PMTILES_METADATA_COLUMNS)
        self.assertEqual(validate_outputs.call_args.kwargs["exact_pmtiles_properties"], vector_asset.PMTILES_METADATA_COLUMNS)
        self.assertFalse(validate_outputs.call_args.kwargs["validate_geometry"])

    def test_ogrinfo_fgb_summary_falls_back_to_text_output_without_json_support(self):
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if "-json" in command:
                return mock.Mock(
                    returncode=1,
                    stdout="",
                    stderr="FAILURE: Unknown option name '-json'",
                )
            return mock.Mock(
                returncode=0,
                stdout=(
                    "INFO: Open of `example.fgb'\n"
                    "      using driver `FlatGeobuf' successful.\n"
                    "\n"
                    "Layer name: example\n"
                    "Geometry: Unknown (any)\n"
                    "Feature Count: 2\n"
                    "Layer SRS WKT:\n"
                    "(unknown)\n"
                    "  feature_id: String (0.0)\n"
                    "  geometry_hash: String (0.0)\n"
                    "  properties_hash: String (0.0)\n"
                ),
                stderr="",
            )

        with mock.patch("scripts.vector_asset.subprocess.run", side_effect=run):
            summary, errors = vector_asset.ogrinfo_fgb_summary(Path("example.fgb"))

        self.assertEqual(errors, [])
        self.assertEqual(summary["layer_name"], "example")
        self.assertEqual(
            summary["property_keys"],
            (
                vector_asset.FEATURE_ID_COLUMN,
                vector_asset.GEOMETRY_HASH_COLUMN,
                vector_asset.PROPERTIES_HASH_COLUMN,
            ),
        )
        self.assertEqual(
            calls,
            [
                ["ogrinfo", "-ro", "-al", "-so", "-json", "example.fgb"],
                ["ogrinfo", "-ro", "-al", "-so", "example.fgb"],
            ],
        )

    def test_ogrinfo_text_fgb_summary_rejects_empty_layer(self):
        summary, errors = vector_asset.ogrinfo_text_fgb_summary(
            "Layer name: example\n"
            "Feature Count: 0\n"
            "  feature_id: String (0.0)\n"
        )

        self.assertIsNone(summary)
        self.assertEqual(errors, ["FGB layer has no features."])


if __name__ == "__main__":
    unittest.main()
