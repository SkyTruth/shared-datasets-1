from __future__ import annotations

import copy
from contextlib import nullcontext
from functools import lru_cache
import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import Barrier, Lock
from unittest import mock

import pytest

from scripts import ci_contract, ci_preflight as preflight
from scripts.check_geospatial_test_results import REQUIRED_TESTS, check_junit
from scripts.check_workflow_syntax import parser_source
from scripts.ci_contract import ALWAYS, CONTRACT_FILES, INGESTION_DEPLOYMENTS, SUITES, TOOLCHAIN, expected_tools, resolve_selection, verify_results
from workflow_helpers import load_workflow, workflow_triggers
from ci_result_fixtures import production_images
from scripts.tested_image_bundle import BUILD_TARGETS


ROOT = Path(__file__).resolve().parents[1]


def plan(suites=SUITES):
    return {
        "schema_version": 1, "base": "a" * 40, "head": "b" * 40,
        "tested_sha": "c" * 40, "tree": "d" * 40,
        "production_image_limits": preflight.production_image_limits(),
        "contract_digest": "e" * 64, "suites": list(suites), "production_image_targets": list(BUILD_TARGETS) if "production-images" in suites else [],
    }


def results_for(validation):
    results = [{
        **{field: validation[field] for field in ("base", "head", "tested_sha", "tree", "contract_digest")},
        "suite": suite, "status": "success", "tools": expected_tools(suite),
        "commands": [{"argv": ["executed-test"], "exit_code": 0}],
    } for suite in validation["suites"]]
    for result in results:
        if result["suite"] == "production-images":
            result["production_images"] = production_images(validation["tested_sha"])
            result["production_images"]["images"] = {target: image for target, image in result["production_images"]["images"].items() if target in validation["production_image_targets"]}
    return results


def jobs_for(validation):
    jobs = {"geospatial-changes": {"result": "success"}}
    for suite in ("lint", "tests", "geospatial-integration", "production-images", "browser"):
        jobs[suite] = {"result": "success" if suite in validation["suites"] else "skipped"}
    jobs["sdk-validation"] = {"result": "success" if "sdk-node22" in validation["suites"] else "skipped"}
    return jobs


@pytest.mark.parametrize("path", ["new/component.ext", "scripts/new-runtime.py", ".github/workflows/new.yml", "uv.lock"])
def test_unknown_paths_and_shared_dependencies_select_every_suite(path):
    assert resolve_selection([path])["suites"] == list(SUITES)
    assert resolve_selection(None)["suites"] == list(SUITES)


@pytest.mark.parametrize("path", [
    "scripts/README.md", "scripts/operator-notes.md",
    "ingestion/README.md", "ingestion/wdpa_monthly/README.md",
    "ingestion/common/nested/operations.md", "services/README.md",
    "services/catalog_viewer/README.md", "services/nested/docs/operations.md",
    "api/python/README.md", "api/nested/package/README.md",
    "scripts/nested/README.md", ".github/README.md", "terraform/modules/cloud_run_job/README.md",
    "web/README.md", "tests/browser/README.md", "api/python/notes.md",
])
def test_component_documentation_only_selects_always(path):
    assert set(resolve_selection([path])["suites"]) == ALWAYS


@pytest.mark.parametrize("path", [
    "tests/fixtures/README.md", "scripts/README.md.py", "pyproject.toml",
])
def test_documentation_exception_does_not_hide_unknown_or_shared_dependencies(path):
    assert resolve_selection([path])["suites"] == list(SUITES)


def test_documentation_does_not_reduce_other_changed_path_requirements():
    paths = ["scripts/README.md", "ingestion/wdpa_monthly/run.py"]
    assert set(resolve_selection(paths)["suites"]) == ALWAYS | {"geospatial-integration", "production-images", "browser"}
    assert resolve_selection(["scripts/README.md", "scripts/unproved.py"])["suites"] == list(SUITES)


@pytest.mark.parametrize("path", ["scripts/slack_incidents.py", "scripts/operational_safety_check.py",
    "scripts/repo_alerts.py", ".github/workflows/operational-safety-check.yml"])
def test_unrelated_automation_uses_core_suites_without_image_builds(path):
    assert set(resolve_selection([path])["suites"]) == ALWAYS
    assert resolve_selection([path])["production_image_targets"] == []
    component = "ingestion/mpatlas_monthly/run.py"
    assert resolve_selection([path, component])["suites"] == resolve_selection([component])["suites"]
    assert resolve_selection([path, component])["production_image_targets"] == ["mpatlas-monthly"]
    assert set(resolve_selection([path, "scripts/ci_contract.py"])["suites"]) == ALWAYS


def test_generated_import_closure_includes_relative_imports_packages_and_script_calls(tmp_path, monkeypatch):
    files = {"ingestion/mpatlas_monthly/Dockerfile":
        'FROM python:3.12\nCOPY --from=builder /binary /bin/binary\nCOPY ingestion/mpatlas_monthly /app/job\nCOPY ["scripts/entry.py", "/app/entry.py"]\n',
        "ingestion/mpatlas_monthly/run.py": "from . import worker\n",
        "ingestion/mpatlas_monthly/worker.py": "from scripts import shared\n",
        "scripts/shared.py": "import scripts.leaf\nsubprocess.run(['python', 'scripts/entry.py'])\n",
        "scripts/leaf.py": "", "scripts/entry.py": "", "scripts/__init__.py": "",
        "scripts/unrelated.py": ""}
    for name, contents in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    with monkeypatch.context() as context:
        context.setattr(ci_contract, "ROOT", tmp_path)
        context.setattr(ci_contract, "RECIPES", {"mpatlas-monthly": "ingestion/mpatlas_monthly/Dockerfile"})
        context.setattr(ci_contract, "registry", lambda *args, **kwargs: {})
        context.setattr(ci_contract, "NATIVE_TESTS", ("ingestion/mpatlas_monthly/run.py",))
        ci_contract.dependency_inputs.cache_clear()
        images, native, *_ = ci_contract.dependency_inputs()
        inputs = images["mpatlas-monthly"]
        assert {"scripts/shared.py", "scripts/leaf.py", "scripts/entry.py", "scripts/__init__.py"} <= native
        assert "scripts/entry.py" in inputs
        assert "scripts/shared.py" not in inputs and "scripts/leaf.py" not in inputs
        assert "scripts/unrelated.py" not in inputs
        assert ci_contract.matches("ingestion/mpatlas_monthly/new.py", inputs)
        assert not ci_contract.matches("ingestion/mpatlas_monthly_other/new.py", inputs)
        assert ci_contract.matches("scripts/leaf.py", {"scripts/*.py"})
        assert ci_contract.matches("new/component.py", {"/"})
        assert "/binary" not in inputs
    ci_contract.dependency_inputs.cache_clear()


