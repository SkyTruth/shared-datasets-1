from __future__ import annotations

import unittest
from pathlib import Path

from workflow_helpers import (
    load_workflow,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_TF_DIR = REPO_ROOT / "terraform/envs/prod"
SCHEDULED_INGESTION_DEPLOY_IAM_SYNC_WORKFLOW = (
    REPO_ROOT / ".github/workflows/scheduled-ingestion-deploy-iam-sync.yml"
)
GCLOUD_COMPOSITE_TEMP_PREFIX = "gcloud/tmp/parallel_composite_uploads/see_gcloud_storage_cp_help_for_details/"


def terraform_resource_block(text: str, resource_type: str, resource_name: str) -> str:
    start = text.index(f'resource "{resource_type}" "{resource_name}"')
    brace_start = text.index("{", start)
    depth = 0
    for offset, character in enumerate(text[brace_start:], start=brace_start):
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : offset + 1]
    raise AssertionError(f"resource block not closed: {resource_type}.{resource_name}")


def terraform_resource_blocks(text: str, resource_type: str) -> list[str]:
    blocks = []
    marker = f'resource "{resource_type}"'
    offset = 0
    while True:
        start = text.find(marker, offset)
        if start == -1:
            return blocks
        brace_start = text.index("{", start)
        depth = 0
        for block_end, character in enumerate(text[brace_start:], start=brace_start):
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start : block_end + 1])
                    offset = block_end + 1
                    break


