from __future__ import annotations

import subprocess
import unittest

from scripts import terraform_state_backend_guard


class TerraformStateBackendGuardTests(unittest.TestCase):
    def test_known_roots_have_isolated_state_uris(self):
        self.assertEqual(
            terraform_state_backend_guard.state_uri("prod"),
            "gs://skytruth-shared-datasets-1-terraform-state/000-system/terraform/state/prod/default.tfstate",
        )
        self.assertEqual(
            terraform_state_backend_guard.state_uri("preview"),
            "gs://skytruth-shared-datasets-1-terraform-state/000-system/terraform/state/preview/default.tfstate",
        )

    def test_existing_numeric_generation_passes(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="123\n", stderr="")

        self.assertEqual(terraform_state_backend_guard.check_state_exists("prod", runner=runner), "123")

    def test_missing_state_fails_closed_with_migration_direction(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="not found")

        with self.assertRaisesRegex(RuntimeError, "Terraform State Migration"):
            terraform_state_backend_guard.check_state_exists("preview", runner=runner)


if __name__ == "__main__":
    unittest.main()
