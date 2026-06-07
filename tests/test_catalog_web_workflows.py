from __future__ import annotations

import unittest
from pathlib import Path

from workflow_helpers import (
    load_workflow,
    python_literal_string_set,
    terraform_targets,
    workflow_all_step_runs,
    workflow_steps_by_name,
    workflow_triggers,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_DEPLOY = REPO_ROOT / ".github/workflows/catalog-web-deploy.yml"
PMTILES_CDN_SYNC = REPO_ROOT / ".github/workflows/pmtiles-cdn-sync.yml"
PMTILES_CDN_SYNC_READINESS = REPO_ROOT / ".github/workflows/pmtiles-cdn-sync-readiness.yml"
SCRATCH_CLEANUP_IAM_SYNC = REPO_ROOT / ".github/workflows/scratch-cleanup-iam-sync.yml"
SCRATCH_CLEANUP_IAM_SYNC_READINESS = REPO_ROOT / ".github/workflows/scratch-cleanup-iam-sync-readiness.yml"


def assert_readiness_workflow(
    testcase: unittest.TestCase,
    workflow_path: Path,
    *,
    expected_name: str,
) -> None:
    workflow = load_workflow(workflow_path)
    trigger = workflow_triggers(workflow)
    job = workflow["jobs"]["readiness"]
    steps = workflow_steps_by_name(workflow, "readiness")

    testcase.assertEqual(workflow["name"], expected_name)
    testcase.assertEqual(trigger["pull_request"]["branches"], ["main"])
    testcase.assertNotIn("workflow_dispatch", trigger)
    testcase.assertNotIn("environment", job)
    testcase.assertEqual(workflow["permissions"], {"contents": "read"})
    testcase.assertIn(
        "Missing repository variable: GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER",
        steps["Validate Terraform auth configuration"]["run"],
    )
    testcase.assertIn(
        "Missing repository variable: GCP_TERRAFORM_SERVICE_ACCOUNT",
        steps["Validate Terraform auth configuration"]["run"],
    )


def assert_protected_terraform_sync(
    testcase: unittest.TestCase,
    workflow_path: Path,
    *,
    expected_name: str,
    push_paths: set[str],
    plan_name: str,
    enforce_step_name: str,
    expected_targets: set[str],
    blocked_resources: set[str],
    readiness_path: Path,
    readiness_name: str,
) -> dict:
    workflow = load_workflow(workflow_path)
    trigger = workflow_triggers(workflow)
    job = workflow["jobs"]["sync"]
    steps = workflow_steps_by_name(workflow, "sync")
    all_runs = workflow_all_step_runs(workflow, "sync")
    plan_run = steps["Terraform plan"]["run"]
    enforce_run = steps[enforce_step_name]["run"]

    testcase.assertEqual(workflow["name"], expected_name)
    testcase.assertEqual(trigger["push"]["branches"], ["main"])
    testcase.assertEqual(set(trigger["push"]["paths"]), push_paths)
    testcase.assertIn("workflow_dispatch", trigger)
    testcase.assertNotIn("pull_request", trigger)
    testcase.assertEqual(job["if"], "${{ github.event_name != 'pull_request' }}")
    testcase.assertEqual(job["environment"], "shared-datasets-production")
    testcase.assertEqual(
        job["concurrency"],
        {"group": "prod-terraform-state", "cancel-in-progress": False},
    )
    testcase.assertEqual(steps["Check out repository"]["with"]["ref"], "main")
    testcase.assertIn("may only apply from main", steps["Validate main ref"]["run"])

    testcase.assertEqual(terraform_targets(plan_run), expected_targets)
    testcase.assertIn("-refresh=false", plan_run)
    testcase.assertIn(f'out="${{RUNNER_TEMP}}/{plan_name}.tfplan"', plan_run)
    testcase.assertEqual(python_literal_string_set(enforce_run, "allowed_exact"), expected_targets)
    testcase.assertIn("Refusing automatic", enforce_run)
    testcase.assertIn("terraform -chdir=terraform/envs/prod", steps["Terraform apply"]["run"])
    testcase.assertIn(" apply ", steps["Terraform apply"]["run"])

    for resource in blocked_resources:
        testcase.assertNotIn(f"-target={resource}", plan_run)
        testcase.assertNotIn(f'"{resource}"', enforce_run)

    testcase.assertIn("Missing repository variable: GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER", all_runs)
    testcase.assertIn("Missing repository variable: GCP_TERRAFORM_SERVICE_ACCOUNT", all_runs)
    testcase.assertNotIn(
        "vars.GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER || vars.GCP_WORKLOAD_IDENTITY_PROVIDER",
        all_runs,
    )

    assert_readiness_workflow(testcase, readiness_path, expected_name=readiness_name)
    return workflow


class CatalogWebWorkflowTests(unittest.TestCase):
    def test_catalog_web_deploy_uses_publisher_identity_and_no_cache_publish_helper(self):
        workflow = load_workflow(CATALOG_DEPLOY)
        trigger = workflow_triggers(workflow)
        env = workflow["env"]
        job = workflow["jobs"]["deploy"]
        steps = workflow_steps_by_name(workflow, "deploy")
        step_runs = "\n".join(str(step.get("run", "")) for step in steps.values())

        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertEqual(
            set(trigger["push"]["paths"]),
            {
                ".github/workflows/catalog-web-deploy.yml",
                "catalog/**",
                "docs/assets/**",
                "scripts/catalog_docs.py",
                "scripts/catalog_site.py",
                "scripts/catalog_web_publish.py",
                "web/catalog/**",
            },
        )
        self.assertEqual(trigger["workflow_run"]["workflows"], ["Approved dataset mutation"])
        self.assertEqual(trigger["workflow_run"]["types"], ["completed"])
        self.assertIn("workflow_dispatch", trigger)
        self.assertEqual(job["environment"], "shared-datasets-production")
        job_condition = " ".join(job["if"].split())
        self.assertEqual(
            job_condition,
            "${{ github.event_name != 'workflow_run' || github.event.workflow_run.conclusion == 'success' }}",
        )
        self.assertEqual(job["concurrency"]["group"], "catalog-web-deploy")
        self.assertFalse(job["concurrency"]["cancel-in-progress"])
        self.assertEqual(steps["Check out repository"]["with"]["ref"], "main")
        self.assertEqual(
            env["PUBLISHER_SERVICE_ACCOUNT"],
            "shared-datasets-publisher@shared-datasets-1.iam.gserviceaccount.com",
        )
        self.assertEqual(env["SHARED_DATASETS_ALLOW_CANONICAL_MUTATION"], "1")
        self.assertEqual(env["CATALOG_WEB_CACHE_CONTROL"], "no-cache, max-age=0, must-revalidate")
        self.assertIn("Catalog web deploy may only publish from main", steps["Validate main ref"]["run"])
        self.assertIn(
            "Missing repository variable: GCP_WORKLOAD_IDENTITY_PROVIDER",
            steps["Validate publisher auth configuration"]["run"],
        )
        self.assertIn("uv run python scripts/catalog_site.py", steps["Build catalog web bundle"]["run"])
        self.assertIn("Collect release indexes", steps)
        self.assertIn(
            'gcloud storage cp "gs://${SHARED_DATASETS_BUCKET}/_catalog/releases/*.json"',
            steps["Collect release indexes"]["run"],
        )
        self.assertIn(
            '--release-index-dir "${RUNNER_TEMP}/release-indexes"',
            steps["Build catalog web bundle"]["run"],
        )
        self.assertIn("--latest-from-release-index", steps["Build catalog web bundle"]["run"])
        self.assertNotIn("--release-index-assets-only", steps["Build catalog web bundle"]["run"])
        self.assertIn("uv run python scripts/catalog_web_publish.py", steps["Publish catalog web bundle"]["run"])
        self.assertIn(
            '--catalog-source "catalog/shared-datasets-catalog.csv"',
            steps["Publish catalog web bundle"]["run"],
        )
        self.assertIn(
            '--catalog-destination "gs://${SHARED_DATASETS_BUCKET}/_catalog/shared-datasets-catalog.csv"',
            steps["Publish catalog web bundle"]["run"],
        )
        self.assertIn("node --check web/catalog/app.js", steps["Check browser JavaScript syntax"]["run"])
        self.assertNotIn("shared-datasets-publish-plan", step_runs)

    def test_pmtiles_cdn_sync_has_explicit_resource_change_allowlist(self):
        workflow = assert_protected_terraform_sync(
            self,
            PMTILES_CDN_SYNC,
            expected_name="PMTiles CDN sync",
            push_paths={
                "catalog/shared-datasets-catalog.csv",
                "docs/assets/**",
                "terraform/envs/prod/pmtiles_cdn.tf",
                "terraform/envs/prod/shared_bucket_public.tf",
                "terraform/envs/prod/variables.tf",
                "terraform/envs/prod/versions.tf",
            },
            plan_name="pmtiles-cdn-sync",
            enforce_step_name="Enforce PMTiles resource-change allowlist",
            expected_targets={
                "google_compute_url_map.pmtiles_cdn",
            },
            blocked_resources={
                "google_compute_backend_bucket.pmtiles_cdn",
                "google_cloud_run_v2_service.catalog_viewer",
                "google_secret_manager_secret_iam_member.pmtiles_cdn_catalog_viewer_signer",
                "google_storage_bucket.shared_bucket",
                "google_storage_managed_folder.shared_bucket_public_prefixes",
                "google_storage_managed_folder_iam_member.shared_bucket_public_object_viewers",
            },
            readiness_path=PMTILES_CDN_SYNC_READINESS,
            readiness_name="PMTiles CDN sync readiness",
        )
        steps = workflow_steps_by_name(workflow, "sync")
        verify_run = steps["Verify catalog CDN routes"]["run"]

        self.assertIn("unused-by-pmtiles-cdn-sync", steps["Terraform plan"]["run"])
        self.assertIn('--path="/_catalog/*"', steps["Invalidate catalog CDN cache"]["run"])
        self.assertIn(
            'check_catalog_url "https://tiles.skytruth.org/_catalog/shared-datasets-catalog.csv" "text/csv"',
            verify_run,
        )
        self.assertIn(
            'check_catalog_url "https://tiles.skytruth.org/_catalog/web/catalog.json" "application/json"',
            verify_run,
        )
        self.assertIn('check_catalog_url "https://tiles.skytruth.org/_catalog/web/index.html" "text/html"', verify_run)
        self.assertIn('release_index_url = "https://tiles.skytruth.org/_catalog/releases/wdpa-marine.json"', verify_run)
        self.assertIn('check_public_artifact_url "https://tiles.skytruth.org/artifacts/${wdpa_metadata_path}"', verify_run)
        self.assertIn("expected 200 without redirect", verify_run)
        self.assertNotIn("curl -L", verify_run)
        self.assertNotIn("--location", verify_run)

    def test_scratch_cleanup_iam_sync_has_explicit_resource_change_allowlist(self):
        workflow = assert_protected_terraform_sync(
            self,
            SCRATCH_CLEANUP_IAM_SYNC,
            expected_name="Scratch cleanup IAM sync",
            push_paths={
                "terraform/envs/prod/canonical_mutation_iam.tf",
                "terraform/envs/prod/scratch_cleanup_iam_sync/**",
                "terraform/envs/prod/variables.tf",
                "terraform/envs/prod/versions.tf",
            },
            plan_name="scratch-cleanup-iam-sync",
            enforce_step_name="Enforce scratch cleanup IAM resource-change allowlist",
            expected_targets={
                "google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_viewer",
                "google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_cleanup_user",
            },
            blocked_resources={
                "google_compute_url_map.pmtiles_cdn",
                "google_storage_bucket.shared_bucket",
                "google_storage_managed_folder.shared_bucket_public_prefixes",
            },
            readiness_path=SCRATCH_CLEANUP_IAM_SYNC_READINESS,
            readiness_name="Scratch cleanup IAM sync readiness",
        )

        plan_step = workflow_steps_by_name(workflow, "sync")["Terraform plan"]["run"]
        self.assertIn("terraform/envs/prod/scratch_cleanup_iam_sync", plan_step)
        self.assertNotIn("unused-by-scratch-cleanup-iam-sync", plan_step)


if __name__ == "__main__":
    unittest.main()