class ScheduledIngestionIamTerraformTests(unittest.TestCase):
    def test_shared_bucket_conditions_cover_hns_folder_resources(self):
        iam_tf = (PROD_TF_DIR / "canonical_mutation_iam.tf").read_text()

        self.assertIn("shared_bucket_object_resource_prefix", iam_tf)
        self.assertIn("shared_bucket_folder_resource_prefix", iam_tf)
        self.assertIn(
            "canonical_mutation_publisher_folder_condition",
            iam_tf,
        )
        self.assertIn(
            "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}${prefix}')",
            iam_tf,
        )
        self.assertIn(
            'resource "google_storage_bucket_iam_member" "shared_datasets_publisher_folder_user"',
            iam_tf,
        )
        self.assertIn(
            "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/')",
            iam_tf,
        )
        self.assertIn(
            "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/pending-publishes/')",
            iam_tf,
        )

    def test_scheduled_ingestion_writers_can_create_folders_under_owned_roots(self):
        job_bindings = {
            "sea_ice_daily.tf": (
                "sea_ice_job_object_user",
                [
                    "200-imagery-derived/250-weather-climate/ims-sea-ice-extent/",
                    "_catalog/releases/",
                ],
            ),
            "wdpa_monthly.tf": (
                "wdpa_job_object_user",
                [
                    "100-geographic-reference/130-protected-areas/wdpa-marine/",
                    "100-geographic-reference/130-protected-areas/wdpa-terrestrial/",
                    "_catalog/releases/",
                ],
            ),
            "eamlis_monthly.tf": (
                "eamlis_job_object_user",
                [
                    "300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/",
                    "_catalog/releases/",
                ],
            ),
        }

        for file_name, (resource_name, folder_prefixes) in job_bindings.items():
            with self.subTest(file_name=file_name):
                block = terraform_resource_block(
                    (PROD_TF_DIR / file_name).read_text(),
                    "google_storage_bucket_iam_member",
                    resource_name,
                )
                self.assertIn('role   = "roles/storage.objectUser"', block)
                for prefix in folder_prefixes:
                    self.assertIn(
                        "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}"
                        f"{prefix}')",
                        block,
                    )

    def test_direct_conditional_object_user_grants_include_folder_prefixes(self):
        for path in PROD_TF_DIR.glob("*.tf"):
            for block in terraform_resource_blocks(
                path.read_text(),
                "google_storage_bucket_iam_member",
            ):
                if (
                    'role   = "roles/storage.objectUser"' not in block
                    or "condition {" not in block
                    or "local.shared_bucket_object_resource_prefix" not in block
                ):
                    continue
                with self.subTest(file_name=path.name):
                    self.assertIn("local.shared_bucket_folder_resource_prefix", block)

    def test_scheduled_ingestion_log_alerts_autoclose_after_quiet_hour(self):
        monitoring_tf = (PROD_TF_DIR / "monitoring.tf").read_text()
        for resource_name in (
            "scheduled_ingestion_cloud_run_failure",
            "scheduled_ingestion_scheduler_failure",
        ):
            with self.subTest(resource_name=resource_name):
                block = terraform_resource_block(
                    monitoring_tf,
                    "google_monitoring_alert_policy",
                    resource_name,
                )
                self.assertIn('auto_close           = "3600s"', block)

    def test_github_readonly_identity_is_bucket_viewer_only(self):
        readonly_tf = (PROD_TF_DIR / "github_readonly_iam.tf").read_text()
        outputs_tf = (PROD_TF_DIR / "outputs.tf").read_text()
        variables_tf = (PROD_TF_DIR / "variables.tf").read_text()

        self.assertIn('resource "google_iam_workload_identity_pool_provider" "github_readonly"', readonly_tf)
        self.assertIn("assertion.workflow == 'Catalog drift guard'", readonly_tf)
        self.assertIn("assertion.workflow == 'Bucket hygiene audit'", readonly_tf)
        self.assertIn("assertion.workflow == 'Dataset breaking change alert'", readonly_tf)
        self.assertIn('account_id   = "shared-datasets-gh-readonly"', readonly_tf)
        self.assertIn('role               = "roles/iam.workloadIdentityUser"', readonly_tf)
        self.assertIn('role   = "roles/storage.objectViewer"', readonly_tf)
        self.assertNotIn('roles/storage.objectUser', readonly_tf)
        self.assertIn("github_readonly_workload_identity_provider", outputs_tf)
        self.assertIn("github_readonly_service_account", outputs_tf)
        readonly_provider_var = 'variable "github_readonly_workload_identity_pool_' + "provider" + '_id"'
        self.assertIn(readonly_provider_var, variables_tf)

    def test_publisher_can_cleanup_pending_publish_scratch_only(self):
        iam_tf = (PROD_TF_DIR / "canonical_mutation_iam.tf").read_text()
        viewer_block = terraform_resource_block(
            iam_tf,
            "google_storage_bucket_iam_member",
            "shared_datasets_publisher_pending_publish_viewer",
        )
        block = terraform_resource_block(
            iam_tf,
            "google_storage_bucket_iam_member",
            "shared_datasets_publisher_pending_publish_cleanup_user",
        )

        self.assertIn('role   = "roles/storage.objectViewer"', viewer_block)
        self.assertIn('role   = "roles/storage.objectUser"', block)
        self.assertIn("pending_publish_sources_read_only", viewer_block)
        self.assertIn("pending_publish_cleanup", block)
        self.assertIn("_scratch/pending-publishes/", iam_tf)
        self.assertIn("_scratch/cleanup-audit/", iam_tf)
        self.assertIn(GCLOUD_COMPOSITE_TEMP_PREFIX, iam_tf)
        self.assertNotIn("_scratch/*", viewer_block)
        self.assertNotIn("_scratch/*", block)

    def test_scratch_cleanup_iam_sync_root_matches_publisher_temp_prefixes(self):
        sync_tf = (PROD_TF_DIR / "scratch_cleanup_iam_sync/main.tf").read_text()

        self.assertIn(GCLOUD_COMPOSITE_TEMP_PREFIX, sync_tf)
        self.assertIn("shared_datasets_publisher_pending_publish_viewer", sync_tf)
        self.assertIn("shared_datasets_publisher_pending_publish_cleanup_user", sync_tf)
        self.assertIn("pending_publish_sources_read_only", sync_tf)
        self.assertIn("pending_publish_cleanup", sync_tf)

    def test_publisher_has_bucket_level_list_only_role_for_scratch_cleanup(self):
        iam_tf = (PROD_TF_DIR / "canonical_mutation_iam.tf").read_text()
        role_block = terraform_resource_block(
            iam_tf,
            "google_project_iam_custom_role",
            "shared_datasets_publisher_object_lister",
        )
        binding_block = terraform_resource_block(
            iam_tf,
            "google_storage_bucket_iam_member",
            "shared_datasets_publisher_object_lister",
        )

        self.assertIn('role_id     = "sharedDatasetsPublisherObjectLister"', role_block)
        self.assertIn('permissions = ["storage.objects.list"]', role_block)
        self.assertNotIn("storage.objects.get", role_block)
        self.assertNotIn("storage.objects.create", role_block)
        self.assertNotIn("storage.objects.delete", role_block)
        self.assertIn(
            "role   = google_project_iam_custom_role.shared_datasets_publisher_object_lister.name",
            binding_block,
        )
        self.assertIn("member = module.shared_datasets_publisher_service_account.member", binding_block)
        self.assertNotIn("condition {", binding_block)

    def test_scheduled_ingestion_deploy_role_is_limited_to_job_deploy_actions(self):
        iam_tf = (PROD_TF_DIR / "scheduled_ingestion_deploy_iam.tf").read_text()
        role_block = terraform_resource_block(
            iam_tf,
            "google_project_iam_custom_role",
            "scheduled_ingestion_deployer",
        )
        binding_block = terraform_resource_block(
            iam_tf,
            "google_project_iam_member",
            "github_actions_scheduled_ingestion_deployer",
        )

        self.assertIn('role_id     = "sharedDatasetsScheduledIngestionDeployer"', role_block)
        for permission in (
            "cloudscheduler.jobs.enable",
            "cloudscheduler.jobs.get",
            "run.executions.get",
            "run.executions.list",
            "run.jobs.get",
            "run.jobs.run",
            "run.jobs.update",
            "run.operations.get",
            "run.operations.list",
            "run.tasks.get",
            "run.tasks.list",
        ):
            with self.subTest(permission=permission):
                self.assertIn(f'"{permission}"', role_block)
        self.assertNotIn("run.jobs.create", role_block)
        self.assertNotIn("run.jobs.delete", role_block)
        self.assertNotIn("cloudscheduler.jobs.create", role_block)
        self.assertNotIn("cloudscheduler.jobs.delete", role_block)
        self.assertIn(
            "role    = google_project_iam_custom_role.scheduled_ingestion_deployer.name",
            binding_block,
        )
        self.assertIn(
            'member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"',
            binding_block,
        )

    def test_scheduled_ingestion_deploy_iam_sync_workflow_uses_constrained_apply(self):
        # Caller wiring is asserted in detail in
        # tests/test_prod_terraform_target_apply_workflow.py; keep a pointer
        # assertion here so IAM .tf changes stay linked to the sync workflow.
        workflow = load_workflow(SCHEDULED_INGESTION_DEPLOY_IAM_SYNC_WORKFLOW)
        job = workflow["jobs"]["sync"]

        self.assertEqual(job["uses"], "./.github/workflows/prod-terraform-target-apply.yml")
        self.assertIn(
            "google_project_iam_custom_role.scheduled_ingestion_deployer",
            job["with"]["allowed_exact"],
        )


if __name__ == "__main__":
    unittest.main()
