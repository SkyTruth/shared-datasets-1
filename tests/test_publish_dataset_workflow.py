from pathlib import Path
import unittest
from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]


class PublishDatasetWorkflowTests(unittest.TestCase):
    def test_capture_and_apply_share_exact_artifact_and_executor(self):
        workflow = load_workflow(ROOT / ".github/workflows/publish-dataset.yml")
        self.assertEqual(
            workflow_triggers(workflow)["pull_request"]["types"], ["closed"]
        )
        gate = workflow_steps_by_name(workflow, "reviewed_pr_plans")
        apply = workflow_steps_by_name(workflow, "apply-approved-pr-plans")
        self.assertEqual(
            gate["Check out immutable workflow code"]["with"]["ref"],
            "${{ github.workflow_sha }}",
        )
        self.assertIn(
            "dataset_mutation_authorization.py capture",
            gate["Capture accepted immutable mutation plan"]["run"],
        )
        self.assertNotIn("if", gate["Preserve exact authorization envelope"])
        self.assertIn(
            "needs.reviewed_pr_plans.outputs.executor_sha",
            apply["Check out captured immutable executor"]["with"]["ref"],
        )
        self.assertIn(
            "outputs.artifact_id",
            apply["Download captured authorization"]["with"]["artifact-ids"],
        )
        self.assertEqual(
            workflow["jobs"]["apply-approved-pr-plans"]["environment"],
            "shared-datasets-production",
        )
        names = list(apply)
        self.assertLess(
            names.index("Verify captured authorization"),
            names.index("Authenticate to Google Cloud"),
        )
        self.assertLess(
            names.index("Recheck acceptance immediately before mutation"),
            names.index("Promote approved staged objects"),
        )
        all_runs = "\n".join(step.get("run", "") for step in apply.values())
        self.assertNotIn("event-from-pr", all_runs)
        self.assertNotIn("extract publish", all_runs)
        self.assertNotIn("pulls/", all_runs)
        self.assertIn(
            "--expected-sha256", apply["Verify captured authorization"]["run"]
        )

    def test_existing_execution_order_and_no_direct_dispatch_fallback(self):
        workflow = load_workflow(ROOT / ".github/workflows/publish-dataset.yml")
        apply = workflow_steps_by_name(workflow, "apply-approved-pr-plans")
        names = list(apply)
        required = [
            "Check approved schema compatibility",
            "Promote approved staged objects",
            "Finalize promoted release metadata",
            "Rebuild promoted release index",
            "Delete approved canonical objects",
        ]
        self.assertEqual(
            [names.index(name) for name in required],
            sorted(names.index(name) for name in required),
        )
        self.assertEqual(
            set(workflow_triggers(workflow)["workflow_dispatch"]["inputs"]),
            {"pr_number"},
        )
        self.assertIn(
            "github.actor == 'jonaraphael'", workflow["jobs"]["reviewed_pr_plans"]["if"]
        )

    def test_preview_reads_committed_head_document_without_mutation(self):
        workflow = load_workflow(
            ROOT / ".github/workflows/dataset-breaking-change-alert.yml"
        )
        steps = workflow_steps_by_name(workflow, "alert")
        self.assertEqual(
            steps["Check out trusted repository code"]["with"]["ref"],
            "${{ github.event.pull_request.base.sha }}",
        )
        self.assertIn(
            "dataset_mutation_authorization.py preview",
            steps["Validate checked-in proposed mutation plan"]["run"],
        )
        text = str(steps)
        self.assertNotIn("publish_workflow.py promote", text)
        self.assertNotIn("extract publish", text)
        self.assertIn("GCP_READONLY_SERVICE_ACCOUNT", text)
