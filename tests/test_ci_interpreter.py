"""The interpreter executing locked Python commands must match recorded pins."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import sysconfig
from unittest import mock

import pytest
import yaml

from scripts import ci_preflight
from scripts.ci_contract import SUITES, TOOLCHAIN, expected_tools

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("installed", [True, False])
def test_hosted_python_bootstrap_exports_the_real_default_interpreter(tmp_path, installed):
    """Run the actual uv installer and consume its GitHub PATH output offline."""
    action = yaml.safe_load((ROOT / ".github/actions/ci-python/action.yml").read_text())
    bootstrap = action["runs"]["steps"][-1]
    managed = tmp_path / "managed"
    managed.mkdir()
    ambient = tmp_path / "ambient"
    ambient.mkdir()
    wrong_python = ambient / "python"
    wrong_python.write_text("#!/bin/sh\nprintf '3.12.3\\n'\n")
    wrong_python.chmod(0o755)
    environment = {**os.environ, "PATH": str(ambient) + os.pathsep + os.environ["PATH"],
                   "UV_OFFLINE": "1", "UV_CACHE_DIR": str(tmp_path / "cache"),
                   "UV_PYTHON_INSTALL_DIR": str(managed), "UV_PYTHON_BIN_DIR": str(tmp_path / "bin"),
                   "GITHUB_PATH": str(tmp_path / "github-path")}
    environment.pop("VIRTUAL_ENV", None)
    assert subprocess.check_output(["python", "--version"], env=environment, text=True).strip() == "3.12.3"
    if installed:
        # Seed uv's managed-installation boundary with the real pinned runtime;
        # this also works in CI images where Python lives under /usr/local.
        assert subprocess.check_output([sys.executable, "-c", "import platform; print(platform.python_version())"],
                                       text=True).strip() == TOOLCHAIN["python"]
        available = json.loads(subprocess.check_output(
            ["uv", "python", "list", "--only-downloads", "--output-format", "json", TOOLCHAIN["python"]],
            env=environment, text=True))
        key = next(item["key"] for item in available
                   if item["implementation"] == "cpython" and item["variant"] == "default")
        installation = managed / key
        (installation / "bin").mkdir(parents=True)
        (installation / "lib/python3.12").mkdir(parents=True)
        (installation / "bin/python3.12").symlink_to(sys._base_executable)
        for config in Path(sysconfig.get_path("stdlib")).glob("_sysconfigdata_*.py"):
            shutil.copyfile(config, installation / "lib/python3.12" / config.name)
    result = subprocess.run(["bash", "-euo", "pipefail", "-c", bootstrap["run"]],
                            cwd=tmp_path, env=environment, text=True, capture_output=True)
    assert (result.returncode == 0) is installed, result.stdout + result.stderr
    path_file = Path(environment["GITHUB_PATH"])
    if not installed:
        assert not path_file.exists()
        return
    exported, = path_file.read_text().splitlines()
    assert exported == environment["UV_PYTHON_BIN_DIR"]
    environment["PATH"] = exported + os.pathsep + environment["PATH"]
    actual = subprocess.check_output(["python", "-I", "-c", "import platform; print(platform.python_version())"],
                                     cwd=tmp_path, env=environment, text=True).strip()
    assert actual == TOOLCHAIN["python"]
    assert not (tmp_path / ".venv").exists()


# A shell command whose program is the PATH `python`, not `uv run` or a container.
BARE_PYTHON = re.compile(r"(?m)(?:^\s*(?:\w+=\S*\s+)*|\$\(\s*|[|&;]\s*)python3?\s")


def local_steps(steps):
    for step in steps:
        action = step.get("uses", "")
        if action.startswith("./.github/actions/"):
            yield from local_steps(yaml.safe_load((ROOT / action / "action.yml").read_text())["runs"]["steps"])
        else:
            yield step


def installs_default_python(step):
    return "uv python install --default" in step.get("run", "")


def puts_python_on_path(step):
    return installs_default_python(step) or step.get("uses", "").startswith("actions/setup-python@")


def workflow_jobs():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        for job_id, job in yaml.safe_load(path.read_text())["jobs"].items():
            yield pytest.param(path.name, list(local_steps(job.get("steps", []))), id=f"{path.name}/{job_id}")


@pytest.mark.parametrize("workflow,steps", [case for case in workflow_jobs() if case.values[0] == "ci.yml"])
def test_hosted_validation_installs_default_python_before_bare_python_commands(workflow, steps):
    bare = [i for i, step in enumerate(steps) if BARE_PYTHON.search(step.get("run", ""))]
    if bare:
        assert any(installs_default_python(step) for step in steps[:bare[0]])


@pytest.mark.parametrize("workflow,steps", workflow_jobs())
def test_setup_uv_never_stands_in_for_the_bare_python_pin(workflow, steps):
    """setup-uv >= 6 selects uv's interpreter without activating a venv on PATH."""
    uv_installed = False
    for step in steps:
        if puts_python_on_path(step):
            return
        uv_installed = uv_installed or step.get("uses", "").startswith("astral-sh/setup-uv@")
        assert not (uv_installed and BARE_PYTHON.search(step.get("run", ""))), step.get("name")


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
    plan = {"base": "a" * 40, "tested_sha": "b" * 40,
            "production_image_limits": ci_preflight.production_image_limits(), **ci_preflight.resolve_selection(None)}
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
