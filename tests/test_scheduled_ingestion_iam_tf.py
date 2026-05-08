from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_TF_DIR = REPO_ROOT / "terraform/envs/prod"


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
        self.assertIn('account_id   = "shared-datasets-gh-readonly"', readonly_tf)
        self.assertIn('role               = "roles/iam.workloadIdentityUser"', readonly_tf)
        self.assertIn('role   = "roles/storage.objectViewer"', readonly_tf)
        self.assertNotIn('roles/storage.objectUser', readonly_tf)
        self.assertIn("github_readonly_workload_identity_provider", outputs_tf)
        self.assertIn("github_readonly_service_account", outputs_tf)
        self.assertIn('variable "github_readonly_workload_identity_pool_provider_id"', variables_tf)


if __name__ == "__main__":
    unittest.main()
