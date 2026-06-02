from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DEPLOY = REPO_ROOT / ".github/workflows/catalog-web-deploy.yml"
PMTILES_CDN_SYNC = REPO_ROOT / ".github/workflows/pmtiles-cdn-sync.yml"
SCRATCH_CLEANUP_IAM_SYNC = REPO_ROOT / ".github/workflows/scratch-cleanup-iam-sync.yml"


class CatalogWebWorkflowTests(unittest.TestCase):
    def test_catalog_web_deploy_uses_publisher_identity_and_no_cache_publish_helper(self):
        workflow = CATALOG_DEPLOY.read_text()

        self.assertIn("push:", workflow)
        self.assertIn("branches:", workflow)
        self.assertIn("- main", workflow)
        self.assertIn("web/catalog/**", workflow)
        self.assertIn("scripts/catalog_site.py", workflow)
        self.assertIn("scripts/catalog_web_publish.py", workflow)
        self.assertIn("shared-datasets-production", workflow)
        self.assertIn("PUBLISHER_SERVICE_ACCOUNT: shared-datasets-publisher@shared-datasets-1.iam.gserviceaccount.com", workflow)
        self.assertIn("SHARED_DATASETS_ALLOW_CANONICAL_MUTATION: \"1\"", workflow)
        self.assertIn("no-cache, max-age=0, must-revalidate", workflow)
        self.assertIn("Validate publisher auth configuration", workflow)
        self.assertIn("Missing repository variable: GCP_WORKLOAD_IDENTITY_PROVIDER", workflow)
        self.assertIn("scripts/catalog_web_publish.py", workflow)
        self.assertIn('--catalog-source "catalog/shared-datasets-catalog.csv"', workflow)
        self.assertIn('--catalog-destination "gs://${SHARED_DATASETS_BUCKET}/_catalog/shared-datasets-catalog.csv"', workflow)
        self.assertIn("node --check web/catalog/app.js", workflow)
        self.assertNotIn("shared-datasets-publish-plan", workflow)

    def test_pmtiles_cdn_sync_has_explicit_resource_change_allowlist(self):
        workflow = PMTILES_CDN_SYNC.read_text()

        self.assertIn("PMTiles CDN sync", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("Check PMTiles CDN sync readiness", workflow)
        self.assertNotIn('- ".github/workflows/pmtiles-cdn-sync.yml"', workflow)
        self.assertIn("github.event_name != 'pull_request'", workflow)
        self.assertIn("group: prod-terraform-state", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("terraform -chdir=terraform/envs/prod plan", workflow)
        self.assertIn("-refresh=false", workflow)
        self.assertIn("-target=google_compute_url_map.pmtiles_cdn", workflow)
        self.assertNotIn("-target=google_compute_backend_bucket.pmtiles_cdn", workflow)
        self.assertNotIn("-target=google_storage_bucket.shared_bucket", workflow)
        self.assertNotIn("-target=google_storage_managed_folder.shared_bucket_public_prefixes", workflow)
        self.assertNotIn("-target=google_storage_managed_folder_iam_member.shared_bucket_public_object_viewers", workflow)
        self.assertIn("unused-by-pmtiles-cdn-sync", workflow)
        self.assertIn("terraform -chdir=terraform/envs/prod apply", workflow)
        self.assertIn("invalidate-cdn-cache", workflow)
        self.assertIn('--path="/_catalog/*"', workflow)
        self.assertIn("Verify catalog CDN routes", workflow)
        self.assertIn('"$status" == "200"', workflow)
        self.assertIn("https://tiles.skytruth.org/_catalog/shared-datasets-catalog.csv", workflow)
        self.assertIn("https://tiles.skytruth.org/_catalog/web/catalog.json", workflow)
        self.assertIn("https://tiles.skytruth.org/_catalog/web/index.html", workflow)
        self.assertIn("text/csv", workflow)
        self.assertIn("application/json", workflow)
        self.assertIn("text/html", workflow)
        self.assertNotIn("curl -L", workflow)
        self.assertNotIn("--location", workflow)
        self.assertIn("allowed_exact", workflow)
        self.assertIn("google_compute_url_map.pmtiles_cdn", workflow)
        self.assertNotIn("google_compute_backend_bucket.pmtiles_cdn", workflow)
        self.assertNotIn("google_storage_managed_folder.shared_bucket_public_prefixes", workflow)
        self.assertNotIn("google_storage_managed_folder_iam_member.shared_bucket_public_object_viewers", workflow)
        self.assertNotIn("google_storage_bucket.shared_bucket", workflow)
        self.assertNotIn("changed <= {\"cors\"}", workflow)
        self.assertIn("Refusing automatic PMTiles CDN sync", workflow)
        self.assertIn("Validate Terraform auth configuration", workflow)
        self.assertIn("Missing repository variable: GCP_TERRAFORM_SERVICE_ACCOUNT", workflow)
        self.assertIn("vars.GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER || vars.GCP_WORKLOAD_IDENTITY_PROVIDER", workflow)
        self.assertIn("vars.GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER", workflow)
        self.assertIn("vars.GCP_TERRAFORM_SERVICE_ACCOUNT", workflow)
        self.assertNotIn("Read current ingestion images", workflow)
        self.assertNotIn("gcloud run jobs describe", workflow)
        self.assertNotIn("vars.GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER != '' && vars.GCP_TERRAFORM_SERVICE_ACCOUNT != ''", workflow)

    def test_scratch_cleanup_iam_sync_has_explicit_resource_change_allowlist(self):
        workflow = SCRATCH_CLEANUP_IAM_SYNC.read_text()

        self.assertIn("Scratch cleanup IAM sync", workflow)
        self.assertIn("pull_request:", workflow)
        self.assertIn("Check scratch cleanup IAM sync readiness", workflow)
        self.assertIn("github.event_name != 'pull_request'", workflow)
        self.assertIn("shared-datasets-production", workflow)
        self.assertIn("group: prod-terraform-state", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("terraform -chdir=terraform/envs/prod plan", workflow)
        self.assertIn("-refresh=false", workflow)
        self.assertIn("-target=google_project_iam_custom_role.shared_datasets_publisher_object_lister", workflow)
        self.assertIn("-target=google_storage_bucket_iam_member.shared_datasets_publisher_object_lister", workflow)
        self.assertIn(
            "-target=google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_cleanup_user",
            workflow,
        )
        self.assertIn("unused-by-scratch-cleanup-iam-sync", workflow)
        self.assertIn("terraform -chdir=terraform/envs/prod apply", workflow)
        self.assertIn("allowed_exact", workflow)
        self.assertIn("google_project_iam_custom_role.shared_datasets_publisher_object_lister", workflow)
        self.assertIn("google_storage_bucket_iam_member.shared_datasets_publisher_object_lister", workflow)
        self.assertIn(
            "google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_cleanup_user",
            workflow,
        )
        self.assertNotIn("-target=google_storage_bucket.shared_bucket", workflow)
        self.assertNotIn("google_compute_url_map.pmtiles_cdn", workflow)
        self.assertNotIn("google_storage_managed_folder.shared_bucket_public_prefixes", workflow)
        self.assertIn("Refusing automatic scratch cleanup IAM sync", workflow)
        self.assertIn("Validate Terraform auth configuration", workflow)
        self.assertIn("Missing repository variable: GCP_TERRAFORM_SERVICE_ACCOUNT", workflow)
        self.assertIn("vars.GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER || vars.GCP_WORKLOAD_IDENTITY_PROVIDER", workflow)


if __name__ == "__main__":
    unittest.main()
