from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts import terraform_plan_allowlist
from workflow_helpers import load_workflow, workflow_steps_by_name


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests/fixtures/terraform_targets"
EXPECTED_IMAGE = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/eamlis-monthly@sha256:" + "a" * 64


def plan_with(*changes: tuple[str, list[str]]) -> dict:
    return {
        "resource_changes": [
            {"address": address, "change": {"actions": actions}}
            for address, actions in changes
        ]
    }


class TerraformPlanAllowlistTests(unittest.TestCase):
    def test_image_fixtures_through_real_workflow_steps_without_site_packages(self):
        cases = (
            ("allowed-image-update", 0, ""),
            ("wrong-image", 1, "unexpected image for"),
            ("unrelated-resource-change", 1, "update google_storage_bucket.shared_bucket"),
            ("replace", 1, "delete/create module."),
            ("delete", 1, "delete module."),
        )
        for target, image_variable in (("eamlis-monthly", "EAMLIS_MONTHLY_IMAGE"), ("sea-ice-daily", "SEA_ICE_DAILY_IMAGE")):
            workflow = load_workflow(REPO_ROOT / f".github/workflows/{target}-deploy.yml")
            script = workflow_steps_by_name(workflow, "deploy")[f"Enforce {target} resource-change allowlist"]["run"]
            for fixture, exit_code, message in cases:
                with self.subTest(target=target, fixture=fixture), tempfile.TemporaryDirectory() as tmp:
                    directory = Path(tmp)
                    binary = directory / "bin"
                    binary.mkdir()
                    # -S proves the workflow validator does not need uv sync or site packages.
                    python = binary / "python"
                    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" -S "$@"\n')
                    python.chmod(0o755)
                    text = (FIXTURES / f"job-image-{fixture}.json").read_text()
                    if target == "sea-ice-daily":
                        text = text.replace("eamlis_monthly_job", "sea_ice_daily_job").replace("eamlis-monthly", target)
                    (directory / f"{target}.tfplan.json").write_text(text)
                    result = subprocess.run(
                        ["bash", "-c", script], cwd=REPO_ROOT, capture_output=True, text=True,
                        env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                             "RUNNER_TEMP": str(directory), image_variable: EXPECTED_IMAGE.replace("eamlis-monthly", target)},
                    )
                    self.assertEqual(result.returncode, exit_code, result.stdout + result.stderr)
                    self.assertEqual(result.stderr, "")
                    if message:
                        self.assertIn(message, result.stdout)
                    else:
                        self.assertEqual(result.stdout, "")

    def test_image_target_requires_its_exact_address_and_paired_image(self):
        address = terraform_plan_allowlist.JOB_IMAGE_TARGETS["eamlis-monthly"]
        valid = ["--allowed-exact", address, "--job-image-target", "eamlis-monthly", "--expected-image", EXPECTED_IMAGE]
        for args in (
            valid[:-2], valid[:-1] + [""],
            ["--allowed-exact", address, "--expected-image", EXPECTED_IMAGE],
            ["--allowed-exact", "other.resource", *valid[2:]],
            ["--allowed-exact", address + "\nother.resource", *valid[2:]],
            [*valid, "--allowed-patterns", ".*"],
            [*valid[:3], "wdpa-monthly", *valid[4:]],
        ):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                self.run_main(plan_with(), *args)
            self.assertEqual(error.exception.code, 2)

    def test_image_target_ignores_empty_noop_and_read_plans(self):
        address = terraform_plan_allowlist.JOB_IMAGE_TARGETS["eamlis-monthly"]
        for plan in (plan_with(), plan_with((address, ["no-op"]), ("data.google_project.current", ["read"])),
                     plan_with((address, []))):
            with self.subTest(plan=plan):
                exit_code, output = self.run_main(
                    plan, "--allowed-exact", address, "--job-image-target", "eamlis-monthly", "--expected-image", EXPECTED_IMAGE,
                )
                self.assertEqual((exit_code, output), (0, ""))

    def test_image_target_refuses_create_reverse_replace_and_missing_image(self):
        address = terraform_plan_allowlist.JOB_IMAGE_TARGETS["eamlis-monthly"]
        for actions in (["create"], ["create", "delete"], ["update"]):
            with self.subTest(actions=actions):
                exit_code, output = self.run_main(
                    plan_with((address, actions)), "--allowed-exact", address,
                    "--job-image-target", "eamlis-monthly", "--expected-image", EXPECTED_IMAGE,
                )
                self.assertEqual(exit_code, 1)
                self.assertIn("unexpected image" if actions == ["update"] else "/".join(actions), output)

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

    def test_existing_callers_still_allow_allowlisted_creates_deletes_and_replaces(self):
        for actions in (["create"], ["delete"], ["delete", "create"]):
            with self.subTest(actions=actions):
                self.assertEqual(self.run_main(plan_with(("allowed.resource", actions)), "--allowed-exact", "allowed.resource"), (0, ""))


if __name__ == "__main__":
    unittest.main()
