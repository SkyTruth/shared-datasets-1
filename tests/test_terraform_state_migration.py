from __future__ import annotations

import unittest
from pathlib import Path

from workflow_helpers import load_workflow, workflow_steps_by_name


REPO_ROOT = Path(__file__).resolve().parents[1]
PROD_TF = REPO_ROOT / "terraform/envs/prod"


class TerraformStateMigrationTests(unittest.TestCase):
    def test_prod_and_preview_backends_use_isolated_bucket(self):
        prod_versions = (PROD_TF / "versions.tf").read_text()
        preview_versions = (REPO_ROOT / "terraform/envs/preview/versions.tf").read_text()

        for text in (prod_versions, preview_versions):
            self.assertIn('bucket = "skytruth-shared-datasets-1-terraform-state"', text)
        self.assertIn('prefix = "000-system/terraform/state/prod"', prod_versions)
        self.assertIn('prefix = "000-system/terraform/state/preview"', preview_versions)

    def test_apply_workflows_guard_state_before_init(self):
        roots = {
            "prod-terraform-target-apply.yml": "prod",
            "pmtiles-cdn-sync.yml": "prod",
            "catalog-viewer-deploy.yml": "prod",
            "metadata-service-deploy.yml": "prod",
            "sea-ice-daily-deploy.yml": "prod",
            "feature-preview-deploy.yml": "preview",
            "feature-preview-destroy.yml": "preview",
        }
        for filename, root in roots.items():
            with self.subTest(filename=filename):
                workflow = load_workflow(REPO_ROOT / ".github/workflows" / filename)
                job_name = next(
                    name
                    for name, job in workflow["jobs"].items()
                    if any(step.get("name") == "Terraform init" for step in job.get("steps", []))
                )
                steps = workflow_steps_by_name(workflow, job_name)
                names = list(steps)
                self.assertIn(
                    f"python scripts/terraform_state_backend_guard.py {root}",
                    steps["Require migrated Terraform state"]["run"],
                )
                self.assertLess(names.index("Require migrated Terraform state"), names.index("Terraform init"))

    def test_migration_workflows_are_main_only_and_generation_preserving(self):
        state_migration = (REPO_ROOT / ".github/workflows/terraform-state-migration.yml").read_text()
        migration_script = (REPO_ROOT / "scripts/terraform_state_migrate.sh").read_text()
        storage_sync = (REPO_ROOT / ".github/workflows/storage-hardening-sync.yml").read_text()

        self.assertIn('GITHUB_REF}" != "refs/heads/main"', state_migration)
        self.assertIn("MIGRATE_TERRAFORM_STATE", state_migration)
        self.assertIn("Refusing to overwrite existing destination state", migration_script)
        self.assertIn('for key in ("lineage", "serial", "outputs")', migration_script)
        self.assertIn("source_generation_before", migration_script)
        self.assertIn("source_generation_after", migration_script)
        self.assertIn("state list", migration_script)
        self.assertIn("source.addresses", migration_script)
        self.assertIn("destination.addresses", migration_script)
        self.assertIn("cmp -s", migration_script)
        self.assertIn('"resource_addresses": addresses', migration_script)
        self.assertIn("HARDEN_STORAGE_ADD_SCOPED", storage_sync)
        self.assertIn("HARDEN_STORAGE_REMOVE_BROAD", storage_sync)
        self.assertIn("terraform_plan_allowlist.py", storage_sync)


if __name__ == "__main__":
    unittest.main()
