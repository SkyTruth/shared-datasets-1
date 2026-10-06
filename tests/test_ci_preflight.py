from __future__ import annotations

import copy
import json
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest

from scripts import ci_preflight as preflight
from scripts.check_geospatial_test_results import REQUIRED_TESTS
from scripts.check_workflow_syntax import parser_source
from scripts.ci_contract import (
    ALWAYS, CONTRACT_FILES, SUITES, TOOLCHAIN, check_junit, expected_tools, select_suites,
    verify_results,
)
from workflow_helpers import load_workflow, workflow_triggers
from ci_result_fixtures import production_images


ROOT = Path(__file__).resolve().parents[1]


def plan(suites=SUITES):
    return {
        "schema_version": 1, "base": "a" * 40, "head": "b" * 40,
        "tested_sha": "c" * 40, "tree": "d" * 40,
        "contract_digest": "e" * 64, "suites": list(suites),
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
    return results


def jobs_for(validation):
    jobs = {"geospatial-changes": {"result": "success"}}
    for suite in ("lint", "tests", "geospatial-integration", "production-images", "browser"):
        jobs[suite] = {"result": "success" if suite in validation["suites"] else "skipped"}
    jobs["sdk-validation"] = {"result": "success" if "sdk-node22" in validation["suites"] else "skipped"}
    return jobs


@pytest.mark.parametrize("path", ["new/component.ext", "scripts/new-runtime.py", ".github/workflows/new.yml", "uv.lock"])
def test_unknown_paths_and_shared_dependencies_select_every_suite(path):
    assert select_suites([path])[0] == list(SUITES)
    assert select_suites(None)[0] == list(SUITES)


def test_cross_component_dependencies_and_explicit_unselected_suites():
    assert set(select_suites(["docs/consumer-guide.md"])[0]) == ALWAYS
    assert set(select_suites(["api/python/src/skytruth_shared_datasets/snapshot.py"])[0]) == ALWAYS | {"sdk-node22", "sdk-node24", "browser", "production-images"}
    assert set(select_suites(["catalog/shared-datasets-catalog.csv"])[0]) == set(SUITES)
    assert "geospatial-integration" in select_suites(["ingestion/common/reset.py"])[0]
    assert "geospatial-integration" in select_suites(["tests/test_wdpa_translation_inputs.py"])[0]


@pytest.mark.parametrize("suites", [SUITES, tuple(ALWAYS)])
def test_complete_results_pass_with_only_expected_job_skips(suites):
    validation = plan(suites)
    verify_results(validation, results_for(validation), jobs_for(validation))


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


def test_real_command_failure_remains_failure_and_stops_later_commands(tmp_path):
    validation = plan(["tests"])
    commands = [(["first"], ROOT), (["second"], ROOT)]
    with (
        mock.patch.object(preflight, "validate_checkout"),
        mock.patch.object(preflight, "probe_tools", return_value=expected_tools("tests")),
        mock.patch.object(preflight, "suite_commands", return_value=commands),
        mock.patch.object(preflight.subprocess, "run", return_value=subprocess.CompletedProcess(["first"], 7)) as run,
    ):
        result = preflight.run_suite(ROOT, validation, "tests", tmp_path)
    assert result["status"] == "failure"
    assert result["commands"][0]["exit_code"] == 7
    assert run.call_count == 1


def fixture_repo(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    executable = os.environ.get("CI_GIT", "git")
    environment = {**os.environ, "GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@invalid.example", "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@invalid.example"}

    def run(*args):
        return subprocess.check_output([executable, "-C", str(root), "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false", *args], env=environment, text=True).strip()

    run("init", "-b", "main")
    for filename in CONTRACT_FILES:
        target = root / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("fixture\n")
    run("add", ".")
    run("commit", "-m", "initial")
    return root, run


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


def test_real_diff_docs_only_does_not_select_suites_for_trailing_nul(tmp_path):
    source, run = fixture_repo(tmp_path)
    base = run("rev-parse", "HEAD")
    (source / "docs").mkdir()
    (source / "docs/guide.md").write_text("documentation\n")
    run("add", ".")
    run("commit", "-m", "docs")
    validation = preflight.make_plan(source, base, "HEAD")
    assert validation["changed_paths"] == ["docs/guide.md"]
    assert set(validation["suites"]) == ALWAYS


def test_required_workflow_has_no_path_filters_and_gate_is_always_evaluated():
    workflow = load_workflow(ROOT / ".github/workflows/ci.yml")
    assert workflow_triggers(workflow)["pull_request"] is None
    ready = workflow["jobs"]["ci-ready"]
    assert workflow["jobs"]["geospatial-changes"]["name"] == "geospatial-changes"
    assert ready["name"] == "ci-ready"
    assert ready["if"] == "always()"
    assert set(ready["needs"]) == {"geospatial-changes", "lint", "tests", "geospatial-integration", "production-images", "sdk-validation", "browser"}
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


def test_terraform_initialization_cannot_rewrite_approved_provider_locks(tmp_path):
    commands = preflight.suite_commands('lint', ROOT, plan(), tmp_path)
    initializations = [args for args, _ in commands if args[0] == 'terraform' and 'init' in args]
    assert len(initializations) == 3
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
    from scripts import ci_runtime
    server = {'Os': 'linux', 'Arch': architecture, 'Version': '29.8.2'}
    with mock.patch.object(ci_runtime.subprocess, 'check_output', return_value=json.dumps(server)) as execute:
        record = ci_runtime.local_runtime()
    assert record == {'platform': f'linux/{architecture}', 'server_architecture': architecture, 'docker_version': '29.8.2'}
    execute.assert_called_once_with(['docker', 'version', '--format', '{{json .Server}}'], text=True)


@pytest.mark.parametrize('operating_system,architecture', [('darwin', 'arm64'), ('windows', 'amd64'), ('linux', 'riscv64'), ('linux', 'ppc64le')])
def test_unsupported_docker_runtime_fails_before_validation(operating_system, architecture):
    from scripts import ci_runtime
    server = {'Os': operating_system, 'Arch': architecture, 'Version': '29.8.2'}
    with mock.patch.object(ci_runtime.subprocess, 'check_output', return_value=json.dumps(server)), pytest.raises(ValueError, match='Linux Docker server'):
        ci_runtime.local_runtime()


@pytest.mark.parametrize('image_platform', ['linux/amd64', 'windows/arm64'])
def test_mismatched_image_platform_cannot_silently_enable_emulation(tmp_path, image_platform):
    from scripts import ci_runtime
    with mock.patch.object(ci_runtime.subprocess, 'check_output', return_value=image_platform) as execute, mock.patch.object(ci_runtime.subprocess, 'run') as run, pytest.raises(ValueError, match='differs from native runtime'):
        ci_runtime.run_container(ROOT, tmp_path, 'geospatial-integration', 'pinned-image', 'linux/arm64')
    execute.assert_called_once_with(['docker', 'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}}', 'pinned-image'], text=True)
    run.assert_not_called()


@pytest.mark.parametrize('exit_code', [0, 7])
def test_copied_runtime_retains_evidence_and_obeys_actual_container_exit(tmp_path, exit_code):
    from scripts import ci_runtime
    (tmp_path / 'plan.json').write_text(json.dumps(plan()))
    state = {'Running': False, 'Status': 'exited', 'ExitCode': exit_code}
    with mock.patch.object(ci_runtime.subprocess, 'check_output', side_effect=['linux/arm64', 'owned-container', json.dumps(state), 'sha256:test-image']), mock.patch.object(ci_runtime.subprocess, 'run') as run:
        if exit_code:
            with pytest.raises(ValueError, match='container failed'):
                ci_runtime.run_container(ROOT, tmp_path, 'browser', 'pinned-image', 'linux/arm64')
        else:
            record = ci_runtime.run_container(ROOT, tmp_path, 'browser', 'pinned-image', 'linux/arm64')
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
    from scripts import ci_runtime
    (tmp_path / 'plan.json').write_text(json.dumps(plan()))
    state = {'Running': False, 'Status': 'exited', 'ExitCode': 0}
    with mock.patch.object(ci_runtime.subprocess, 'check_output', side_effect=['linux/arm64', 'owned-container', json.dumps(state), 'sha256:test-image']) as execute, mock.patch.object(ci_runtime.subprocess, 'run'):
        record = ci_runtime.run_container(ROOT, tmp_path, suite, 'pinned-image', 'linux/arm64')
    create = execute.call_args_list[1].args[0]
    assert 'CI=true' in create
    assert create[create.index('--platform') + 1] == 'linux/arm64'
    assert '--entrypoint' not in create
    assert record['environment']['CI'] == 'true'
    assert not any('TOKEN' in value or 'GITHUB_RUN_ID' in value for value in create)
