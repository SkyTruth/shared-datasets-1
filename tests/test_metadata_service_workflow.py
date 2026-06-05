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
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/metadata-service-deploy.yml"
INDEX_LOAD_WORKFLOW = REPO_ROOT / ".github/workflows/feature-metadata-index-load.yml"


class MetadataServiceWorkflowTests(unittest.TestCase):
    def test_metadata_service_deploy_workflow_is_protected_and_digest_pinned(self):
        workflow = load_workflow(DEPLOY_WORKFLOW)
        trigger = workflow_triggers(workflow)
        gate = workflow["jobs"]["deploy_gate"]
        deploy = workflow["jobs"]["deploy"]
        gate_steps = workflow_steps_by_name(workflow, "deploy_gate")
        deploy_steps = workflow_steps_by_name(workflow, "deploy")
        plan_run = deploy_steps["Terraform plan"]["run"]
        enforce_run = deploy_steps["Enforce metadata-service resource-change allowlist"]["run"]

        self.assertEqual(workflow["name"], "Feature metadata service deploy")
        self.assertEqual(trigger["push"]["branches"], ["main"])
        self.assertEqual(
            trigger["workflow_dispatch"]["inputs"]["deploy_metadata_service"],
            {
                "description": "Build the metadata-service image and apply Terraform.",
                "required": False,
                "type": "boolean",
                "default": False,
            },
        )
        self.assertEqual(gate["if"], "${{ github.event_name != 'pull_request' }}")
        self.assertEqual(gate["outputs"]["deploy_enabled"], "${{ steps.gate.outputs.deploy_enabled }}")
        self.assertIn("ENABLE_METADATA_SERVICE_DEPLOY", workflow["env"])
        self.assertIn(
            "Metadata-service deploy is deferred; skipping Docker build and Terraform.",
            gate_steps["Decide whether to deploy"]["run"],
        )
        self.assertEqual(deploy["needs"], "deploy_gate")
        self.assertEqual(deploy["if"], "${{ needs.deploy_gate.outputs.deploy_enabled == 'true' }}")
        self.assertEqual(deploy["environment"], "shared-datasets-production")
        self.assertEqual(
            deploy["concurrency"],
            {"group": "prod-terraform-state", "cancel-in-progress": False},
        )
        self.assertEqual(deploy_steps["Check out repository"]["with"]["ref"], "main")

        build_run = deploy_steps["Build metadata-service image"]["run"]
        self.assertIn("-f services/metadata_service/Dockerfile", build_run)
        self.assertIn("--platform linux/amd64", build_run)
        self.assertIn("docker push", build_run)
        self.assertIn("fully_qualified_digest", build_run)
        self.assertIn("METADATA_SERVICE_IMAGE=${image_digest}", build_run)

        self.assertEqual(terraform_targets(plan_run), set())
        self.assertIn("metadata_service_image=${METADATA_SERVICE_IMAGE}", plan_run)
        self.assertIn("unused-by-metadata-service-deploy", plan_run)
        self.assertIn(
            "terraform -chdir=terraform/envs/prod show -json",
            deploy_steps["Export Terraform plan JSON"]["run"],
        )
        self.assertIn("terraform -chdir=terraform/envs/prod apply", deploy_steps["Terraform apply"]["run"])
        self.assertLess(
            list(deploy_steps).index("Enforce metadata-service resource-change allowlist"),
            list(deploy_steps).index("Terraform apply"),
        )
        self.assertGreaterEqual(
            python_literal_string_set(enforce_run, "allowed_exact"),
            {
                "google_cloud_run_v2_service.metadata_service",
                "google_firestore_database.feature_metadata",
                "google_monitoring_alert_policy.dataset_object_written_by_unapproved_principal",
                "google_storage_bucket_iam_member.metadata_index_loader_object_viewer",
                "google_storage_bucket_iam_member.metadata_index_loader_index_load_creator",
                "google_service_account_iam_member.metadata_index_loader_github_wif",
            },
        )

    def test_metadata_service_deploy_allowlist_patterns_match_real_terraform_addresses(self):
        workflow = load_workflow(DEPLOY_WORKFLOW)
        enforce_run = workflow_steps_by_name(workflow, "deploy")[
            "Enforce metadata-service resource-change allowlist"
        ]["run"]
        patterns = [
            re.compile(pattern)
            for pattern in re.findall(r're\.compile\(r"([^"]+)"\)', enforce_run)
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
                pattern.match(
                    'google_iap_web_cloud_run_service_iam_member.metadata_service_accessors'
                    '["user:jona@skytruth.org"]'
                )
                for pattern in patterns
            )
        )
        self.assertFalse(
            any(pattern.match("google_storage_bucket_iam_member.unrelated") for pattern in patterns)
        )

    def test_feature_metadata_index_load_workflow_is_protected_exact_generation_and_no_clobber(self):
        workflow = load_workflow(INDEX_LOAD_WORKFLOW)
        trigger = workflow_triggers(workflow)
        job = workflow["jobs"]["load"]
        steps = workflow_steps_by_name(workflow, "load")

        self.assertEqual(workflow["name"], "Feature metadata index load")
        self.assertEqual(job["environment"], "shared-datasets-production")
        self.assertEqual(
            job["concurrency"]["group"],
            "feature-metadata-index-load-${{ inputs.asset_slug }}-${{ inputs.release }}",
        )
        self.assertEqual(
            workflow["env"]["INDEX_LOADER_SERVICE_ACCOUNT"],
            "metadata-index-loader@shared-datasets-1.iam.gserviceaccount.com",
        )
        self.assertEqual(steps["Check out repository"]["with"]["ref"], "main")
        for input_name in (
            "asset_slug",
            "release",
            "sidecar_uri",
            "sidecar_generation",
            "schema_uri",
            "schema_generation",
            "manifest_uri",
            "manifest_generation",
        ):
            self.assertEqual(trigger["workflow_dispatch"]["inputs"][input_name]["required"], True)

        validate_run = steps["Validate index-load request"]["run"]
        download_run = steps["Download exact-generation release metadata bundle"]["run"]
        load_run = steps["Load Firestore index and write local record"]["run"]
        upload_step = steps["Upload index-load record no-clobber"]
        upload_run = upload_step["run"]

        self.assertIn('release_prefix = f"gs://{bucket}/{asset_root}/releases/{release}/"', validate_run)
        self.assertIn(
            'uv run python scripts/gcs_asset.py download '
            '"${SIDECAR_URI}" "${SIDECAR_PATH}" --generation "${SIDECAR_GENERATION}"',
            download_run,
        )
        self.assertIn(
            'uv run python scripts/gcs_asset.py download '
            '"${SCHEMA_URI}" "${SCHEMA_PATH}" --generation "${SCHEMA_GENERATION}"',
            download_run,
        )
        self.assertIn(
            'uv run python scripts/gcs_asset.py download '
            '"${MANIFEST_URI}" "${MANIFEST_PATH}" --generation "${MANIFEST_GENERATION}"',
            download_run,
        )
        self.assertIn("uv run python scripts/feature_metadata_index.py", load_run)
        self.assertIn("--index-load-record", load_run)
        self.assertNotIn("--dry-run", load_run)
        self.assertEqual(upload_step["env"]["SHARED_DATASETS_ALLOW_CANONICAL_MUTATION"], "1")
        self.assertIn("uv run python scripts/gcs_asset.py upload", upload_run)
        self.assertIn('"${INDEX_LOAD_URI}"', upload_run)
        self.assertNotIn("--replace-generation", upload_run)
        self.assertNotIn("--unsafe-overwrite", upload_run)


if __name__ == "__main__":
    unittest.main()
