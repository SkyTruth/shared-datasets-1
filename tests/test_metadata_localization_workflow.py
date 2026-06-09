from __future__ import annotations

import unittest
from pathlib import Path

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/metadata-localization.yml"


class MetadataLocalizationWorkflowTests(unittest.TestCase):
    def test_workflow_runs_after_approved_dataset_mutation_and_uses_pipeline(self):
        workflow = load_workflow(WORKFLOW)
        trigger = workflow_triggers(workflow)
        steps = workflow_steps_by_name(workflow, "materialize")
        step_names = list(steps)

        self.assertEqual(trigger["workflow_run"]["workflows"], ["Approved dataset mutation"])
        self.assertEqual(trigger["workflow_run"]["types"], ["completed"])
        self.assertIn("workflow_dispatch", trigger)
        self.assertIn("translation_source_uri", trigger["workflow_dispatch"]["inputs"])
        self.assertIn("asset_slug", trigger["workflow_dispatch"]["inputs"])
        self.assertIn("release", trigger["workflow_dispatch"]["inputs"])
        self.assertIn("fail_on_stale", trigger["workflow_dispatch"]["inputs"])

        self.assertIn(
            "feature_metadata_translation_pipeline.py",
            steps["Materialize reviewed translation-source promotions"]["run"],
        )
        self.assertIn("--publish-plan", steps["Materialize reviewed translation-source promotions"]["run"])
        self.assertIn(
            "feature_metadata_translation_pipeline.py",
            steps["Materialize manual translation source"]["run"],
        )
        self.assertIn("--translation-source-uri", steps["Materialize manual translation source"]["run"])
        self.assertEqual(steps["Upload materialization report"]["uses"], "actions/upload-artifact@v4")
        self.assertLess(
            step_names.index("Prepare reviewed publish plan"),
            step_names.index("Validate publisher auth configuration"),
        )
        self.assertIn(
            "steps.reviewed_plan.outputs.publish_plan != ''",
            steps["Validate publisher auth configuration"]["if"],
        )

    def test_workflow_keeps_canonical_writes_in_approved_runtime(self):
        workflow = load_workflow(WORKFLOW)
        env = workflow["env"]
        job = workflow["jobs"]["materialize"]
        steps = workflow_steps_by_name(workflow, "materialize")
        reviewed_promotion_env = steps["Materialize reviewed translation-source promotions"]["env"]
        manual_dispatch_env = steps["Materialize manual translation source"]["env"]
        run_scripts = "\n".join(str(step.get("run", "")) for step in steps.values())

        self.assertEqual(job["environment"], "shared-datasets-production")
        self.assertEqual(
            env["PUBLISHER_SERVICE_ACCOUNT"],
            "shared-datasets-publisher@shared-datasets-1.iam.gserviceaccount.com",
        )
        self.assertEqual(reviewed_promotion_env["SHARED_DATASETS_ALLOW_CANONICAL_MUTATION"], "1")
        self.assertEqual(manual_dispatch_env["SHARED_DATASETS_ALLOW_CANONICAL_MUTATION"], "1")
        self.assertIn(
            "Feature metadata localization materialization must run from main",
            steps["Validate manual dispatch ref"]["run"],
        )
        self.assertNotIn("scripts/gcs_asset.py upload", run_scripts)
        self.assertNotIn("scripts/gcs_asset.py copy", run_scripts)


if __name__ == "__main__":
    unittest.main()
