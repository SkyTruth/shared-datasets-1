from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_TF = REPO_ROOT / "terraform/envs/prod"


class MetadataServiceTerraformTests(unittest.TestCase):
    def test_metadata_service_is_iap_protected_and_uses_firestore_index(self):
        metadata_tf = (PROD_TF / "metadata_service.tf").read_text()
        variables_tf = (PROD_TF / "variables.tf").read_text()
        outputs_tf = (PROD_TF / "outputs.tf").read_text()
        main_tf = (PROD_TF / "main.tf").read_text()
        monitoring_tf = (PROD_TF / "monitoring.tf").read_text()

        self.assertIn('"firestore.googleapis.com"', main_tf)
        self.assertIn('resource "google_firestore_database" "feature_metadata"', metadata_tf)
        self.assertIn('type                        = "FIRESTORE_NATIVE"', metadata_tf)
        self.assertIn('delete_protection_state     = "DELETE_PROTECTION_ENABLED"', metadata_tf)
        self.assertIn(
            'depends_on = [google_project_service.required["firestore.googleapis.com"]]',
            metadata_tf,
        )
        self.assertIn(
            'depends_on = [google_project_service.required["iam.googleapis.com"]]',
            metadata_tf,
        )
        self.assertIn(
            'depends_on = [google_project_service.required["artifactregistry.googleapis.com"]]',
            main_tf,
        )
        self.assertNotIn("depends_on = [google_project_service.required]", metadata_tf)
        self.assertNotIn("depends_on = [google_storage_bucket.shared_bucket]", metadata_tf)
        self.assertIn('account_id   = "metadata-service"', metadata_tf)
        self.assertIn('account_id   = "metadata-index-loader"', metadata_tf)
        self.assertIn('resource "google_cloud_run_v2_service" "metadata_service"', metadata_tf)
        self.assertIn("iap_enabled         = true", metadata_tf)
        self.assertIn('role    = "roles/datastore.viewer"', metadata_tf)
        self.assertIn('role    = "roles/datastore.user"', metadata_tf)
        self.assertIn('resource "google_storage_bucket_iam_member" "metadata_service_object_viewer"', metadata_tf)
        self.assertIn('resource "google_storage_bucket_iam_member" "metadata_index_loader_object_viewer"', metadata_tf)
        self.assertIn('resource "google_storage_bucket_iam_member" "metadata_index_loader_index_load_creator"', metadata_tf)
        self.assertIn('resource "google_storage_bucket_iam_member" "metadata_index_loader_index_load_folder_admin"', metadata_tf)
        self.assertIn('role   = "roles/storage.objectCreator"', metadata_tf)
        self.assertIn('role   = "roles/storage.folderAdmin"', metadata_tf)
        self.assertIn("metadata_index_loader_record_creator_condition", metadata_tf)
        self.assertIn("metadata_index_loader_index_load_folder_condition", metadata_tf)
        self.assertIn(
            'metadata_index_loader_record_creator_condition = join(" && ", [',
            metadata_tf,
        )
        self.assertIn(
            'metadata_index_loader_index_load_folder_condition = join(" && ", [',
            metadata_tf,
        )
        self.assertIn("resource.name.extract", metadata_tf)
        self.assertIn("resource.name.endsWith('.json')", metadata_tf)
        self.assertIn(
            "!resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/')",
            metadata_tf,
        )
        self.assertIn(
            "!resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/')",
            metadata_tf,
        )
        self.assertNotIn('metadata_index_loader_record_creator_condition = join(" || "', metadata_tf)
        self.assertIn("/index-loads/", metadata_tf)
        self.assertIn('resource "google_service_account_iam_member" "metadata_index_loader_github_wif"', metadata_tf)
        self.assertIn('role               = "roles/iam.workloadIdentityUser"', metadata_tf)
        self.assertIn("module.metadata_index_loader_service_account.email", metadata_tf)
        self.assertIn("environment:${var.github_publish_environment}", metadata_tf)
        self.assertIn("canonical_dataset_top_level_prefixes", metadata_tf)
        self.assertNotIn("_catalog/web/catalog.json", metadata_tf)
        self.assertIn('FEATURE_METADATA_MAX_IDS', metadata_tf)
        self.assertIn('default     = 500', variables_tf)
        self.assertIn('default     = 10485760', variables_tf)
        self.assertIn("metadata_service_uri", outputs_tf)
        self.assertIn("metadata_index_loader_service_account", outputs_tf)
        self.assertIn('resource "google_monitoring_alert_policy" "metadata_service_error_logs"', monitoring_tf)
        self.assertIn("module.metadata_index_loader_service_account.email", monitoring_tf)
        self.assertIn("dataset_write_allowed_principal_filter", monitoring_tf)


if __name__ == "__main__":
    unittest.main()
