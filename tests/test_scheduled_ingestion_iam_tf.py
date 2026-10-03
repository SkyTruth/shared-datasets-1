from __future__ import annotations

import json
import re
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
    def test_translation_notice_permissions_only_create_scoped_exports_and_read_existing_secret(self):
        text = (PROD_TF_DIR / "translation_notices.tf").read_text()
        for name in ("translation_debt_writer", "publisher_translation_debt_writer"):
            block = terraform_resource_block(text, "google_storage_bucket_iam_member", name)
            self.assertIn('role   = "roles/storage.objectCreator"', block)
            self.assertIn("shared_bucket_object_resource_prefix}_scratch/translation-debt/", block)
            self.assertIn("shared_bucket_folder_resource_prefix}_scratch/translation-debt/", block)
            self.assertNotIn("objectUser", block)
        secret = terraform_resource_block(text, "google_secret_manager_secret_iam_member", "translation_notice")
        self.assertIn('role      = "roles/secretmanager.secretAccessor"', secret)
        self.assertIn("google_secret_manager_secret.slack_webhook_url.secret_id", secret)
        self.assertIn("google_project_iam_member.github_actions_translation_notice_iam_manager", secret)

    def test_translation_notice_deployer_manages_only_existing_secret_metadata_and_iam(self):
        text = (PROD_TF_DIR / "scheduled_ingestion_deploy_iam.tf").read_text()
        role = terraform_resource_block(text, "google_project_iam_custom_role", "translation_notice_iam_manager")
        permissions = re.search(r"permissions\s*=\s*\[(.*?)\]", role, re.S).group(1)
        self.assertEqual(set(re.findall(r'"([^"]+)"', permissions)), {
            "secretmanager.secrets.get",
            "secretmanager.secrets.getIamPolicy",
            "secretmanager.secrets.setIamPolicy",
        })
        binding = terraform_resource_block(text, "google_project_iam_member", "github_actions_translation_notice_iam_manager")
        self.assertIn("google_project_iam_custom_role.translation_notice_iam_manager.name", binding)
        self.assertIn("serviceAccount:${var.github_actions_terraform_service_account_email}", binding)
        self.assertIn("resource.name == 'projects/${var.project_id}/secrets/${local.slack_webhook_secret_id}'", binding)
        self.assertIn("resource.name == 'projects/${data.google_project.current.number}/secrets/${local.slack_webhook_secret_id}'", binding)
        self.assertNotIn("startsWith", binding)
        # The bootstrap plan must not need permission to read the secret it unlocks.
        self.assertNotIn("google_secret_manager_secret.", binding)

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
                    if "_catalog/wdpa-monthly-execution.json" in block:
                        self.assertIn("resource.name ==", block)
                        self.assertNotIn("startsWith", block)
                        self.assertNotIn("local.shared_bucket_folder_resource_prefix", block)
                    else:
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

    def test_scratch_cleanup_iam_has_single_owner_in_prod_root(self):
        partial_root = PROD_TF_DIR / "scratch_cleanup_iam_sync"
        self.assertFalse((partial_root / "main.tf").exists())
        self.assertFalse((partial_root / "versions.tf").exists())

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
            "cloudscheduler.jobs.pause",
            "run.executions.get",
            "run.executions.list",
            "run.jobs.create",
            "run.jobs.get",
            "run.jobs.getIamPolicy",
            "run.jobs.list",
            "run.jobs.run",
            "run.jobs.runWithOverrides",
            "run.jobs.update",
            "run.operations.get",
            "run.operations.list",
            "run.tasks.get",
            "run.tasks.list",
        ):
            with self.subTest(permission=permission):
                self.assertIn(f'"{permission}"', role_block)
        self.assertNotIn("run.jobs.setIamPolicy", role_block)
        self.assertNotIn("run.jobs.delete", role_block)
        self.assertNotIn("cloudscheduler.jobs.create", role_block)
        self.assertNotIn("cloudscheduler.jobs.update", role_block)
        self.assertNotIn("cloudscheduler.jobs.delete", role_block)
        self.assertIn(
            "role    = google_project_iam_custom_role.scheduled_ingestion_deployer.name",
            binding_block,
        )
        self.assertIn(
            'member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"',
            binding_block,
        )

    def test_provisioned_observer_bootstrap_role_has_no_project_grant(self):
        text = (PROD_TF_DIR / "wdpa_observer_bootstrap_iam.tf").read_text()
        role = terraform_resource_block(text, "google_project_iam_custom_role", "wdpa_observer_bootstrap")
        permissions = re.search(r"permissions\s*=\s*\[(.*?)\]", role, re.S).group(1)
        self.assertEqual(set(re.findall(r'"([^"]+)"', permissions)), {
            "cloudscheduler.jobs.create", "cloudscheduler.jobs.update", "run.jobs.setIamPolicy"})
        all_terraform = "\n".join(path.read_text() for path in PROD_TF_DIR.glob("*.tf"))
        self.assertNotIn(
            'resource "google_project_iam_member" "github_actions_wdpa_observer_bootstrap"',
            all_terraform,
        )
        self.assertNotRegex(
            all_terraform,
            r"role\s*=\s*google_project_iam_custom_role\.wdpa_observer_bootstrap\.name",
        )

    def test_wdpa_reset_reader_matches_only_the_approved_supplement(self):
        block = terraform_resource_block(
            (PROD_TF_DIR / "wdpa_reset_iam.tf").read_text(),
            "google_storage_bucket_iam_member",
            "wdpa_reset_translation_reader",
        )
        self.assertIn('role   = "roles/storage.objectViewer"', block)
        self.assertIn('member = module.wdpa_job_service_account.member', block)
        self.assertNotIn("startsWith", block)
        self.assertNotIn(" || ", block)
        supplements = set()
        for slug in ("wdpa-marine", "wdpa-terrestrial"):
            plan_dir = (
                REPO_ROOT / ".github/dataset-plans" / slug
                / "prelaunch-feature-id-reset-20260930"
            )
            documents = list(plan_dir.glob("*.json"))
            self.assertEqual(len(documents), 1)
            inventory = json.loads(documents[0].read_text())["publish"]["identity_reset"]["inventory"]
            supplements.add(inventory["translation_supplement"]["path"])
        self.assertEqual(len(supplements), 1)
        source_path = supplements.pop().split("/", 3)[3]
        expression = next(line.strip() for line in block.splitlines() if line.strip().startswith("expression"))
        self.assertEqual(expression.split("=", 1)[1].strip(),
                         '"resource.name == \'${local.shared_bucket_object_resource_prefix}' + source_path + '\'"')

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