@pytest.mark.parametrize("paths,expected", [
    (["ingestion/eamlis_monthly/checksum_repair.py", "tests/test_eamlis_checksum_repair.py"], {"eamlis-monthly"}),
    (["ingestion/mpatlas_monthly/run.py", "tests/test_mpatlas_monthly.py"], {"mpatlas-monthly"}),
    (["ingestion/sea_ice_daily/run.py"], {"sea-ice-daily"}),
    (["ingestion/wdpa_monthly/build.py"], {"wdpa-monthly"}),
    (["ingestion/common/runtime.py"], {"mpatlas-monthly", "eamlis-monthly", "sea-ice-daily", "wdpa-monthly", "catalog-viewer"}),
    (["ingestion/__init__.py"], set(BUILD_TARGETS) - {"dataset-usage"}),
    (["ingestion/common/decision_events.py"], {"mpatlas-monthly", "eamlis-monthly", "sea-ice-daily", "wdpa-monthly", "monitoring-slack", "catalog-viewer"}),
    (["services/catalog_viewer/run.py"], {"catalog-viewer"}),
    (["services/feature_preview_service/run.py"], {"catalog-viewer"}),
    (["services/http_base.py"], {"catalog-viewer"}),
    (["api/python/src/skytruth_shared_datasets/client.py"], {"catalog-viewer"}),
    (["services/monitoring_slack/run.py"], {"monitoring-slack"}),
    (["ingestion/dataset_usage/run.py"], {"dataset-usage"}),
    (["docs/assets/ims-sea-ice-extent.md"], {"sea-ice-daily"}),
    (["terraform/envs/prod/eamlis_monthly.tf"], {"eamlis-monthly"}),
    (["terraform/envs/prod/catalog_viewer.tf"], {"catalog-viewer"}),
    (["terraform/envs/prod/mpatlas_monthly.tf"], {"mpatlas-monthly"}),
    (["terraform/envs/prod/scheduled_ingestion_deploy_iam.tf"], set()),
    (["README.md"], set()),
])
def test_image_selection_follows_component_and_installed_probe_inputs(paths, expected):
    assert set(resolve_selection(paths)["production_image_targets"]) == expected
    assert resolve_selection(paths)["production_image_targets"] == resolve_selection(list(reversed(paths)))["production_image_targets"]


@pytest.mark.parametrize("path", [
    source
    for line in (ROOT / "services/catalog_viewer/Dockerfile").read_text().splitlines()
    if line.startswith("COPY ")
    for source in shlex.split(line)[1:-1]
    if source.startswith("ingestion/")
])
def test_viewer_copied_ingestion_inputs_select_its_image_and_browser(path):
    assert "catalog-viewer" in resolve_selection([path])["production_image_targets"]
    assert "browser" in resolve_selection([path])["suites"]


@pytest.mark.parametrize("paths", [None, ["unknown/input"], ["scripts/new-runtime.py"],
    ["tests/new_contract.py"], ["scripts/production_image_contracts.py"],
    ["ingestion/eamlis_monthly/run.py", "scripts/new-runtime.py"]])
def test_unproved_image_inputs_keep_every_recipe(paths):
    from scripts.tested_image_bundle import BUILD_TARGETS
    assert resolve_selection(paths)["production_image_targets"] == list(BUILD_TARGETS)


@pytest.mark.parametrize("paths", ["ingestion/eamlis_monthly/run.py", [None], [1], {}])
def test_malformed_image_comparison_fails(paths):
    with pytest.raises(ValueError, match="changed-path list"):
        resolve_selection(paths)["production_image_targets"]


@pytest.mark.parametrize("script", ["scripts/catalog_site.py", "scripts/release_feature_model.py", "scripts/compare_releases.py"])
def test_browser_fixture_imports_and_script_calls_keep_browser_coverage(script):
    assert "browser" in resolve_selection([script])["suites"]


def test_cross_component_dependencies_and_explicit_unselected_suites():
    assert set(resolve_selection(["docs/consumer-guide.md"])["suites"]) == ALWAYS
    assert set(resolve_selection(["api/python/src/skytruth_shared_datasets/snapshot.py"])["suites"]) == ALWAYS | {"sdk-node22", "sdk-node24", "browser", "production-images"}
    assert set(resolve_selection(["catalog/shared-datasets-catalog.csv"])["suites"]) == set(SUITES)
    assert "geospatial-integration" in resolve_selection(["ingestion/common/reset.py"])["suites"]
    assert "geospatial-integration" in resolve_selection(["tests/test_wdpa_translation_inputs.py"])["suites"]


@pytest.mark.parametrize("suites", [SUITES, tuple(ALWAYS)])
def test_complete_results_pass_with_only_expected_job_skips(suites):
    validation = plan(suites)
    verify_results(validation, results_for(validation), jobs_for(validation))


@pytest.mark.parametrize('suite', ['browser', 'geospatial-integration', 'production-images'])
def test_cross_run_reuse_cannot_substitute_for_fresh_validation(suite):
    validation = plan(ALWAYS | {suite})
    results = results_for(validation)
    wrapper = next(result for result in results if result['suite'] == suite)
    wrapper['reused_from'] = {'pr_number': 42, 'run_id': 100, 'run_attempt': 1}
    with pytest.raises(ValueError, match='cross-run validation reuse'):
        verify_results(validation, results, jobs_for(validation))


@pytest.mark.parametrize("defect", ["missing", "duplicate", "failure", "cancelled", "skipped", "no-commands", "failed-command", "wrong-tools"])
def test_selected_suite_cannot_be_missing_or_fabricate_success(defect):
    validation = plan()
    results = results_for(validation)
    if defect == "missing":
        results.pop()
    elif defect == "duplicate":
        results.append(copy.deepcopy(results[0]))
    elif defect == "no-commands":
        results[0]["commands"] = []
    elif defect == "failed-command":
        results[0]["commands"][0]["exit_code"] = 1
    elif defect == "wrong-tools":
        results[0]["tools"]["python"] = "3.11.0"
    else:
        results[0]["status"] = defect
    with pytest.raises(ValueError):
        verify_results(validation, results, jobs_for(validation))


@pytest.mark.parametrize("field", ["base", "head", "tested_sha", "tree", "contract_digest"])
def test_evidence_is_invalid_after_revision_tree_or_toolchain_change(field):
    validation = plan()
    results = results_for(validation)
    results[0][field] = "changed"
    with pytest.raises(ValueError, match="stale or mismatched"):
        verify_results(validation, results)


@pytest.mark.parametrize("outcome", ["failure", "cancelled", "skipped", None])
def test_failed_or_missing_sdk_matrix_job_cannot_pass_gate(outcome):
    validation = plan()
    jobs = jobs_for(validation)
    jobs["sdk-validation"] = {"result": outcome}
    with pytest.raises(ValueError, match="SDK matrix"):
        verify_results(validation, results_for(validation), jobs)


def test_each_node_matrix_entry_is_required_even_if_aggregate_job_claims_success():
    validation = plan()
    results = [result for result in results_for(validation) if result["suite"] != "sdk-node24"]
    with pytest.raises(ValueError, match="missing suite results"):
        verify_results(validation, results, jobs_for(validation))


def test_unselected_suite_cannot_run_or_return_an_unexpected_result():
    validation = plan(tuple(ALWAYS))
    unexpected = results_for(plan(["browser"]))[0]
    with pytest.raises(ValueError, match="unexpected suite"):
        verify_results(validation, [*results_for(validation), unexpected])
    jobs = jobs_for(validation)
    jobs["browser"]["result"] = "success"
    with pytest.raises(ValueError, match="expected skipped"):
        verify_results(validation, results_for(validation), jobs)


def test_empty_collection_and_unexpected_skips_fail(tmp_path):
    report = tmp_path / "pytest.xml"
    report.write_text('<testsuite tests="100"/>')
    with pytest.raises(ValueError, match="empty"):
        check_junit(report, native=False)
    report.write_text('<testsuite><testcase classname="ordinary" name="test_behavior"><skipped/></testcase></testsuite>')
    with pytest.raises(ValueError, match="unexpected skipped"):
        check_junit(report, native=False)
    classname, name = REQUIRED_TESTS[0]
    report.write_text(f'<testsuite><testcase classname="{classname}" name="{name}"><skipped/></testcase></testsuite>')
    check_junit(report, native=False)
    with pytest.raises(ValueError, match="unexpected skipped"):
        check_junit(report, native=True)


def test_missing_tools_and_wrong_versions_fail_before_commands(tmp_path):
    validation = plan(["tests"])
    with mock.patch.object(preflight, "validate_checkout"), mock.patch.object(preflight, "probe_tools", side_effect=FileNotFoundError("gitleaks missing")):
        result = preflight.run_suite(ROOT, validation, "tests", tmp_path)
    assert result["status"] == "failure"
    assert not result["commands"]
    assert json.loads((tmp_path / "result.json").read_text())["status"] == "failure"
    with mock.patch.object(preflight.subprocess, "check_output", return_value="Python 3.11.0"):
        with pytest.raises(ValueError, match="required 3.12.12"):
            preflight.probe_tools("tests", dict(os.environ))


