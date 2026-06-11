from __future__ import annotations

import contextlib
import io
import json
import os
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

    def test_breaking_changes_from_schema_results_include_blocked_and_reordered_diffs(self):
        old = [
            {"name": "a", "type": "Integer"},
            {"name": "b", "type": "String"},
        ]
        waived = dataset_alerts.check_schema_compatibility(
            asset_slug="asset",
            dataset_path=Path("asset.fgb"),
            fields=[{"name": "a", "type": "Integer"}],
            compatibility_waiver={
                "asset_slug": "asset",
                "blocked_changes": [{"kind": "removed", "field": "b"}],
                "rationale": "Source removed a retired field and reviewer approved the contract break.",
                "consumer_impact": "Known consumers use field a only.",
                "reviewer": "jonaraphael",
                "pr_reference": "https://github.com/SkyTruth/shared-datasets-1/pull/123",
                "migration_path": "Pin the prior dated release if b is required.",
            },
            snapshot_loader=lambda _uri: ({"fields": old}, 10),
        )
        reordered = dataset_alerts.check_schema_compatibility(
            asset_slug="asset",
            dataset_path=Path("asset.fgb"),
            fields=[old[1], old[0]],
            snapshot_loader=lambda _uri: ({"fields": old}, 10),
        )

        changes = dataset_alerts.breaking_changes_from_schema_results([waived, reordered])

        summaries = [change["summary"] for change in changes]
        self.assertIn("Schema field removed: `b`.", summaries)
        self.assertIn("Schema field order changed.", summaries)
        self.assertTrue(any("Pin the prior dated release" in change["consumer_action"] for change in changes))

    def test_breaking_alert_payload_is_brief_slug_scoped_and_fingerprinted(self):
        changes = [
            {
                "category": "feature_identity",
                "summary": "feature_id changed from source IDs to generated IDs.",
                "consumer_action": "Refresh joins that use feature_id before reading latest.",
                "affected_surfaces": ["latest/asset.fgb", "latest/asset.metadata.ndjson.gz"],
            },
            {
                "category": "schema",
                "summary": "Schema field removed: `old_name`.",
                "consumer_action": "Pin the prior release if old_name is required.",
                "affected_surfaces": ["latest canonical schema", "old_name"],
            },
        ]

        alert = dataset_alerts.build_breaking_alert(
            asset_slug="asset",
            changes=changes,
            phase="planned",
            row={"title": "Asset Title"},
            pr_number="123",
            pr_url="https://github.com/SkyTruth/shared-datasets-1/pull/123",
        )
        reordered = dataset_alerts.build_breaking_alert(
            asset_slug="asset",
            changes=list(reversed(changes)),
            phase="planned",
            row={"title": "Asset Title"},
            pr_number="123",
            pr_url="https://github.com/SkyTruth/shared-datasets-1/pull/123",
        )

        self.assertEqual(alert["title"], "BREAKING planned: asset latest contract")
        self.assertIn("*Asset:* Asset Title (`asset`)", alert["body"])
        self.assertIn("*Status:* Planned in PR #123", alert["body"])
        self.assertIn("ignore if you do not consume `asset@latest`", alert["body"])
        self.assertNotIn("@channel", alert["body"])
        self.assertNotIn("@here", alert["body"])
        self.assertEqual(alert["fingerprint"], reordered["fingerprint"])

        changed = dataset_alerts.build_breaking_alert(
            asset_slug="asset",
            changes=[{**changes[0], "summary": "Different feature_id policy."}],
            phase="planned",
        )
        self.assertNotEqual(alert["fingerprint"], changed["fingerprint"])

    def test_collect_breaking_changes_returns_empty_for_non_breaking_publish(self):
        changes = dataset_alerts.collect_breaking_changes(
            plan={
                "asset_slug": "asset",
                "proposal_id": "pr-123",
                "promotions": [
                    {
                        "source_uri": "gs://bucket/_scratch/pending-publishes/asset/pr-123/asset.fgb",
                        "destination_uri": "gs://bucket/100/ref/asset/latest/asset.fgb",
                    }
                ],
            },
            plan_type="publish",
        )

        self.assertEqual(changes, [])
        self.assertIsNone(dataset_alerts.build_breaking_alert(asset_slug="asset", changes=changes, phase="live"))

    def test_delete_plan_latest_target_is_breaking_change(self):
        changes = dataset_alerts.collect_breaking_changes(
            plan={
                "asset_slug": "asset",
                "proposal_id": "pr-123",
                "deletions": [
                    {
                        "uri": "gs://bucket/100/ref/asset/latest/asset.fgb",
                        "generation": "123",
                        "reason": "Remove invalid latest object after replacement.",
                    }
                ],
            },
            plan_type="delete",
        )

        self.assertEqual(changes[0]["category"], "lifecycle_delete")
        self.assertIn("latest", changes[0]["summary"])

    def test_catalog_contract_changes_are_breaking_when_surfaces_are_removed_or_restricted(self):
        current_row = {
            "canonical_path": "gs://bucket/100/ref/asset/latest/asset.fgb",
            "canonical_format": "fgb",
            "available_formats": "fgb;pmtiles",
            "access_tier": "public",
            "has_pmtiles": "true",
            "metadata_paths": (
                "README.md;latest/asset.metadata.ndjson.gz;"
                "latest/asset.schema.json;latest/asset.manifest.json"
            ),
        }
        proposed_row = {
            "canonical_path": "gs://bucket/100/ref/asset/latest/asset.csv",
            "canonical_format": "csv",
            "available_formats": "csv",
            "access_tier": "private",
            "has_pmtiles": "false",
            "metadata_paths": "README.md",
        }

        changes = dataset_alerts.collect_breaking_changes(
            plan={"asset_slug": "asset", "proposal_id": "pr-123", "promotions": []},
            plan_type="publish",
            current_row=current_row,
            proposed_row=proposed_row,
        )

        categories = {change["category"] for change in changes}
        summaries = "\n".join(change["summary"] for change in changes)
        self.assertEqual(
            categories,
            {"access", "artifact_set", "format", "metadata_sidecar", "path", "pmtiles_lookup"},
        )
        self.assertIn("Catalog canonical path changed", summaries)
        self.assertIn("Catalog canonical format changed", summaries)
        self.assertIn("Available formats removed", summaries)
        self.assertIn("Access tier changed", summaries)
        self.assertIn("PMTiles availability changed", summaries)
        self.assertIn("Metadata Sidecar removed", summaries)
        self.assertIn("Release Manifest removed", summaries)
        self.assertIn("Schema Sidecar removed", summaries)

    def test_catalog_contract_additions_do_not_create_breaking_alerts(self):
        current_row = {
            "canonical_path": "gs://bucket/100/ref/asset/latest/asset.fgb",
            "canonical_format": "fgb",
            "available_formats": "fgb",
            "access_tier": "private",
            "has_pmtiles": "false",
            "metadata_paths": "README.md",
        }
        proposed_row = {
            **current_row,
            "available_formats": "fgb;pmtiles",
            "access_tier": "public",
            "has_pmtiles": "true",
            "metadata_paths": "README.md;latest/asset.metadata.ndjson.gz",
        }

        changes = dataset_alerts.collect_breaking_changes(
            plan={"asset_slug": "asset", "proposal_id": "pr-123", "promotions": []},
            plan_type="publish",
            current_row=current_row,
            proposed_row=proposed_row,
        )

        self.assertEqual(changes, [])

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

    def test_upload_summary_defaults_to_updated_dataset_title(self):
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

        self.assertEqual(title, "Dataset updated")
        self.assertIn("Source: source.", body)
        self.assertIn("*Rows:* `10`", body)
        self.assertIn("`id`, `name`, `status`, `source`, `updated`, +1 more", body)
        self.assertIn("gs://bucket/100/ref/asset", body)
        self.assertEqual(fields, {})

    def test_upload_summary_new_dataset_title_is_explicit(self):
        title, _body, _fields = dataset_alerts.build_upload_summary(
            asset_slug="asset",
            row={"canonical_path": "gs://bucket/100/ref/asset/latest/asset.fgb"},
            changed_paths=["gs://bucket/100/ref/asset/latest/asset.fgb"],
            new_dataset=True,
        )

        self.assertEqual(title, "New dataset added!")
        self.assertEqual(dataset_alerts.upload_summary_status(new_dataset=True), "new")
        self.assertEqual(dataset_alerts.upload_summary_status(new_dataset=False), "success")

    def test_row_count_from_asset_doc_reads_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs_dir = Path(tmp)
            (docs_dir / "asset.md").write_text("---\nrow_count: 304572\n---\n\n# Asset\n")

            self.assertEqual(dataset_alerts.row_count_from_asset_doc("asset", docs_dir=docs_dir), 304572)

    def test_upload_summary_falls_back_to_asset_doc_row_count(self):
        row = {
            "canonical_path": "gs://bucket/100/ref/asset/latest/asset.fgb",
            "source": "source",
        }
        with (
            mock.patch.object(dataset_alerts, "load_catalog", return_value={"asset": row}),
            mock.patch.object(dataset_alerts, "row_count_from_asset_doc", return_value=42),
            mock.patch.object(dataset_alerts, "notify") as notify,
        ):
            dataset_alerts.upload_summary(
                asset_slug="asset",
                changed_path=["gs://bucket/100/ref/asset/latest/asset.fgb"],
                release_path=None,
                row_count=None,
                dataset_path=None,
                sample_column=[],
                dry_run=True,
            )

        self.assertIn("*Rows:* `42`", notify.call_args.kwargs["body"])
        self.assertEqual(notify.call_args.kwargs["title"], "Dataset updated")
        self.assertEqual(notify.call_args.kwargs["status"], "success")
        self.assertTrue(notify.call_args.kwargs["dry_run"])

    def test_upload_summary_new_dataset_flag_sets_new_status(self):
        row = {
            "canonical_path": "gs://bucket/100/ref/asset/latest/asset.fgb",
            "source": "source",
        }
        with (
            mock.patch.object(dataset_alerts, "load_catalog", return_value={"asset": row}),
            mock.patch.object(dataset_alerts, "row_count_from_asset_doc", return_value=42),
            mock.patch.object(dataset_alerts, "notify") as notify,
        ):
            dataset_alerts.upload_summary(
                asset_slug="asset",
                changed_path=["gs://bucket/100/ref/asset/latest/asset.fgb"],
                release_path=None,
                row_count=None,
                dataset_path=None,
                sample_column=[],
                new_dataset=True,
                dry_run=True,
            )

        self.assertEqual(notify.call_args.kwargs["title"], "New dataset added!")
        self.assertEqual(notify.call_args.kwargs["status"], "new")

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
                mock.patch.dict(os.environ, {dataset_alerts.ALLOW_CANONICAL_MUTATION_ENV: "1"}),
            ):
                dataset_alerts.check_schema(
                    asset_slug="asset",
                    dataset_path=path,
                    snapshot_uri="gs://bucket/_catalog/schema-snapshots/asset.json",
                    dry_run=False,
                    upload_snapshot=True,
                    skip_snapshot_upload=False,
                )

        write_snapshot.assert_called_once()

    def test_check_schema_default_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "asset.csv"
            path.write_text("id,name,added\n1,alpha,new\n")

            stderr = io.StringIO()
            stdout = io.StringIO()
            with (
                mock.patch.object(
                    dataset_alerts,
                    "load_snapshot",
                    return_value=({"fields": [{"name": "id", "type": "Integer"}]}, 12),
                ),
                mock.patch.object(dataset_alerts, "write_snapshot") as write_snapshot,
                contextlib.redirect_stderr(stderr),
                contextlib.redirect_stdout(stdout),
            ):
                dataset_alerts.check_schema(
                    asset_slug="asset",
                    dataset_path=path,
                    snapshot_uri="gs://bucket/_catalog/schema-snapshots/asset.json",
                    dry_run=False,
                    skip_snapshot_upload=False,
                )

        write_snapshot.assert_not_called()
        self.assertIn("schema snapshot upload skipped", stderr.getvalue())
        self.assertIn('"alert_type": "dataset_schema_changed"', stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
