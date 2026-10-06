"""The interpreter executing locked Python commands must match recorded pins."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest

from scripts import ci_preflight
from scripts.ci_contract import SUITES, TOOLCHAIN, expected_tools


@pytest.mark.parametrize("actual,success", [(TOOLCHAIN["python"], True), ("3.13.1", False)])
def test_executing_interpreter_proof_rejects_a_different_version(actual, success):
    command = ci_preflight.interpreter_command()
    program = f"import platform\nplatform.python_version = lambda: {actual!r}\n" + command[-1]
    completed = subprocess.run(
        [sys.executable, "-O", "-c", program],
        text=True, capture_output=True, check=False,
    )
    assert (completed.returncode == 0) is success
    assert f"Validation interpreter: {actual}" in completed.stdout
    if not success:
        assert "Required validation interpreter" in completed.stderr


@pytest.mark.parametrize("suite", SUITES)
def test_each_locked_sync_is_followed_by_executing_interpreter_evidence(tmp_path, suite):
    plan = {"base": "a" * 40, "tested_sha": "b" * 40}
    commands = [command for command, _ in ci_preflight.suite_commands(suite, Path.cwd(), plan, tmp_path)]
    for index, command in enumerate(commands):
        if command[:2] == ["uv", "sync"]:
            assert commands[index + 1] == ci_preflight.interpreter_command()


def test_suite_pins_uv_interpreter_selection(tmp_path):
    plan = {key: "fixture" for key in ("base", "head", "tested_sha", "tree", "contract_digest")}
    plan["suites"] = ["tests"]
    with (
        mock.patch.object(ci_preflight, "validate_checkout"),
        mock.patch.object(ci_preflight, "probe_tools", return_value=expected_tools("tests")),
        mock.patch.object(ci_preflight, "suite_commands", return_value=[(["controlled-failure"], Path.cwd())]),
        mock.patch.object(ci_preflight.subprocess, "run", return_value=subprocess.CompletedProcess([], 1)) as run,
    ):
        result = ci_preflight.run_suite(Path.cwd(), plan, "tests", tmp_path)
    assert result["status"] == "failure"
    assert run.call_args.kwargs["env"]["UV_PYTHON"] == TOOLCHAIN["python"]
