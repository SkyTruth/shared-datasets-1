"""Exercise workflow imports with isolated Python and only declared locked deps.

Import probes never execute production CLIs. The selectors also run end to end,
including their lazy YAML/HCL imports and GitHub-output serialization.
"""
from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import textwrap
import tomllib

import pytest
import yaml

from scripts.ci_toolchain import TOOLCHAIN

ROOT = Path(__file__).resolve().parents[1]


def steps(job):
    """Include every local composite, including the receipt action."""
    for step in job.get("steps", []):
        action = step.get("uses", "")
        if action.startswith("./.github/actions/"):
            yield from steps({"steps": yaml.safe_load((ROOT / action / "action.yml").read_text())["runs"]["steps"]})
        else:
            yield step


def dependency_flags(command):
    tokens = shlex.split(command)
    flags = []
    for index, token in enumerate(tokens):
        if token in {"--no-dev", "--all-groups", "--no-project"}:
            flags.append(token)
        elif token in {"--group", "--only-group", "--extra"}:
            flags.extend([token, tokens[index + 1]])
    return tuple(flags)


def python_commands(run):
    """Resolve literal repo scripts, -c programs and shell heredocs."""
    run = re.sub(r"\$\{\{.*?\}\}", "fixture", run).replace("\\\n", " ")
    for line in run.splitlines():
        if line.lstrip().startswith(("echo ", "printf ", "#")):
            continue
        for match in re.finditer(r'(?:\bpython(?:3)?|"\$interpreter")(?:\s+-[IS])*\s+([\w./-]+\.py)', line):
            yield line, "script", match[1]
        for match in re.finditer(r"\bpython(?:3)?\s+-c\s+(['\"])(.*?)\1", line):
            yield line, "inline", match[2]
        if re.search(r"\buv run\s+pytest\b", line):
            yield line, "inline", "import pytest"
    for match in re.finditer(r"([^\n]*\bpython(?:3)?[^\n]*<<\s*['\"]?(\w+)['\"]?[^\n]*\n)(.*?)^\s*\2\s*$", run, re.M | re.S):
        yield match[1], "inline", textwrap.dedent(match[3])


def workflow_cases():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        for job_id, job in yaml.safe_load(path.read_text())["jobs"].items():
            synced = None
            uv_available = False
            for index, step in enumerate(steps(job)):
                if step.get("uses", "").startswith("astral-sh/setup-uv@"):
                    uv_available = True
                run = step.get("run", "")
                if "uv sync " in run:
                    synced = dependency_flags(next(line for line in run.splitlines() if "uv sync " in line))
                commands = list(python_commands(run))
                if not commands:
                    assert not re.search(r'(?m)^\s*(?:python3?|uv run|"\$interpreter")\s', run), (path.name, job_id, run)
                    continue
                probes = []
                for line, kind, source in commands:
                    flags = None  # setup-python and uv sync do not activate a venv.
                    if "uv run " in line:
                        assert uv_available, (path.name, job_id, step.get("name"), "uv not installed")
                        flags = synced if "--no-sync" in line else dependency_flags(line)
                        assert flags is not None, (path.name, job_id, step.get("name"), "uv run --no-sync before sync")
                        if "--no-project" in flags:
                            flags = None
                    elif '"$interpreter"' in line:
                        flags = synced  # Safety health explicitly selects .venv/bin/python.
                    probes.append((flags, kind, source))
                yield pytest.param(probes, id=f"{path.name}/{job_id}/{index}-{step.get('name', step.get('id', 'run'))}")


