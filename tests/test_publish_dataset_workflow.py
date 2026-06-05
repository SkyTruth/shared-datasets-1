from __future__ import annotations

import unittest
from pathlib import Path

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/publish-dataset.yml"


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
        summary_run = self.apply_steps["Send dataset upload summary"]["run"]
        cleanup_run = self.apply_steps["Delete promoted scratch source objects"]["run"]

        assert_ordered(
            self,
            names,
            "Check approved schema compatibility",
            "Promote approved staged objects",
            "Rebuild promoted release index",
            "Send dataset upload summary",
            "Delete promoted scratch source objects",
            "Delete approved canonical objects",
        )

        self.assertIn("filename == canonical_filename", compatibility_run + promote_run + summary_run)
        self.assertNotIn('f"{asset_slug}{suffix}"', compatibility_run + promote_run + summary_run)
        self.assertNotIn('f"{slug}{suffix}"', compatibility_run + promote_run + summary_run)
        self.assertLess(promote_run.index('"stat", promotion["source_uri"]'), promote_run.index('"copy",'))
        self.assertLess(promote_run.index('"copy",'), promote_run.index('"stat", promotion["destination_uri"]'))
        self.assertLess(promote_run.index('"stat", promotion["destination_uri"]'), promote_run.index('"download",'))
        self.assertLess(promote_run.index('"download",'), promote_run.index('"check-schema",'))

        self.assertIn("catalog/shared-datasets-catalog.csv", release_index_run)
        self.assertIn('"release-index"', release_index_run)
        self.assertIn('"rebuild"', release_index_run)
        self.assertIn("scripts/dataset_alerts.py", summary_run)
        self.assertIn("upload-summary", summary_run)
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", self.apply_steps["Send dataset upload summary"]["env"])
        self.assertIn('promotion["source_generation"]', cleanup_run)
        self.assertIn('"delete"', cleanup_run)
        self.assertIn('"--confirm"', cleanup_run)

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


if __name__ == "__main__":
    unittest.main()
