from __future__ import annotations

import json
import os
import pathlib
import unittest
from unittest import mock

from temp_workspace import workspace

from scripts import catalog_csv, publish_workflow


BUCKET = "skytruth-shared-datasets-1"
CANONICAL_PATH = f"gs://{BUCKET}/industry/mining/demo-asset/latest/demo-asset.fgb"
CATALOG_CSV = (
    "asset_slug,title,canonical_path\n"
    f"demo-asset,Demo asset,{CANONICAL_PATH}\n"
)


def make_promotion(**overrides) -> dict:
    promotion = {
        "source_uri": f"gs://{BUCKET}/_scratch/pending-publishes/demo-asset/demo-asset.fgb",
        "source_generation": "111",
        "destination_uri": CANONICAL_PATH,
        "destination_generation": "222",
        "content_type": "application/octet-stream",
        "cache_control": "",
    }
    promotion.update(overrides)
    return promotion


class IsSchemaTargetTests(unittest.TestCase):
    row = {"asset_slug": "demo-asset", "canonical_path": CANONICAL_PATH}

    def check(self, destination_uri: str, *, row=None, asset_slug: str = "demo-asset") -> bool:
        return publish_workflow.is_schema_target(asset_slug, destination_uri, bucket=BUCKET, row=row)

    def test_rejects_other_buckets_formats_and_prefixes(self):
        with self.subTest("other bucket"):
            self.assertFalse(self.check("gs://other-bucket/a/latest/demo-asset.fgb", row=self.row))
        with self.subTest("non schema format"):
            self.assertFalse(
                self.check(f"gs://{BUCKET}/industry/mining/demo-asset/latest/demo-asset.pmtiles", row=self.row)
            )
        with self.subTest("outside latest/releases"):
            self.assertFalse(
                self.check(f"gs://{BUCKET}/industry/mining/demo-asset/source/demo-asset.fgb", row=self.row)
            )

    def test_catalog_row_pins_canonical_path_and_release_copies(self):
        self.assertTrue(self.check(CANONICAL_PATH, row=self.row))
        self.assertTrue(
            self.check(
                f"gs://{BUCKET}/industry/mining/demo-asset/releases/2026-07-01/demo-asset.fgb",
                row=self.row,
            )
        )
        with self.subTest("release copy with a different filename is not the schema contract"):
            self.assertFalse(
                self.check(
                    f"gs://{BUCKET}/industry/mining/demo-asset/releases/2026-07-01/other.fgb",
                    row=self.row,
                )
            )
        with self.subTest("release copy under another asset root"):
            self.assertFalse(
                self.check(
                    f"gs://{BUCKET}/industry/mining/other-asset/releases/2026-07-01/demo-asset.fgb",
                    row=self.row,
                )
            )

    def test_without_catalog_row_falls_back_to_slug_stem(self):
        self.assertTrue(self.check(f"gs://{BUCKET}/a/latest/demo-asset.csv", row=None))
        self.assertFalse(self.check(f"gs://{BUCKET}/a/latest/other.csv", row=None))


class ReleasePathTests(unittest.TestCase):
    def test_returns_first_release_directory(self):
        destinations = [
            CANONICAL_PATH,
            f"gs://{BUCKET}/industry/mining/demo-asset/releases/2026-07-01/demo-asset.fgb",
            f"gs://{BUCKET}/industry/mining/demo-asset/releases/2026-07-01/demo-asset.pmtiles",
        ]
        self.assertEqual(
            publish_workflow.release_path_for(destinations),
            f"gs://{BUCKET}/industry/mining/demo-asset/releases/2026-07-01/",
        )

    def test_returns_empty_without_release_destinations(self):
        self.assertEqual(publish_workflow.release_path_for([CANONICAL_PATH]), "")


