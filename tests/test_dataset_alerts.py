from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_schema_compatibility_allows_additions_and_warns_on_reorder(self):
        old = [
            {"name": "a", "type": "Integer"},
            {"name": "b", "type": "String"},
        ]
        added = dataset_alerts.check_schema_compatibility(
            asset_slug="asset",
            dataset_path=Path("asset.fgb"),
            fields=old + [{"name": "c", "type": "Real"}],
            snapshot_loader=lambda _uri: ({"fields": old}, 10),
        )
        reordered = dataset_alerts.check_schema_compatibility(
            asset_slug="asset",
            dataset_path=Path("asset.fgb"),
            fields=[old[1], old[0]],
            snapshot_loader=lambda _uri: ({"fields": old}, 10),
        )

        self.assertEqual(added.blocked_diffs, [])
        self.assertEqual(added.warning_diffs, [])
        self.assertEqual(reordered.blocked_diffs, [])
        self.assertEqual(reordered.warning_diffs[0]["kind"], "reordered")

    def test_schema_compatibility_blocks_removed_renamed_and_type_changed(self):
        old = [
            {"name": "a", "type": "Integer"},
            {"name": "b", "type": "String"},
        ]
        cases = [
            [{"name": "a", "type": "Integer"}],
            [{"name": "renamed_a", "type": "Integer"}, {"name": "b", "type": "String"}],
            [{"name": "a", "type": "Real"}, {"name": "b", "type": "String"}],
        ]

        for fields in cases:
            with self.subTest(fields=fields):
                with self.assertRaisesRegex(dataset_alerts.SchemaCompatibilityError, "blocked incompatible"):
                    dataset_alerts.check_schema_compatibility(
                        asset_slug="asset",
                        dataset_path=Path("asset.fgb"),
                        fields=fields,
                        snapshot_loader=lambda _uri: ({"fields": old}, 10),
                    )

    def test_schema_compatibility_accepts_exact_reviewed_waiver(self):
        old = [
            {"name": "a", "type": "Integer"},
            {"name": "b", "type": "String"},
        ]
        waiver = {
            "asset_slug": "asset",
            "blocked_changes": [{"kind": "removed", "field": "b"}],
            "rationale": "Source removed a retired field and reviewer approved the contract break.",
            "consumer_impact": "Known consumers use field a only.",
            "reviewer": "jonaraphael",
            "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/123",
            "migration_path": "Consumers that need b should pin the prior dated release.",
        }

        result = dataset_alerts.check_schema_compatibility(
            asset_slug="asset",
            dataset_path=Path("asset.fgb"),
            fields=[{"name": "a", "type": "Integer"}],
            compatibility_waiver=waiver,
            snapshot_loader=lambda _uri: ({"fields": old}, 10),
        )

        self.assertEqual(result.blocked_diffs[0]["kind"], "removed")
        self.assertEqual(result.waiver["reviewer"], "jonaraphael")

    def test_schema_compatibility_rejects_incomplete_waiver(self):
        old = [
            {"name": "a", "type": "Integer"},
            {"name": "b", "type": "String"},
        ]

        with self.assertRaisesRegex(dataset_alerts.SchemaCompatibilityError, "consumer_impact"):
            dataset_alerts.check_schema_compatibility(
                asset_slug="asset",
                dataset_path=Path("asset.fgb"),
                fields=[{"name": "a", "type": "Integer"}],
                compatibility_waiver={
                    "asset_slug": "asset",
                    "blocked_changes": [{"kind": "removed", "field": "b"}],
                    "rationale": "Approved break.",
                    "reviewer": "jonaraphael",
                    "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/123",
                    "migration_path": "Pin the prior release.",
                },
                snapshot_loader=lambda _uri: ({"fields": old}, 10),
            )

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

    def test_ogr_schema_requires_json_output_support(self):
        calls = []

        def runner(command, **kwargs):
            calls.append(command)
            return subprocess.CompletedProcess(
                command,
                1,
                stdout="",
                stderr="FAILURE: Unknown option name '-json'",
            )

        with self.assertRaises(subprocess.CalledProcessError):
            dataset_alerts.schema_from_ogr(Path("asset.fgb"), runner=runner)

        self.assertEqual(calls, [["ogrinfo", "-ro", "-al", "-so", "-json", "asset.fgb"]])

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
            sample_columns=["id", "name", "status", "source", "updated", "notes"],
        )

        self.assertEqual(title, "New dataset added!")
        self.assertIn("Source: source.", body)
        self.assertIn("*Rows:* `10`", body)
        self.assertIn("`id`, `name`, `status`, `source`, `updated`, +1 more", body)
        self.assertIn("gs://bucket/100/ref/asset", body)
        self.assertEqual(fields, {})

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

    def test_schema_warning_logging_failure_is_nonfatal(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            raise subprocess.CalledProcessError(1, command)

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            dataset_alerts.emit_cloud_logging_warning(
                {"alert_type": "dataset_schema_changed", "asset_slug": "asset"},
                runner=runner,
            )

        self.assertEqual(len(calls), 1)
        self.assertIn("continuing schema snapshot update", stderr.getvalue())

    def test_check_schema_updates_snapshot_when_warning_logging_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset.csv"
            path.write_text("id,name,added\n1,alpha,new\n")

            original_emit = dataset_alerts.emit_cloud_logging_warning

            def failing_emit(payload, **kwargs):
                def runner(command, **runner_kwargs):
                    raise subprocess.CalledProcessError(1, command)

                return original_emit(payload, runner=runner, **kwargs)

            with (
                mock.patch.object(
                    dataset_alerts,
                    "load_snapshot",
                    return_value=({"fields": [{"name": "id", "type": "Integer"}]}, 12),
                ),
                mock.patch.object(dataset_alerts, "write_snapshot") as write_snapshot,
                mock.patch.object(dataset_alerts, "emit_cloud_logging_warning", side_effect=failing_emit),
            ):
                dataset_alerts.check_schema(
                    asset_slug="asset",
                    dataset_path=path,
                    snapshot_uri="gs://bucket/_catalog/schema-snapshots/asset.json",
                    dry_run=False,
                    skip_snapshot_upload=False,
                )

        write_snapshot.assert_called_once()


if __name__ == "__main__":
    unittest.main()
