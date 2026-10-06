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
PREVIEW_TERRAFORM = REPO_ROOT / ".github/workflows/preview-terraform-iam-sync.yml"
SCHEDULED_INGESTION = REPO_ROOT / ".github/workflows/scheduled-ingestion-deploy-iam-sync.yml"
SCRATCH_CLEANUP = REPO_ROOT / ".github/workflows/scratch-cleanup-iam-sync.yml"



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
            {"group": "prod-terraform-state", "queue": "max", "cancel-in-progress": False},
        )
        self.assertEqual(self.steps["Check out repository"]["with"]["ref"], "${{ inputs.executor_sha }}")
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

    def test_every_mutation_requires_trusted_source_proof_and_a_record(self):
        inputs = self.trigger["workflow_call"]["inputs"]
        for key in ("caller_workflow", "executor_sha", "source_run_id", "source_run_attempt", "readiness_target"):
            self.assertTrue(inputs[key]["required"], key)
        names = list(self.steps)
        self.assertEqual(self.steps["Check out trusted verifier"]["with"]["ref"], "${{ github.workflow_sha }}")
        self.assertLess(names.index("Verify executor before candidate checkout"), names.index("Check out repository"))
        self.assertLess(names.index("Verify tested main revision"), names.index("Authenticate to Google Cloud"))
        self.assertLess(names.index("Claim tested deployment revision"), names.index("Terraform apply"))
        self.assertNotIn("if", self.steps["Verify executor before candidate checkout"])
        self.assertNotIn("if", self.steps["Verify tested main revision"])
        self.assertEqual(self.steps["Terraform apply"]["if"], "${{ steps.revision.outputs.proceed == 'true' }}")
        self.assertIn('--workflow "$CALLER_WORKFLOW"', self.steps["Verify tested main revision"]["run"])
        self.assertIn('--plan-scope "$TARGETS"', self.steps["Claim tested deployment revision"]["run"])

    def test_terraform_dir_is_restricted_to_prod(self):
        inputs = self.trigger["workflow_call"]["inputs"]
        self.assertEqual(inputs["terraform_dir"]["default"], "terraform/envs/prod")
        validate_run = self.steps["Validate Terraform directory"]["run"]
        self.assertIn('"${TERRAFORM_DIR}" == *..*', validate_run)
        self.assertIn("^terraform/envs/prod(/[A-Za-z0-9_.-]+)*$", validate_run)
        self.assertIn("terraform_dir must stay under terraform/envs/prod", validate_run)

    def test_plan_is_targeted_and_allowlist_enforced_before_apply(self):
        plan_run = self.steps["Terraform plan"]["run"]
        enforce_run = self.steps["Enforce resource-change allowlist"]["run"]
        enforce_env = self.steps["Enforce resource-change allowlist"]["env"]
        apply_run = self.steps["Terraform apply"]["run"]
        step_names = [step["name"] for step in self.job["steps"] if "name" in step]

        self.assertIn("-refresh=true", plan_run)
        self.assertNotIn("-refresh=false", plan_run)
        self.assertIn('plan_args+=("-target=${target}")', plan_run)
        self.assertIn('plan_args+=("-var=${tf_var}")', plan_run)
        self.assertIn("scripts/terraform_plan_allowlist.py", enforce_run)
        self.assertIn("--allowed-exact", enforce_run)
        self.assertEqual(enforce_env["ALLOWED_EXACT"], "${{ inputs.allowed_exact }}")
        self.assertIn('terraform_retry.sh" -chdir="${TERRAFORM_DIR}" apply -input=false', apply_run)
        self.assertLess(step_names.index("Terraform plan"), step_names.index("Enforce resource-change allowlist"))
        self.assertLess(step_names.index("Enforce resource-change allowlist"), step_names.index("Terraform apply"))

    def test_optional_post_apply_wait(self):
        wait_step = self.steps["Wait after apply"]
        self.assertIn("inputs.post_apply_wait_seconds > 0", wait_step["if"])
        self.assertIn("steps.revision.outputs.proceed == 'true'", wait_step["if"])
        self.assertIn('sleep "${WAIT_SECONDS}"', wait_step["run"])