class CheckApprovedReviewTests(unittest.TestCase):
    def run_command(self, reviews: list[dict]) -> int:
        with workspace({"reviews.json": json.dumps(reviews)}):
            return publish_workflow.main(["check-approved-review", "--reviews-json", "reviews.json"])

    def test_accepts_approved_review_from_required_reviewer(self):
        reviews = [{"state": "APPROVED", "user": {"login": "jonaraphael"}}]
        self.assertEqual(self.run_command(reviews), 0)

    def test_rejects_missing_or_wrong_reviewer(self):
        with self.subTest("no reviews"):
            self.assertEqual(self.run_command([]), 1)
        with self.subTest("approved by someone else"):
            self.assertEqual(self.run_command([{"state": "APPROVED", "user": {"login": "mallory"}}]), 1)
        with self.subTest("required reviewer only commented"):
            self.assertEqual(self.run_command([{"state": "COMMENTED", "user": {"login": "jonaraphael"}}]), 1)


class FakeBlob:
    def __init__(self, text: str = "", *, content_type: str = "", cache_control: str = "", generation: int = 0):
        self.text = text
        self.content_type = content_type
        self.cache_control = cache_control
        self.generation = generation

    def reload(self):
        pass

    def download_as_text(self) -> str:
        return self.text


class FakeBucket:
    def __init__(self, blobs: dict):
        self.blobs = blobs

    def blob(self, name: str, generation: int | None = None) -> FakeBlob:
        return self.blobs[(name, int(generation))]


class FakeClient:
    def __init__(self, blobs: dict):
        self.blobs = blobs

    def bucket(self, name: str) -> FakeBucket:
        return FakeBucket(self.blobs)


class PromoteTests(unittest.TestCase):
    """Pin command_promote's canonical-mutation safety wiring."""

    CATALOG_JSON_URI = f"gs://{BUCKET}/_catalog/web/catalog.json"
    CATALOG_JSON_SOURCE = f"gs://{BUCKET}/_scratch/pending-publishes/demo-asset/catalog.json"

    def run_promote(self, promotions: list[dict]) -> list[list[str]]:
        plan = {"asset_slug": "demo-asset", "promotions": promotions}
        with (
            workspace({"publish-plan.json": json.dumps(plan)}),
            mock.patch.dict(os.environ, {"SHARED_DATASETS_BUCKET": BUCKET}),
            mock.patch.object(publish_workflow, "catalog_row", return_value=None),
            mock.patch.object(publish_workflow.subprocess, "run") as run,
        ):
            exit_code = publish_workflow.main(["promote", "--plan-json", "publish-plan.json"])
        self.assertEqual(exit_code, 0)
        return [list(call.args[0]) for call in run.call_args_list]

    def run_catalog_json_promote(self, *, source_text: str, destination_text: str) -> list[list[str]]:
        blobs = {
            ("_scratch/pending-publishes/demo-asset/catalog.json", 111): FakeBlob(source_text),
            ("_catalog/web/catalog.json", 7): FakeBlob(destination_text),
        }
        destination = FakeBlob(content_type="application/json", cache_control="", generation=7)
        promotion = make_promotion(
            source_uri=self.CATALOG_JSON_SOURCE,
            destination_uri=self.CATALOG_JSON_URI,
            destination_generation="7",
            content_type="application/json",
        )
        with (
            mock.patch("scripts.gcs_asset.get_client", return_value=FakeClient(blobs)),
            mock.patch("scripts.gcs_asset.get_blob", return_value=destination),
        ):
            return self.run_promote([promotion])

    def test_replace_generation_only_for_existing_destinations(self):
        latest = f"gs://{BUCKET}/industry/mining/demo-asset/latest/demo-asset.pmtiles"
        release = f"gs://{BUCKET}/industry/mining/demo-asset/releases/2026-07-01/demo-asset.pmtiles"
        calls = self.run_promote(
            [
                make_promotion(
                    destination_uri=latest,
                    destination_generation="222",
                    content_type="application/vnd.pmtiles",
                    cache_control="public, max-age=60",
                ),
                make_promotion(destination_uri=release, destination_generation="", content_type=""),
            ]
        )

        commands = [(call[4], call[5:]) for call in calls]
        self.assertEqual([name for name, _ in commands], ["stat", "copy", "stat", "stat", "copy", "stat"])

        replace_copy = commands[1][1]
        self.assertEqual(replace_copy[:2], [make_promotion()["source_uri"], latest])
        self.assertEqual(replace_copy[2:6], ["--source-generation", "111", "--replace-generation", "222"])
        self.assertEqual(replace_copy[6:], ["--content-type", "application/vnd.pmtiles", "--cache-control", "public, max-age=60"])

        no_clobber_copy = commands[4][1]
        self.assertNotIn("--replace-generation", no_clobber_copy)
        self.assertEqual(no_clobber_copy[2:], ["--source-generation", "111"])

        self.assertFalse(any("scripts/dataset_alerts.py" in part for call in calls for part in call))

    def test_catalog_json_skips_copy_when_destination_already_matches(self):
        calls = self.run_catalog_json_promote(
            source_text=json.dumps({"generated_at": "2026-07-02T00:00:00Z", "assets": [1]}),
            destination_text=json.dumps({"generated_at": "2020-01-01T00:00:00Z", "assets": [1]}),
        )
        self.assertEqual([call[4] for call in calls], ["stat"])

    def test_catalog_json_copies_when_payload_differs(self):
        calls = self.run_catalog_json_promote(
            source_text=json.dumps({"generated_at": "2026-07-02T00:00:00Z", "assets": [1]}),
            destination_text=json.dumps({"generated_at": "2020-01-01T00:00:00Z", "assets": [2]}),
        )
        self.assertEqual([call[4] for call in calls], ["stat", "copy", "stat"])
        self.assertIn("--replace-generation", calls[1])

    def test_catalog_json_copies_when_source_is_malformed(self):
        calls = self.run_catalog_json_promote(
            source_text="not json",
            destination_text=json.dumps({"generated_at": "2020-01-01T00:00:00Z", "assets": [1]}),
        )
        self.assertEqual([call[4] for call in calls], ["stat", "copy", "stat"])


