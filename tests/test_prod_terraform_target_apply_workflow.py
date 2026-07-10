from __future__ import annotations

import unittest
from pathlib import Path

from workflow_helpers import (
    assert_target_apply_caller,
    load_workflow,
    workflow_steps_by_name,
    workflow_triggers,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REUSABLE = REPO_ROOT / ".github/workflows/prod-terraform-target-apply.yml"
ARTIFACT_REGISTRY = REPO_ROOT / ".github/workflows/artifact-registry-iam-sync.yml"
METADATA_INDEX_LOADER = REPO_ROOT / ".github/workflows/metadata-index-loader-iam-sync.yml"
PREVIEW_TERRAFORM = REPO_ROOT / ".github/workflows/preview-terraform-iam-sync.yml"
SCHEDULED_INGESTION = REPO_ROOT / ".github/workflows/scheduled-ingestion-deploy-iam-sync.yml"
SCRATCH_CLEANUP = REPO_ROOT / ".github/workflows/scratch-cleanup-iam-sync.yml"

REUSABLE_PATH_ENTRY = ".github/workflows/prod-terraform-target-apply.yml"


class ReusableTargetApplyWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.workflow = load_workflow(REUSABLE)
        self.trigger = workflow_triggers(self.workflow)
        self.job = self.workflow["jobs"]["sync"]
        self.steps = workflow_steps_by_name(self.workflow, "sync")

    def test_only_callable_and_protected(self):
        self.assertEqual(list(self.trigger), ["workflow_call"])
        self.assertEqual(self.job["environment"], "shared-datasets-production")
        self.assertEqual(
            self.job["concurrency"],
            {"group": "prod-terraform-state", "cancel-in-progress": False},
        )
        self.assertEqual(self.steps["Check out repository"]["with"]["ref"], "main")
        self.assertIn('GITHUB_REF}" != "refs/heads/main"', self.steps["Validate main ref"]["run"])
        self.assertIn("may only apply from main", self.steps["Validate main ref"]["run"])
        self.assertIn(
            "Missing repository variable: GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER",
            self.steps["Validate Terraform auth configuration"]["run"],
        )
        self.assertIn(
            "Missing repository variable: GCP_TERRAFORM_SERVICE_ACCOUNT",
            self.steps["Validate Terraform auth configuration"]["run"],
        )
        self.assertIn(
            "GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER",
            self.workflow["env"]["TERRAFORM_WORKLOAD_IDENTITY_PROVIDER"],
        )
        self.assertIn("GCP_TERRAFORM_SERVICE_ACCOUNT", self.workflow["env"]["TERRAFORM_SERVICE_ACCOUNT"])

    def test_terraform_dir_is_restricted_to_prod(self):
        inputs = self.trigger["workflow_call"]["inputs"]
        self.assertEqual(inputs["terraform_dir"]["default"], "terraform/envs/prod")
        validate_run = self.steps["Validate Terraform directory"]["run"]
        self.assertIn("^terraform/envs/prod(/[A-Za-z0-9_./-]+)?$", validate_run)
        self.assertIn("terraform_dir must stay under terraform/envs/prod", validate_run)

    def test_plan_is_targeted_and_allowlist_enforced_before_apply(self):
        plan_run = self.steps["Terraform plan"]["run"]
        enforce_run = self.steps["Enforce resource-change allowlist"]["run"]
        enforce_env = self.steps["Enforce resource-change allowlist"]["env"]
        apply_run = self.steps["Terraform apply"]["run"]
        step_names = [step["name"] for step in self.job["steps"] if "name" in step]

        self.assertIn("-refresh=false", plan_run)
        self.assertIn('plan_args+=("-target=${target}")', plan_run)
        self.assertIn('plan_args+=("-var=${tf_var}")', plan_run)
        self.assertIn("scripts/terraform_plan_allowlist.py", enforce_run)
        self.assertIn("--allowed-exact", enforce_run)
        self.assertEqual(enforce_env["ALLOWED_EXACT"], "${{ inputs.allowed_exact }}")
        self.assertIn('terraform -chdir="${TERRAFORM_DIR}" apply -input=false', apply_run)
        self.assertLess(step_names.index("Terraform plan"), step_names.index("Enforce resource-change allowlist"))
        self.assertLess(step_names.index("Enforce resource-change allowlist"), step_names.index("Terraform apply"))

    def test_optional_post_apply_wait(self):
        wait_step = self.steps["Wait after apply"]
        self.assertEqual(wait_step["if"], "${{ inputs.post_apply_wait_seconds > 0 }}")
        self.assertIn('sleep "${WAIT_SECONDS}"', wait_step["run"])


class TargetApplyCallerTests(unittest.TestCase):
    def test_scheduled_ingestion_deploy_iam_sync_caller(self):
        assert_target_apply_caller(
            self,
            SCHEDULED_INGESTION,
            expected_name="Scheduled ingestion deploy IAM sync",
            push_paths={
                ".github/workflows/scheduled-ingestion-deploy-iam-sync.yml",
                REUSABLE_PATH_ENTRY,
                "terraform/envs/prod/main.tf",
                "terraform/envs/prod/scheduled_ingestion_deploy_iam.tf",
                "terraform/envs/prod/variables.tf",
                "terraform/envs/prod/versions.tf",
            },
            sync_name="Scheduled ingestion deploy IAM sync",
            refusal_prefix="Refusing automatic scheduled ingestion deploy IAM sync",
            expected_targets={
                "google_project_iam_custom_role.scheduled_ingestion_deployer",
                "google_project_iam_member.github_actions_scheduled_ingestion_deployer",
            },
            expected_tf_vars={
                "wdpa_monthly_image=unused-by-scheduled-ingestion-deploy-iam-sync",
                "sea_ice_daily_image=unused-by-scheduled-ingestion-deploy-iam-sync",
                "eamlis_monthly_image=unused-by-scheduled-ingestion-deploy-iam-sync",
            },
        )

    def test_metadata_index_loader_iam_sync_caller(self):
        expected_targets = {
            'google_project_service.required["firestore.googleapis.com"]',
            "google_firestore_database.feature_metadata",
            "module.metadata_index_loader_service_account.google_service_account.this",
            "google_project_iam_member.metadata_index_loader_firestore_user",
            "google_storage_bucket_iam_member.metadata_index_loader_object_viewer",
            "google_storage_bucket_iam_member.metadata_index_loader_index_load_creator",
            "google_storage_bucket_iam_member.metadata_index_loader_index_load_folder_admin",
            "google_service_account_iam_member.metadata_index_loader_github_wif",
        }
        assert_target_apply_caller(
            self,
            METADATA_INDEX_LOADER,
            expected_name="Feature metadata index-loader IAM sync",
            push_paths={
                ".github/workflows/metadata-index-loader-iam-sync.yml",
                REUSABLE_PATH_ENTRY,
                "terraform/envs/prod/main.tf",
                "terraform/envs/prod/metadata_service.tf",
                "terraform/envs/prod/variables.tf",
                "terraform/envs/prod/versions.tf",
            },
            sync_name="Metadata index-loader IAM sync",
            refusal_prefix="Refusing metadata index-loader IAM sync",
            expected_targets=expected_targets,
            expected_allowed_exact=expected_targets
            - {"module.metadata_index_loader_service_account.google_service_account.this"},
            expected_allowed_patterns={r"^module\.metadata_index_loader_service_account\."},
            expected_tf_vars={
                "metadata_service_image=unused-by-metadata-index-loader-iam-sync",
                "wdpa_monthly_image=unused-by-metadata-index-loader-iam-sync",
                "sea_ice_daily_image=unused-by-metadata-index-loader-iam-sync",
                "eamlis_monthly_image=unused-by-metadata-index-loader-iam-sync",
            },
            blocked_resources={"google_cloud_run_v2_service.metadata_service"},
        )

    def test_preview_terraform_iam_sync_caller_blocks_deletes(self):
        assert_target_apply_caller(
            self,
            PREVIEW_TERRAFORM,
            expected_name="Preview Terraform IAM sync",
            push_paths={
                ".github/workflows/preview-terraform-iam-sync.yml",
                REUSABLE_PATH_ENTRY,
                "terraform/envs/prod/main.tf",
                "terraform/envs/prod/preview_terraform_iam.tf",
                "terraform/envs/prod/variables.tf",
                "terraform/envs/prod/versions.tf",
            },
            sync_name="Preview Terraform IAM sync",
            refusal_prefix="Refusing automatic preview Terraform IAM sync",
            expected_block_deletes=True,
            expected_targets={
                "google_project_iam_custom_role.preview_terraform",
                "module.feature_preview_service_account.google_service_account.this",
                "module.feature_preview_loader_service_account.google_service_account.this",
                "google_project_iam_member.github_actions_preview_terraform",
                "google_service_account_iam_member.feature_preview_loader_github_wif",
                "google_service_account_iam_member.feature_preview_service_self_sign_blob",
                "google_project_iam_member.feature_preview_service_firestore_viewer",
                "google_project_iam_member.feature_preview_loader_firestore_user",
            },
            expected_tf_vars={
                "wdpa_monthly_image=unused-by-preview-terraform-iam-sync",
                "sea_ice_daily_image=unused-by-preview-terraform-iam-sync",
                "eamlis_monthly_image=unused-by-preview-terraform-iam-sync",
            },
        )

    def test_scratch_cleanup_iam_sync_caller_uses_isolated_terraform_dir(self):
        assert_target_apply_caller(
            self,
            SCRATCH_CLEANUP,
            expected_name="Scratch cleanup IAM sync",
            push_paths={
                REUSABLE_PATH_ENTRY,
                "terraform/envs/prod/canonical_mutation_iam.tf",
                "terraform/envs/prod/scratch_cleanup_iam_sync/**",
                "terraform/envs/prod/variables.tf",
                "terraform/envs/prod/versions.tf",
            },
            sync_name="Scratch cleanup IAM sync",
            refusal_prefix="Refusing automatic scratch cleanup IAM sync",
            expected_terraform_dir="terraform/envs/prod/scratch_cleanup_iam_sync",
            expected_targets={
                "google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_viewer",
                "google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_cleanup_user",
            },
            blocked_resources={
                "google_compute_url_map.pmtiles_cdn",
                "google_storage_bucket.shared_bucket",
                "google_storage_managed_folder.shared_bucket_public_prefixes",
            },
        )

    def test_artifact_registry_iam_sync_runs_bootstrap_then_writer(self):
        common_paths = {
            ".github/workflows/artifact-registry-iam-sync.yml",
            REUSABLE_PATH_ENTRY,
            "terraform/envs/prod/artifact_registry_iam.tf",
            "terraform/envs/prod/main.tf",
            "terraform/envs/prod/variables.tf",
            "terraform/envs/prod/versions.tf",
        }
        tf_vars = {
            "wdpa_monthly_image=unused-by-artifact-registry-iam-sync",
            "sea_ice_daily_image=unused-by-artifact-registry-iam-sync",
            "eamlis_monthly_image=unused-by-artifact-registry-iam-sync",
        }
        assert_target_apply_caller(
            self,
            ARTIFACT_REGISTRY,
            expected_name="Artifact Registry IAM sync",
            push_paths=common_paths,
            job_name="bootstrap",
            sync_name="Artifact Registry IAM sync",
            refusal_prefix="Refusing automatic Artifact Registry IAM bootstrap",
            expected_post_apply_wait_seconds=30,
            expected_targets={
                "google_project_iam_custom_role.artifact_registry_iam_policy_manager",
                "google_project_iam_member.github_actions_artifact_registry_iam_policy_manager",
            },
            expected_tf_vars=tf_vars,
        )
        assert_target_apply_caller(
            self,
            ARTIFACT_REGISTRY,
            expected_name="Artifact Registry IAM sync",
            push_paths=common_paths,
            job_name="writer",
            expected_job_if=None,
            expected_needs="bootstrap",
            sync_name="Artifact Registry writer binding sync",
            refusal_prefix="Refusing automatic Artifact Registry IAM writer sync",
            expected_targets={
                "google_artifact_registry_repository_iam_member.github_actions_artifact_registry_writer",
            },
            expected_tf_vars=tf_vars,
            blocked_resources={"roles/artifactregistry.admin"},
        )


if __name__ == "__main__":
    unittest.main()
