from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PREVIEW_WORKFLOW = REPO_ROOT / ".github/workflows/feature-preview-deploy.yml"
PREVIEW_DESTROY_WORKFLOW = REPO_ROOT / ".github/workflows/feature-preview-destroy.yml"
PREVIEW_INDEX_LOAD_WORKFLOW = REPO_ROOT / ".github/workflows/feature-preview-index-load.yml"
ARTIFACT_REGISTRY_IAM_WORKFLOW = REPO_ROOT / ".github/workflows/artifact-registry-iam-sync.yml"
PREVIEW_TERRAFORM_IAM_WORKFLOW = REPO_ROOT / ".github/workflows/preview-terraform-iam-sync.yml"
PMTILES_CDN_SYNC_WORKFLOW = REPO_ROOT / ".github/workflows/pmtiles-cdn-sync.yml"
SCRATCH_CLEANUP_IAM_SYNC_WORKFLOW = REPO_ROOT / ".github/workflows/scratch-cleanup-iam-sync.yml"
PREVIEW_TF = REPO_ROOT / "terraform/envs/preview"
PROD_TF = REPO_ROOT / "terraform/envs/prod"


class FeaturePreviewTests(unittest.TestCase):
    def test_preview_terraform_uses_isolated_preview_resources(self):
        main_tf = (PREVIEW_TF / "main.tf").read_text()
        catalog_viewer_tf = (PREVIEW_TF / "catalog_viewer.tf").read_text()
        variables_tf = (PREVIEW_TF / "variables.tf").read_text()
        versions_tf = (PREVIEW_TF / "versions.tf").read_text()
        outputs_tf = (PREVIEW_TF / "outputs.tf").read_text()

        self.assertIn('prefix = "000-system/terraform/state/preview"', versions_tf)
        self.assertIn('default     = "skytruth-shared-datasets-1-preview"', variables_tf)
        self.assertIn('default     = "feature-preview-service"', variables_tf)
        self.assertIn('default     = "feature-preview-catalog-viewer"', variables_tf)
        self.assertIn('default     = "feature-preview-loader"', variables_tf)
        self.assertIn('default     = "feature-preview"', variables_tf)
        self.assertIn("preview_catalog_viewer_image", variables_tf)
        self.assertIn('resource "google_storage_bucket" "preview_bucket"', main_tf)
        self.assertIn('force_destroy               = true', main_tf)
        self.assertIn('public_access_prevention    = "enforced"', main_tf)
        self.assertIn('method          = ["GET", "HEAD", "OPTIONS"]', main_tf)
        self.assertIn('resource "google_firestore_database" "feature_preview"', main_tf)
        self.assertIn('delete_protection_state     = "DELETE_PROTECTION_DISABLED"', main_tf)
        self.assertIn('deletion_policy             = "DELETE"', main_tf)
        self.assertIn('resource "google_cloud_run_v2_service" "feature_preview_service"', main_tf)
        self.assertIn('resource "google_cloud_run_v2_service" "feature_preview_catalog_viewer"', catalog_viewer_tf)
        self.assertIn('"SHARED_DATASETS_SITE_PREFIX"', catalog_viewer_tf)
        self.assertIn('"FEATURE_PREVIEW_FIRESTORE_DATABASE"', catalog_viewer_tf)
        self.assertIn('"FEATURE_PREVIEW_COLLECTION_ROOT"', catalog_viewer_tf)
        self.assertIn('"FEATURE_PREVIEW_MAX_IDS"', catalog_viewer_tf)
        self.assertIn('"FEATURE_PREVIEW_MAX_FIELDS"', catalog_viewer_tf)
        self.assertIn('"FEATURE_PREVIEW_MAX_RESPONSE_BYTES"', catalog_viewer_tf)
        self.assertIn('"CATALOG_VIEWER_SIGNING_SERVICE_ACCOUNT"', catalog_viewer_tf)
        self.assertIn("local.preview_service_account_email", catalog_viewer_tf)
        self.assertIn('resource "google_cloud_run_v2_service_iam_member" "feature_preview_catalog_viewer_iap_invoker"', catalog_viewer_tf)
        self.assertIn('resource "google_iap_web_cloud_run_service_iam_member" "feature_preview_catalog_viewer_accessors"', catalog_viewer_tf)
        self.assertNotIn('module "feature_preview_service_account"', main_tf)
        self.assertNotIn('module "feature_preview_loader_service_account"', main_tf)
        self.assertIn("local.preview_service_account_email", main_tf)
        self.assertIn("local.preview_loader_member", main_tf)
        self.assertIn("destroy = false", main_tf)
        self.assertIn('iap_enabled         = true', main_tf)
        self.assertIn('iap_enabled         = true', catalog_viewer_tf)
        self.assertIn('"FEATURE_PREVIEW_FIRESTORE_DATABASE"', main_tf)
        self.assertNotIn('resource "google_project_iam_member"', main_tf)
        self.assertIn("preview_service_uri", outputs_tf)
        self.assertIn("preview_catalog_viewer_uri", outputs_tf)
        self.assertIn("preview_bucket", outputs_tf)

    def test_preview_deploy_workflow_keeps_control_plane_separate_from_source_ref(self):
        workflow = PREVIEW_WORKFLOW.read_text()

        self.assertIn("name: Deploy Feature Branch to Preview", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("preview_data_mode:", workflow)
        self.assertIn("Preview data handling", workflow)
        self.assertIn("default: preserve", workflow)
        self.assertIn("- preserve", workflow)
        self.assertIn("- reset", workflow)
        self.assertNotIn("inputs.ref", workflow)
        self.assertIn("Validate selected ref", workflow)
        self.assertIn("Select the branch or tag to deploy from the workflow branch dropdown.", workflow)
        self.assertIn("refs/heads/*|refs/tags/*", workflow)
        self.assertIn("Only branch or tag refs are valid preview deploy sources", workflow)
        self.assertIn("PREVIEW_DATA_MODE: ${{ github.event.inputs.preview_data_mode || 'preserve' }}", workflow)
        self.assertIn("Preview data mode must be either preserve or reset", workflow)
        self.assertIn("Preview data mode: ${PREVIEW_DATA_MODE}", workflow)
        self.assertIn("Check out preview control plane", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("PREVIEW_REF: ${{ github.ref_name }}", workflow)
        self.assertIn("PREVIEW_SOURCE_REF: ${{ github.ref }}", workflow)
        self.assertIn("Check out selected feature branch", workflow)
        self.assertIn("ref: ${{ github.ref }}", workflow)
        self.assertIn("path: preview-source", workflow)
        self.assertIn("IMAGE_NAME: preview-service", workflow)
        self.assertIn("CATALOG_VIEWER_IMAGE_NAME: preview-catalog-viewer", workflow)
        self.assertIn("Validate preview IAM bootstrap", workflow)
        self.assertIn("Preview IAM bootstrap is incomplete", workflow)
        self.assertIn("missing_bootstrap=0", workflow)
        self.assertIn("Missing preview service account", workflow)
        self.assertIn("Missing preview loader service account", workflow)
        self.assertIn("Preview Terraform IAM sync", workflow)
        self.assertIn("feature-preview-service@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com", workflow)
        self.assertIn("feature-preview-loader@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com", workflow)
        self.assertIn("PREVIEW_LOADER_WIF_MEMBER", workflow)
        self.assertIn("preview service self signBlob binding", workflow)
        self.assertIn("preview-source/services/feature_preview_service/Dockerfile", workflow)
        self.assertIn("preview-source/services/catalog_viewer/Dockerfile", workflow)
        self.assertIn("preview Firestore database override", workflow)
        self.assertIn("catalog viewer site-prefix override", workflow)
        self.assertIn("Build preview service image", workflow)
        self.assertIn("Build preview catalog viewer image", workflow)
        self.assertIn("PREVIEW_SERVICE_IMAGE", workflow)
        self.assertIn("PREVIEW_CATALOG_VIEWER_IMAGE", workflow)
        self.assertIn("PREVIEW_TERRAFORM_DIR: preview-source/terraform/envs/preview", workflow)
        self.assertNotIn("feature_preview_service_image=", workflow)
        self.assertNotIn("inputs.action", workflow)
        self.assertIn('terraform -chdir="${PREVIEW_TERRAFORM_DIR}" init', workflow)
        self.assertIn("Release stable preview bootstrap state", workflow)
        self.assertIn('terraform -chdir="${PREVIEW_TERRAFORM_DIR}" state rm', workflow)
        self.assertIn("module.feature_preview_service_account.google_service_account.this", workflow)
        self.assertIn("module.feature_preview_loader_service_account.google_service_account.this", workflow)
        self.assertIn("google_service_account_iam_member.feature_preview_loader_github_wif", workflow)
        self.assertIn("Terraform reset plan", workflow)
        self.assertIn("if: ${{ env.PREVIEW_DATA_MODE == 'reset' }}", workflow)
        self.assertIn("-destroy", workflow)
        self.assertIn("Terraform apply reset plan", workflow)
        self.assertIn("Wait for preview database ID reuse", workflow)
        self.assertIn("sleep 330", workflow)
        self.assertIn('terraform -chdir="${PREVIEW_TERRAFORM_DIR}" plan', workflow)
        self.assertIn('-var="preview_catalog_viewer_image=${PREVIEW_CATALOG_VIEWER_IMAGE}"', workflow)
        self.assertIn('terraform -chdir="${PREVIEW_TERRAFORM_DIR}" apply', workflow)
        self.assertIn("Enforce preview resource-change allowlist", workflow)
        self.assertIn("google_storage_bucket.preview_bucket", workflow)
        self.assertIn("google_firestore_database.feature_preview", workflow)
        self.assertIn("google_cloud_run_v2_service.feature_preview_service", workflow)
        self.assertIn("google_cloud_run_v2_service.feature_preview_catalog_viewer", workflow)
        self.assertIn("def require_env_value", workflow)
        self.assertIn("if actual is not None and actual != expected_value", workflow)
        self.assertIn(
            'require_env_value(value_violations, address, env, "SHARED_DATASETS_BUCKET", expected["bucket"])',
            workflow,
        )
        self.assertIn(
            'require_env_value(value_violations, address, env, "SHARED_DATASETS_SITE_PREFIX", "_catalog/web")',
            workflow,
        )
        self.assertIn('"preview Firestore database"', workflow)
        self.assertIn('"preview collection root"', workflow)
        self.assertIn('terraform -chdir="${PREVIEW_TERRAFORM_DIR}" output preview_service_uri', workflow)
        self.assertIn('terraform -chdir="${PREVIEW_TERRAFORM_DIR}" output preview_catalog_viewer_uri', workflow)
        self.assertIn("Collect preview release indexes", workflow)
        self.assertIn('gcloud storage ls "gs://${SHARED_DATASETS_BUCKET}/_catalog/releases/*.json"', workflow)
        self.assertIn('gcloud storage cp "gs://${SHARED_DATASETS_BUCKET}/_catalog/releases/*.json"', workflow)
        self.assertIn("Preview data reset requested; building catalog without prior release indexes.", workflow)
        self.assertIn("Build preview catalog web bundle", workflow)
        self.assertIn("--release-index-assets-only", workflow)
        self.assertIn("--latest-from-release-index", workflow)
        self.assertIn("--force-access-tier private", workflow)
        self.assertIn("Publish preview catalog web bundle", workflow)
        self.assertNotIn("terraform -chdir=terraform/envs/prod", workflow)
        self.assertNotIn("-target=", workflow)
        self.assertNotIn("Enforce preview reset resource-change allowlist", workflow)
        self.assertNotIn("Refusing preview reset", workflow)
        deploy_create_section = workflow.split("      - name: Terraform plan", 1)[1]
        self.assertNotIn("google_project_iam_member.feature_preview_service_firestore_viewer", deploy_create_section)
        self.assertNotIn("google_project_iam_member.feature_preview_loader_firestore_user", deploy_create_section)
        self.assertNotIn("module.feature_preview_service_account.", deploy_create_section)
        self.assertNotIn("module.feature_preview_loader_service_account.", deploy_create_section)

    def test_preview_destroy_workflow_only_runs_destroy_path(self):
        workflow = PREVIEW_DESTROY_WORKFLOW.read_text()

        self.assertIn("name: Destroy Preview Environment", workflow)
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("environment: shared-datasets-production", workflow)
        self.assertIn("name: Destroy feature branch preview", workflow)
        self.assertIn("Validate main ref", workflow)
        self.assertIn('GITHUB_REF}" != "refs/heads/main"', workflow)
        self.assertIn("Destroy Preview Environment may only apply from main", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("terraform -chdir=terraform/envs/preview init", workflow)
        self.assertIn("Release stable preview bootstrap state", workflow)
        self.assertIn("terraform -chdir=terraform/envs/preview state rm", workflow)
        self.assertIn("module.feature_preview_service_account.google_service_account.this", workflow)
        self.assertIn("module.feature_preview_loader_service_account.google_service_account.this", workflow)
        self.assertIn("google_service_account_iam_member.feature_preview_loader_github_wif", workflow)
        self.assertIn("terraform -chdir=terraform/envs/preview plan", workflow)
        self.assertIn("-destroy", workflow)
        self.assertIn("terraform -chdir=terraform/envs/preview apply", workflow)
        self.assertIn("Enforce preview resource-change allowlist", workflow)
        self.assertIn("google_storage_bucket.preview_bucket", workflow)
        self.assertIn("google_firestore_database.feature_preview", workflow)
        self.assertIn("google_cloud_run_v2_service.feature_preview_service", workflow)
        self.assertIn("google_cloud_run_v2_service.feature_preview_catalog_viewer", workflow)
        self.assertNotIn("preview-source", workflow)
        self.assertNotIn("docker build", workflow)
        self.assertNotIn("feature_preview_service_image=", workflow)
        self.assertNotIn("terraform -chdir=terraform/envs/prod", workflow)
        self.assertNotIn("-target=", workflow)
        self.assertNotIn("legacy_preview_project_iam_exact", workflow)
        self.assertNotIn("feature-preview-service@shared-datasets-1.iam.gserviceaccount.com", workflow)
        self.assertNotIn("feature-preview-loader@shared-datasets-1.iam.gserviceaccount.com", workflow)
        self.assertNotIn("condition must be scoped to", workflow)
        self.assertNotIn("feature_preview_service_firestore_viewer", workflow)
        self.assertNotIn("feature_preview_loader_firestore_user", workflow)
        destroy_allowlist_section = workflow.split("      - name: Enforce preview resource-change allowlist", 1)[1]
        self.assertNotIn("module.feature_preview_service_account.", destroy_allowlist_section)
        self.assertNotIn("module.feature_preview_loader_service_account.", destroy_allowlist_section)
        self.assertNotIn("google_service_account_iam_member.feature_preview_loader_github_wif", destroy_allowlist_section)

    def test_preview_deploy_workflow_allowlist_patterns_match_real_addresses(self):
        workflow = PREVIEW_WORKFLOW.read_text()
        patterns = [
            re.compile(pattern)
            for pattern in re.findall(r're\.compile\(r"([^"]+)"\)', workflow)
        ]

        self.assertFalse(
            any(
                pattern.match("module.feature_preview_service_account.google_service_account.this")
                for pattern in patterns
            )
        )
        self.assertFalse(
            any(
                pattern.match("module.feature_preview_loader_service_account.google_service_account.this")
                for pattern in patterns
            )
        )
        self.assertTrue(
            any(
                pattern.match('google_iap_web_cloud_run_service_iam_member.feature_preview_service_accessors["user:jona@skytruth.org"]')
                for pattern in patterns
            )
        )
        self.assertTrue(
            any(
                pattern.match(
                    'google_iap_web_cloud_run_service_iam_member.feature_preview_catalog_viewer_accessors["user:jona@skytruth.org"]'
                )
                for pattern in patterns
            )
        )
        self.assertFalse(
            any(pattern.match("google_iap_web_cloud_run_service_iam_member.feature_preview_service_accessors") for pattern in patterns)
        )
        self.assertFalse(
            any(
                pattern.match("google_iap_web_cloud_run_service_iam_member.feature_preview_catalog_viewer_accessors")
                for pattern in patterns
            )
        )

    def test_preview_index_load_uses_preview_bucket_database_and_source_ref(self):
        workflow = PREVIEW_INDEX_LOAD_WORKFLOW.read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("Validate main ref", workflow)
        self.assertIn('GITHUB_REF}" != "refs/heads/main"', workflow)
        self.assertIn("Check out preview control plane", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("Check out requested source ref", workflow)
        self.assertIn("path: preview-source", workflow)
        self.assertIn("SHARED_DATASETS_BUCKET: skytruth-shared-datasets-1-preview", workflow)
        self.assertIn("feature-preview-loader@shared-datasets-1.iam.gserviceaccount.com", workflow)
        self.assertIn("FEATURE_PREVIEW_FIRESTORE_DATABASE: feature-preview", workflow)
        self.assertIn("FEATURE_PREVIEW_COLLECTION_ROOT: feature_preview_index", workflow)
        self.assertIn("CATALOG_WEB_CACHE_CONTROL", workflow)
        self.assertIn("group: feature-preview-index-load", workflow)
        self.assertIn("Preview-bucket release .metadata.ndjson.gz URI.", workflow)
        self.assertIn('"SIDECAR_URI": ".metadata.ndjson.gz"', workflow)
        self.assertIn('f"{asset_slug}.metadata.ndjson.gz"', workflow)
        self.assertIn("Verify requested source ref supports preview Firestore database", workflow)
        self.assertIn("working-directory: preview-source", workflow)
        self.assertIn('release_prefix = f"gs://{bucket}/{asset_root}/releases/{release}/"', workflow)
        self.assertIn(
            'gs://{bucket}/000-system/feature-preview/index-loads/{asset_slug}/{release}/{load_id}.json',
            workflow,
        )
        self.assertIn('RELEASE_INDEX_URI": f"gs://{bucket}/_catalog/releases/{asset_slug}.json"', workflow)
        self.assertIn('uv run python scripts/gcs_asset.py stat "${RELEASE_INDEX_URI}"', workflow)
        self.assertIn("--collection-root \"${FEATURE_PREVIEW_COLLECTION_ROOT}\"", workflow)
        self.assertIn("SHARED_DATASETS_ALLOW_CANONICAL_MUTATION: \"1\"", workflow)
        self.assertIn("Download preview release indexes", workflow)
        self.assertIn('gcloud storage cp "gs://${SHARED_DATASETS_BUCKET}/_catalog/releases/*.json"', workflow)
        self.assertIn("Build refreshed preview catalog web bundle", workflow)
        self.assertIn("--release-index-assets-only", workflow)
        self.assertIn("--latest-from-release-index", workflow)
        self.assertIn("--force-access-tier private", workflow)
        self.assertIn("Publish refreshed preview catalog web bundle", workflow)
        self.assertIn('scripts/catalog_web_publish.py', workflow)
        self.assertNotIn("skytruth-shared-datasets-1/", workflow)
        self.assertNotIn("--replace-generation", workflow)
        self.assertNotIn("--unsafe-overwrite", workflow)

    def test_main_contains_preview_source_mechanics(self):
        service_run = REPO_ROOT / "services/feature_preview_service/run.py"
        service_dockerfile = REPO_ROOT / "services/feature_preview_service/Dockerfile"
        catalog_viewer_run = REPO_ROOT / "services/catalog_viewer/run.py"
        catalog_viewer_dockerfile = REPO_ROOT / "services/catalog_viewer/Dockerfile"
        index_loader = REPO_ROOT / "scripts/feature_preview_index.py"

        self.assertTrue(service_run.exists())
        self.assertTrue(service_dockerfile.exists())
        self.assertTrue(catalog_viewer_run.exists())
        self.assertTrue(catalog_viewer_dockerfile.exists())
        self.assertTrue(index_loader.exists())
        self.assertIn("FEATURE_PREVIEW_FIRESTORE_DATABASE", service_run.read_text())
        self.assertIn("feature_preview_run.GcsSidecarFeatureIndex", catalog_viewer_run.read_text())
        self.assertIn("FEATURE_PREVIEW_COLLECTION_ROOT", catalog_viewer_run.read_text())
        self.assertIn("FEATURE_PREVIEW_FIRESTORE_DATABASE", index_loader.read_text())
        self.assertIn("google-cloud-firestore", service_dockerfile.read_text())
        self.assertIn("google-cloud-firestore", catalog_viewer_dockerfile.read_text())

    def test_prod_terraform_sync_workflows_share_state_concurrency(self):
        for workflow_path in (
            ARTIFACT_REGISTRY_IAM_WORKFLOW,
            PREVIEW_TERRAFORM_IAM_WORKFLOW,
            PMTILES_CDN_SYNC_WORKFLOW,
            SCRATCH_CLEANUP_IAM_SYNC_WORKFLOW,
        ):
            with self.subTest(workflow=workflow_path.name):
                workflow = workflow_path.read_text()

                self.assertIn("group: prod-terraform-state", workflow)
                self.assertIn("cancel-in-progress: false", workflow)

    def test_artifact_registry_iam_sync_grants_preview_image_push_only(self):
        artifact_registry_tf = (PROD_TF / "artifact_registry_iam.tf").read_text()
        workflow = ARTIFACT_REGISTRY_IAM_WORKFLOW.read_text()
        custom_role = re.search(
            r'resource "google_project_iam_custom_role" "artifact_registry_iam_policy_manager" \{(?P<body>.*?)\n\}',
            artifact_registry_tf,
            re.DOTALL,
        )
        self.assertIsNotNone(custom_role)
        permissions = re.search(r"permissions = \[(?P<body>.*?)\n  \]", custom_role.group("body"), re.DOTALL)
        self.assertIsNotNone(permissions)

        self.assertIn(
            'resource "google_artifact_registry_repository_iam_member" "github_actions_artifact_registry_writer"',
            artifact_registry_tf,
        )
        self.assertEqual(
            [
                "artifactregistry.repositories.getIamPolicy",
                "artifactregistry.repositories.setIamPolicy",
            ],
            re.findall(r'"([^"]+)"', permissions.group("body")),
        )
        self.assertIn(
            'resource "google_project_iam_member" "github_actions_artifact_registry_iam_policy_manager"',
            artifact_registry_tf,
        )
        self.assertIn("google_project_iam_custom_role.artifact_registry_iam_policy_manager.name", artifact_registry_tf)
        self.assertIn("google_artifact_registry_repository.jobs.repository_id", artifact_registry_tf)
        self.assertIn("roles/artifactregistry.writer", artifact_registry_tf)
        self.assertIn("var.github_actions_terraform_service_account_email", artifact_registry_tf)
        self.assertIn("google_project_iam_member.github_actions_artifact_registry_iam_policy_manager", artifact_registry_tf)
        self.assertIn("Artifact Registry IAM sync", workflow)
        self.assertIn("shared-datasets-production", workflow)
        self.assertIn("Validate main ref", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("group: prod-terraform-state", workflow)
        self.assertIn("terraform -chdir=terraform/envs/prod plan", workflow)
        self.assertIn(
            "-target=google_project_iam_custom_role.artifact_registry_iam_policy_manager",
            workflow,
        )
        self.assertIn(
            "-target=google_project_iam_member.github_actions_artifact_registry_iam_policy_manager",
            workflow,
        )
        self.assertIn(
            "-target=google_artifact_registry_repository_iam_member.github_actions_artifact_registry_writer",
            workflow,
        )
        self.assertIn("allowed_exact", workflow)
        self.assertIn(
            "google_project_iam_custom_role.artifact_registry_iam_policy_manager",
            workflow,
        )
        self.assertIn(
            "google_project_iam_member.github_actions_artifact_registry_iam_policy_manager",
            workflow,
        )
        self.assertIn(
            "google_artifact_registry_repository_iam_member.github_actions_artifact_registry_writer",
            workflow,
        )
        self.assertIn("Terraform apply Artifact Registry IAM policy manager bootstrap", workflow)
        self.assertIn("Wait for Artifact Registry IAM policy manager propagation", workflow)
        self.assertIn("sleep 30", workflow)
        self.assertIn("Terraform apply Artifact Registry writer binding", workflow)
        self.assertIn("Refusing automatic Artifact Registry IAM bootstrap", workflow)
        self.assertIn("Refusing automatic Artifact Registry IAM writer sync", workflow)
        self.assertNotIn("roles/artifactregistry.admin", artifact_registry_tf)
        self.assertNotIn("roles/artifactregistry.admin", workflow)

    def test_preview_terraform_iam_sync_uses_custom_role_and_allowlist(self):
        preview_terraform_tf = (PROD_TF / "preview_terraform_iam.tf").read_text()
        workflow = PREVIEW_TERRAFORM_IAM_WORKFLOW.read_text()

        self.assertIn("import {", preview_terraform_tf)
        self.assertIn("projects/shared-datasets-1/roles/sharedDatasetsPreviewTerraform", preview_terraform_tf)
        self.assertNotIn("serviceAccounts/feature-preview-service", preview_terraform_tf)
        self.assertNotIn("serviceAccounts/feature-preview-loader", preview_terraform_tf)
        self.assertIn('role_id     = "sharedDatasetsPreviewTerraform"', preview_terraform_tf)
        self.assertIn('module "feature_preview_service_account"', preview_terraform_tf)
        self.assertIn('module "feature_preview_loader_service_account"', preview_terraform_tf)
        self.assertIn(
            'resource "google_service_account_iam_member" "feature_preview_loader_github_wif"',
            preview_terraform_tf,
        )
        self.assertIn(
            'resource "google_service_account_iam_member" "feature_preview_service_self_sign_blob"',
            preview_terraform_tf,
        )
        self.assertIn("google_project_iam_custom_role.catalog_viewer_sign_blob.name", preview_terraform_tf)
        self.assertIn(
            'resource "google_project_iam_member" "github_actions_preview_terraform"',
            preview_terraform_tf,
        )
        self.assertIn(
            'resource "google_project_iam_member" "feature_preview_service_firestore_viewer"',
            preview_terraform_tf,
        )
        self.assertIn(
            'resource "google_project_iam_member" "feature_preview_loader_firestore_user"',
            preview_terraform_tf,
        )
        self.assertIn('account_id   = "feature-preview-service"', preview_terraform_tf)
        self.assertIn('account_id   = "feature-preview-loader"', preview_terraform_tf)
        self.assertIn("databases/feature-preview", preview_terraform_tf)
        self.assertIn("storage.buckets.create", preview_terraform_tf)
        self.assertIn("datastore.databases.create", preview_terraform_tf)
        self.assertIn("datastore.databases.getMetadata", preview_terraform_tf)
        self.assertIn("datastore.locations.get", preview_terraform_tf)
        self.assertIn("iam.serviceAccounts.create", preview_terraform_tf)
        self.assertNotIn("iam.serviceAccounts.delete", preview_terraform_tf)
        self.assertIn("run.services.create", preview_terraform_tf)
        self.assertIn("iap.webServices.setIamPolicy", preview_terraform_tf)
        self.assertIn("resourcemanager.projects.setIamPolicy", preview_terraform_tf)
        self.assertNotIn("roles/storage.admin", preview_terraform_tf)
        self.assertNotIn("roles/editor", preview_terraform_tf)
        self.assertIn("Preview Terraform IAM sync", workflow)
        self.assertIn("shared-datasets-production", workflow)
        self.assertIn("Validate main ref", workflow)
        self.assertIn('GITHUB_REF}" != "refs/heads/main"', workflow)
        self.assertIn("Preview Terraform IAM sync may only apply from main", workflow)
        self.assertIn("ref: main", workflow)
        self.assertIn("terraform/envs/prod/main.tf", workflow)
        self.assertIn("terraform/envs/prod/preview_terraform_iam.tf", workflow)
        self.assertIn("terraform -chdir=terraform/envs/prod plan", workflow)
        self.assertIn(
            "-target=google_project_iam_custom_role.preview_terraform",
            workflow,
        )
        self.assertIn(
            "-target=module.feature_preview_service_account.google_service_account.this",
            workflow,
        )
        self.assertIn(
            "-target=module.feature_preview_loader_service_account.google_service_account.this",
            workflow,
        )
        self.assertIn(
            "-target=google_project_iam_member.github_actions_preview_terraform",
            workflow,
        )
        self.assertIn(
            "-target=google_service_account_iam_member.feature_preview_loader_github_wif",
            workflow,
        )
        self.assertIn(
            "-target=google_service_account_iam_member.feature_preview_service_self_sign_blob",
            workflow,
        )
        self.assertIn(
            "-target=google_project_iam_member.feature_preview_service_firestore_viewer",
            workflow,
        )
        self.assertIn(
            "-target=google_project_iam_member.feature_preview_loader_firestore_user",
            workflow,
        )
        self.assertIn("allowed_exact", workflow)
        self.assertIn(
            "google_project_iam_custom_role.preview_terraform",
            workflow,
        )
        self.assertIn(
            "module.feature_preview_service_account.google_service_account.this",
            workflow,
        )
        self.assertIn(
            "module.feature_preview_loader_service_account.google_service_account.this",
            workflow,
        )
        self.assertIn(
            "google_project_iam_member.github_actions_preview_terraform",
            workflow,
        )
        self.assertIn(
            "google_service_account_iam_member.feature_preview_loader_github_wif",
            workflow,
        )
        self.assertIn(
            "google_service_account_iam_member.feature_preview_service_self_sign_blob",
            workflow,
        )
        self.assertIn(
            "google_project_iam_member.feature_preview_service_firestore_viewer",
            workflow,
        )
        self.assertIn(
            "google_project_iam_member.feature_preview_loader_firestore_user",
            workflow,
        )
        self.assertIn('actions == ["delete"]', workflow)
        self.assertIn("Refusing automatic preview Terraform IAM sync", workflow)
        self.assertNotIn("roles/storage.admin", workflow)
        self.assertNotIn("roles/editor", workflow)

if __name__ == "__main__":
    unittest.main()
