from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from scripts import terraform_plan_allowlist


def plan_with(*changes: tuple[str, list[str]]) -> dict:
    return {
        "resource_changes": [
            {"address": address, "change": {"actions": actions}}
            for address, actions in changes
        ]
    }


class TerraformPlanAllowlistTests(unittest.TestCase):
    def run_main(self, plan: dict, *args: str) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = terraform_plan_allowlist.main(
                    [str(plan_path), "--refusal-prefix", "Refusing test sync", *args]
                )
        return exit_code, stdout.getvalue()

    def test_allows_exact_addresses_and_ignores_noops(self):
        plan = plan_with(
            ("google_project_iam_member.allowed", ["update"]),
            ("google_storage_bucket.untouched", ["no-op"]),
            ("google_storage_bucket.read_only", ["read"]),
        )
        exit_code, output = self.run_main(plan, "--allowed-exact", "google_project_iam_member.allowed")
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, "")

    def test_refuses_non_allowlisted_changes(self):
        plan = plan_with(("google_storage_bucket.shared_bucket", ["update"]))
        exit_code, output = self.run_main(plan, "--allowed-exact", "google_project_iam_member.allowed")
        self.assertEqual(exit_code, 1)
        self.assertIn(
            "Refusing test sync because the Terraform plan changes non-allowlisted resources:",
            output,
        )
        self.assertIn("- update google_storage_bucket.shared_bucket", output)

    def test_allowed_patterns_match_module_addresses(self):
        plan = plan_with(
            ("module.loader_service_account.google_service_account.this", ["create"]),
            ("module.other_module.google_service_account.this", ["create"]),
        )
        exit_code, output = self.run_main(
            plan,
            "--allowed-exact",
            "",
            "--allowed-patterns",
            r"^module\.loader_service_account\.",
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("module.other_module", output)
        self.assertNotIn("- create module.loader_service_account", output)

    def test_block_deletes_refuses_allowlisted_deletes(self):
        plan = plan_with(("google_project_iam_member.allowed", ["delete"]))
        exit_code, output = self.run_main(
            plan,
            "--allowed-exact",
            "google_project_iam_member.allowed",
            "--block-deletes",
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("- delete google_project_iam_member.allowed", output)

    def test_block_deletes_refuses_allowlisted_replaces(self):
        plan = plan_with(("google_project_iam_member.allowed", ["delete", "create"]))
        exit_code, output = self.run_main(
            plan,
            "--allowed-exact",
            "google_project_iam_member.allowed",
            "--block-deletes",
        )
        self.assertEqual(exit_code, 1)
        self.assertIn("- delete/create google_project_iam_member.allowed", output)

    def test_replace_actions_are_checked_against_allowlist(self):
        plan = plan_with(("google_firestore_database.feature_metadata", ["delete", "create"]))
        exit_code, output = self.run_main(plan, "--allowed-exact", "other.resource")
        self.assertEqual(exit_code, 1)
        self.assertIn("- delete/create google_firestore_database.feature_metadata", output)

    def test_repeated_exact_and_pattern_arguments_are_combined(self):
        plan = plan_with(
            ("google_storage_bucket.one", ["update"]),
            ("google_storage_bucket.two", ["update"]),
            ('google_storage_bucket_iam_member.viewer["member"]', ["create"]),
        )
        exit_code, output = self.run_main(
            plan,
            "--allowed-exact",
            "google_storage_bucket.one",
            "--allowed-exact",
            "google_storage_bucket.two",
            "--allowed-pattern",
            r"^google_storage_bucket_iam_member\.viewer\[",
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(output, "")

    def test_required_action_refuses_other_allowlisted_actions(self):
        plan = plan_with(
            ("google_storage_bucket_iam_member.old", ["delete"]),
            ("google_storage_bucket_iam_member.changed", ["update"]),
        )
        exit_code, output = self.run_main(
            plan,
            "--allowed-exact",
            "google_storage_bucket_iam_member.old\ngoogle_storage_bucket_iam_member.changed",
            "--require-action",
            "delete",
        )
        self.assertEqual(exit_code, 1)
        self.assertNotIn("- delete google_storage_bucket_iam_member.old", output)
        self.assertIn("- update google_storage_bucket_iam_member.changed", output)


if __name__ == "__main__":
    unittest.main()
