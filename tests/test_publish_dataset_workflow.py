from __future__ import annotations

import unittest
from pathlib import Path


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/publish-dataset.yml"


class PublishDatasetWorkflowTests(unittest.TestCase):
    def test_schema_snapshot_update_runs_after_approved_promotion_copy(self):
        workflow = WORKFLOW.read_text()
        step = workflow.split("name: Promote approved staged objects", 1)[1].split(
            "name: Delete approved canonical objects",
            1,
        )[0]

        copy_index = step.index('"scripts/gcs_asset.py",\n                  "copy"')
        stat_index = step.index('"stat", promotion["destination_uri"]')
        download_index = step.index('"download",\n                          promotion["source_uri"]')
        check_schema_index = step.index('"check-schema",')

        self.assertLess(copy_index, stat_index)
        self.assertLess(stat_index, download_index)
        self.assertLess(download_index, check_schema_index)

    def test_manual_promotion_updates_schema_snapshot_after_copy(self):
        workflow = WORKFLOW.read_text()
        step = workflow.split("name: Promote staged object manually", 1)[1].split(
            "reviewed_pr_plans:",
            1,
        )[0]

        copy_index = step.index('uv run python scripts/gcs_asset.py "${copy_args[@]}"')
        stat_index = step.index('uv run python scripts/gcs_asset.py stat "${DESTINATION_URI}"')
        download_index = step.index('"download",\n                      source_uri', stat_index)
        check_schema_index = step.index('"check-schema",', stat_index)

        self.assertLess(copy_index, stat_index)
        self.assertLess(stat_index, download_index)
        self.assertLess(download_index, check_schema_index)

    def test_schema_target_detection_uses_catalog_canonical_filename(self):
        workflow = WORKFLOW.read_text()

        self.assertIn("filename == canonical_filename", workflow)
        self.assertNotIn('f"{asset_slug}{suffix}"', workflow)
        self.assertNotIn('f"{slug}{suffix}"', workflow)

    def test_workflow_dispatch_can_apply_pr_plan_for_jonaraphael_only(self):
        workflow = WORKFLOW.read_text()

        self.assertIn("pr_number:", workflow)
        self.assertIn("github.actor == 'jonaraphael'", workflow)
        self.assertIn("github.event.inputs.pr_number != ''", workflow)
        self.assertIn("github.event.inputs.pr_number == ''", workflow)
        self.assertIn('gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}"', workflow)
        self.assertIn("scripts/reviewed_dataset_plan.py event-from-pr", workflow)
        self.assertIn("--event-path \"${{ steps.pr_event.outputs.event_path }}\"", workflow)


if __name__ == "__main__":
    unittest.main()
