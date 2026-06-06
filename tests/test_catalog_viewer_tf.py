from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_TF = REPO_ROOT / "terraform/envs/prod"


class CatalogViewerTerraformTests(unittest.TestCase):
    def test_catalog_viewer_cloud_run_is_iap_protected_and_read_only(self):
        catalog_viewer_tf = (PROD_TF / "catalog_viewer.tf").read_text()
        variables_tf = (PROD_TF / "variables.tf").read_text()
        outputs_tf = (PROD_TF / "outputs.tf").read_text()
        main_tf = (PROD_TF / "main.tf").read_text()

        self.assertIn('"iap.googleapis.com"', main_tf)
        self.assertIn('account_id   = "catalog-viewer"', catalog_viewer_tf)
        self.assertIn('resource "google_cloud_run_v2_service" "catalog_viewer"', catalog_viewer_tf)
        self.assertIn('ingress             = "INGRESS_TRAFFIC_ALL"', catalog_viewer_tf)
        self.assertIn('launch_stage        = "BETA"', catalog_viewer_tf)
        self.assertIn("iap_enabled         = true", catalog_viewer_tf)
        self.assertIn("pmtiles_browser_allowed_origins", catalog_viewer_tf)
        self.assertIn('catalog_viewer_service_name      = "catalog-viewer"', catalog_viewer_tf)
        self.assertIn('data "google_cloud_run_v2_service" "catalog_viewer_live"', catalog_viewer_tf)
        self.assertIn("data.google_cloud_run_v2_service.catalog_viewer_live.uri", catalog_viewer_tf)
        self.assertNotIn("google_cloud_run_v2_service.catalog_viewer.uri", catalog_viewer_tf)
        self.assertIn('role   = "roles/storage.objectViewer"', catalog_viewer_tf)
        self.assertNotIn('role   = "roles/storage.objectUser"', catalog_viewer_tf)
        self.assertIn('permissions = ["iam.serviceAccounts.signBlob"]', catalog_viewer_tf)
        self.assertIn('resource "google_service_account_iam_member" "catalog_viewer_self_sign_blob"', catalog_viewer_tf)
        self.assertIn('resource "google_cloud_run_v2_service_iam_member" "catalog_viewer_iap_invoker"', catalog_viewer_tf)
        self.assertIn('resource "google_iap_web_cloud_run_service_iam_member" "catalog_viewer_accessors"', catalog_viewer_tf)
        self.assertIn('roles/iap.httpsResourceAccessor', catalog_viewer_tf)
        self.assertIn('"CATALOG_VIEWER_METADATA_CDN_BASE_URL"', catalog_viewer_tf)
        self.assertIn('value = "https://${var.pmtiles_cdn_host}/private"', catalog_viewer_tf)
        self.assertIn('"CATALOG_VIEWER_CDN_SIGNING_KEY_NAME"', catalog_viewer_tf)
        self.assertIn('"CATALOG_VIEWER_CDN_SIGNING_SECRET_ID"', catalog_viewer_tf)
        self.assertIn('${google_secret_manager_secret.pmtiles_cdn_signed_request_key.id}/versions/latest', catalog_viewer_tf)
        self.assertNotIn('"CATALOG_VIEWER_CDN_SIGNING_SECRET_VERSION"', catalog_viewer_tf)
        self.assertIn('"CATALOG_VIEWER_METADATA_CDN_SIGNED_URL_TTL_SECONDS"', catalog_viewer_tf)
        self.assertIn("google_secret_manager_secret_iam_member.pmtiles_cdn_catalog_viewer_signer", catalog_viewer_tf)
        self.assertNotIn('resource "google_compute_backend_service" "catalog_viewer"', catalog_viewer_tf)
        self.assertNotIn('google_compute_global_address.catalog_viewer', outputs_tf)
        self.assertNotIn('variable "catalog_viewer_host"', variables_tf)
        self.assertIn('default     = ["domain:skytruth.org"]', variables_tf)
        self.assertIn('default     = ["skytruth.org"]', variables_tf)
        self.assertIn('default     = 900', variables_tf)
        self.assertIn('catalog_viewer_uri', outputs_tf)
        self.assertIn('catalog_viewer_service_account', outputs_tf)

    def test_prod_tfvars_pin_live_viewer_and_prod_runtime_settings(self):
        prod_tfvars = (PROD_TF / "production.auto.tfvars").read_text()

        self.assertIn("catalog_viewer_image", prod_tfvars)
        self.assertIn("catalog-viewer:20260509032023", prod_tfvars)
        self.assertIn('pmtiles_serving_mode                   = "cdn"', prod_tfvars)
        self.assertIn("pmtiles_cdn_grant_fill_service_account = true", prod_tfvars)

    def test_pmtiles_secret_iam_policy_bootstrap_is_condition_scoped(self):
        pmtiles_cdn_tf = (PROD_TF / "pmtiles_cdn.tf").read_text()

        binding_start = pmtiles_cdn_tf.index(
            'resource "google_project_iam_member" '
            '"github_actions_pmtiles_cdn_secret_iam_policy_manager"'
        )
        accessor_start = pmtiles_cdn_tf.index(
            'resource "google_secret_manager_secret_iam_member" "pmtiles_cdn_catalog_viewer_signer"',
            binding_start,
        )
        binding_block = pmtiles_cdn_tf[binding_start:accessor_start]
        accessor_block = pmtiles_cdn_tf[accessor_start:]

        self.assertEqual(pmtiles_cdn_tf.count('role    = "roles/secretmanager.admin"'), 1)
        self.assertIn('role    = "roles/secretmanager.admin"', binding_block)
        self.assertIn(
            'member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"',
            binding_block,
        )
        self.assertIn("condition {", binding_block)
        self.assertIn("pmtiles_cdn_signed_request_key_iam_policy_admin", binding_block)
        self.assertIn(
            "resource.name == 'projects/${var.project_id}/secrets/${var.pmtiles_cdn_secret_id}'",
            binding_block,
        )
        self.assertIn(
            "resource.name == 'projects/${data.google_project.current.number}/secrets/${var.pmtiles_cdn_secret_id}'",
            binding_block,
        )
        self.assertIn("depends_on = [google_project_service.required]", binding_block)
        self.assertIn(
            "google_project_iam_member.github_actions_pmtiles_cdn_secret_iam_policy_manager",
            accessor_block,
        )


if __name__ == "__main__":
    unittest.main()