class DetectSchemaTargetsTests(unittest.TestCase):
    def test_reports_replacement_schema_targets_only(self):
        plan = {
            "asset_slug": "demo-asset",
            "promotions": [
                make_promotion(),
                make_promotion(destination_generation=""),
                make_promotion(
                    destination_uri=f"gs://{BUCKET}/industry/mining/demo-asset/latest/demo-asset.pmtiles"
                ),
            ],
        }
        files = {
            "publish-plan.json": json.dumps(plan),
            "catalog/shared-datasets-catalog.csv": CATALOG_CSV,
        }
        with workspace(files), mock.patch.dict(os.environ, {"SHARED_DATASETS_BUCKET": BUCKET}):
            exit_code = publish_workflow.main(
                ["detect-schema-targets", "--plan-json", "publish-plan.json", "--github-output", "out.txt"]
            )
            output = pathlib.Path("out.txt").read_text()
        self.assertEqual(exit_code, 0)
        self.assertIn("has_schema_targets=true\n", output)
        self.assertIn("schema_target_count=1\n", output)


class CheckSchemaCompatibilityTests(unittest.TestCase):
    def test_skips_new_and_non_schema_destinations_without_subprocess_calls(self):
        plan = {
            "asset_slug": "demo-asset",
            "promotions": [
                make_promotion(destination_generation=""),
                make_promotion(
                    destination_uri=f"gs://{BUCKET}/industry/mining/demo-asset/latest/demo-asset.pmtiles"
                ),
            ],
        }
        files = {
            "publish-plan.json": json.dumps(plan),
            "catalog/shared-datasets-catalog.csv": CATALOG_CSV,
        }
        with (
            workspace(files),
            mock.patch.dict(os.environ, {"SHARED_DATASETS_BUCKET": BUCKET}),
            mock.patch.object(publish_workflow.subprocess, "run") as run,
        ):
            exit_code = publish_workflow.main(
                ["check-schema-compatibility", "--plan-json", "publish-plan.json", "--phase", "live"]
            )
            self.assertTrue(pathlib.Path("schema-results").is_dir())
        self.assertEqual(exit_code, 0)
        run.assert_not_called()