@pytest.mark.parametrize("outcome", ["pass", "fail", "crash"])
def test_parallel_python_command_preserves_failures_and_worker_crashes(tmp_path, outcome):
    fixture = tmp_path / "test_workers.py"
    fixture.write_text(
        "import os\nfrom pathlib import Path\nimport pytest\n"
        "@pytest.mark.parametrize('case', range(4))\n"
        "def test_case(case):\n"
        f"    if case == 0 and {outcome!r} == 'crash': os._exit(7)\n"
        f"    Path({str(tmp_path)!r}, f'case-{{case}}.txt').write_text(os.environ['PYTEST_XDIST_WORKER'])\n"
        f"    assert case != 0 or {outcome!r} != 'fail'\n"
    )
    command = next(command for command, _ in preflight.suite_commands("tests", ROOT, plan(), tmp_path) if "pytest" in command)
    completed = subprocess.run(
        [*command, str(fixture)], cwd=ROOT, capture_output=True, text=True, timeout=60,
        env={key: value for key, value in os.environ.items() if not key.startswith("PYTEST_XDIST_WORKER")},
    )
    if outcome == "pass":
        assert completed.returncode == 0, completed.stdout + completed.stderr
        check_junit(tmp_path / "pytest.xml", native=False)
        assert {path.name for path in tmp_path.glob("case-*.txt")} == {f"case-{case}.txt" for case in range(4)}
        assert {path.read_text() for path in tmp_path.glob("case-*.txt")} == {f"gw{worker}" for worker in range(4)}
    else:
        assert completed.returncode != 0
        with pytest.raises(ValueError, match="failed test"):
            check_junit(tmp_path / "pytest.xml", native=False)
        assert "replacing crashed worker" not in completed.stdout.lower()


@pytest.mark.parametrize("failed_check", ["audit_shared_datasets.py", "check_identity_resolutions.py"])
def test_local_hygiene_failure_stops_python_validation(tmp_path, failed_check):
    validation = plan(["tests"])

    def execute(command, **kwargs):
        failure = any(argument.endswith(failed_check) for argument in command)
        return subprocess.CompletedProcess(command, 7 if failure else 0)

    with (
        mock.patch.object(preflight, "validate_checkout"),
        mock.patch.object(preflight, "probe_tools", return_value=expected_tools("tests")),
        mock.patch.object(preflight.subprocess, "run", side_effect=execute) as run,
    ):
        result = preflight.run_suite(ROOT, validation, "tests", tmp_path)
    assert result["status"] == "failure"
    assert result["commands"][-1]["exit_code"] == 7
    assert any(argument.endswith(failed_check) for argument in result["commands"][-1]["argv"])
    assert "pytest" not in {argument for call in run.call_args_list for argument in call.args[0]}