class TargetApplyCallerTests(unittest.TestCase):
    def test_scheduled_ingestion_deploy_iam_sync_caller(self):
        assert_target_apply_caller(
            self,
            SCHEDULED_INGESTION,
            expected_name="Scheduled ingestion deploy IAM sync",
            push_paths=None,
            sync_name="Scheduled ingestion deploy IAM sync",
            expected_needs="bootstrap",
            refusal_prefix="Refusing automatic scheduled ingestion deploy IAM sync",
            expected_targets={
                "google_project_iam_custom_role.scheduled_ingestion_deployer",
                "google_project_iam_member.github_actions_scheduled_ingestion_deployer",
                "google_project_iam_custom_role.wdpa_observer_bootstrap",
                "google_project_iam_member.github_actions_wdpa_observer_bootstrap",
                "google_storage_bucket_iam_member.wdpa_reset_translation_reader",
                'google_secret_manager_secret_iam_member.translation_notice["wdpa"]',
                'google_secret_manager_secret_iam_member.translation_notice["eamlis"]',
                'google_storage_bucket_iam_member.translation_debt_writer["wdpa"]',
                'google_storage_bucket_iam_member.translation_debt_writer["eamlis"]',
                "google_storage_bucket_iam_member.publisher_translation_debt_writer",
            },
            expected_tf_vars={
                "wdpa_monthly_image=unused-by-scheduled-ingestion-deploy-iam-sync",
                "sea_ice_daily_image=unused-by-scheduled-ingestion-deploy-iam-sync",
                "eamlis_monthly_image=unused-by-scheduled-ingestion-deploy-iam-sync",
            },
        )

    def test_translation_notice_bootstrap_precedes_secret_reads_and_job_deploys(self):
        workflow = load_workflow(SCHEDULED_INGESTION)
        self.assertIn("workflow_call", workflow_triggers(workflow))
        bootstrap = workflow["jobs"]["bootstrap"]
        self.assertNotIn("needs", bootstrap)
        self.assertNotIn("concurrency", bootstrap)
        self.assertEqual(bootstrap["uses"], "./.github/workflows/prod-terraform-target-apply.yml")
        inputs = bootstrap["with"]
        expected = {
            "google_project_iam_custom_role.translation_notice_iam_manager",
            "google_project_iam_member.github_actions_translation_notice_iam_manager",
        }
        self.assertEqual(set(inputs["targets"].split()), expected)
        self.assertEqual(set(inputs["allowed_exact"].split()), expected)
        self.assertEqual(inputs["post_apply_wait_seconds"], 30)
        for name in ("eamlis-monthly-deploy.yml", "wdpa-monthly-deploy.yml"):
            with self.subTest(workflow=name):
                jobs = load_workflow(REPO_ROOT / ".github/workflows" / name)["jobs"]
                self.assertNotIn("iam", jobs)
                steps = workflow_steps_by_name({"jobs": jobs}, "deploy")
                self.assertIn("Verify live deployment permissions", steps)
                self.assertIn("Verify tested main revision", steps)


    def test_preview_terraform_iam_sync_caller_blocks_deletes(self):
        assert_target_apply_caller(
            self,
            PREVIEW_TERRAFORM,
            expected_name="Preview Terraform IAM sync",
            push_paths=None,
            sync_name="Preview Terraform IAM sync",
            refusal_prefix="Refusing automatic preview Terraform IAM sync",
            expected_block_deletes=True,
            expected_needs="bootstrap",
            expected_targets={
                "module.feature_preview_service_account.google_service_account.this",
                "module.feature_preview_loader_service_account.google_service_account.this",
                "google_service_account_iam_member.feature_preview_loader_github_wif",
                "google_service_account_iam_member.feature_preview_service_self_sign_blob",
            },
            expected_tf_vars={
                "wdpa_monthly_image=unused-by-preview-terraform-iam-sync",
                "sea_ice_daily_image=unused-by-preview-terraform-iam-sync",
                "eamlis_monthly_image=unused-by-preview-terraform-iam-sync",
            },
        )

    def test_preview_role_bootstrap_retains_existing_narrow_authority(self):
        workflow = load_workflow(PREVIEW_TERRAFORM)
        bootstrap = workflow["jobs"]["bootstrap"]["with"]
        self.assertEqual(set(bootstrap["targets"].split()), {
            "google_project_iam_custom_role.preview_terraform", "google_project_iam_member.github_actions_preview_terraform",
        })
        self.assertEqual(bootstrap["targets"], bootstrap["allowed_exact"])
        self.assertTrue(bootstrap["block_deletes"])
        self.assertEqual(bootstrap["readiness_target"], "iam-bootstrap")
        self.assertEqual(workflow["jobs"]["sync"]["with"]["readiness_target"], "preview-service-account-iam")

    def test_scratch_cleanup_iam_sync_caller_uses_full_prod_root(self):
        assert_target_apply_caller(
            self,
            SCRATCH_CLEANUP,
            expected_name="Scratch cleanup IAM sync",
            push_paths=None,
            sync_name="Scratch cleanup IAM sync",
            refusal_prefix="Refusing automatic scratch cleanup IAM sync",
            expected_targets={
                "google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_viewer",
                "google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_cleanup_user",
            },
            blocked_resources={
                "google_compute_url_map.pmtiles_cdn",
                "google_storage_bucket.shared_bucket",
                "google_storage_managed_folder.shared_bucket_public_prefixes",
            },
            expected_tf_vars={
                "wdpa_monthly_image=unused-by-scratch-cleanup-iam-sync",
                "sea_ice_daily_image=unused-by-scratch-cleanup-iam-sync",
                "eamlis_monthly_image=unused-by-scratch-cleanup-iam-sync",
            },
        )

    def test_artifact_registry_iam_sync_runs_bootstrap_then_writer(self):
        tf_vars = {
            "wdpa_monthly_image=unused-by-artifact-registry-iam-sync",
            "sea_ice_daily_image=unused-by-artifact-registry-iam-sync",
            "eamlis_monthly_image=unused-by-artifact-registry-iam-sync",
        }
        assert_target_apply_caller(
            self,
            ARTIFACT_REGISTRY,
            expected_name="Artifact Registry IAM sync",
            push_paths=None,
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
            push_paths=None,
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
