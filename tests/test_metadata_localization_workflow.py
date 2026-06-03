from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/metadata-localization.yml"


class MetadataLocalizationWorkflowTests(unittest.TestCase):
    def test_workflow_runs_after_approved_dataset_mutation_and_uses_pipeline(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("workflow_run:", workflow)
        self.assertIn("Approved dataset mutation", workflow)
        self.assertIn("feature_metadata_translation_pipeline.py", workflow)
        self.assertIn("--publish-plan", workflow)
        self.assertIn("--translation-source-uri", workflow)
        self.assertIn("actions/upload-artifact@v4", workflow)

    def test_workflow_keeps_canonical_writes_in_approved_runtime(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("environment: shared-datasets-production", workflow)
        self.assertIn("PUBLISHER_SERVICE_ACCOUNT: shared-datasets-publisher@shared-datasets-1.iam.gserviceaccount.com", workflow)
        self.assertIn('SHARED_DATASETS_ALLOW_CANONICAL_MUTATION: "1"', workflow)
        self.assertIn("Feature metadata localization materialization must run from main", workflow)
        self.assertNotIn("scripts/gcs_asset.py upload", workflow)
        self.assertNotIn("scripts/gcs_asset.py copy", workflow)


if __name__ == "__main__":
    unittest.main()
