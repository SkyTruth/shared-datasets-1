"""Selection and read-only planning follow producer boundaries, never duplicate lists."""
from pathlib import Path
import shutil
import json
import subprocess
from unittest.mock import patch

import pytest
import yaml

from scripts import ci_contract as ci, pr_terraform_plan as planner, production_image_contracts as images
from scripts import terraform_target_contracts as contracts, tested_image_bundle as bundle
from scripts.terraform_target_contracts import registry

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name', registry(section='deployments'))
def test_deployment_prerequisites_match_ci_needs_and_conditions(name):
    declarations = registry(section='deployments')
    job = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs'][name.replace('_', '-')]
    expected = set(declarations[name].get('terraform_prerequisites', []))
    assert expected == {parent.replace('-', '_') for parent in job['needs'] if parent.replace('-', '_') in declarations}
    for parent in expected:
        clause = f"(needs.geospatial-changes.outputs.{parent} != 'true' || needs.{parent.replace('_', '-')}.result == 'success')"
        assert clause in job['if']


@pytest.mark.parametrize('path', ['terraform/envs/preview/main.tf', 'terraform/envs/prod/.terraform.lock.hcl', 'terraform/new/unowned.tf', None])
def test_unregistered_terraform_skips_with_notice(path, capsys):
    plan = {'changed_paths': [path] if path else None, 'deployments': []}
    assert planner.scopes(plan) == []
    assert planner.selected(plan) is False
    assert 'no registered production scope' in capsys.readouterr().out


@pytest.mark.parametrize('path', ['terraform/envs/prod/pr_plan_iam.tf', 'terraform/envs/prod/github_readonly_iam.tf'])
def test_registered_manual_owner_still_requires_live_plan(path):
    plan = {'changed_paths': [path], 'deployments': []}
    assert planner.selected(plan) and planner.scopes(plan)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'repo'
    for name in ('.github', 'scripts', 'terraform', 'ingestion', 'services', 'catalog', 'api/python/src'):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns('.terraform', '__pycache__', 'node_modules'))
    return root


def test_workflow_closure_discovers_new_script_and_nested_composite(source):
    workflow = source / '.github/workflows/catalog-viewer-deploy.yml'
    with workflow.open('a') as stream:
        stream.write('\n  new-check:\n    runs-on: ubuntu-latest\n    steps:\n    - uses: ./.github/actions/new-check\n')
    action = source / '.github/actions/new-check/action.yml'
    action.parent.mkdir()
    action.write_text('runs:\n  using: composite\n  steps:\n  - shell: bash\n    run: python scripts/new_probe.py\n')
    (source / 'scripts/new_probe.py').write_text('from scripts import new_dependency\n')
    (source / 'scripts/new_dependency.py').write_text('VALUE = 1\n')
    assert ci.select_deployments(['scripts/new_dependency.py'], root=source) == ['catalog_viewer']
    assert ci.select_deployments(['scripts/unrelated_new_file.py'], root=source) == []
    assert all('inputs' not in row for row in registry(source, section='deployments').values())


@pytest.mark.parametrize('job,suites', [('production-images', {'production-images'}), ('browser', {'browser'}),
    ('geospatial-integration', {'geospatial-integration'}), ('sdk-validation', {'sdk-node22', 'sdk-node24'}), ('tests', {'tests'})])
def test_ci_job_diff_selects_its_suite_only(source, job, suites):
    before = (source / '.github/workflows/ci.yml').read_bytes()
    document = yaml.safe_load(before)
    document['jobs'][job]['timeout-minutes'] = 99
    (source / '.github/workflows/ci.yml').write_text(yaml.safe_dump(document))
    with patch.object(ci, 'repository_files', return_value={'.github/workflows/ci.yml': before}):
        plan = ci.resolve_selection(['.github/workflows/ci.yml'], base='base', root=source)
    assert set(plan['suites']) == ci.ALWAYS | suites
    assert bool(plan['production_image_targets']) == ('production-images' in suites)


def test_shared_ci_scheduling_changes_select_all(source):
    before = (source / '.github/workflows/ci.yml').read_bytes()
    document = yaml.safe_load(before)
    document['permissions']['actions'] = 'read'
    (source / '.github/workflows/ci.yml').write_text(yaml.safe_dump(document))
    with patch.object(ci, 'repository_files', return_value={'.github/workflows/ci.yml': before}):
        assert ci.changed_ci_suites('base', source) == set(ci.SUITES)


def test_hcl_index_ignores_comment_declarations_and_preserves_indentation(source):
    path = source / 'terraform/envs/prod/pr_plan_iam.tf'
    before = path.read_text()
    path.write_text('# resource "fake" "comment" {\n' + '\n'.join('  ' + line for line in before.splitlines()))
    assert planner.scopes({'changed_paths': ['terraform/envs/prod/pr_plan_iam.tf'], 'deployments': []}, source)
    rows = contracts.resource_index(source)['terraform/envs/prod/pr_plan_iam.tf']
    assert 'fake.comment' not in rows
    assert 'google_service_account.pr_plan' in rows


def test_planner_image_bindings_follow_consumer_declarations(source):
    path = source / 'terraform/envs/prod/catalog_viewer.tf'
    path.write_text(path.read_text().replace('"google_cloud_run_v2_service" "catalog_viewer"', '"google_cloud_run_v2_service" "renamed_viewer"'))
    consumers = bundle.image_consumers(source)
    assert consumers['google_cloud_run_v2_service.renamed_viewer'] == 'catalog_viewer_image'
    assert 'google_cloud_run_v2_service.catalog_viewer' not in consumers


def test_cross_file_local_policy_has_explicit_reasoned_inputs():
    exceptions = registry(section='deployments')['catalog_viewer']['input_exceptions']
    for path in ('terraform/envs/prod/canonical_mutation_iam.tf', 'catalog/categories.yaml'):
        assert exceptions[path]
        assert 'catalog_viewer' in ci.select_deployments([path])
    assert ci.select_deployments(['catalog/unrelated-policy.yaml']) == []


def test_monitoring_installed_probe_never_executes_test_module_source(source, monkeypatch):
    monkeypatch.setattr(images, 'ROOT', source)
    test = source / 'tests/test_monitoring_slack.py'
    test.parent.mkdir()
    test.write_text('raise RuntimeError("test source must not execute")\n')
    command = images.commands('monitoring-slack', 'a' * 40)[1]
    result = subprocess.run([images.sys.executable, '-c', command[-1]], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert command[-1] == images.MONITORING_PROBE


def test_cli_reports_failed_stage_without_provider_values(tmp_path, monkeypatch, capsys):
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'changed_paths': ['terraform/envs/prod/terraform_state_read_iam.tf'], 'deployments': []}))
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': {side: {'repo': {'full_name': planner.REPOSITORY}} for side in ('head', 'base')}}))
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'pull_request')
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(event))
    monkeypatch.setattr('sys.argv', ['pr_terraform_plan.py', 'run', '--plan', str(plan)])
    def run(command, **kwargs):
        if command[2] == 'version':
            return subprocess.CompletedProcess(command, 0, '{"terraform_version":"1.8.5"}', '')
        assert command[2] == 'init'
        return subprocess.CompletedProcess(command, 1, 'secret-provider-value', 'secret-provider-value')
    monkeypatch.setattr(planner.subprocess, 'run', run)
    with pytest.raises(SystemExit) as error:
        planner.main()
    assert error.value.code == 1
    captured = capsys.readouterr()
    assert 'Terraform init failed' in captured.err
    assert 'secret-provider-value' not in captured.out + captured.err
