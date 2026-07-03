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
        self.assertIn(
            "scripts/publish_workflow.py check-approved-review --reviews-json reviews.json",
            acceptance_run,
        )

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
        self.assertIn("scripts/reviewed_dataset_plan.py extract publish", all_apply_runs)
        self.assertIn("scripts/reviewed_dataset_plan.py extract delete", all_apply_runs)
        self.assertNotIn("gsutil", all_apply_runs)
        self.assertNotIn("gcloud storage", all_apply_runs)
        all_apply_text = str(apply_job)
        self.assertNotIn("Single-object fallback", all_apply_text)
        self.assertNotIn("Promote staged object manually", all_apply_text)
        self.assertNotIn("github.event.inputs.pr_number == ''", all_apply_text)

    def test_publish_plan_promotes_then_rebuilds_summarizes_and_cleans_up(self):
        names = step_names(self.workflow, "apply-approved-pr-plans")

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

        expected_commands = {
            "Validate publish destination layouts": (
                "scripts/publish_workflow.py validate-plan-paths --plan-type publish --plan-json publish-plan.json"
            ),
            "Validate delete target layouts": (
                "scripts/publish_workflow.py validate-plan-paths --plan-type delete --plan-json delete-plan.json"
            ),
            "Collect reviewed catalog rows": (
                'scripts/publish_workflow.py collect-catalog-rows --event-path "${EVENT_PATH}"'
            ),
            "Check approved schema compatibility": (
                "scripts/publish_workflow.py check-schema-compatibility --plan-json publish-plan.json --phase live"
            ),
            "Promote approved staged objects": (
                "uv run python scripts/publish_workflow.py promote --plan-json publish-plan.json"
            ),
            "Rebuild promoted release index": (
                "scripts/publish_workflow.py rebuild-release-index --plan-json publish-plan.json"
            ),
            "Send dataset upload summary": (
                "scripts/publish_workflow.py upload-summary --plan-json publish-plan.json"
            ),
            "Delete promoted scratch source objects": (
                "scripts/publish_workflow.py delete-scratch-sources --plan-json publish-plan.json"
            ),
            "Delete approved canonical objects": (
                "scripts/publish_workflow.py delete-canonical-objects --plan-json delete-plan.json"
            ),
        }
        for step_name, command in expected_commands.items():
            with self.subTest(step=step_name):
                self.assertIn(command, self.apply_steps[step_name]["run"])

        finalize_run = self.apply_steps["Finalize promoted release metadata"]["run"]
        self.assertIn("scripts/finalize_promoted_release_metadata.py", finalize_run)
        self.assertIn("--publish-plan publish-plan.json", finalize_run)
        self.assertIn("--output finalized-release-metadata.json", finalize_run)

        breaking_run = self.apply_steps["Send breaking change alert"]["run"]
        self.assertIn("scripts/publish_workflow.py live-breaking-alert", breaking_run)
        self.assertIn("--plan-type publish", breaking_run)
        self.assertIn("--summary-json publish-breaking-alert.json", breaking_run)
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", self.apply_steps["Send breaking change alert"]["env"])
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", self.apply_steps["Send dataset upload summary"]["env"])

    def test_delete_plan_sends_live_breaking_alert_after_delete(self):
        names = step_names(self.workflow, "apply-approved-pr-plans")
        delete_alert_run = self.apply_steps["Send deletion breaking change alert"]["run"]

        assert_ordered(
            self,
            names,
            "Delete approved canonical objects",
            "Send deletion breaking change alert",
        )
        self.assertIn("scripts/publish_workflow.py live-breaking-alert", delete_alert_run)
        self.assertIn("--plan-type delete", delete_alert_run)
        self.assertIn("--summary-json delete-breaking-alert.json", delete_alert_run)
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", self.apply_steps["Send deletion breaking change alert"]["env"])

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
            'scripts/publish_workflow.py collect-proposed-catalog-row --head-sha "${HEAD_SHA}"',
            steps["Collect proposed catalog row from PR"]["run"],
        )
        self.assertIn(
            "scripts/publish_workflow.py detect-schema-targets",
            steps["Detect planned schema compatibility targets"]["run"],
        )
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
        self.assertIn(
            "scripts/publish_workflow.py check-schema-compatibility --plan-json publish-plan.json --phase planned",
            steps["Collect planned schema compatibility results"]["run"],
        )
        self.assertIn(
            "scripts/publish_workflow.py planned-breaking-alert",
            steps["Summarize planned publish breaking alert"]["run"],
        )
        self.assertIn(
            "scripts/publish_workflow.py planned-breaking-alert",
            steps["Summarize planned delete breaking alert"]["run"],
        )
        self.assertIn(
            "scripts/publish_workflow.py send-planned-breaking-alerts",
            steps["Send planned breaking alerts"]["run"],
        )
        self.assertIn("SHARED_DATASETS_SLACK_WEBHOOK_URL", steps["Send planned breaking alerts"]["env"])
        self.assertNotIn("gcs_asset.py copy", all_runs)
        self.assertNotIn("gcs_asset.py delete", all_runs)
        self.assertNotIn("publish_workflow.py promote", all_runs)
        self.assertNotIn("publish_workflow.py delete-scratch-sources", all_runs)
        self.assertNotIn("publish_workflow.py delete-canonical-objects", all_runs)
        self.assertNotIn("publish_workflow.py live-breaking-alert", all_runs)


if __name__ == "__main__":
    unittest.main()