@pytest.mark.parametrize("script", ["scripts/ci_preflight.py", "scripts/production_image_contracts.py"])
def test_image_host_entrypoints_require_only_standard_library(script):
    completed = subprocess.run([preflight.sys.executable, "-I", "-S", script, "--help"],
                               cwd=ROOT, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def test_image_suite_uses_verified_interpreter_without_installing_host_dependencies(tmp_path):
    commands = preflight.suite_commands("production-images", ROOT, plan(), tmp_path)
    assert len(commands) == len(BUILD_TARGETS)
    command, cwd = commands[0]
    assert command[:2] == [preflight.sys.executable, "-I"]
    # Exercise the actual command entrypoint with site packages disabled.
    completed = subprocess.run([command[0], "-S", *command[1:command.index("--output")], "--help"],
                               cwd=cwd, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr


def fixture_git(root):
    executable = os.environ.get("CI_GIT", "git")
    environment = {**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@invalid.example", "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@invalid.example"}

    def run(*args):
        return subprocess.check_output([executable, "-C", str(root), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args], env=environment, text=True).strip()
    return run


@lru_cache(maxsize=4)
def fixture_template(source, tree, overrides):
    # One immutable object store per source tree; each case gets a private index,
    # branch, working tree and new objects; cases only mutate their clones.
    from scripts.tested_image_bundle import repository_files
    work = Path(os.environ.get("SHARED_DATASETS_WORKDIR", Path(tempfile.gettempdir()) / "shared-datasets-1")) / "_scratch/ci-git-fixtures"
    work.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(dir=work))
    files = repository_files(tree, source)
    files.update((name, raw) for name, raw, mode in overrides)
    for filename, raw in files.items():
        if raw is not None and (filename in CONTRACT_FILES or filename.startswith((".github/", "scripts/", "terraform/", "ingestion/", "services/", "catalog/", "api/", ".claude/"))):
            path = root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            shutil.copymode(source / filename, path)
    (root / preflight.INGESTION_ROLLOUT_HOLDS).write_text("{}\n")
    git = fixture_git(root)
    git("init", "-b", "main")
    git("add", ".")
    git("commit", "-m", "initial")
    return root


def fixture_repo(tmp_path, *, image_targets=BUILD_TARGETS):
    root = tmp_path / "source"
    # Include local producer edits in focused checks as well as frozen commits.
    changed = set(preflight.git(ROOT, "diff", "--name-only", "-z", "HEAD", raw=True).split("\0"))
    changed.update(preflight.git(ROOT, "ls-files", "--others", "--exclude-standard", "-z", raw=True).split("\0"))
    overrides = tuple((name, (ROOT / name).read_bytes() if (ROOT / name).is_file() else None,
                       (ROOT / name).stat().st_mode if (ROOT / name).is_file() else None) for name in sorted(changed) if name)
    template = fixture_template(ROOT, preflight.git(ROOT, "rev-parse", "HEAD^{tree}"), overrides)
    subprocess.run([os.environ.get("CI_GIT", "git"), "-c", "core.hooksPath=/dev/null", "clone", "--shared", "--quiet", str(template), str(root)], check=True)
    git = fixture_git(root)
    git("remote", "remove", "origin")
    if not image_targets:
        # Static-HCL cases construct their complete scope; unrelated production
        # declarations add parser work and can hide fixture dependencies.
        for path in (root / "terraform").rglob("*.tf"):
            path.unlink()
        git("add", ".")
        git("commit", "-m", "non-image fixture")
    return root, git


def emit_plan(root, base, work):
    output, github_output = work / "plan.json", work / "github-output"
    result = subprocess.run([preflight.sys.executable, str(ROOT / "scripts/ci_preflight.py"), "plan", "--base", base,
        "--output", str(output), "--github-output", str(github_output)], cwd=root,
        env={key: value for key, value in os.environ.items() if not key.startswith("GITHUB_")}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(output.read_text()), dict(line.split("=", 1) for line in github_output.read_text().splitlines())


def test_committed_ingestion_holds_leave_ci_and_images_required(tmp_path):
    source, git = fixture_repo(tmp_path)
    holds = source / preflight.INGESTION_ROLLOUT_HOLDS
    holds.write_text(json.dumps({target: {"owner": "jonaraphael", "reason": "review pending", "expires_at": "2099-01-01T00:00:00Z"}
                                 for target in INGESTION_DEPLOYMENTS}))
    git("add", ".")
    git("commit", "-m", "hold rollouts")
    base = git("rev-parse", "HEAD")
    runtime = source / "ingestion/common/publication.py"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text("# shared runtime edit\n")
    git("add", ".")
    git("commit", "-m", "change shared ingestion")
    runtime_head = git("rev-parse", "HEAD")
    holds.write_text("{}\n")  # The plan reads the committed tree.

    plan, emitted = emit_plan(source, base, tmp_path)
    assert plan["deployments"] == ["catalog_viewer"]
    assert {emitted[target] for target in INGESTION_DEPLOYMENTS} == {"false"}
    assert {"lint", "tests", "production-images", "geospatial-integration"} <= set(plan["suites"])
    assert {"mpatlas-monthly", "eamlis-monthly", "wdpa-monthly", "sea-ice-daily"} <= set(json.loads(emitted["production-image-targets"]))

    git("add", ".")
    git("commit", "-m", "clear holds")
    assert set(preflight.make_plan(source, runtime_head, "HEAD")["deployments"]) == INGESTION_DEPLOYMENTS
    assert set(preflight.make_plan(source, base, "HEAD")["deployments"]) == INGESTION_DEPLOYMENTS | {"catalog_viewer"}


@pytest.mark.parametrize("invalid", [[], ["wdpa", "wdpa"], {"catalog_viewer": {}}, {"wdpa": {}},
    {"wdpa": {"owner": "outsider", "reason": "review", "expires_at": "2099-01-01T00:00:00Z"}},
    {"wdpa": {"owner": "jonaraphael", "reason": "review", "expires_at": "2099-01-01T00:00:00"}}])
def test_invalid_ingestion_rollout_hold_fails_plan(tmp_path, invalid):
    source, git = fixture_repo(tmp_path)
    base = git("rev-parse", "HEAD")
    (source / preflight.INGESTION_ROLLOUT_HOLDS).write_text(json.dumps(invalid))
    git("add", ".")
    git("commit", "-m", "invalid hold")
    with pytest.raises(ValueError, match="rollout hold"):
        preflight.make_plan(source, base, "HEAD")


def test_automation_plan_cli_outputs_and_gate_preserve_mandatory_checks(tmp_path):
    source, git = fixture_repo(tmp_path)
    base = git("rev-parse", "HEAD")
    for filename in ("scripts/slack_incidents.py", "tests/test_wdpa_incident_recovery.py"):
        target = source / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# automation fixture\n")
    git("add", ".")
    git("commit", "-m", "automation")
    validation, emitted = emit_plan(source, base, tmp_path)
    assert validation["suites"] == ["lint", "tests"]
    assert validation["deployments"] == []
    assert json.loads(emitted["production-image-targets"]) == []
    assert {suite for suite in SUITES if emitted[suite] == "true"} == ALWAYS
    commands = {suite: [args for args, _ in preflight.suite_commands(suite, source, validation, tmp_path / suite)]
                for suite in validation["suites"]}
    assert ["gitleaks", "git", "--redact", "--log-opts=HEAD", "."] in commands["tests"]
    assert ["uv", "run", "--no-sync", "pytest", "-n", "4", "--max-worker-restart", "0", "-o",
            "xfail_strict=true", f"--junitxml={tmp_path / 'tests' / 'pytest.xml'}"] in commands["tests"]
    for script in ("admission_check", "repo_guardrails", "catalog_docs", "catalog_site", "check_identity_resolutions"):
        assert any(f"scripts/{script}.py" in args for args in commands["tests"])
    assert ["uv", "run", "--no-sync", "ruff", "check", "."] in commands["lint"]
    assert any("scripts/check_workflow_syntax.py" in args for args in commands["lint"])
    assert any(args[0] == "terraform" and "validate" in args for args in commands["lint"])
    results, jobs = results_for(validation), jobs_for(validation)
    verify_results(validation, results, jobs)
    for suite in ALWAYS:
        with pytest.raises(ValueError, match="missing suite results"):
            verify_results(validation, [result for result in results if result["suite"] != suite], jobs)
        failed = copy.deepcopy(results)
        next(result for result in failed if result["suite"] == suite)["commands"][0]["exit_code"] = 1
        with pytest.raises(ValueError, match="failed command"):
            verify_results(validation, failed, jobs)
        skipped = copy.deepcopy(jobs)
        skipped[suite]["result"] = "skipped"
        with pytest.raises(ValueError, match=f"job {suite}: expected success"):
            verify_results(validation, results, skipped)


@pytest.mark.parametrize("suites", [SUITES, ("lint", "tests")])
@pytest.mark.parametrize("outcome", ["success", "failure", "missing", "crash", "stale", "source-change"])
def test_local_driver_bounds_parallel_suites_and_requires_complete_evidence(tmp_path, monkeypatch, suites, outcome):
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "source.txt").write_text("tracked fixture")
    (tmp_path / "repo" / "source-link").symlink_to("source.txt")
    unrelated = tmp_path / "catalog-viewer.docker.tar"
    unrelated.write_bytes(b"prior run")
    validation = plan(suites)
    reports = {report["suite"]: report for report in results_for(validation)}
    barrier, lock = Barrier(2), Lock()
    calls, outputs = [], []
    active = peak = 0

    def execute(suite, work):
        nonlocal active, peak
        with lock:
            calls.append(suite)
            active += 1
            peak = max(peak, active)
            first_pair = len(calls) <= 2
        try:
            if first_pair:
                barrier.wait(timeout=5)
            if suite == "lint":
                if outcome == "crash":
                    raise subprocess.CalledProcessError(7, ["docker", "start"])
                if outcome == "missing":
                    return
                if outcome == "failure":
                    reports[suite]["status"] = "failure"
                if outcome == "stale":
                    reports[suite]["tested_sha"] = "f" * 40
            output = work / suite / "result.json"
            outputs.append(output)
            output.write_text(json.dumps(reports[suite]))
        finally:
            with lock:
                active -= 1

    def container(root, work, suite, image, platform):
        execute(suite, work)
        return {"transport": "container", "exit_code": 0}

    def command(argv, **kwargs):
        if "run-suite" in argv:
            output = Path(argv[argv.index("--output") + 1])
            assert kwargs["cwd"] == output.parent / "production-image-repo"
            assert (kwargs["cwd"] / "source-link").is_symlink()
            (kwargs["cwd"] / "host-cache").write_text("host-only")
            images = output / "images"
            images.mkdir()
            for image in reports["production-images"]["production_images"]["images"].values():
                (images / image["archive"]).write_bytes(b"verified fixture")
            (images / "manifest.json").write_text(json.dumps(reports["production-images"]["production_images"]))
            execute("production-images", output.parent)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setenv("SHARED_DATASETS_WORKDIR", str(tmp_path))
    monkeypatch.setattr(preflight, "LOCAL_COORDINATION_DIRECTORY", tmp_path / "coordination")
    monkeypatch.setattr(preflight, "isolated_merge", lambda *args: (tmp_path / "repo", validation["base"], validation["head"]))
    monkeypatch.setattr(preflight, "make_plan", lambda *args, **kwargs: validation)
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/tools/docker")
    monkeypatch.setattr(preflight, "local_runtime", lambda: {"server_architecture": "amd64", "platform": "linux/amd64"})
    monkeypatch.setattr(preflight, "isolated_host_environment", lambda *args: nullcontext({}))
    monkeypatch.setattr(preflight, "run_container", container)
    monkeypatch.setattr(preflight.subprocess, "run", command)
    monkeypatch.setattr(preflight, "git", lambda root, *args: ("changed" if outcome == "source-change" else "") if args[0] == "status" else args[-1].split("^")[0])
    monkeypatch.setattr(preflight.sys, "argv", ["ci_preflight.py", "--base", validation["base"], "--head", validation["head"]])
    assert preflight.main() == (0 if outcome == "success" else 1)
    assert len(calls) == len(set(calls)) and set(calls) <= set(suites)
    if outcome in {"success", "stale", "source-change"}:
        assert set(calls) == set(suites)
    assert active == 0 and peak == 2
    assert not (tmp_path / "repo" / "host-cache").exists()
    assert unrelated.read_bytes() == b"prior run"
    assert len(set(outputs)) == len(outputs)
    if "production-images" in suites:
        assert "production-images" in calls[:2]
        image_dirs = list((tmp_path / "_scratch").glob("ci-preflight-*/production-images/images"))
        assert len(image_dirs) == 1
        archives = list(image_dirs[0].glob("*.docker.tar"))
        assert len(archives) == (0 if outcome == "success" else 7)
        assert json.loads((image_dirs[0] / "manifest.json").read_text()) == reports["production-images"]["production_images"]
    evidence = list(tmp_path.rglob("preflight.json"))
    assert len(evidence) == (1 if outcome == "success" else 0)
    if evidence:
        final = json.loads(evidence[0].read_text())
        verify_results(validation, final["results"])
        assert set(final["runtime"]["suites"]) == set(suites)
        if "production-images" in suites:
            assert final["runtime"]["production_image_archives_retained"] is False


def test_prospective_merge_preserves_source_index_and_contains_both_branches(tmp_path):
    source, run = fixture_repo(tmp_path)
    initial = run("rev-parse", "HEAD")
    (source / "main-only.txt").write_text("main\n")
    run("add", ".")
    run("commit", "-m", "main update")
    base = run("rev-parse", "HEAD")
    run("checkout", "-b", "feature", initial)
    (source / "feature-only.txt").write_text("feature\n")
    run("add", ".")
    run("commit", "-m", "feature")
    head = run("rev-parse", "HEAD")
    index_before = (source / ".git/index").read_bytes()
    checkout, resolved_base, original_head = preflight.isolated_merge(source, base, head, tmp_path / "merge")
    assert resolved_base == base and original_head == head
    assert (checkout / "main-only.txt").read_text() == "main\n"
    assert (checkout / "feature-only.txt").read_text() == "feature\n"
    assert run("rev-parse", "HEAD") == head
    assert not run("status", "--porcelain")
    assert (source / ".git/index").read_bytes() == index_before
    validation = preflight.make_plan(checkout, base, "HEAD", original_head=head)
    assert validation["head"] == head
    assert validation["tested_sha"] != head
    assert "main-only.txt" not in validation["changed_paths"]
    assert "" not in validation["changed_paths"]
    preflight.validate_checkout(checkout, validation)
    (checkout / "feature-only.txt").write_text("edited after validation\n")
    with pytest.raises(ValueError, match="clean checkout"):
        preflight.validate_checkout(checkout, validation)


def test_dirty_source_and_shallow_history_cannot_be_validated(tmp_path):
    source, run = fixture_repo(tmp_path)
    head = run("rev-parse", "HEAD")
    (source / "untracked.txt").write_text("uncommitted\n")
    with pytest.raises(ValueError, match="clean"):
        preflight.isolated_merge(source, head, head, tmp_path / "dirty-merge")
    (source / ".git/shallow").write_text(head + "\n")
    with pytest.raises(ValueError, match="full checkout history"):
        preflight.make_plan(source, head, "HEAD")


def test_unavailable_comparison_selects_all_without_claiming_history_check_success(tmp_path):
    source, run = fixture_repo(tmp_path)
    validation = preflight.make_plan(source, "f" * 40, run("rev-parse", "HEAD"))
    assert validation["suites"] == list(SUITES)
    assert validation["changed_paths"] is None
    commands = preflight.suite_commands("tests", source, validation, tmp_path)
    admission = next(args for args, _ in commands if "scripts/admission_check.py" in args)
    assert admission[admission.index("--base") + 1] == "f" * 40


@pytest.mark.parametrize("paths,suites,deployments", [
    (["docs/guide.md"], ALWAYS, []),
    (["terraform/modules/cloud_run_job/README.md"], ALWAYS, []),
    ([".github/README.md"], ALWAYS, []),
    (["ingestion/dataset_usage/README.md"], ALWAYS, []),
    (["services/catalog_viewer/README.md"], ALWAYS, []),
    (["ingestion/common/nested/operations.md"], ALWAYS, []),
    (["ingestion/dataset_usage/README.md", "ingestion/dataset_usage/run.py"],
     ALWAYS | {"geospatial-integration", "production-images"}, ["dataset_usage"]),
    (["services/catalog_viewer/README.md", "services/catalog_viewer/run.py"],
     ALWAYS | {"production-images", "browser"}, ["catalog_viewer"]),
    (["docs/assets/ims-sea-ice-extent.md"], set(SUITES), ["sea_ice"]),
    (["docs/guide.md", "scripts/unproved.py"], ALWAYS, []),
    (["docs/guide.md", "unknown/component.ext"], set(SUITES), []),
    (["docs/new-tool.py"], set(SUITES), []),
    ([".claude/skills/new-tool.py"], set(SUITES), []),
    (["terraform/iam-plan-prerequisites.json"], ALWAYS, []),
    ([".github/workflows/ci.yml"], ALWAYS, []),
    (["terraform/envs/prod/scheduled_ingestion_deploy_iam.tf"], ALWAYS, ["ingestion_iam"]),
    ([], ALWAYS, []),
])
def test_real_diff_tiers_preserve_validation_and_deployment_boundaries(tmp_path, paths, suites, deployments):
    source, run = fixture_repo(tmp_path)
    base = run("rev-parse", "HEAD")
    for path in paths:
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((target.read_text() if target.exists() else "") + ("\n" if target.suffix == ".json" else "\n# fixture change\n"))
    if paths:
        run("add", ".")
        run("commit", "-m", "change")
    validation = json.loads(json.dumps(preflight.make_plan(source, base, "HEAD")))
    assert validation["changed_paths"] == sorted(paths)
    assert set(validation["suites"]) == suites
    assert validation["deployments"] == deployments


def test_required_workflow_has_no_path_filters_and_gate_is_always_evaluated():
    workflow = load_workflow(ROOT / ".github/workflows/ci.yml")
    trigger = workflow_triggers(workflow)["pull_request"]
    assert set(trigger) == {"types"}
    assert "ready_for_review" not in trigger["types"]
    ready = workflow["jobs"]["ci-ready"]
    assert workflow["jobs"]["geospatial-changes"]["name"] == "geospatial-changes"
    assert ready["name"] == "ci-ready"
    assert ready["if"] == "always()"
    assert set(ready["needs"]) == {"geospatial-changes", "lint", "tests", "owner-acceptance", "geospatial-integration", "production-images", "sdk-validation", "browser", "pr-terraform-plan"}
    assert workflow["jobs"]["sdk-validation"]["strategy"]["matrix"]["node"] == ["22", "24"]
    assert not (ROOT / ".github/workflows/sdk-validation.yml").exists()
    assert not (ROOT / ".github/workflows/catalog-browser-smoke.yml").exists()


def test_queue_extension_validation_preserves_other_syntax_for_actionlint():
    source = "name: fixture\non: push\njobs:\n  delivery:\n    runs-on: ubuntu-latest\n    unexpected: still-an-error\n    concurrency:\n      group: prod-state\n      cancel-in-progress: false\n      queue: max\n    steps:\n      - run: echo fixture\n"
    normalized = parser_source(source)
    assert "queue:" not in normalized
    assert "unexpected: still-an-error" in normalized
    assert "cancel-in-progress: false" in normalized
    assert normalized.count("\n") == source.count("\n")
    for invalid in (source.replace("queue: max", "queue: newest"), source.replace("cancel-in-progress: false", "cancel-in-progress: true"), source.replace("cancel-in-progress: false", "cancel-in-progress: 'false'")):
        with pytest.raises(ValueError):
            parser_source(invalid)


def test_hosted_and_linux_image_pins_match_shared_toolchain():
    action = (ROOT / ".github/actions/ci-tools/action.yml").read_text()
    image = (ROOT / ".github/docker/preflight.Dockerfile").read_text()
    native = (ROOT / ".github/docker/geospatial-ci.Dockerfile").read_text()
    for tool in ("python", "uv", "node22", "node24"):
        assert TOOLCHAIN[tool] in action
    assert f"python:{TOOLCHAIN['python']}-slim-bookworm" in image
    assert f"python:{TOOLCHAIN['python']}-slim-bookworm" in native
    assert f"astral-sh/uv:{TOOLCHAIN['uv']}" in image
    assert 'go build -ldflags "-X main.version=${PMTILES_VERSION#v}" -o /pmtiles .' in native
    assert 'cd /go/pkg/mod/github.com/protomaps/go-pmtiles@${PMTILES_VERSION}' in native
    workflow = load_workflow(ROOT / '.github/workflows/ci.yml')
    defaults = load_workflow(ROOT / '.github/actions/ci-tools/action.yml')['inputs']['binaries']['default']
    for suite in ('lint', 'tests'):
        setup, = [step for step in workflow['jobs'][suite]['steps'] if step.get('uses') == './.github/actions/ci-tools']
        installed = set(setup.get('with', {}).get('binaries', defaults).split())
        assert set(expected_tools(suite)) & {'terraform', 'gitleaks', 'actionlint', 'shellcheck'} <= installed


def test_terraform_initialization_cannot_rewrite_approved_provider_locks(tmp_path):
    commands = preflight.suite_commands('lint', ROOT, plan(), tmp_path)
    initializations = [args for args, _ in commands if args[0] == 'terraform' and 'init' in args]
    assert len(initializations) == 2
    assert all('-lockfile=readonly' in args for args in initializations)


def test_browser_releases_only_its_download_cache_before_installing_chromium(tmp_path):
    commands = [args for args, _ in preflight.suite_commands('browser', ROOT, plan(), tmp_path)]
    assert [commands[0], commands[2]] == [
        ['uv', 'sync', '--locked', '--no-dev', '--group', 'browser'],
        ['uv', 'cache', 'clean'],
    ]
    assert commands.index(['uv', 'cache', 'clean']) < next(index for index, args in enumerate(commands) if 'playwright' in args)
    assert commands[-1] == ['npm', 'test']


@pytest.mark.parametrize('architecture', ['amd64', 'arm64'])
def test_local_runtime_uses_the_actual_linux_server_architecture(architecture):
    server = {'Os': 'linux', 'Arch': architecture, 'Version': '29.8.2'}
    with mock.patch.object(preflight.subprocess, 'check_output', return_value=json.dumps(server)) as execute:
        record = preflight.local_runtime()
    assert record == {'platform': f'linux/{architecture}', 'server_architecture': architecture, 'docker_version': '29.8.2'}
    execute.assert_called_once_with(['docker', 'version', '--format', '{{json .Server}}'], text=True)


@pytest.mark.parametrize('operating_system,architecture', [('darwin', 'arm64'), ('windows', 'amd64'), ('linux', 'riscv64'), ('linux', 'ppc64le')])
def test_unsupported_docker_runtime_fails_before_validation(operating_system, architecture):
    server = {'Os': operating_system, 'Arch': architecture, 'Version': '29.8.2'}
    with mock.patch.object(preflight.subprocess, 'check_output', return_value=json.dumps(server)), pytest.raises(ValueError, match='Linux Docker server'):
        preflight.local_runtime()


@pytest.mark.parametrize('image_platform', ['linux/amd64', 'windows/arm64'])
def test_mismatched_image_platform_cannot_silently_enable_emulation(tmp_path, image_platform):
    with mock.patch.object(preflight.subprocess, 'check_output', return_value=image_platform) as execute, mock.patch.object(preflight.subprocess, 'run') as run, pytest.raises(ValueError, match='differs from native runtime'):
        preflight.run_container(ROOT, tmp_path, 'geospatial-integration', 'pinned-image', 'linux/arm64')
    execute.assert_called_once_with(['docker', 'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}}', 'pinned-image'], text=True)
    run.assert_not_called()


@pytest.mark.parametrize('exit_code', [0, 7])
def test_copied_runtime_retains_evidence_and_obeys_actual_container_exit(tmp_path, exit_code):
    (tmp_path / 'plan.json').write_text(json.dumps(plan()))
    state = {'Running': False, 'Status': 'exited', 'ExitCode': exit_code}
    with mock.patch.object(preflight.subprocess, 'check_output', side_effect=['linux/arm64', 'owned-container', json.dumps(state), 'sha256:test-image']), mock.patch.object(preflight.subprocess, 'run') as run:
        if exit_code:
            with pytest.raises(ValueError, match='container failed'):
                preflight.run_container(ROOT, tmp_path, 'browser', 'pinned-image', 'linux/arm64')
        else:
            record = preflight.run_container(ROOT, tmp_path, 'browser', 'pinned-image', 'linux/arm64')
            assert record['image_id'] == 'sha256:test-image'
            assert record['environment']['CI'] == 'true'
            assert record['environment']['OPENSSL_armcap'] == '0'
            assert record['environment']['GITHUB_ACTIONS'] == 'true'
            assert 'GITHUB_RUN_ID' not in record['environment']
            assert not any('TOKEN' in key for key in record['environment'])
        commands = [call.args[0] for call in run.call_args_list]
    assert ['docker', 'cp', f'{ROOT}/.', 'owned-container:/workspace'] in commands
    assert ['docker', 'cp', str(tmp_path / 'browser-event.json'), 'owned-container:/browser-event.json'] in commands
    event = json.loads((tmp_path / 'browser-event.json').read_text())
    assert event['pull_request']['base']['sha'] == plan()['base']
    assert event['pull_request']['head']['sha'] == plan()['head']
    assert ['docker', 'cp', 'owned-container:/evidence/browser/.', str(tmp_path / 'browser')] in commands
    assert commands[-1] == ['docker', 'rm', '-f', 'owned-container']
    assert not any('-v' in command or '--volume' in command for command in commands)


@pytest.mark.parametrize('suite', SUITES)
def test_every_local_suite_runs_in_ci_mode_without_forwarded_credentials(tmp_path, suite):
    (tmp_path / 'plan.json').write_text(json.dumps(plan()))
    state = {'Running': False, 'Status': 'exited', 'ExitCode': 0}
    with mock.patch.object(preflight.subprocess, 'check_output', side_effect=['linux/arm64', 'owned-container', json.dumps(state), 'sha256:test-image']) as execute, mock.patch.object(preflight.subprocess, 'run'):
        record = preflight.run_container(ROOT, tmp_path, suite, 'pinned-image', 'linux/arm64')
    create = execute.call_args_list[1].args[0]
    assert 'CI=true' in create
    assert 'UV_CACHE_DIR=/opt/uv-cache' in create
    assert 'UV_PROJECT_ENVIRONMENT=/evidence/venv' in create
    assert create[create.index('--platform') + 1] == 'linux/arm64'
    assert '--entrypoint' not in create
    assert record['environment']['CI'] == 'true'
    assert not any('TOKEN' in value or 'GITHUB_RUN_ID' in value for value in create)


def context(host="unix:///owned/docker.sock", *, tls_path=None, files=(), skip_verify=False):
    return json.dumps([{
        "Endpoints": {"docker": {"Host": host, "SkipTLSVerify": skip_verify}},
        "TLSMaterial": {"docker": list(files)}, "Storage": {"TLSPath": str(tls_path)},
    }])


def test_secret_environment_and_default_adc_are_not_forwarded(tmp_path):
    original = tmp_path / "original-home"
    adc = original / ".config/gcloud/application_default_credentials.json"
    adc.parent.mkdir(parents=True)
    adc.write_text('{"token": "must-not-be-forwarded"}')
    docker = original / ".docker"
    docker.mkdir()
    (docker / "config.json").write_text('{"auths": {"private-registry": "secret"}}')
    caller = {
        "PATH": "/owned/bin", "HOME": str(original), "DOCKER_HOST": "unix:///owned/docker.sock",
        "DOCKER_CONFIG": str(docker), "GOOGLE_APPLICATION_CREDENTIALS": str(adc), "GOOGLE_CLOUD_PROJECT": "production",
        "GITHUB_TOKEN": "secret", "GH_TOKEN": "secret", "GITHUB_RUN_ID": "123", "GITHUB_EVENT_PATH": "/real-event",
        "SSH_AUTH_SOCK": "/agent", "AWS_SECRET_ACCESS_KEY": "secret", "NPM_TOKEN": "secret",
        "HTTPS_PROXY": "http://proxy.test", "SSL_CERT_FILE": "/owned/ca.pem", "LANG": "C.UTF-8",
    }
    with preflight.isolated_host_environment(tmp_path, caller) as environment:
        assert environment["PATH"] == caller["PATH"]
        assert environment["HTTPS_PROXY"] == caller["HTTPS_PROXY"]
        assert environment["SSL_CERT_FILE"] == caller["SSL_CERT_FILE"]
        assert not any(key in environment for key in (
            "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT", "GITHUB_TOKEN", "GH_TOKEN", "GITHUB_RUN_ID",
            "GITHUB_EVENT_PATH", "SSH_AUTH_SOCK", "AWS_SECRET_ACCESS_KEY", "NPM_TOKEN",
        ))
        assert Path(environment["HOME"]) != original
        assert not (Path(environment["HOME"]) / ".config/gcloud/application_default_credentials.json").exists()
        assert not (Path(environment["CLOUDSDK_CONFIG"]) / "application_default_credentials.json").exists()
        assert json.loads((Path(environment["DOCKER_CONFIG"]) / "config.json").read_text()) == {}
        isolated_home = Path(environment["HOME"])
    assert not isolated_home.exists()
    assert adc.exists()
    assert json.loads((docker / "config.json").read_text())["auths"]


def test_explicit_endpoint_needs_no_context_or_user_configuration(tmp_path):
    with mock.patch("scripts.ci_preflight.subprocess.check_output") as inspect:
        with preflight.isolated_host_environment(tmp_path, {"PATH": "/bin", "DOCKER_HOST": "tcp://localhost:2375"}) as environment:
            assert environment["DOCKER_HOST"] == "tcp://localhost:2375"
            assert "DOCKER_TLS_VERIFY" not in environment
        inspect.assert_not_called()


def test_context_endpoint_is_resolved_before_user_config_is_hidden(tmp_path):
    caller = {"PATH": "/bin", "HOME": "/caller", "DOCKER_CONTEXT": "selected", "DOCKER_HOST": "unix:///overridden.sock"}
    with mock.patch("scripts.ci_preflight.subprocess.check_output", return_value=context()) as inspect:
        with preflight.isolated_host_environment(tmp_path, caller) as environment:
            assert environment["DOCKER_HOST"] == "unix:///owned/docker.sock"
            assert "DOCKER_CONTEXT" not in environment
    assert inspect.call_args.args[0] == ["docker", "context", "inspect", "selected"]
    assert inspect.call_args.kwargs["env"] is caller


@pytest.mark.parametrize("skip_verify", [False, True])
def test_context_preserves_only_required_daemon_tls_material(tmp_path, skip_verify):
    original = tmp_path / "tls-store" / "docker"
    original.mkdir(parents=True)
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (original / name).write_text("transport-only-" + name)
    (original / "registry-token").write_text("not-transport")
    metadata = context("tcp://daemon.test:2376", tls_path=original.parent, files=("ca.pem", "cert.pem", "key.pem"), skip_verify=skip_verify)
    with mock.patch("scripts.ci_preflight.subprocess.check_output", return_value=metadata):
        with preflight.isolated_host_environment(tmp_path, {"PATH": "/bin"}) as environment:
            copied = Path(environment["DOCKER_CERT_PATH"])
            assert copied != original
            assert {path.name for path in copied.iterdir()} == {"ca.pem", "cert.pem", "key.pem"}
            assert (copied / "key.pem").read_text() == "transport-only-key.pem"
            assert (copied / "key.pem").stat().st_mode & 0o777 == 0o600
            assert environment["DOCKER_TLS" if skip_verify else "DOCKER_TLS_VERIFY"] == "1"
    assert not copied.exists()


def test_explicit_tls_copies_daemon_keys_without_registry_auth(tmp_path):
    source = tmp_path / "certificates"
    source.mkdir()
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (source / name).write_text(name)
    (source / "config.json").write_text('{"auths": {"private": "secret"}}')
    caller = {"PATH": "/bin", "DOCKER_HOST": "tcp://daemon.test:2376", "DOCKER_TLS_VERIFY": "1", "DOCKER_CERT_PATH": str(source)}
    with preflight.isolated_host_environment(tmp_path, caller) as environment:
        copied = Path(environment["DOCKER_CERT_PATH"])
        assert {path.name for path in copied.iterdir()} == {"ca.pem", "cert.pem", "key.pem"}
        assert not (copied / "config.json").exists()


@pytest.mark.parametrize("metadata", ["[]", "{}", context("ssh://agent@host"), context(files=("registry-auth",)), context(files=("cert.pem",))])
def test_missing_or_unsupported_context_fails_without_transport_fallback(tmp_path, metadata):
    with mock.patch("scripts.ci_preflight.subprocess.check_output", return_value=metadata):
        with pytest.raises(ValueError):
            with preflight.isolated_host_environment(tmp_path, {"PATH": "/bin"}):
                pytest.fail("unsupported context was accepted")


def test_missing_context_is_reported_as_configuration_failure(tmp_path):
    with mock.patch("scripts.ci_preflight.subprocess.check_output", side_effect=subprocess.CalledProcessError(1, ["docker"])):
        with pytest.raises(ValueError, match="Cannot resolve the Docker context"):
            with preflight.isolated_host_environment(tmp_path, {"PATH": "/bin"}):
                pytest.fail("missing context was accepted")


def test_missing_declared_tls_file_fails_before_using_another_identity(tmp_path):
    metadata = context("tcp://daemon.test:2376", tls_path=tmp_path / "missing", files=("ca.pem",))
    with mock.patch("scripts.ci_preflight.subprocess.check_output", return_value=metadata):
        with pytest.raises(ValueError, match="Missing or invalid Docker transport TLS file"):
            with preflight.isolated_host_environment(tmp_path, {"PATH": "/bin"}):
                pytest.fail("missing TLS file was accepted")


@pytest.mark.parametrize("defect", [None, "whitespace", "secret"])
def test_docs_only_runs_lightweight_checks_through_the_real_runner(tmp_path, monkeypatch, defect):
    source, git = fixture_repo(tmp_path)
    if defect is None:
        # Copy real producer code and inputs once; all three catalog runs share
        # this fixture and one process-owned dependency environment.
        for directory in ("scripts", "catalog", "docs/assets", "web/catalog"):
            shutil.copytree(ROOT / directory, source / directory, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__"))
        for filename in ("pyproject.toml", "uv.lock", ".gitignore"):
            shutil.copyfile(ROOT / filename, source / filename)
        git("add", ".")
        git("commit", "-m", "catalog fixture")
    base = git("rev-parse", "HEAD")
    (source / "README.md").write_text("Updated docs" + ("  " if defect == "whitespace" else "") + "\n")
    git("add", "README.md")
    git("commit", "-m", "docs")
    validation = json.loads(json.dumps(preflight.make_plan(source, base, "HEAD")))
    commands = {suite: [args for args, _ in preflight.suite_commands(suite, source, validation, tmp_path / suite)]
                for suite in validation["suites"]}
    assert commands == {
        "lint": [["git", "diff", "--check", base, validation["tested_sha"]]],
        "tests": [
            ["gitleaks", "git", "--redact", "--log-opts=HEAD", "."],
            ["uv", "sync", "--locked", "--all-groups"],
            preflight.interpreter_command(),
            ["uv", "run", "--no-sync", "python", "scripts/catalog_docs.py", "check"],
            ["uv", "run", "--no-sync", "python", "scripts/catalog_site.py", "--out", str(tmp_path / "tests/catalog-web")],
        ],
    }
    tools = tmp_path / "tools"
    tools.mkdir()
    scanner = tools / "gitleaks"
    scanner.write_text(f"#!/bin/sh\nexit {1 if defect == 'secret' else 0}\n")
    scanner.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("UV_PYTHON", preflight.sys.executable)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(tmp_path / "catalog-venv"))
    monkeypatch.setattr(preflight, "probe_tools", lambda suite, environment: expected_tools(suite))
    if defect:
        suite = "lint" if defect == "whitespace" else "tests"
        result = preflight.run_suite(source, validation, suite, tmp_path / suite)
        retained = json.loads((tmp_path / suite / "result.json").read_text())
        assert retained == result
        assert retained["status"] == "failure"
        assert retained["commands"][-1]["exit_code"] != 0
        return
    results = [preflight.run_suite(source, validation, suite, tmp_path / suite) for suite in validation["suites"]]
    verify_results(validation, results, jobs_for(validation))
    assert (tmp_path / "tests/catalog-web/index.html").is_file()
    assert (tmp_path / "tests/catalog-web/catalog.json").is_file()
    catalog = source / "catalog/shared-datasets-catalog.csv"
    original_catalog = catalog.read_bytes()
    for producer in ("catalog_docs", "catalog_site"):
        catalog.write_bytes(original_catalog)
        if producer == "catalog_docs":
            with catalog.open("a") as stream:
                stream.write("invalid catalog row\n")
        else:
            (source / "web/catalog/index.html").unlink()
        git("add", ".")
        git("commit", "-m", producer + " defect")
        base = git("rev-parse", "HEAD")
        (source / "README.md").write_text(producer + " docs update\n")
        git("add", "README.md")
        git("commit", "-m", "docs")
        validation = preflight.make_plan(source, base, "HEAD")
        assert validation["changed_paths"] == ["README.md"]
        output = tmp_path / producer
        failed = preflight.run_suite(source, validation, "tests", output)
        retained = json.loads((output / "result.json").read_text())
        assert retained == failed
        assert retained["status"] == "failure"
        assert f"scripts/{producer}.py" in retained["commands"][-1]["argv"]
        assert retained["commands"][-1]["exit_code"] != 0
        lint = preflight.run_suite(source, validation, "lint", output / "lint")
        with pytest.raises(ValueError, match="did not execute successfully"):
            verify_results(validation, [lint, retained], jobs_for(validation))
    assert all(not (tmp_path / suite / "pytest.xml").exists() for suite in validation["suites"])


@pytest.mark.parametrize("paths", [None, ["docs/guide.md", "ingestion/wdpa_monthly/run.py"], ["docs/new-tool.py"], ["docs/assets/wdpa-marine.md"]])
def test_non_documentation_changes_still_execute_python_validation(paths):
    validation = {**plan(), "changed_paths": paths}
    commands = [args for args, _ in preflight.suite_commands("tests", ROOT, validation, ROOT)]
    assert any("pytest" in args for args in commands)


def test_expired_holds_do_not_change_ci_selection_or_ready_but_lint_rejects_new_expired_entries(tmp_path):
    source, git = fixture_repo(tmp_path)
    path = source / preflight.INGESTION_ROLLOUT_HOLDS
    expired = {'wdpa': {'owner': 'jonaraphael', 'reason': 'existing hold', 'expires_at': '2020-01-01T00:00:00Z'}}
    path.write_text(json.dumps(expired))
    git('add', '.')
    git('commit', '-m', 'historical hold')
    base = git('rev-parse', 'HEAD')
    (source / 'README.md').write_text('docs\n')
    git('add', '.')
    git('commit', '-m', 'docs')
    selected = preflight.make_plan(source, base, 'HEAD')
    assert preflight.suite_commands('lint', source, selected, tmp_path)
    assert verify_results(selected, results_for(selected), jobs_for(selected))
    expired['eamlis'] = expired['wdpa']
    path.write_text(json.dumps(expired))
    git('add', '.')
    git('commit', '-m', 'add expired hold')
    selected = preflight.make_plan(source, base, 'HEAD')
    with pytest.raises(ValueError, match='already expired'):
        preflight.suite_commands('lint', source, selected, tmp_path)


def test_nested_action_edits_invalidate_the_persisted_validation_plan(tmp_path):
    source, git = fixture_repo(tmp_path)
    base = git("rev-parse", "HEAD")
    plan = preflight.make_plan(source, base, base)
    action = source / ".github/actions/deployment-bootstrap/terraform/action.yml"
    action.write_text(action.read_text() + "\n# changed nested bootstrap\n")
    git("add", ".")
    git("commit", "-m", "change nested bootstrap")
    assert preflight.make_plan(source, base, "HEAD")["contract_digest"] != plan["contract_digest"]


@pytest.mark.parametrize("path", ["scripts/ci_contract.py", "scripts/ci_preflight.py",
                                 "terraform/iam-plan-prerequisites.json"])
def test_ci_administration_does_not_rebuild_production_images(path):
    assert set(resolve_selection([path])["suites"]) == ALWAYS
    assert resolve_selection([path])["production_image_targets"] == []


@pytest.mark.parametrize("consumer", ["image", "native", "browser", "unused", "unproved"])
def test_deleted_module_uses_base_consumers_through_the_real_plan_cli(tmp_path, consumer):
    source, git = fixture_repo(tmp_path)
    for path in source.rglob("*.py"):
        path.write_text("")
    leaf = source / ("tests/fixtures/retired.json" if consumer == "unproved" else "scripts/retired.py")
    leaf.write_text("value = 1\n")
    recipe = source / "ingestion/mpatlas_monthly/Dockerfile"
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text("FROM python:3.12\nCOPY ingestion/mpatlas_monthly /app/job\n")
    if consumer == "image":
        recipe.write_text(recipe.read_text() + "COPY scripts/retired.py /app/scripts/retired.py\n")
        (source / 'tests/test_mpatlas_monthly.py').write_text('from ingestion.mpatlas_monthly import run\n')
    entry = source / {"image": "ingestion/mpatlas_monthly/run.py", "native": ci_contract.NATIVE_TESTS[0],
                      "browser": "tests/browser/server.py", "unused": "tests/test_unused.py", "unproved": "tests/test_unused.py"}[consumer]
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("from scripts import retired\n" if consumer not in {"unused", "unproved"} else "")
    git("add", ".")
    git("commit", "-m", "base consumers")
    base = git("rev-parse", "HEAD")
    leaf.unlink()
    entry.write_text("")  # Both halves of a removed import must use the base graph.
    git("add", ".")
    git("commit", "-m", "remove module and import")
    selected, emitted = emit_plan(source, base, tmp_path)
    if consumer == "unproved":
        assert selected["suites"] == list(SUITES)
        return
    assert selected["production_image_targets"] == (["mpatlas-monthly"] if consumer == "image" else [])
    assert emitted["production-images"] == str(consumer == "image").lower()
    assert ("geospatial-integration" in selected["suites"]) is (consumer in {"image", "native"})
    assert ("browser" in selected["suites"]) is (consumer == "browser")


@pytest.mark.parametrize("released", [0, 1, 2, 4])
def test_lint_releases_at_most_one_held_target(tmp_path, released):
    source, git = fixture_repo(tmp_path)
    holds = {target: dict(owner="jonaraphael", reason="review", expires_at="2099-01-01T00:00:00Z")
             for target in sorted(INGESTION_DEPLOYMENTS)}
    path = source / preflight.INGESTION_ROLLOUT_HOLDS
    path.write_text(json.dumps(holds))
    git("add", ".")
    git("commit", "-m", "holds")
    base = git("rev-parse", "HEAD")
    path.write_text(json.dumps(dict(list(holds.items())[released:])))
    selected = {"base": base, "tested_sha": "HEAD", "changed_paths": [preflight.INGESTION_ROLLOUT_HOLDS]}
    if released > 1:
        with pytest.raises(ValueError, match="at most one rollout hold"):
            preflight.suite_commands("lint", source, selected, tmp_path)
    else:
        assert preflight.suite_commands("lint", source, selected, tmp_path)