@pytest.fixture(scope="session")
def isolated_python(tmp_path_factory):
    directory = tmp_path_factory.mktemp("hosted-python")

    @lru_cache(maxsize=None)
    def interpreter(flags):
        destination = directory / str(len(list(directory.iterdir())))
        environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(destination), "UV_PYTHON": TOOLCHAIN["python"],
                       "UV_CACHE_DIR": str(ROOT / ".uv-cache"), "UV_LINK_MODE": "copy"}
        command = (["uv", "venv", "--no-project", "--python", TOOLCHAIN["python"], str(destination)] if flags is None else
                   ["uv", "sync", "--frozen", "--offline", "--project", str(ROOT), *flags])
        result = subprocess.run(command, env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return destination / "bin/python"
    return interpreter


PROBE = '''
import ast, runpy, sys
from pathlib import Path
root, kind, source = sys.argv[1:]
sys.path[:0] = [root, str(Path(root) / "api/python/src")]
if kind == "script":
    sys.argv = [source]
    runpy.run_path(str(Path(root) / source), run_name="workflow_import_probe")
else:
    # Resolve imports in inline programs without running cloud calls or writes.
    imports = [node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))]
    for node in imports:
        exec(compile(ast.Module(body=[node], type_ignores=[]), "workflow-inline", "exec"))
'''


@pytest.mark.parametrize("probes", list(workflow_cases()))
def test_every_workflow_python_step_imports_in_its_declared_environment(isolated_python, probes):
    for flags, kind, source in probes:
        result = subprocess.run([str(isolated_python(flags)), "-I", "-c", PROBE, str(ROOT), kind, source],
                                cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, f"{source}\n{result.stderr}"


@pytest.mark.parametrize("base", ["HEAD", "7bd972e"])
def test_hosted_selectors_execute_with_actual_workflow_dependency_flags(isolated_python, tmp_path, base):
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    plan = tmp_path / "plan.json"
    output = tmp_path / "github-output"
    selectors = [step["run"] for step in workflow["jobs"]["geospatial-changes"]["steps"] if step.get("id") in {"plan", "pr-plan"}]
    for command in selectors:
        arguments = shlex.split(command.replace("\\\n", " "))
        python = arguments.index("python")
        flags = dependency_flags(command) if "uv run " in command else None
        for key, value in {"$BASE_SHA": base, "$GITHUB_SHA": "HEAD", "$SOURCE_HEAD": "HEAD",
                           "$RUNNER_TEMP/ci-plan/plan.json": str(plan), "$GITHUB_OUTPUT": str(output)}.items():
            arguments = [argument.replace(key, value) for argument in arguments]
        result = subprocess.run([str(isolated_python(flags)), "-I", *arguments[python + 1:]], cwd=ROOT,
            env={**os.environ, "GITHUB_OUTPUT": str(output)}, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    bootstrap = next(step for step in workflow["jobs"]["geospatial-changes"]["steps"]
                     if step.get("uses", "").startswith("astral-sh/setup-uv@"))
    assert bootstrap["with"] == {"version": TOOLCHAIN["uv"], "python-version": TOOLCHAIN["python"], "enable-cache": True}
    payload = json.loads(plan.read_text())
    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert json.loads(values["production-image-targets"]) == payload["production_image_targets"]
    assert values["tested_sha"] == payload["tested_sha"]
    assert values["pr_terraform"] in {"true", "false"}


def test_ci_plan_lock_reuses_existing_versions_and_has_no_runtime_or_test_packages(isolated_python):
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    project = next(package for package in lock["package"] if package["name"] == "shared-datasets-1")
    assert {item["name"] for item in project["dev-dependencies"]["ci-plan"]} == {"pyyaml", "python-hcl2"}
    result = subprocess.run([str(isolated_python(("--only-group", "ci-plan"))), "-I", "-c",
        "import importlib.metadata,json; print(json.dumps(sorted(d.metadata['Name'].lower() for d in importlib.metadata.distributions())))"],
        capture_output=True, text=True, check=True)
    assert set(json.loads(result.stdout)) == {"pyyaml", "python-hcl2", "lark", "regex"}


@pytest.mark.parametrize("missing", ["yaml", "hcl2"])
def test_selector_negative_control_reaches_both_lazy_parser_dependencies(isolated_python, tmp_path, missing):
    program = '''
import importlib.abc, runpy, sys
missing = sys.argv[1]
class MissingDependency(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] == missing:
            raise ModuleNotFoundError("No module named " + repr(fullname))
sys.meta_path.insert(0, MissingDependency())
sys.argv = ["scripts/ci_preflight.py", "plan", "--base", "7bd972e", "--head", "HEAD", "--output", sys.argv[2]]
runpy.run_path(sys.argv[0], run_name="__main__")
'''
    result = subprocess.run([str(isolated_python(("--only-group", "ci-plan"))), "-I", "-c", program, missing, str(tmp_path / "plan.json")],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode != 0
    assert f"ModuleNotFoundError: No module named '{missing}'" in result.stderr
    assert not (tmp_path / "plan.json").exists()


def test_workflow_command_resolver_covers_inline_substitutions_and_heredocs():
    commands = list(python_commands('''python scripts/ci_preflight.py plan
value="$(python -c 'import json; print(json.dumps({}))')"
python - <<'PYTHON'
from scripts import ci_contract
PYTHON
'''))
    assert [(kind, source.strip()) for _, kind, source in commands] == [
        ("script", "scripts/ci_preflight.py"), ("inline", "import json; print(json.dumps({}))"),
        ("inline", "from scripts import ci_contract")]
