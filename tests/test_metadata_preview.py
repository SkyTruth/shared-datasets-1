from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PREVIEW_WORKFLOW = REPO_ROOT / ".github/workflows/metadata-service-preview.yml"
PREVIEW_DESTROY_WORKFLOW = REPO_ROOT / ".github/workflows/metadata-service-preview-destroy.yml"
PREVIEW_INDEX_LOAD_WORKFLOW = REPO_ROOT / ".github/workflows/feature-metadata-preview-index-load.yml"
PREVIEW_TF = REPO_ROOT / "terraform/envs/preview"


class MetadataPreviewTests(unittest.TestCase):
    def test_preview_terraform_uses_isolated_preview_resources(self):
        main_tf = (PREVIEW_TF / "main.tf").read_text()
        variables_tf = (PREVIEW_TF / "variables.tf").read_text()
        versions_tf = (PREVIEW_TF / "versions.tf").read_text()
        outputs_tf = (PREVIEW_TF / "outputs.tf").read_text()

        self.assertIn('prefix = "000-system/terraform/state/preview"', versions_tf)
        self.assertIn('default     = "skytruth-shared-datasets-1-preview"', variables_tf)
        self.assertIn('default     = "metadata-service-preview"', variables_tf)
        self.assertIn('default     = "metadata-index-loader-preview"', variables_tf)
        self.assertIn('default     = "feature-metadata-preview"', variables_tf)
        self.assertIn('resource "google_storage_bucket" "preview_bucket"', main_tf)
        self.assertIn('force_destroy               = true', main_tf)
        self.assertIn('public_access_prevention    = "enforced"', main_tf)
        self.assertIn('resource "google_firestore_database" "feature_metadata_preview"', main_tf)
        self.assertIn('delete_protection_state     = "DELETE_PROTECTION_DISABLED"', main_tf)
        self.assertIn('deletion_policy             = "DELETE"', main_tf)
        self.assertIn('resource "google_cloud_run_v2_service" "metadata_service_preview"', main_tf)
        self.assertIn('iap_enabled         = true', main_tf)
        self.assertIn('"FEATURE_METADATA_FIRESTORE_DATABASE"', main_tf)
        self.assertIn("metadata_preview_service_uri", outputs_tf)

    def test_preview_deploy_workflow_keeps_control_plane_separate_from_source_ref(self):
        workflow = PREVIEW_WORKFLOW.read_text()

        self.assertIn("name: Deploy Feature Branch to Preview", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("Check out preview control plane", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("PREVIEW_REF: ${{ github.ref_name }}", workflow)
        self.assertIn("Check out selected feature branch", workflow)
        self.assertIn("ref: ${{ github.ref }}", workflow)
        self.assertIn("path: preview-source", workflow)
        self.assertIn("preview-source/services/metadata_service/Dockerfile", workflow)
        self.assertIn("The selected feature branch must support", workflow)
        self.assertNotIn("Branch, tag, or SHA to deploy", workflow)
        self.assertNotIn("inputs.action", workflow)
        self.assertIn("terraform -chdir=terraform/envs/preview init", workflow)
        self.assertIn("Terraform reset plan", workflow)
        self.assertIn("-destroy", workflow)
        self.assertIn("Terraform apply reset plan", workflow)
        self.assertIn("terraform -chdir=terraform/envs/preview plan", workflow)
        self.assertIn("terraform -chdir=terraform/envs/preview apply", workflow)
        self.assertIn("Enforce preview resource-change allowlist", workflow)
        self.assertIn("google_storage_bucket.preview_bucket", workflow)
        self.assertIn("google_firestore_database.feature_metadata_preview", workflow)
        self.assertIn("google_cloud_run_v2_service.metadata_service_preview", workflow)
        self.assertIn("SHARED_DATASETS_BUCKET must be", workflow)
        self.assertIn("FEATURE_METADATA_FIRESTORE_DATABASE must be", workflow)
        self.assertNotIn("terraform -chdir=terraform/envs/prod", workflow)
        self.assertNotIn("-target=", workflow)

    def test_preview_destroy_workflow_only_runs_destroy_path(self):
        workflow = PREVIEW_DESTROY_WORKFLOW.read_text()

        self.assertIn("name: Destroy Preview Environment", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("environment: shared-datasets-production", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("terraform -chdir=terraform/envs/preview init", workflow)
        self.assertIn("terraform -chdir=terraform/envs/preview plan", workflow)
        self.assertIn("-destroy", workflow)
        self.assertIn("terraform -chdir=terraform/envs/preview apply", workflow)
        self.assertIn("Enforce preview resource-change allowlist", workflow)
        self.assertIn("google_storage_bucket.preview_bucket", workflow)
        self.assertIn("google_firestore_database.feature_metadata_preview", workflow)
        self.assertIn("google_cloud_run_v2_service.metadata_service_preview", workflow)
        self.assertNotIn("preview-source", workflow)
        self.assertNotIn("docker build", workflow)
        self.assertNotIn("metadata_service_image=", workflow)
        self.assertNotIn("terraform -chdir=terraform/envs/prod", workflow)
        self.assertNotIn("-target=", workflow)

    def test_preview_deploy_workflow_allowlist_patterns_match_real_addresses(self):
        workflow = PREVIEW_WORKFLOW.read_text()
        patterns = [
            re.compile(pattern)
            for pattern in re.findall(r're\.compile\(r"([^"]+)"\)', workflow)
        ]

        self.assertTrue(
            any(
                pattern.match("module.metadata_service_account.google_service_account.this")
                for pattern in patterns
            )
        )
        self.assertTrue(
            any(
                pattern.match("module.metadata_index_loader_service_account.google_service_account.this")
                for pattern in patterns
            )
        )
        self.assertTrue(
            any(
                pattern.match('google_iap_web_cloud_run_service_iam_member.metadata_service_preview_accessors["user:jona@skytruth.org"]')
                for pattern in patterns
            )
        )
        self.assertFalse(
            any(pattern.match("google_iap_web_cloud_run_service_iam_member.metadata_service_accessors") for pattern in patterns)
        )

    def test_preview_index_load_uses_preview_bucket_database_and_source_ref(self):
        workflow = PREVIEW_INDEX_LOAD_WORKFLOW.read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("Check out preview control plane", workflow)
        self.assertIn("Check out requested source ref", workflow)
        self.assertIn("path: preview-source", workflow)
        self.assertIn("SHARED_DATASETS_BUCKET: skytruth-shared-datasets-1-preview", workflow)
        self.assertIn("metadata-index-loader-preview@shared-datasets-1.iam.gserviceaccount.com", workflow)
        self.assertIn("FEATURE_METADATA_FIRESTORE_DATABASE: feature-metadata-preview", workflow)
        self.assertIn("FEATURE_METADATA_COLLECTION_ROOT: feature_metadata", workflow)
        self.assertIn("Verify requested source ref supports preview Firestore database", workflow)
        self.assertIn("working-directory: preview-source", workflow)
        self.assertIn('release_prefix = f"gs://{bucket}/{asset_root}/releases/{release}/"', workflow)
        self.assertIn(
            'gs://{bucket}/000-system/metadata-preview/index-loads/{asset_slug}/{release}/{load_id}.json',
            workflow,
        )
        self.assertIn("--collection-root \"${FEATURE_METADATA_COLLECTION_ROOT}\"", workflow)
        self.assertIn("SHARED_DATASETS_ALLOW_CANONICAL_MUTATION: \"1\"", workflow)
        self.assertNotIn("skytruth-shared-datasets-1/", workflow)
        self.assertNotIn("--replace-generation", workflow)
        self.assertNotIn("--unsafe-overwrite", workflow)

    def test_main_contains_preview_source_mechanics(self):
        service_run = REPO_ROOT / "services/metadata_service/run.py"
        service_dockerfile = REPO_ROOT / "services/metadata_service/Dockerfile"
        index_loader = REPO_ROOT / "scripts/feature_metadata_index.py"

        self.assertTrue(service_run.exists())
        self.assertTrue(service_dockerfile.exists())
        self.assertTrue(index_loader.exists())
        self.assertIn("FEATURE_METADATA_FIRESTORE_DATABASE", service_run.read_text())
        self.assertIn("FEATURE_METADATA_FIRESTORE_DATABASE", index_loader.read_text())


if __name__ == "__main__":
    unittest.main()
