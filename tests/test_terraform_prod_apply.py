from __future__ import annotations

import json
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import terraform_prod_apply


REQUIRED_VARS = [
    "--var",
    "wdpa_monthly_image=image",
    "--var",
    "sea_ice_daily_image=image",
    "--var",
    "eamlis_monthly_image=image",
]


class FakeRunner:
    def __init__(self, plan: dict | None = None, *, failure_stage: str = "") -> None:
        self.plan = plan or {"resource_changes": []}
        self.failure_stage = failure_stage
        self.calls: list[list[str]] = []
        self.audit_payloads: list[dict] = []

    def __call__(self, command, **_kwargs):
        command = list(command)
        self.calls.append(command)
        if "plan" in command:
            if self.failure_stage == "plan":
                return subprocess.CompletedProcess(command, 7, stdout="", stderr="bad plan")
            output = next(item.removeprefix("-out=") for item in command if item.startswith("-out="))
            Path(output).write_bytes(b"saved plan bytes")
            return subprocess.CompletedProcess(command, 0, stdout="planned", stderr="")
        if "show" in command:
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(self.plan), stderr="")
        if command[:3] == ["gcloud", "auth", "list"]:
            return subprocess.CompletedProcess(command, 0, stdout="operator@skytruth.org\n", stderr="")
        if command[:3] == ["git", "rev-parse", "HEAD"]:
            return subprocess.CompletedProcess(command, 0, stdout="abc123\n", stderr="")
        if command[-2:] == ["version", "-json"]:
            return subprocess.CompletedProcess(command, 0, stdout='{"terraform_version":"1.8.5"}', stderr="")
        if command[:3] == ["gcloud", "logging", "write"]:
            if self.failure_stage == "audit":
                return subprocess.CompletedProcess(command, 3, stdout="", stderr="audit denied")
            self.audit_payloads.append(json.loads(command[4]))
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if "apply" in command:
            if self.failure_stage == "apply":
                return subprocess.CompletedProcess(command, 9, stdout="", stderr="bad apply")
            return subprocess.CompletedProcess(command, 0, stdout="applied", stderr="")
        raise AssertionError(f"unexpected command: {command}")


def args(*extra: str) -> list[str]:
    return ["--break-glass", "--reason", "approved emergency", *REQUIRED_VARS, *extra]


def confirm_from_prompt(prompt: str) -> str:
    return prompt.split("'", 2)[1]


