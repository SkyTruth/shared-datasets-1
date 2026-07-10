from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class SharedDatasetConsumersTerraformTests(unittest.TestCase):
    def test_prod_defines_reader_service_accounts_for_expected_projects(self):
        variables_tf = (REPO_ROOT / "terraform/envs/prod/variables.tf").read_text()
        consumers_tf = (REPO_ROOT / "terraform/envs/prod/shared_dataset_consumers.tf").read_text()
        outputs_tf = (REPO_ROOT / "terraform/envs/prod/outputs.tf").read_text()

        for project_id in (
            "cerulean-338116",
            "x30-399415",
            "skytruth-monitor",
            "skytruth-tech",
        ):
            self.assertIn(project_id, variables_tf)

        self.assertIn('service_account_id = optional(string, "shared-datasets-reader")', variables_tf)
        self.assertRegex(consumers_tf, re.compile(r'resource "google_service_account" "shared_dataset_consumers"'))
        self.assertRegex(
            consumers_tf,
            re.compile(r'resource "google_storage_managed_folder_iam_member" "shared_dataset_consumer_object_viewers"'),
        )
        self.assertIn("setproduct", consumers_tf)
        self.assertIn("roles/storage.objectViewer", consumers_tf)
        self.assertIn("shared_dataset_consumer_service_accounts", outputs_tf)


if __name__ == "__main__":
    unittest.main()
