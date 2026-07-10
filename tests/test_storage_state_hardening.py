from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_TF = REPO_ROOT / "terraform/envs/prod"


class StorageStateHardeningTests(unittest.TestCase):
    def test_state_bucket_is_isolated_versioned_and_recoverable(self):
        state_tf = (PROD_TF / "terraform_state_storage.tf").read_text()

        self.assertIn('name                        = "skytruth-shared-datasets-1-terraform-state"', state_tf)
        self.assertIn('public_access_prevention    = "enforced"', state_tf)
        self.assertIn("versioning {", state_tf)
        self.assertIn("enabled = true", state_tf)
        self.assertIn("retention_duration_seconds = 2592000", state_tf)
        self.assertIn("prevent_destroy = true", state_tf)
        self.assertNotIn("hierarchical_namespace", state_tf)
        self.assertIn("terraform_state_workflow_admin", state_tf)
        self.assertIn("terraform_state_breakglass_admin", state_tf)

    def test_shared_hns_bucket_uses_thirty_day_soft_delete_without_versioning(self):
        bucket_tf = (PROD_TF / "shared_bucket_public.tf").read_text()

        self.assertIn("retention_duration_seconds = 2592000", bucket_tf)
        self.assertIn("hierarchical_namespace", bucket_tf)
        self.assertNotIn("versioning {", bucket_tf)

    def test_cdn_and_consumers_use_managed_folder_grants(self):
        bucket_tf = (PROD_TF / "shared_bucket_public.tf").read_text()
        consumers_tf = (PROD_TF / "shared_dataset_consumers.tf").read_text()
        cdn_tf = (PROD_TF / "pmtiles_cdn.tf").read_text()

        self.assertIn("shared_bucket_cdn_fill_object_viewers", bucket_tf)
        self.assertIn("google_storage_managed_folder_iam_member", consumers_tf)
        self.assertIn("setproduct", consumers_tf)
        self.assertNotIn("shared_bucket_cloud_cdn_fill_object_viewer", cdn_tf)
        self.assertNotIn('resource "google_storage_bucket_iam_member"', consumers_tf)

    def test_github_audits_can_list_but_not_read_legacy_state(self):
        github_tf = (PROD_TF / "github_readonly_iam.tf").read_text()

        self.assertIn('permissions = ["storage.objects.list"]', github_tf)
        self.assertIn("exclude_terraform_state_bytes", github_tf)
        self.assertIn("!resource.name.startsWith", github_tf)
        self.assertIn("000-system/terraform/state/", github_tf)

if __name__ == "__main__":
    unittest.main()
