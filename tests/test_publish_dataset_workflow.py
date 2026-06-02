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
        self.assertIn("Approved dataset mutation dispatch must run from main", workflow)
        self.assertIn('gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}"', workflow)
        self.assertIn("scripts/reviewed_dataset_plan.py event-from-pr", workflow)
        self.assertIn("--allow-merged", workflow)
        self.assertIn("--event-path \"${{ steps.pr_event.outputs.event_path }}\"", workflow)

    def test_single_object_fallback_dispatch_is_removed(self):
        workflow = WORKFLOW.read_text()

        self.assertNotIn("Single-object fallback", workflow)
        self.assertNotIn("Promote staged object manually", workflow)
        self.assertNotIn("github.event.inputs.pr_number == ''", workflow)

    def test_merged_self_authored_pr_triggers_reviewed_plan_path(self):
        workflow = WORKFLOW.read_text()

        self.assertIn("pull_request:", workflow)
        self.assertIn("types: [closed]", workflow)
        self.assertIn("github.event.pull_request.merged == true", workflow)
        self.assertIn('PR_AUTHOR: ${{ github.event.pull_request.user.login }}', workflow)
        self.assertIn('if [[ "${PR_AUTHOR}" == "jonaraphael" ]]; then', workflow)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", workflow)
        self.assertIn("github.event.pull_request.base.ref == github.event.repository.default_branch", workflow)

    def test_review_approval_no_longer_mutates_before_merge(self):
        workflow = WORKFLOW.read_text()

        self.assertNotIn("pull_request_review:", workflow)
        self.assertNotIn("github.event_name == 'pull_request_review'", workflow)
        self.assertIn("Validate merged PR acceptance", workflow)
        self.assertIn('pulls/${PR_NUMBER}/reviews?per_page=100', workflow)
        self.assertIn("Merged PR does not have an APPROVED review from jonaraphael", workflow)

    def test_publisher_auth_config_fails_loudly_instead_of_skipping(self):
        workflow = WORKFLOW.read_text()

        self.assertIn("Validate publisher auth configuration", workflow)
        self.assertIn("Missing repository variable: GCP_WORKLOAD_IDENTITY_PROVIDER", workflow)
        self.assertNotIn("vars.GCP_WORKLOAD_IDENTITY_PROVIDER != ''", workflow)

    def test_dataset_upload_summary_runs_after_approved_promotions_before_deletes(self):
        workflow = WORKFLOW.read_text()
        apply_step = workflow.split("apply-approved-pr-plans:", 1)[1]

        promote_index = apply_step.index("name: Promote approved staged objects")
        release_index_index = apply_step.index("name: Rebuild promoted release index")
        summary_index = apply_step.index("name: Send dataset upload summary")
        scratch_cleanup_index = apply_step.index("name: Delete promoted scratch source objects")
        delete_index = apply_step.index("name: Delete approved canonical objects")

        self.assertLess(promote_index, summary_index)
        self.assertLess(promote_index, release_index_index)
        self.assertLess(release_index_index, summary_index)
        self.assertLess(summary_index, scratch_cleanup_index)
        self.assertLess(scratch_cleanup_index, delete_index)
        self.assertIn("scripts/dataset_alerts.py", apply_step[summary_index:delete_index])
        self.assertIn("upload-summary", apply_step[summary_index:delete_index])
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", apply_step[summary_index:delete_index])

    def test_approved_promotion_rebuilds_release_index_for_catalog_assets(self):
        workflow = WORKFLOW.read_text()
        step = workflow.split("name: Rebuild promoted release index", 1)[1].split(
            "name: Send dataset upload summary",
            1,
        )[0]

        self.assertIn("catalog/shared-datasets-catalog.csv", step)
        self.assertIn("asset_exists", step)
        self.assertIn('"release-index"', step)
        self.assertIn('"rebuild"', step)
        self.assertIn('"--asset-slug"', step)
        self.assertIn("Skipping release-index rebuild", step)

    def test_promotion_deletes_reviewed_scratch_sources_after_success(self):
        workflow = WORKFLOW.read_text()
        step = workflow.split("name: Delete promoted scratch source objects", 1)[1].split(
            "name: Delete approved canonical objects",
            1,
        )[0]

        self.assertIn('promotion["source_uri"]', step)
        self.assertIn('promotion["source_generation"]', step)
        self.assertIn('"delete"', step)
        self.assertIn('"--confirm"', step)
        self.assertIn('"DELETE"', step)

    def test_catalog_json_publish_plan_is_idempotent_after_catalog_web_deploy(self):
        workflow = WORKFLOW.read_text()
        step = workflow.split("name: Promote approved staged objects", 1)[1].split(
            "name: Delete approved canonical objects",
            1,
        )[0]

        self.assertIn("catalog_json_destination_already_current", step)
        self.assertIn("_catalog/web/catalog.json", step)
        self.assertIn("generated_at", step)
        self.assertIn("destination_blob.content_type", step)
        self.assertIn("destination_blob.cache_control", step)
        self.assertIn("cache-control metadata", step)

        stat_index = step.index('"stat", promotion["source_uri"]')
        skip_index = step.index("if catalog_json_destination_already_current(promotion):")
        copy_index = step.index('"scripts/gcs_asset.py",\n                  "copy"')

        self.assertLess(stat_index, skip_index)
        self.assertLess(skip_index, copy_index)


if __name__ == "__main__":
    unittest.main()