class RebuildReleaseIndexTests(unittest.TestCase):
    def run_command(self, plan: dict, catalog_csv: str = CATALOG_CSV):
        files = {
            "publish-plan.json": json.dumps(plan),
            "catalog/shared-datasets-catalog.csv": catalog_csv,
        }
        with (
            workspace(files),
            mock.patch.object(publish_workflow.subprocess, "run") as run,
        ):
            exit_code = publish_workflow.main(["rebuild-release-index", "--plan-json", "publish-plan.json"])
        return exit_code, run

    def test_skips_when_plan_asset_is_not_a_catalog_asset(self):
        exit_code, run = self.run_command({"asset_slug": "not-in-catalog", "promotions": []})
        self.assertEqual(exit_code, 0)
        run.assert_not_called()

    def test_rejects_unknown_requested_rebuild_assets(self):
        plan = {
            "asset_slug": "demo-asset",
            "promotions": [],
            "release_index_asset_slugs": ["not-in-catalog"],
        }
        with self.assertRaises(RuntimeError):
            self.run_command(plan)

    def test_rebuilds_deduplicated_catalog_assets(self):
        catalog_csv = CATALOG_CSV + f"other-asset,Other,gs://{BUCKET}/a/latest/other-asset.fgb\n"
        plan = {
            "asset_slug": "demo-asset",
            "promotions": [],
            "release_index_asset_slugs": ["demo-asset", "other-asset"],
        }
        exit_code, run = self.run_command(plan, catalog_csv)
        self.assertEqual(exit_code, 0)
        rebuilt = [call.args[0][-1] for call in run.call_args_list]
        self.assertEqual(rebuilt, ["demo-asset", "other-asset"])


class LiveBreakingAlertTests(unittest.TestCase):
    ENV = {
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": "SkyTruth/shared-datasets-1",
        "GITHUB_RUN_ID": "42",
        "PR_NUMBER": "117",
        "PR_URL": "https://github.com/SkyTruth/shared-datasets-1/pull/117",
        "SHARED_DATASETS_BUCKET": BUCKET,
    }

    def run_command(self, *, has_breaking_changes: bool, comment_already_sent: bool = False):
        plan = {"asset_slug": "demo-asset", "promotions": [make_promotion()]}
        summary = {
            "has_breaking_changes": has_breaking_changes,
            "marker": "shared-datasets-breaking-alert:demo-asset:publish:live",
            "asset_slug": "demo-asset",
            "plan_type": "publish",
        }
        calls = []

        def fake_run(args, check=False, **kwargs):
            calls.append(list(args))
            if "--dry-run" in args:
                pathlib.Path("publish-breaking-alert.json").write_text(json.dumps(summary))
            return mock.Mock(returncode=0)

        files = {
            "publish-plan.json": json.dumps(plan),
            "current-catalog-row.json": "{}",
        }
        with (
            workspace(files),
            mock.patch.dict(os.environ, self.ENV),
            mock.patch.object(publish_workflow.subprocess, "run", side_effect=fake_run),
            mock.patch.object(publish_workflow, "pr_comment_has_marker", return_value=comment_already_sent),
            mock.patch.object(publish_workflow, "post_pr_comment") as post_comment,
        ):
            exit_code = publish_workflow.main(
                [
                    "live-breaking-alert",
                    "--plan-type",
                    "publish",
                    "--plan-json",
                    "publish-plan.json",
                    "--summary-json",
                    "publish-breaking-alert.json",
                ]
            )
        return exit_code, calls, post_comment

    def test_dry_run_only_when_no_breaking_changes(self):
        exit_code, calls, post_comment = self.run_command(has_breaking_changes=False)
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)
        self.assertIn("--dry-run", calls[0])
        post_comment.assert_not_called()

    def test_live_send_arguments_stay_paired(self):
        exit_code, calls, post_comment = self.run_command(has_breaking_changes=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 2)
        send_args = calls[1]
        self.assertNotIn("--dry-run", send_args)
        self.assertNotIn("--summary-json", send_args)
        # Regression: the previous inline filter dropped the summary filename
        # before deleting the --summary-json pair, which swallowed --pr-number
        # and left its value as an orphan positional argument.
        self.assertIn("--pr-number", send_args)
        self.assertEqual(send_args[send_args.index("--pr-number") + 1], "117")
        self.assertIn("--current-catalog-row-json", send_args)
        post_comment.assert_called_once()
        self.assertIn("Live breaking change Slack alert sent", post_comment.call_args.args[1])

    def test_skips_slack_when_marker_comment_exists(self):
        exit_code, calls, post_comment = self.run_command(has_breaking_changes=True, comment_already_sent=True)
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(calls), 1)
        post_comment.assert_not_called()


