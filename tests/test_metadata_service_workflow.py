from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/metadata-service-deploy.yml"
INDEX_LOAD_WORKFLOW = REPO_ROOT / ".github/workflows/feature-metadata-index-load.yml"


class MetadataServiceWorkflowTests(unittest.TestCase):
    def test_metadata_service_deploy_workflow_is_protected_and_digest_pinned(self):
        workflow = DEPLOY_WORKFLOW.read_text()

        self.assertIn("deploy_metadata_service:", workflow)
        self.assertIn("ENABLE_METADATA_SERVICE_DEPLOY", workflow)
        self.assertIn("Check metadata-service deploy gate", workflow)
        self.assertIn("deploy_enabled: ${{ steps.gate.outputs.deploy_enabled }}", workflow)
        self.assertIn("Metadata-service deploy is deferred; skipping Docker build and Terraform.", workflow)
        self.assertIn("needs: deploy_gate", workflow)
        self.assertIn("needs.deploy_gate.outputs.deploy_enabled == 'true'", workflow)
        self.assertIn("environment: shared-datasets-production", workflow)
        self.assertIn("services/metadata_service/Dockerfile", workflow)
        self.assertIn("--platform linux/amd64", workflow)
        self.assertIn("docker push", workflow)
        self.assertIn("fully_qualified_digest", workflow)
        self.assertIn("metadata_service_image=${METADATA_SERVICE_IMAGE}", workflow)
        self.assertIn("terraform -chdir=terraform/envs/prod plan", workflow)
        self.assertIn("terraform -chdir=terraform/envs/prod show -json", workflow)
        self.assertIn("terraform -chdir=terraform/envs/prod apply", workflow)
        self.assertIn("Enforce metadata-service resource-change allowlist", workflow)
        self.assertIn("google_cloud_run_v2_service.metadata_service", workflow)
        self.assertIn("google_firestore_database.feature_metadata", workflow)
        self.assertIn("google_monitoring_alert_policy.dataset_object_written_by_unapproved_principal", workflow)
        self.assertIn("google_storage_bucket_iam_member.metadata_index_loader_object_viewer", workflow)
        self.assertIn("google_storage_bucket_iam_member.metadata_index_loader_index_load_creator", workflow)
        self.assertIn("google_service_account_iam_member.metadata_index_loader_github_wif", workflow)
        self.assertNotIn("-target=", workflow)

    def test_metadata_service_deploy_allowlist_patterns_match_real_terraform_addresses(self):
        workflow = DEPLOY_WORKFLOW.read_text()
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
                pattern.match('google_iap_web_cloud_run_service_iam_member.metadata_service_accessors["user:jona@skytruth.org"]')
                for pattern in patterns
            )
        )
        self.assertFalse(
            any(pattern.match("google_storage_bucket_iam_member.unrelated") for pattern in patterns)
        )

    def test_feature_metadata_index_load_workflow_is_protected_exact_generation_and_no_clobber(self):
        workflow = INDEX_LOAD_WORKFLOW.read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("environment: shared-datasets-production", workflow)
        self.assertIn("metadata-index-loader@shared-datasets-1.iam.gserviceaccount.com", workflow)
        self.assertIn("Authenticate as metadata index loader", workflow)
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
            self.assertIn(f"{input_name}:", workflow)
        self.assertIn('release_prefix = f"gs://{bucket}/{asset_root}/releases/{release}/"', workflow)
        self.assertIn('uv run python scripts/gcs_asset.py download "${SIDECAR_URI}" "${SIDECAR_PATH}" --generation "${SIDECAR_GENERATION}"', workflow)
        self.assertIn('uv run python scripts/gcs_asset.py download "${SCHEMA_URI}" "${SCHEMA_PATH}" --generation "${SCHEMA_GENERATION}"', workflow)
        self.assertIn('uv run python scripts/gcs_asset.py download "${MANIFEST_URI}" "${MANIFEST_PATH}" --generation "${MANIFEST_GENERATION}"', workflow)
        self.assertIn("uv run python scripts/feature_metadata_index.py", workflow)
        self.assertIn("--index-load-record", workflow)
        self.assertNotIn("--dry-run", workflow)
        self.assertIn("SHARED_DATASETS_ALLOW_CANONICAL_MUTATION: \"1\"", workflow)
        self.assertIn("uv run python scripts/gcs_asset.py upload", workflow)
        self.assertIn('"${INDEX_LOAD_URI}"', workflow)
        self.assertNotIn("--replace-generation", workflow)
        self.assertNotIn("--unsafe-overwrite", workflow)


if __name__ == "__main__":
    unittest.main()
