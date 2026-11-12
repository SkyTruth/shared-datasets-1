"""Narrow work at producer boundaries without weakening selected evidence."""
import copy
import json
from pathlib import Path

import pytest

from scripts import ci_contract as ci

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('path', [
    'ingestion/sea_ice_daily/run.py', 'ingestion/mpatlas_monthly/run.py',
    'api/python/src/skytruth_shared_datasets/snapshot.py', 'web/catalog/styles.css',
    'README.md',
])
def test_every_non_documentation_change_runs_control_plane_tests(path):
    assert ('tests-control-plane' in ci.resolve_selection([path])['suites']) is (path != 'README.md')


@pytest.mark.parametrize('path', [
    'scripts/ci_preflight.py', 'scripts/deployment_revision.py', 'scripts/ci_contract.py',
    'ingestion/common/publication.py', 'tests/test_ci_image_execution.py',
    'tests/conftest.py', '.github/workflows/ci.yml', 'terraform/envs/prod/main.tf',
    'tests/fixtures/terraform_targets/failed-main.json', 'new/unknown.runtime',
])
def test_changed_control_contracts_and_unknown_inputs_keep_control_plane_tests(path):
    assert 'tests-control-plane' in ci.resolve_selection([path])['suites']


@pytest.mark.parametrize('section,key,expected', [
    ('workflows', 'mpatlas-monthly-deploy.yml', {'mpatlas'}),
    ('targets', 'Cron alert policy sync', {'cron_alert_policy'}),
    ('plan_scopes', 'dataset-usage-deploy.yml', {'dataset_usage'}),
    ('image_checks', 'monitoring-slack', {'cron_alert_policy'}),
])
def test_registry_changes_select_only_the_owning_workflow(section, key, expected):
    before = json.loads((ROOT / '.github/terraform-targets.json').read_text())
    after = copy.deepcopy(before)
    after[section][key]['fixture_change'] = True
    assert ci.registry_deployment_targets(json.dumps(before), json.dumps(after)) == expected


def test_registry_formatting_does_not_select_delivery_but_shared_policy_stays_broad():
    before = json.loads((ROOT / '.github/terraform-targets.json').read_text())
    raw = json.dumps(before)
    assert ci.registry_deployment_targets(raw, json.dumps(before, indent=3)) == set()
    after = copy.deepcopy(before)
    after['permission_probes']['fixture'] = [['https://example.test/probe', ['read']]]
    assert ci.registry_deployment_targets(raw, json.dumps(after)) == set(ci.DEPLOYMENTS)
    assert set(ci.select_deployments(['.github/terraform-targets.json'])) == set(ci.DEPLOYMENTS)


def test_missing_selected_control_plane_result_still_refuses_the_gate():
    from test_ci_preflight import plan, results_for
    validation = plan(ci.ALWAYS | {'tests-control-plane'})
    results = [r for r in results_for(validation) if r['suite'] != 'tests-control-plane']
    with pytest.raises(ValueError, match='missing suite results'):
        ci.verify_results(validation, results)


def test_real_plan_uses_registry_snapshots_without_unrelated_infrastructure(tmp_path):
    from scripts import ci_preflight as preflight
    from test_ci_preflight import fixture_repo
    root, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    path = root / '.github/terraform-targets.json'
    registry = json.loads(path.read_text())
    registry['targets']['Preview Terraform IAM bootstrap']['post_apply_wait_seconds'] = 1
    path.write_text(json.dumps(registry))
    git('add', '.')
    git('commit', '-m', 'one reviewed scope')
    plan = preflight.make_plan(root, base, 'HEAD')
    assert plan['deployments'] == ['preview_terraform_iam']
    assert plan['production_image_targets'] == []
    assert plan['production_image_limits'] == {}


def test_docs_plan_never_initializes_the_hcl_parser(tmp_path, monkeypatch):
    from scripts import ci_preflight as preflight, terraform_target_contracts as contracts
    from test_ci_preflight import fixture_repo
    root, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    (root / 'README.md').write_text('Documentation only\n')
    git('add', '.')
    git('commit', '-m', 'docs')
    def forbidden(*args, **kwargs):
        raise AssertionError('documentation does not consume production HCL')
    monkeypatch.setattr(contracts, 'resource_index', forbidden)
    plan = preflight.make_plan(root, base, 'HEAD')
    assert plan['suites'] == ['lint', 'tests']
    assert plan['production_image_limits'] == {}