class UploadSummaryTests(unittest.TestCase):
    def run_command(self, plan: dict, catalog_csv: str = CATALOG_CSV):
        calls = []

        def fake_run(args, check=False, **kwargs):
            calls.append(list(args))
            return mock.Mock(returncode=0)

        files = {
            "publish-plan.json": json.dumps(plan),
            "catalog/shared-datasets-catalog.csv": catalog_csv,
        }
        with (
            workspace(files),
            mock.patch.dict(os.environ, {"SHARED_DATASETS_BUCKET": BUCKET}),
            mock.patch.object(publish_workflow.subprocess, "run", side_effect=fake_run),
        ):
            exit_code = publish_workflow.main(["upload-summary", "--plan-json", "publish-plan.json"])
        return exit_code, calls

    def test_skips_non_catalog_assets(self):
        plan = {"asset_slug": "not-in-catalog", "promotions": []}
        exit_code, calls = self.run_command(plan)
        self.assertEqual(exit_code, 0)
        self.assertEqual(calls, [])

    def test_new_dataset_flag_for_unconditioned_canonical_promotion(self):
        plan = {
            "asset_slug": "demo-asset",
            "promotions": [make_promotion(destination_generation="")],
        }
        exit_code, calls = self.run_command(plan)
        self.assertEqual(exit_code, 0)
        download_call, summary_call = calls
        self.assertIn("download", download_call)
        self.assertIn("--new-dataset", summary_call)
        self.assertIn("--dataset-path", summary_call)
        self.assertIn("--changed-path", summary_call)

    def test_replacement_without_schema_target_sends_summary_without_dataset(self):
        plan = {
            "asset_slug": "demo-asset",
            "promotions": [
                make_promotion(
                    destination_uri=f"gs://{BUCKET}/industry/mining/demo-asset/latest/demo-asset.pmtiles"
                )
            ],
        }
        exit_code, calls = self.run_command(plan)
        self.assertEqual(exit_code, 0)
        (summary_call,) = calls
        self.assertNotIn("--new-dataset", summary_call)
        self.assertNotIn("--dataset-path", summary_call)


