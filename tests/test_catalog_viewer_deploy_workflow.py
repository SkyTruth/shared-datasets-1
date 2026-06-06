from __future__ import annotations

import re
import unittest
from pathlib import Path

from workflow_helpers import (
    load_workflow,
    python_literal_string_set,
    terraform_targets,
    workflow_steps_by_name,
    workflow_triggers,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/catalog-viewer-deploy.yml"


class CatalogViewerDeployWorkflowTests(unittest.TestCase):
    def test_catalog_viewer_deploy_workflow_is_protected_and_digest_pinned(self):
        workflow = load_workflow(DEPLOY_WORKFLOW)
        trigger = workflow_triggers(workflow)
        deploy = workflow["jobs"]["deploy"]
        steps = workflow_steps_by_name(workflow, "deploy")
        plan_run = steps["Terraform plan"]["run"]
        enforce_run = steps["Enforce catalog-viewer resource-change allowlist"]["run"]

        self.assertEqual(workflow["name"], "Catalog viewer deploy")
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertEqual(trigger["workflow_dispatch"], {})
        self.assertEqual(deploy["if"], "${{ github.event_name != 'pull_request' }}")
        self.assertEqual(deploy["environment"], "shared-datasets-production")
        self.assertEqual(
            deploy["concurrency"],
            {"group": "prod-terraform-state", "cancel-in-progress": False},
        )
        self.assertEqual(steps["Check out repository"]["with"]["ref"], "main")

        build_run = steps["Build catalog-viewer image"]["run"]
        self.assertIn("-f services/catalog_viewer/Dockerfile", build_run)
        self.assertIn("--platform linux/amd64", build_run)
        self.assertIn("docker push", build_run)
        self.assertIn("docker buildx imagetools inspect", build_run)
        self.assertIn("CATALOG_VIEWER_IMAGE=${image_ref}", build_run)

        self.assertEqual(
            terraform_targets(plan_run),
            {
                "google_cloud_run_v2_service.catalog_viewer",
                "google_cloud_run_v2_service_iam_member.catalog_viewer_iap_invoker",
                "google_iap_web_cloud_run_service_iam_member.catalog_viewer_accessors",
                "google_project_iam_custom_role.catalog_viewer_sign_blob",
                "google_secret_manager_secret_iam_member.pmtiles_cdn_catalog_viewer_signer",
                "google_service_account_iam_member.catalog_viewer_self_sign_blob",
                "google_storage_bucket_iam_member.catalog_viewer_object_viewer",
                "module.catalog_viewer_service_account.google_service_account.this",
            },
        )
        self.assertIn("-refresh=false", plan_run)
        self.assertIn("catalog_viewer_image=${CATALOG_VIEWER_IMAGE}", plan_run)
        self.assertIn(
            "terraform -chdir=terraform/envs/prod show -json",
            steps["Export Terraform plan JSON"]["run"],
        )
        self.assertIn("terraform -chdir=terraform/envs/prod apply", steps["Terraform apply"]["run"])
        self.assertLess(
            list(steps).index("Enforce catalog-viewer resource-change allowlist"),
            list(steps).index("Terraform apply"),
        )
        self.assertGreaterEqual(
            python_literal_string_set(enforce_run, "allowed_exact"),
            {
                "google_cloud_run_v2_service.catalog_viewer",
                "google_cloud_run_v2_service_iam_member.catalog_viewer_iap_invoker",
                "google_secret_manager_secret_iam_member.pmtiles_cdn_catalog_viewer_signer",
                "google_storage_bucket_iam_member.catalog_viewer_object_viewer",
            },
        )

    def test_catalog_viewer_deploy_allowlist_patterns_match_real_terraform_addresses(self):
        workflow = load_workflow(DEPLOY_WORKFLOW)
        enforce_run = workflow_steps_by_name(workflow, "deploy")[
            "Enforce catalog-viewer resource-change allowlist"
        ]["run"]
        patterns = [re.compile(pattern) for pattern in re.findall(r're\.compile\(r"([^"]+)"\)', enforce_run)]

        self.assertTrue(
            any(pattern.match("module.catalog_viewer_service_account.google_service_account.this") for pattern in patterns)
        )
        self.assertTrue(
            any(
                pattern.match(
                    'google_iap_web_cloud_run_service_iam_member.catalog_viewer_accessors'
                    '["user:jona@skytruth.org"]'
                )
                for pattern in patterns
            )
        )
        self.assertFalse(any(pattern.match("google_storage_bucket_iam_member.unrelated") for pattern in patterns))


if __name__ == "__main__":
    unittest.main()
