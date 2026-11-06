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
    seen = set()
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
                    probe = (flags, kind, source)
                    if probe not in seen:
                        probes.append(probe)
                        seen.add(probe)
                if probes:
                    yield pytest.param(probes, id=f"{path.name}/{job_id}/{index}-{step.get('name', step.get('id', 'run'))}")


@pytest.fixture(scope="session")
def isolated_python(tmp_path_factory):
    directory = tmp_path_factory.mktemp("hosted-python")

    @lru_cache(maxsize=None)
    def interpreter(flags):
        destination = directory / str(len(list(directory.iterdir())))
        environment = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(destination), "UV_PYTHON": TOOLCHAIN["python"],
                       "UV_CACHE_DIR": os.environ.get("UV_CACHE_DIR", str(ROOT / ".uv-cache")), "UV_LINK_MODE": "copy"}
        environment.pop("VIRTUAL_ENV", None)
        command = (["uv", "venv", "--no-project", "--python", TOOLCHAIN["python"], str(destination)] if flags is None else
                   ["uv", "sync", "--frozen", "--project", str(ROOT), *flags])
        result = subprocess.run(command, env=environment, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "VIRTUAL_ENV" not in result.stderr, result.stderr
        return destination / "bin/python"
    return interpreter


def workflow_environment_cases():
    groups = {}
    for row in workflow_cases():
        for flags, kind, source in row.values[0]:
            groups.setdefault(flags, []).append((kind, source))
    return [pytest.param(flags, probes, id=str(flags)) for flags, probes in groups.items()]


PROBE = '''
import ast, json, runpy, sys
from pathlib import Path
root, probes = sys.argv[1:]
sys.path[:0] = [root, str(Path(root) / "api/python/src")]
for kind, source in json.loads(probes):
    try:
        if kind == "script":
            sys.argv = [source]
            runpy.run_path(str(Path(root) / source), run_name="workflow_import_probe")
        else:
            imports = [node for node in ast.walk(ast.parse(source)) if isinstance(node, (ast.Import, ast.ImportFrom))]
            for node in imports:
                exec(compile(ast.Module(body=[node], type_ignores=[]), "workflow-inline", "exec"))
    except Exception as error:
        raise RuntimeError(source) from error
'''


@pytest.mark.parametrize("flags,probes", workflow_environment_cases())
def test_every_workflow_python_step_imports_in_its_declared_environment(isolated_python, flags, probes):
    result = subprocess.run([str(isolated_python(flags)), "-I", "-c", PROBE, str(ROOT), json.dumps(probes)],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_every_production_image_command_executes_in_empty_hosted_interpreter(isolated_python, tmp_path):
    from scripts.terraform_target_contracts import production_image_limits
    limits = tmp_path / 'limits.json'
    limits.write_text(json.dumps(production_image_limits()))
    program = '''
import sys, json
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.production_image_contracts import BUILD_TARGETS, commands
limits = json.loads(Path(sys.argv[2]).read_text())
for target in BUILD_TARGETS:
    assert len(commands(target, "a" * 40, limits=limits[target])) > 1, target
assert "hcl2" not in sys.modules and "yaml" not in sys.modules
'''
    result = subprocess.run([str(isolated_python(None)), '-I', '-c', program, str(ROOT), str(limits)],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_health_main_reports_missing_probe_dependencies_in_empty_interpreter(isolated_python, tmp_path):
    program = '''
import json, os, sys
from unittest.mock import Mock
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
from scripts import operational_safety_check as s, slack_notify
s.GitHub = Mock()
s.alerts.context = Mock(return_value={"id": 1})
s.policy_warnings = Mock(return_value=([], []))
s.audit_freshness = Mock(return_value=[])
s.deployed_image_lag = Mock(return_value=[])
s.previous_health = Mock(return_value={"problems":[], "warnings":[], "notified_at":None})
slack_notify.notify = Mock()
s.subprocess.check_call = Mock()
s.main()
report = json.loads((Path(sys.argv[-1]) / "health/cache.json").read_text())
assert any("(freshness): ModuleNotFoundError" in p for p in report["problems"])
slack_notify.notify.assert_called_once()
s.subprocess.check_call.assert_not_called()
'''
    result = subprocess.run([str(isolated_python(None)), '-I', '-c', program, str(ROOT),
        '--health', '--work-dir', str(tmp_path)], cwd=ROOT,
        env={**os.environ, 'SAFETY_PROBE_OUTCOME': 'skipped'}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.fixture(scope="module")
def selector_repository(tmp_path_factory):
    from test_ci_preflight import fixture_repo
    root, git = fixture_repo(tmp_path_factory.mktemp("selector-repository"))
    path = root / "terraform/envs/prod/main.tf"
    path.write_text(path.read_text() + "\n# representative infrastructure change\n")
    git("add", ".")
    git("commit", "-m", "changed production input")
    return root


@pytest.mark.parametrize("base", ["HEAD", "HEAD~1"])
def test_hosted_selectors_execute_with_actual_workflow_dependency_flags(isolated_python, tmp_path, base, selector_repository):
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
        result = subprocess.run([str(isolated_python(flags)), "-I", *arguments[python + 1:]], cwd=selector_repository,
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
def test_selector_negative_control_reaches_both_lazy_parser_dependencies(isolated_python, tmp_path, missing, selector_repository):
    program = '''
import importlib.abc, runpy, sys
missing = sys.argv[1]
class MissingDependency(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] == missing:
            raise ModuleNotFoundError("No module named " + repr(fullname))
sys.meta_path.insert(0, MissingDependency())
sys.argv = ["scripts/ci_preflight.py", "plan", "--base", "HEAD~1", "--head", "HEAD", "--output", sys.argv[2]]
runpy.run_path(sys.argv[0], run_name="__main__")
'''
    result = subprocess.run([str(isolated_python(("--only-group", "ci-plan"))), "-I", "-c", program, missing, str(tmp_path / "plan.json")],
                            cwd=selector_repository, capture_output=True, text=True)
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


def test_bootstrap_handles_cold_cache_and_exported_virtualenv(tmp_path, tmp_path_factory, monkeypatch):
    import shutil
    source = tmp_path / 'clean-checkout'
    source.mkdir()
    for name in ('pyproject.toml', 'uv.lock', 'README.md'):
        shutil.copyfile(ROOT / name, source / name)
    monkeypatch.setitem(globals(), 'ROOT', source)
    monkeypatch.setenv('VIRTUAL_ENV', '/home/runner/work/shared-datasets-1/.venv')
    monkeypatch.setenv('UV_CACHE_DIR', str(source / '.uv-cache'))
    interpreter = isolated_python.__wrapped__(tmp_path_factory)(("--only-group", "ci-plan"))
    done = subprocess.run([str(interpreter), '-I', '-c', 'import yaml, hcl2'], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_workflow_imports_keep_every_unique_program_environment_without_duplicate_probes():
    probes = [probe for row in workflow_cases() for probe in row.values[0]]
    assert len(probes) == len(set(probes))
    assert {None, (), ('--no-dev',), ('--only-group', 'ci-plan')} <= {p[0] for p in probes}


def test_batched_imports_reach_late_failures_without_ambient_site_packages(isolated_python):
    probes = [('inline', 'import json'), ('inline', 'import yaml')]
    result = subprocess.run([str(isolated_python(None)), '-I', '-c', PROBE, str(ROOT), json.dumps(probes)],
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "No module named 'yaml'" in result.stderr
    assert 'RuntimeError: import yaml' in result.stderr


def test_production_policy_paths_use_real_yaml_and_hcl_parsers_without_pytest_shims(isolated_python):
    from scripts.terraform_target_contracts import resource_index, workflow
    program = '''
import json, sys, yaml, hcl2
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.terraform_target_contracts import resource_index, workflow
assert yaml.safe_load.__module__ == 'yaml'
assert hcl2.loads.__module__.startswith('hcl2')
assert 'pytest' not in sys.modules and 'conftest' not in sys.modules
root = Path(sys.argv[1])
print(json.dumps([sorted(workflow(root, 'ci.yml')['jobs']),
                  {name: sorted(rows) for name, rows in resource_index(root).items()}]))
'''
    result = subprocess.run([str(isolated_python(('--only-group', 'ci-plan'))), '-I', '-c', program, str(ROOT)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [sorted(workflow(ROOT, 'ci.yml')['jobs']),
                                        {name: sorted(rows) for name, rows in resource_index(ROOT).items()}]
