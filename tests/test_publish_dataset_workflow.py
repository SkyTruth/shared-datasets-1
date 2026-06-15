from __future__ import annotations

import unittest
from pathlib import Path

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/publish-dataset.yml"
BREAKING_ALERT_WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/dataset-breaking-change-alert.yml"


def step_names(workflow: dict, job_name: str) -> list[str]:
    return [step["name"] for step in workflow["jobs"][job_name]["steps"] if "name" in step]


def assert_ordered(testcase: unittest.TestCase, names: list[str], *expected_order: str) -> None:
    positions = [names.index(name) for name in expected_order]
    testcase.assertEqual(positions, sorted(positions))


class PublishDatasetWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow(WORKFLOW)
        self.trigger = workflow_triggers(self.workflow)
        self.reviewed_steps = workflow_steps_by_name(self.workflow, "reviewed_pr_plans")
        self.apply_steps = workflow_steps_by_name(self.workflow, "apply-approved-pr-plans")

    def test_triggers_only_from_merged_same_repo_prs_or_restricted_dispatch(self):
        reviewed_job = self.workflow["jobs"]["reviewed_pr_plans"]
        reviewed_if = reviewed_job["if"]

        self.assertEqual(self.trigger["pull_request"], {"branches": ["main"], "types": ["closed"]})
        self.assertEqual(
            self.trigger["workflow_dispatch"]["inputs"]["pr_number"],
            {
                "description": "Same-repo PR number containing a publish/delete plan. Restricted to jonaraphael.",
                "required": False,
                "type": "string",
            },
        )
        self.assertNotIn("pull_request_review", self.trigger)
        self.assertIn("github.event.pull_request.merged == true", reviewed_if)
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", reviewed_if)
        self.assertIn("github.event.pull_request.base.ref == github.event.repository.default_branch", reviewed_if)
        self.assertIn("github.actor == 'jonaraphael'", reviewed_if)
        self.assertIn("github.event.inputs.pr_number != ''", reviewed_if)

        prepare_run = self.reviewed_steps["Prepare PR event payload"]["run"]
        acceptance_run = self.reviewed_steps["Validate merged PR acceptance"]["run"]
        self.assertIn(
            "Approved dataset mutation dispatch must run from main",
            self.reviewed_steps["Validate main ref"]["run"],
        )
        self.assertIn('gh api "repos/${GITHUB_REPOSITORY}/pulls/${PR_NUMBER}"', prepare_run)
        self.assertIn("scripts/reviewed_dataset_plan.py event-from-pr", prepare_run)
        self.assertIn("--allow-merged", prepare_run)
        self.assertIn('if [[ "${PR_AUTHOR}" == "jonaraphael" ]]; then', acceptance_run)
        self.assertIn('pulls/${PR_NUMBER}/reviews?per_page=100', acceptance_run)
        self.assertIn("Merged PR does not have an APPROVED review from jonaraphael", acceptance_run)

    def test_apply_job_is_protected_and_has_no_single_object_fallback(self):
        apply_job = self.workflow["jobs"]["apply-approved-pr-plans"]
        all_apply_runs = "\n".join(str(step.get("run", "")) for step in apply_job["steps"])

        self.assertEqual(apply_job["needs"], "reviewed_pr_plans")
        self.assertEqual(apply_job["environment"], "shared-datasets-production")
        self.assertEqual(apply_job["concurrency"]["cancel-in-progress"], False)
        self.assertEqual(self.workflow["permissions"]["issues"], "write")
        self.assertIn("has_publish_plan == 'true'", apply_job["if"])
        self.assertIn("has_delete_plan == 'true'", apply_job["if"])
        self.assertEqual(
            self.workflow["env"]["PUBLISHER_SERVICE_ACCOUNT"],
            "shared-datasets-publisher@shared-datasets-1.iam.gserviceaccount.com",
        )
        self.assertEqual(self.workflow["env"]["SHARED_DATASETS_ALLOW_CANONICAL_MUTATION"], "1")
        self.assertIn(
            "Missing repository variable: GCP_WORKLOAD_IDENTITY_PROVIDER",
            self.apply_steps["Validate publisher auth configuration"]["run"],
        )
        self.assertNotIn("Single-object fallback", all_apply_runs)
        self.assertNotIn("Promote staged object manually", all_apply_runs)
        self.assertNotIn("github.event.inputs.pr_number == ''", all_apply_runs)

    def test_publish_plan_promotes_then_rebuilds_summarizes_and_cleans_up(self):
        names = step_names(self.workflow, "apply-approved-pr-plans")
        promote_run = self.apply_steps["Promote approved staged objects"]["run"]
        compatibility_run = self.apply_steps["Check approved schema compatibility"]["run"]
        release_index_run = self.apply_steps["Rebuild promoted release index"]["run"]
        finalize_run = self.apply_steps["Finalize promoted release metadata"]["run"]
        breaking_run = self.apply_steps["Send breaking change alert"]["run"]
        summary_run = self.apply_steps["Send dataset upload summary"]["run"]
        cleanup_run = self.apply_steps["Delete promoted scratch source objects"]["run"]

        assert_ordered(
            self,
            names,
            "Check approved schema compatibility",
            "Promote approved staged objects",
            "Finalize promoted release metadata",
            "Rebuild promoted release index",
            "Send breaking change alert",
            "Send dataset upload summary",
            "Delete promoted scratch source objects",
            "Delete approved canonical objects",
        )

        self.assertIn("filename == canonical_filename", compatibility_run + promote_run + summary_run)
        self.assertIn("schema-results", compatibility_run + breaking_run)
        self.assertIn("breaking-alert", breaking_run)
        self.assertIn("shared-datasets-breaking-alert", breaking_run)
        self.assertIn("current-catalog-row.json", breaking_run)
        self.assertIn("proposed-catalog-row.json", breaking_run)
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", self.apply_steps["Send breaking change alert"]["env"])
        self.assertIn('not promotion.get("destination_generation")', compatibility_run)
        self.assertIn("Skipping schema compatibility check for new destination", compatibility_run)
        self.assertNotIn('f"{asset_slug}{suffix}"', compatibility_run + promote_run + summary_run)
        self.assertNotIn('f"{slug}{suffix}"', compatibility_run + promote_run + summary_run)
        self.assertLess(promote_run.index('"stat", promotion["source_uri"]'), promote_run.index('"copy",'))
        self.assertLess(promote_run.index('"copy",'), promote_run.index('"stat", promotion["destination_uri"]'))
        self.assertLess(promote_run.index('"stat", promotion["destination_uri"]'), promote_run.index('"download",'))
        self.assertLess(promote_run.index('"download",'), promote_run.index('"check-schema",'))
        self.assertIn('"--upload-snapshot"', promote_run)

        self.assertIn("scripts/finalize_promoted_release_metadata.py", finalize_run)
        self.assertIn("--publish-plan publish-plan.json", finalize_run)
        self.assertIn("--output finalized-release-metadata.json", finalize_run)
        self.assertIn("catalog/shared-datasets-catalog.csv", release_index_run)
        self.assertIn("release_index_asset_slugs", release_index_run)
        self.assertIn("requested release-index rebuild asset is not in catalog", release_index_run)
        self.assertIn("No catalog asset release indexes requested for rebuild", release_index_run)
        self.assertIn('"release-index"', release_index_run)
        self.assertIn('"rebuild"', release_index_run)
        self.assertIn("scripts/dataset_alerts.py", summary_run)
        self.assertIn("upload-summary", summary_run)
        self.assertIn("not a catalog asset", summary_run)
        self.assertIn("canonical_promotion", summary_run)
        self.assertIn('canonical_promotion.get("destination_generation", "")', summary_run)
        self.assertIn('"--new-dataset"', summary_run)
        self.assertLess(
            summary_run.index("not a catalog asset"),
            summary_run.index("scripts/dataset_alerts.py"),
        )
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", self.apply_steps["Send dataset upload summary"]["env"])
        self.assertIn('promotion["source_generation"]', cleanup_run)
        self.assertIn('"delete"', cleanup_run)
        self.assertIn('"--confirm"', cleanup_run)

    def test_delete_plan_sends_live_breaking_alert_after_delete(self):
        names = step_names(self.workflow, "apply-approved-pr-plans")
        delete_alert_run = self.apply_steps["Send deletion breaking change alert"]["run"]

        assert_ordered(
            self,
            names,
            "Delete approved canonical objects",
            "Send deletion breaking change alert",
        )
        self.assertIn("breaking-alert", delete_alert_run)
        self.assertIn("--plan-type", delete_alert_run)
        self.assertIn("delete", delete_alert_run)
        self.assertIn("current-catalog-row.json", delete_alert_run)
        self.assertIn("proposed-catalog-row.json", delete_alert_run)
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", self.apply_steps["Send deletion breaking change alert"]["env"])

    def test_catalog_json_publish_plan_can_skip_when_destination_already_matches(self):
        promote_run = self.apply_steps["Promote approved staged objects"]["run"]

        self.assertIn("catalog_json_destination_already_current", promote_run)
        self.assertIn("_catalog/web/catalog.json", promote_run)
        self.assertIn("generated_at", promote_run)
        self.assertIn("destination_blob.content_type", promote_run)
        self.assertIn("destination_blob.cache_control", promote_run)
        self.assertIn("cache-control metadata", promote_run)
        self.assertLess(
            promote_run.index('"stat", promotion["source_uri"]'),
            promote_run.index("if catalog_json_destination_already_current(promotion):"),
        )
        self.assertLess(
            promote_run.index("if catalog_json_destination_already_current(promotion):"),
            promote_run.index('"copy",'),
        )

    def test_planned_breaking_change_workflow_is_scoped_and_read_only(self):
        workflow = load_workflow(BREAKING_ALERT_WORKFLOW)
        trigger = workflow_triggers(workflow)
        steps = workflow_steps_by_name(workflow, "alert")
        all_runs = "\n".join(str(step.get("run", "")) for step in workflow["jobs"]["alert"]["steps"])

        self.assertEqual(
            trigger["pull_request"],
            {
                "branches": ["main"],
                "types": ["opened", "edited", "synchronize", "reopened", "ready_for_review"],
            },
        )
        job_if = workflow["jobs"]["alert"]["if"]
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", job_if)
        self.assertIn("github.event.pull_request.draft == false", job_if)
        self.assertEqual(workflow["permissions"]["id-token"], "write")
        self.assertEqual(workflow["permissions"]["issues"], "write")
        self.assertEqual(steps["Check out trusted repository code"]["with"]["ref"], "main")
        self.assertIn(
            "contents/catalog/shared-datasets-catalog.csv",
            steps["Collect proposed catalog row from PR"]["run"],
        )
        self.assertIn("has_schema_targets", steps["Detect planned schema compatibility targets"]["run"])
        self.assertIn("schema_target_count", steps["Detect planned schema compatibility targets"]["run"])
        self.assertIn(
            "steps.schema_targets.outputs.has_schema_targets == 'true'",
            steps["Validate read-only GCP auth configuration"]["if"],
        )
        self.assertIn(
            "steps.schema_targets.outputs.has_schema_targets == 'true'",
            steps["Authenticate read-only to Google Cloud"]["if"],
        )
        self.assertIn(
            "steps.schema_targets.outputs.has_schema_targets == 'true'",
            steps["Collect planned schema compatibility results"]["if"],
        )
        self.assertIn("GCP_READONLY_WORKLOAD_IDENTITY_PROVIDER", steps["Validate read-only GCP auth configuration"]["run"])
        self.assertIn("vars.GCP_READONLY_SERVICE_ACCOUNT", str(steps["Authenticate read-only to Google Cloud"]["with"]))
        self.assertIn("check-schema-compatibility", steps["Collect planned schema compatibility results"]["run"])
        self.assertIn("breaking-alert", steps["Send planned breaking alerts"]["run"])
        self.assertIn("shared-datasets-breaking-alert", steps["Send planned breaking alerts"]["run"])
        self.assertIn("proposed-catalog-row.json", steps["Send planned breaking alerts"]["run"])
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", steps["Send planned breaking alerts"]["env"])
        self.assertNotIn("gcs_asset.py copy", all_runs)
        self.assertNotIn("gcs_asset.py delete", all_runs)


if __name__ == "__main__":
    unittest.main()
