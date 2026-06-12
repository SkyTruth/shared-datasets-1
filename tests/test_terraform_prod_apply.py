from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from scripts import terraform_prod_apply


class TerraformProdApplyTests(unittest.TestCase):
    def test_resource_change_summary_ignores_noops(self):
        plan = {
            "resource_changes": [
                {"address": "a", "change": {"actions": ["no-op"]}},
                {"address": "b", "change": {"actions": ["update"]}},
            ]
        }

        self.assertEqual(terraform_prod_apply.resource_change_summary(plan), ["update b"])

    def test_successful_apply_does_not_post_summary(self):
        calls = []

        def runner(command, **_kwargs):
            calls.append(command)
            if "show" in command:
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"resource_changes": []}), stderr="")
            if "output" in command:
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({}), stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with mock.patch.object(terraform_prod_apply, "notify", return_value=True) as notify:
            code = terraform_prod_apply.run_apply(
                [
                    "--terraform-bin",
                    "terraform",
                    "--plan-file",
                    str(Path("/tmp/test.tfplan")),
                    "--var",
                    "wdpa_monthly_image=image",
                    "--var",
                    "sea_ice_daily_image=image",
                    "--var",
                    "eamlis_monthly_image=image",
                    "--slack-dry-run",
                ],
                runner=runner,
            )

        self.assertEqual(code, 0)
        self.assertTrue(any("plan" in call for call in calls))
        self.assertTrue(any("apply" in call for call in calls))
        self.assertFalse(any("output" in call for call in calls))
        notify.assert_not_called()

    def test_plan_failure_posts_failure_and_preserves_exit_code(self):
        def runner(command, **_kwargs):
            if "plan" in command:
                return subprocess.CompletedProcess(command, 7, stdout="", stderr="bad plan")
            return subprocess.CompletedProcess(command, 0, stdout="image", stderr="")

        with mock.patch.object(terraform_prod_apply, "notify", return_value=True) as notify:
            code = terraform_prod_apply.run_apply(
                [
                    "--terraform-bin",
                    "terraform",
                    "--plan-file",
                    str(Path("/tmp/test.tfplan")),
                    "--var",
                    "wdpa_monthly_image=image",
                    "--var",
                    "sea_ice_daily_image=image",
                    "--var",
                    "eamlis_monthly_image=image",
                ],
                runner=runner,
            )

        self.assertEqual(code, 7)
        self.assertIn("failed", notify.call_args.kwargs["title"])

    def test_apply_failure_posts_failure_and_preserves_exit_code(self):
        def runner(command, **_kwargs):
            if "show" in command:
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"resource_changes": []}), stderr="")
            if "apply" in command:
                return subprocess.CompletedProcess(command, 9, stdout="", stderr="bad apply")
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

        with mock.patch.object(terraform_prod_apply, "notify", return_value=True) as notify:
            code = terraform_prod_apply.run_apply(
                [
                    "--terraform-bin",
                    "terraform",
                    "--plan-file",
                    str(Path("/tmp/test.tfplan")),
                    "--var",
                    "wdpa_monthly_image=image",
                    "--var",
                    "sea_ice_daily_image=image",
                    "--var",
                    "eamlis_monthly_image=image",
                ],
                runner=runner,
            )

        self.assertEqual(code, 9)
        self.assertIn("failed", notify.call_args.kwargs["title"])


if __name__ == "__main__":
    unittest.main()