class TerraformProdApplyTests(unittest.TestCase):
    def test_resource_change_summary_ignores_reads_and_noops(self):
        plan = {
            "resource_changes": [
                {"address": "a", "change": {"actions": ["no-op"]}},
                {"address": "read", "change": {"actions": ["read"]}},
                {"address": "b", "change": {"actions": ["update"]}},
            ]
        }

        self.assertEqual(terraform_prod_apply.resource_change_summary(plan), ["update b"])

    def test_successful_apply_is_confirmed_audited_and_announced(self):
        runner = FakeRunner({"resource_changes": [{"address": "b", "change": {"actions": ["update"]}}]})

        with mock.patch.object(terraform_prod_apply, "notify", return_value=True) as notify:
            code = terraform_prod_apply.run_apply(
                args(),
                runner=runner,
                binary_resolver=lambda _value: "/safe/terraform",
                input_fn=confirm_from_prompt,
                stdin_isatty=lambda: True,
            )

        self.assertEqual(code, 0)
        self.assertEqual([event["event"] for event in runner.audit_payloads], ["apply_started", "apply_succeeded"])
        self.assertEqual(runner.audit_payloads[0]["resource_actions"], ["update b"])
        self.assertEqual(runner.audit_payloads[0]["operator"], "operator@skytruth.org")
        self.assertTrue(any("apply" in call for call in runner.calls))
        self.assertIn("succeeded", notify.call_args.kwargs["title"])

    def test_missing_break_glass_acknowledgement_is_usage_error(self):
        with self.assertRaises(SystemExit):
            terraform_prod_apply.run_apply(
                ["--reason", "emergency"],
                runner=FakeRunner(),
                stdin_isatty=lambda: True,
            )

    def test_noninteractive_execution_is_refused(self):
        with self.assertRaises(SystemExit):
            terraform_prod_apply.run_apply(args(), runner=FakeRunner(), stdin_isatty=lambda: False)

    def test_destructive_plan_requires_separate_opt_in(self):
        runner = FakeRunner(
            {"resource_changes": [{"address": "replace_me", "change": {"actions": ["delete", "create"]}}]}
        )

        with mock.patch.object(terraform_prod_apply, "notify", return_value=True):
            code = terraform_prod_apply.run_apply(
                args(),
                runner=runner,
                binary_resolver=lambda _value: "/safe/terraform",
                input_fn=confirm_from_prompt,
                stdin_isatty=lambda: True,
            )

        self.assertEqual(code, 2)
        self.assertFalse(any("apply" in call for call in runner.calls))

    def test_destructive_plan_can_be_explicitly_authorized(self):
        runner = FakeRunner(
            {"resource_changes": [{"address": "replace_me", "change": {"actions": ["delete", "create"]}}]}
        )

        with mock.patch.object(terraform_prod_apply, "notify", return_value=True):
            code = terraform_prod_apply.run_apply(
                args("--allow-destroy"),
                runner=runner,
                binary_resolver=lambda _value: "/safe/terraform",
                input_fn=confirm_from_prompt,
                stdin_isatty=lambda: True,
            )

        self.assertEqual(code, 0)

    def test_confirmation_must_match_exact_plan_hash(self):
        runner = FakeRunner()

        with mock.patch.object(terraform_prod_apply, "notify", return_value=True):
            code = terraform_prod_apply.run_apply(
                args(),
                runner=runner,
                binary_resolver=lambda _value: "/safe/terraform",
                input_fn=lambda _prompt: "apply shared-datasets-1 wrong",
                stdin_isatty=lambda: True,
            )

        self.assertEqual(code, 2)
        self.assertFalse(any("apply" in call for call in runner.calls))

    def test_audit_failure_blocks_apply(self):
        runner = FakeRunner(failure_stage="audit")

        with mock.patch.object(terraform_prod_apply, "notify", return_value=True):
            code = terraform_prod_apply.run_apply(
                args(),
                runner=runner,
                binary_resolver=lambda _value: "/safe/terraform",
                input_fn=confirm_from_prompt,
                stdin_isatty=lambda: True,
            )

        self.assertEqual(code, 3)
        self.assertFalse(any("apply" in call for call in runner.calls))

    def test_plan_failure_preserves_exit_code_and_posts_failure(self):
        runner = FakeRunner(failure_stage="plan")

        with mock.patch.object(terraform_prod_apply, "notify", return_value=True) as notify:
            code = terraform_prod_apply.run_apply(
                args(),
                runner=runner,
                binary_resolver=lambda _value: "/safe/terraform",
                input_fn=confirm_from_prompt,
                stdin_isatty=lambda: True,
            )

        self.assertEqual(code, 7)
        self.assertIn("failed", notify.call_args.kwargs["title"])

    def test_apply_failure_is_audited_as_started_and_reported(self):
        runner = FakeRunner(failure_stage="apply")

        with mock.patch.object(terraform_prod_apply, "notify", return_value=True) as notify:
            code = terraform_prod_apply.run_apply(
                args(),
                runner=runner,
                binary_resolver=lambda _value: "/safe/terraform",
                input_fn=confirm_from_prompt,
                stdin_isatty=lambda: True,
            )

        self.assertEqual(code, 9)
        self.assertEqual([event["event"] for event in runner.audit_payloads], ["apply_started"])
        self.assertIn("failed", notify.call_args.kwargs["title"])

    def test_binary_resolver_rejects_group_writable_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "terraform"
            binary.write_text("#!/bin/sh\n")
            binary.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR | stat.S_IWGRP)

            with self.assertRaisesRegex(ValueError, "group- or world-writable"):
                terraform_prod_apply.resolve_terraform_binary(str(binary))

    def test_binary_resolver_accepts_private_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "terraform"
            binary.write_text("#!/bin/sh\n")
            binary.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

            self.assertEqual(terraform_prod_apply.resolve_terraform_binary(str(binary)), str(binary.resolve()))


if __name__ == "__main__":
    unittest.main()