def test_runtime_selection_does_not_parse_unrelated_terraform(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('runtime selection does not consume Terraform ownership')
    monkeypatch.setattr(ci, 'resource_index', forbidden)
    ci.deployment_paths.cache_clear()
    assert ci.resolve_selection(['ingestion/mpatlas_monthly/run.py'])['deployments'] == ['mpatlas']
    with pytest.raises(AssertionError, match='Terraform ownership'):
        ci.select_deployments(['terraform/envs/prod/mpatlas_monthly.tf'])


def test_selected_image_limits_ignore_unrelated_terraform(tmp_path):
    from scripts.terraform_target_contracts import production_image_limits
    directory = tmp_path / 'terraform/envs/prod'
    directory.mkdir(parents=True)
    (directory / 'mpatlas_monthly.tf').write_bytes((ROOT / 'terraform/envs/prod/mpatlas_monthly.tf').read_bytes())
    (directory / 'unrelated.tf').write_text('invalid unrelated HCL')
    assert production_image_limits(tmp_path, targets=['mpatlas-monthly']) == production_image_limits(ROOT, targets=['mpatlas-monthly'])


def test_provider_setup_has_one_owner_and_partitions_cover_every_test(tmp_path):
    import shlex
    from workflow_helpers import load_workflow
    from scripts.ci_preflight import suite_commands
    plan = {'base': 'a' * 40, 'tested_sha': 'b' * 40, 'changed_paths': ['scripts/ci_contract.py']}
    partitions = []
    for suite in ('lint', 'tests', 'tests-control-plane'):
        commands = [args for args, _ in suite_commands(suite, ROOT, plan, tmp_path)]
        initializations = [args for args in commands if args[0] == 'terraform' and 'init' in args]
        assert len(initializations) == (2 if suite == 'tests-control-plane' else 0)
        if suite != 'lint':
            pytest_command, = [args for args in commands if 'pytest' in args]
            assert all(commands.index(args) < commands.index(pytest_command) for args in initializations)
            partitions.append({arg for arg in pytest_command if arg.startswith('tests/')})
        assert not any('init' in shlex.split(step.get('run', '')) and shlex.split(step.get('run', ''))[:1] == ['terraform']
                       for step in load_workflow(ROOT / '.github/workflows/ci.yml')['jobs'][suite]['steps'])
    assert not partitions[0] & partitions[1]
    expected = {p.relative_to(ROOT).as_posix() for pattern in ('test_*.py', '*_test.py') for p in (ROOT / 'tests').rglob(pattern)}
    assert partitions[0] | partitions[1] == expected
    assert ci.PROVIDER_TESTS <= partitions[1]
    provider_consumers = {name for name in expected if any(marker in (ROOT / name).read_text()
                          for marker in ('contracts.prepare(', 'contracts.graph('))}
    assert provider_consumers <= partitions[1]


@pytest.mark.parametrize('job,condition', [
    ('prepare', 'false'), ('prepare', 'true'), ('sync', 'always()'),
])
def test_terraform_scope_cannot_bypass_required_preparation_or_failure(job, condition):
    import yaml
    from scripts.deployment_receipt_contracts import terraform_boundary
    workflow = yaml.safe_load((ROOT / '.github/workflows/prod-terraform-target-apply.yml').read_text())
    assert terraform_boundary(workflow) == []
    workflow['jobs'][job]['if'] = condition
    assert terraform_boundary(workflow)


@pytest.mark.parametrize('result,proceed,expected', [
    ('success', 'true', True), ('success', 'false', False),
    ('skipped', 'true', True), ('skipped', '', True),
    ('failure', 'true', False), ('failure', '', False),
    ('cancelled', 'true', False), ('cancelled', '', False),
])
def test_terraform_apply_admission_checks_dependency_result(result, proceed, expected):
    import subprocess
    import yaml
    workflow = yaml.safe_load((ROOT / '.github/workflows/prod-terraform-target-apply.yml').read_text())
    expression = workflow['jobs']['sync']['if'].removeprefix('${{').removesuffix('}}').strip()
    cases = [{'needs': {'prepare': {'result': result, 'outputs': {'proceed': proceed}}},
              'cancelled': cancelled, 'failed': failed}
             for cancelled, failed in ((False, False), (True, False), (False, True))]
    program = """const expression = process.argv[1], cases = JSON.parse(process.argv[2]);
console.log(JSON.stringify(cases.map(c => Function('needs', 'cancelled', 'failure', 'return '+expression)
    (c.needs, () => c.cancelled, () => c.failed))));"""
    actual = json.loads(subprocess.check_output(['node', '-e', program, expression, json.dumps(cases)], text=True))
    assert actual == [expected, False, False]
