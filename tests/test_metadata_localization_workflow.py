from pathlib import Path
import unittest
from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers


class MetadataLocalizationWorkflowTests(unittest.TestCase):
    def test_automatic_input_is_verified_upstream_artifact_not_pr_lookup(self):
        workflow = load_workflow(
            Path(__file__).resolve().parents[1]
            / ".github/workflows/metadata-localization.yml"
        )
        steps = workflow_steps_by_name(workflow, "materialize")
        self.assertIn("workflow_run", workflow_triggers(workflow))
        self.assertIn(
            "dataset_mutation_authorization.py from-run",
            steps["Prepare reviewed publish plan"]["run"],
        )
        self.assertIn(
            "steps.reviewed_plan.outputs.executor_sha",
            steps["Check out upstream immutable executor"]["with"]["ref"],
        )
        self.assertIn(
            "--expected-sha256", steps["Verify upstream authorization"]["run"]
        )
        names = list(steps)
        self.assertLess(
            names.index("Recheck upstream acceptance before publisher authentication"),
            names.index("Authenticate as shared datasets publisher"),
        )
        all_runs = "\n".join(step.get("run", "") for step in steps.values())
        for forbidden in (
            "gh pr list",
            "resolve-workflow-run-pr",
            "event-from-pr",
            "extract publish",
        ):
            self.assertNotIn(forbidden, all_runs)
        self.assertIn(
            "has_publish_plan == 'true'",
            steps["Materialize reviewed translation-source promotions"]["if"],
        )
        self.assertIn(
            "--publish-plan publish-plan.json",
            steps["Materialize reviewed translation-source promotions"]["run"],
        )

    def test_manual_materialization_contract_is_preserved(self):
        workflow = load_workflow(
            Path(__file__).resolve().parents[1]
            / ".github/workflows/metadata-localization.yml"
        )
        steps = workflow_steps_by_name(workflow, "materialize")
        self.assertEqual(
            set(workflow_triggers(workflow)["workflow_dispatch"]["inputs"]),
            {"translation_source_uri", "asset_slug", "release", "fail_on_stale"},
        )
        self.assertIn(
            "--translation-source-uri",
            steps["Materialize manual translation source"]["run"],
        )
        self.assertEqual(
            workflow["jobs"]["materialize"]["environment"], "shared-datasets-production"
        )
