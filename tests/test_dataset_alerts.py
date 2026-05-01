from __future__ import annotations

import tempfile
import unittest
import json
import subprocess
from pathlib import Path

from scripts import dataset_alerts


class DatasetAlertsTests(unittest.TestCase):
    def test_schema_diff_detects_added_removed_type_changed_reordered_and_renamed(self):
        old = [
            {"name": "a", "type": "Integer"},
            {"name": "b", "type": "String"},
        ]
        self.assertEqual(dataset_alerts.diff_schemas(old, old), [])

        added = dataset_alerts.diff_schemas(old, old + [{"name": "c", "type": "Real"}])
        self.assertEqual(added[0]["kind"], "added")

        removed = dataset_alerts.diff_schemas(old, [{"name": "a", "type": "Integer"}])
        self.assertEqual(removed[0]["kind"], "removed")

        type_changed = dataset_alerts.diff_schemas(old, [{"name": "a", "type": "Real"}, {"name": "b", "type": "String"}])
        self.assertEqual(type_changed[0]["kind"], "type_changed")

        reordered = dataset_alerts.diff_schemas(old, [{"name": "b", "type": "String"}, {"name": "a", "type": "Integer"}])
        self.assertEqual(reordered[0]["kind"], "reordered")

        renamed = dataset_alerts.diff_schemas(old, [{"name": "renamed_a", "type": "Integer"}, {"name": "b", "type": "String"}])
        self.assertEqual(renamed[0]["kind"], "renamed")

    def test_csv_schema_infers_basic_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset.csv"
            path.write_text("id,value,name\n1,1.5,alpha\n2,2.5,beta\n")

            schema = dataset_alerts.schema_from_csv(path)

        self.assertEqual(
            schema,
            [
                {"name": "id", "type": "Integer"},
                {"name": "value", "type": "Real"},
                {"name": "name", "type": "String"},
            ],
        )

    def test_ogr_schema_falls_back_to_text_output_for_older_gdal(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            if "-json" in command:
                return subprocess.CompletedProcess(
                    command,
                    1,
                    stdout="",
                    stderr="FAILURE: Unknown option name '-json'",
                )
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=(
                    "Layer name: land\n"
                    "Geometry: Multi Polygon\n"
                    "Feature Count: 11\n"
                    "featurecla: String (11.0)\n"
                    "scalerank: Integer (3.0)\n"
                    "min_zoom: Real (5.1)\n"
                ),
                stderr="",
            )

        schema = dataset_alerts.schema_from_ogr(Path("asset.fgb"), runner=runner)

        self.assertEqual(
            schema,
            [
                {"name": "featurecla", "type": "String"},
                {"name": "scalerank", "type": "Integer"},
                {"name": "min_zoom", "type": "Real"},
            ],
        )
        self.assertEqual(len(calls), 2)

    def test_upload_summary_uses_catalog_values(self):
        title, body, fields = dataset_alerts.build_upload_summary(
            asset_slug="asset",
            row={
                "category": "100-geographic-reference",
                "subcategory": "130-protected-areas",
                "canonical_path": "gs://bucket/100/ref/asset/latest/asset.fgb",
                "available_formats": "fgb;pmtiles",
                "source": "source",
                "license": "license",
            },
            changed_paths=["gs://bucket/100/ref/asset/latest/asset.fgb"],
            release_path="gs://bucket/100/ref/asset/releases/2026-04-30/",
            row_count=10,
        )

        self.assertEqual(title, "Dataset upload: asset")
        self.assertIn("100-geographic-reference/130-protected-areas", body)
        self.assertIn("gs://bucket/100/ref/asset", body)
        self.assertEqual(fields["Rows"], "10")
        self.assertEqual(fields["Formats"], "fgb;pmtiles")

    def test_schema_warning_writes_structured_cloud_log(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))

        dataset_alerts.emit_cloud_logging_warning(
            {"alert_type": "dataset_schema_changed", "asset_slug": "asset"},
            runner=runner,
        )

        command, kwargs = calls[0]
        self.assertEqual(command[:3], ["gcloud", "logging", "write"])
        self.assertEqual(command[3], dataset_alerts.SCHEMA_ALERT_LOG_NAME)
        self.assertEqual(json.loads(command[4])["asset_slug"], "asset")
        self.assertEqual(kwargs, {"check": True})


if __name__ == "__main__":
    unittest.main()