class DeleteVerificationTests(unittest.TestCase):
    def run_delete(self, exists_returncode: int) -> tuple[int, list[list[str]]]:
        calls = []

        def fake_run(args, check=False, **kwargs):
            calls.append(list(args))
            returncode = exists_returncode if "exists" in args else 0
            return mock.Mock(returncode=returncode)

        with mock.patch.object(publish_workflow.subprocess, "run", side_effect=fake_run):
            exit_code = publish_workflow.delete_object_with_verification(
                "gs://bucket/object",
                "123",
                exists_label="deleted object still exists",
                verify_label="could not verify deleted object absence",
            )
        return exit_code, calls

    def test_absent_object_succeeds(self):
        exit_code, calls = self.run_delete(exists_returncode=1)
        self.assertEqual(exit_code, 0)
        self.assertIn("delete", calls[0])
        self.assertIn("--confirm", calls[0])
        self.assertIn("exists", calls[1])

    def test_still_present_object_fails(self):
        exit_code, _ = self.run_delete(exists_returncode=0)
        self.assertEqual(exit_code, 1)

    def test_unverifiable_absence_propagates_exit_code(self):
        exit_code, _ = self.run_delete(exists_returncode=3)
        self.assertEqual(exit_code, 3)

    def test_delete_canonical_objects_stats_before_delete(self):
        plan = {"deletions": [{"uri": "gs://bucket/object", "generation": "123"}]}
        calls = []

        def fake_run(args, check=False, **kwargs):
            calls.append(list(args))
            returncode = 1 if "exists" in args else 0
            return mock.Mock(returncode=returncode)

        with (
            workspace({"delete-plan.json": json.dumps(plan)}),
            mock.patch.object(publish_workflow.subprocess, "run", side_effect=fake_run),
        ):
            exit_code = publish_workflow.main(
                ["delete-canonical-objects", "--plan-json", "delete-plan.json"]
            )
        self.assertEqual(exit_code, 0)
        self.assertIn("stat", calls[0])
        self.assertIn("delete", calls[1])
        self.assertIn("exists", calls[2])

    def test_delete_scratch_sources_deduplicates_shared_sources(self):
        plan = {
            "promotions": [
                make_promotion(),
                make_promotion(destination_uri=f"gs://{BUCKET}/a/releases/2026-07-01/demo-asset.fgb"),
            ]
        }
        calls = []

        def fake_run(args, check=False, **kwargs):
            calls.append(list(args))
            returncode = 1 if "exists" in args else 0
            return mock.Mock(returncode=returncode)

        with (
            workspace({"publish-plan.json": json.dumps(plan)}),
            mock.patch.object(publish_workflow.subprocess, "run", side_effect=fake_run),
        ):
            exit_code = publish_workflow.main(
                ["delete-scratch-sources", "--plan-json", "publish-plan.json"]
            )
        self.assertEqual(exit_code, 0)
        delete_calls = [call for call in calls if "delete" in call]
        self.assertEqual(len(delete_calls), 1)


class CollectCatalogRowTests(unittest.TestCase):
    def test_catalog_row_from_text(self):
        row = catalog_csv.catalog_row_from_text(CATALOG_CSV, "demo-asset")
        self.assertEqual(row["canonical_path"], CANONICAL_PATH)
        self.assertIsNone(catalog_csv.catalog_row_from_text(CATALOG_CSV, "missing"))
        with self.assertRaises(catalog_csv.CatalogCsvError):
            catalog_csv.catalog_row_from_text("", "demo-asset")

    def test_collect_catalog_rows_writes_current_and_proposed(self):
        plan = {"asset_slug": "demo-asset"}
        event = {
            "pull_request": {
                "merge_commit_sha": "merged-sha",
                "head": {"sha": "head-sha"},
                "base": {"sha": "base-sha"},
            }
        }
        files = {
            "publish-plan.json": json.dumps(plan),
            "event.json": json.dumps(event),
        }
        with (
            workspace(files),
            mock.patch.dict(os.environ, {"GITHUB_REPOSITORY": "SkyTruth/shared-datasets-1"}),
            mock.patch.object(publish_workflow, "first_parent", return_value="parent-sha") as parent,
            mock.patch.object(publish_workflow, "remote_catalog_text", return_value=CATALOG_CSV) as remote,
        ):
            exit_code = publish_workflow.main(["collect-catalog-rows", "--event-path", "event.json"])
            current = json.loads(pathlib.Path("current-catalog-row.json").read_text())
            proposed = json.loads(pathlib.Path("proposed-catalog-row.json").read_text())
        self.assertEqual(exit_code, 0)
        parent.assert_called_once_with("merged-sha")
        self.assertEqual([call.args[0] for call in remote.call_args_list], ["parent-sha", "merged-sha"])
        self.assertEqual(current["asset_slug"], "demo-asset")
        self.assertEqual(proposed["asset_slug"], "demo-asset")

    def test_collect_catalog_rows_without_plan_fails_loudly(self):
        with (
            workspace({"event.json": "{}"}),
            mock.patch.object(publish_workflow, "remote_catalog_text") as remote,
            self.assertRaises(RuntimeError),
        ):
            publish_workflow.main(["collect-catalog-rows", "--event-path", "event.json"])
        remote.assert_not_called()


if __name__ == "__main__":
    unittest.main()
