"""Registry facts and serialized CI plans survive dependency-free execution."""
import json
import copy
import os
from pathlib import Path
import runpy
import subprocess
import sys

import pytest
import yaml

from scripts import ci_contract, terraform_target_contracts as contracts
from scripts.tested_image_bundle import BUILD_TARGETS
from test_ci_preflight import emit_plan, fixture_repo

ROOT = Path(__file__).resolve().parents[1]


def test_historical_record_targets_derive_live_workflows_from_the_registry(monkeypatch):
    expected = {'monitoring-slack': 'cron-alert-policy-sync.yml', 'wdpa-monthly-build': 'wdpa-monthly-build.yml',
                'typescript-sdk': 'publish-typescript-sdk.yml', 'pmtiles-cdn': 'pmtiles-cdn-sync.yml',
                'catalog-web': 'catalog-web-deploy.yml', 'catalog-viewer': 'catalog-viewer-deploy.yml',
                'wdpa-monthly': 'wdpa-monthly-deploy.yml', 'mpatlas-monthly': 'mpatlas-monthly-deploy.yml',
                'eamlis-monthly': 'eamlis-monthly-deploy.yml', 'sea-ice-daily': 'sea-ice-daily-deploy.yml',
                'dataset-usage': 'dataset-usage-deploy.yml'}
    script = ROOT / 'scripts/deployment_revision.py'
    assert runpy.run_path(str(script))['TARGET_WORKFLOWS'] == expected
    original = contracts.registry
    workflows = original(section='workflows')
    workflows['replacement-sdk.yml'] = workflows.pop('publish-typescript-sdk.yml')
    workflows['replacement-viewer.yml'] = workflows.pop('catalog-viewer-deploy.yml')
    monkeypatch.setattr(contracts, 'registry', lambda *args, section='targets', **kwargs:
                        workflows if section == 'workflows' else original(*args, section=section, **kwargs))
    assert runpy.run_path(str(script))['TARGET_WORKFLOWS'] == expected | {
        'catalog-viewer': 'replacement-viewer.yml', 'typescript-sdk': 'replacement-sdk.yml'}


def test_plan_cli_serializes_caps_from_the_tested_tree(tmp_path):
    root, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    source = root / 'terraform/envs/prod/sea_ice_daily.tf'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes((ROOT / 'terraform/envs/prod/sea_ice_daily.tf').read_bytes())
    # Use the real declaration's whitespace without relying on formatting.
    import re
    source.write_text(re.sub(r'(\bcpu\s*=\s*)"2"', r'\1"1"', source.read_text()))
    git('add', '.')
    git('commit', '-m', 'Change a production cap')
    plan, _ = emit_plan(root, base, tmp_path)
    limits = plan['production_image_limits']['sea-ice-daily']
    assert limits == ['--cpu-period', '100000', '--cpu-quota', '100000', '--memory', '512m']
    assert limits != contracts.production_image_limits()['sea-ice-daily']


@pytest.mark.parametrize('target', BUILD_TARGETS)
def test_serialized_caps_execute_the_actual_image_cli_with_only_stdlib(tmp_path, target):
    limits = contracts.production_image_limits()[target]
    binary = tmp_path / 'docker'
    binary.write_text(f'#!{sys.executable}\nimport sys\n'
                      'if sys.argv[1:3] == ["image", "inspect"]: print("sha256:" + "a" * 64)\n')
    binary.chmod(0o755)
    done = subprocess.run([sys.executable, '-I', '-S', 'scripts/production_image_contracts.py',
                           '--target', target, '--limits', json.dumps(limits)], cwd=ROOT,
                          env={**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
                               'VIRTUAL_ENV': str(tmp_path / 'mismatch')}, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_gate_eligibility_matches_every_post_gate_caller_and_cannot_escape_main():
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    jobs = workflow['jobs']
    callers = {name for name, job in jobs.items() if 'ci-ready' in
               (job['needs'] if isinstance(job.get('needs'), list) else [job.get('needs')])}
    plan = {'deployments': list(ci_contract.DEPLOYMENTS), 'readiness_targets': ['sea_ice'], 'suites': list(ci_contract.SUITES)}
    assert set(ci_contract.delivery_outputs(plan, 'push', 'refs/heads/main')) == callers
    assert all(ci_contract.delivery_outputs(plan, 'push', 'refs/heads/main').values())
    for event, ref in [('pull_request', 'refs/heads/main'), ('workflow_dispatch', 'refs/heads/main'), ('push', 'refs/heads/topic')]:
        assert not any(ci_contract.delivery_outputs(plan, event, ref).values())
    assert jobs['ci-ready']['outputs']['delivery'] == "${{ job.status == 'success' && github.event_name == 'push' && github.ref == 'refs/heads/main' && steps.verify.outputs.delivery || '{}' }}"
    assert jobs['ci-ready']['outputs']['bootstrap'] == "${{ job.status == 'success' && github.event_name == 'push' && github.ref == 'refs/heads/main' && steps.verify.outputs.bootstrap || '[]' }}"
    verify = next(step for step in jobs['ci-ready']['steps'] if step.get('id') == 'verify')
    assert '--github-output "$GITHUB_OUTPUT"' in verify['run']
    for name in callers:
        assert f"fromJSON(needs.ci-ready.outputs.delivery || '{{}}').{name}" in jobs[name]['if']


def test_bootstrap_has_no_independent_python_helpers_and_uses_existing_owners():
    assert not list((ROOT / '.github/actions').rglob('*.py'))
    action = yaml.safe_load((ROOT / '.github/actions/deployment-bootstrap/action.yml').read_text())
    commands = [step.get('run', '') for step in action['runs']['steps']]
    assert 'python -I -S scripts/deployment_revision.py accept' in commands
    assert any('python scripts/tested_image_bundle.py "$TRUSTED_WORKFLOW_SHA"' in command for command in commands)


@pytest.mark.parametrize('successful', [True, False])
def test_verify_cli_emits_delivery_only_after_serialized_results_pass(tmp_path, monkeypatch, successful):
    from scripts import ci_preflight as preflight
    from test_ci_preflight import jobs_for, results_for
    root, git = fixture_repo(tmp_path)
    head = git('rev-parse', 'HEAD')
    plan = preflight.make_plan(root, head, head)
    reports = results_for(plan)
    if not successful:
        reports[0]['status'] = 'failure'
    plan_file, jobs_file, output = (tmp_path / name for name in ('plan.json', 'jobs.json', 'delivery-output'))
    plan_file.write_text(json.dumps(plan))
    jobs_file.write_text(json.dumps(jobs_for(plan)))
    for report in reports:
        directory = tmp_path / 'results' / report['suite']
        directory.mkdir(parents=True)
        (directory / 'result.json').write_text(json.dumps(report))
    monkeypatch.delenv('GITHUB_RUN_ID', raising=False)
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, 'argv', ['ci_preflight.py', 'verify', '--plan', str(plan_file),
        '--results', str(tmp_path / 'results'), '--jobs', str(jobs_file), '--github-output', str(output)])
    assert preflight.main() == (0 if successful else 1)
    if successful:
        values = dict(line.split('=', 1) for line in output.read_text().splitlines())
        assert json.loads(values['delivery']) == ci_contract.delivery_outputs(plan, 'push', 'refs/heads/main')
        assert json.loads(values['bootstrap']) == []
    else:
        assert not output.exists()


@pytest.mark.parametrize('scope,owner,bootstrap', [
    ('Monitoring alert policy IAM bootstrap', 'cron_alert_policy', True),
    ('Monitoring Slack transport resources', 'cron_alert_policy', True),
    ('Dataset usage deploy IAM bootstrap', 'dataset_usage', True),
    ('viewer-bootstrap', 'catalog_viewer', True),
    ('Cron alert policy sync', 'cron_alert_policy', False),
    ('terraform/envs/prod/monitoring_alert_policy_iam.tf', 'cron_alert_policy', True),
    ('terraform/envs/prod/dataset_usage.tf', 'dataset_usage', True),
    ('terraform/envs/prod/pmtiles_cdn.tf', 'catalog_viewer', True),
])
def test_changed_scope_reaches_its_bootstrap_through_the_real_gate(tmp_path, monkeypatch, scope, owner, bootstrap):
    root, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    registry = root / '.github/terraform-targets.json'
    value = json.loads(registry.read_text())
    if scope.startswith('terraform/'):
        path = root / scope
        path.write_text(path.read_text() + '\n# changed bootstrap policy source\n')
    elif scope == 'viewer-bootstrap':
        scopes = value['plan_scopes']['catalog-viewer-deploy.yml']
        scopes['bootstrap']['exact'].append(scopes['runtime']['exact'][0])
    else:
        value['targets'][scope]['post_apply_wait_seconds'] = 1
    if not scope.startswith('terraform/'):
        registry.write_text(json.dumps(value))
    git('add', '.')
    git('commit', '-m', 'one changed registry scope')
    plan, _ = emit_plan(root, base, tmp_path / 'selection')
    assert owner in plan['deployments']
    conditional = {'dataset_usage', 'catalog_viewer', 'cron_alert_policy'}
    assert set(plan.get('bootstrap_targets', [])) & conditional == ({owner} if bootstrap else set())
    assert_bootstrap_gate(root, plan, tmp_path, monkeypatch)


@pytest.mark.parametrize('changed,bootstrap', [
    ('scripts/tested_image_bundle.py', False),
    ('scripts/terraform_target_contracts.py', False),
    ('.github/workflows/prod-terraform-target-apply.yml', False),
    ('pyproject.toml', False),
    ('terraform/envs/prod/main.tf', True),
    ('terraform/envs/prod/variables.tf', True),
    ('terraform/envs/prod/production.auto.tfvars', True),
    ('terraform/envs/prod/.terraform.lock.hcl', True),
    ('terraform/envs/prod/github-workflow-identities.json', True),
    ('terraform/envs/prod/canonical_mutation_iam.tf', True),
    ('terraform/modules/service_account/main.tf', True),
    ('terraform/envs/prod/new_cross_file_locals.tf', True),
    ('removed-bootstrap', True),
    ('renamed-bootstrap', True),
])
def test_bootstrap_eligibility_distinguishes_executor_and_shared_inputs_through_the_gate(tmp_path, monkeypatch, changed, bootstrap):
    root, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    owners = {'dataset_usage', 'catalog_viewer', 'cron_alert_policy'}
    # Select the actual callers independently of the bootstrap input. Uncertainty
    # must cover selected owners without authorizing an unrelated delivery.
    for workflow in ('dataset-usage-deploy', 'catalog-viewer-deploy', 'cron-alert-policy-sync'):
        source = root / f'.github/workflows/{workflow}.yml'
        source.write_text(source.read_text() + '\n# exercise this selected executor\n')
    if changed in {'removed-bootstrap', 'renamed-bootstrap'}:
        source = root / 'terraform/envs/prod/monitoring_alert_policy_iam.tf'
        if changed == 'removed-bootstrap':
            # Keep declarations valid after removing their original source file.
            destination = root / 'terraform/envs/prod/dataset_usage.tf'
            destination.write_text(destination.read_text() + '\n' + source.read_text())
            source.unlink()
        else:
            source.rename(source.with_name('renamed_monitoring_bootstrap.tf'))
    else:
        source = root / changed
        if source.suffix == '.json':
            source.write_text(source.read_text() + '\n')
        else:
            source.write_text((source.read_text() if source.exists() else '') + '\n# input changed\n')
    git('add', '.')
    git('commit', '-m', 'exercise bootstrap input ownership')
    plan, _ = emit_plan(root, base, tmp_path / 'selection')
    assert owners <= set(plan['deployments'])
    assert set(plan.get('bootstrap_targets', [])) <= set(plan['deployments'])
    assert set(plan.get('bootstrap_targets', [])) & owners == (owners if bootstrap else set())
    assert_bootstrap_gate(root, plan, tmp_path, monkeypatch)


def test_bootstrap_missing_comparison_and_explicit_shared_inputs_retain_owners():
    owners = {'dataset_usage', 'catalog_viewer', 'cron_alert_policy'}
    assert owners <= ci_contract.bootstrap_deployments(None)
    assert owners <= ci_contract.bootstrap_deployments(['.github/terraform-targets.json'])
    assert 'catalog_viewer' in ci_contract.bootstrap_deployments(['catalog/categories.yaml'])
    assert ci_contract.bootstrap_deployments([]) == set()


def test_missing_registry_snapshot_preserves_bootstrap_through_the_gate(tmp_path, monkeypatch):
    from scripts import ci_preflight as preflight

    root, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    registry = root / '.github/terraform-targets.json'
    registry.write_text(registry.read_text() + '\n')
    git('add', '.')
    git('commit', '-m', 'registry comparison unavailable')
    original = preflight.git

    def missing_snapshot(root, *args, **kwargs):
        if args == ('show', f'{base}:.github/terraform-targets.json'):
            raise subprocess.CalledProcessError(128, ['git', *args])
        return original(root, *args, **kwargs)

    monkeypatch.setattr(preflight, 'git', missing_snapshot)
    plan, _ = emit_plan(root, base, tmp_path / 'selection')
    assert plan['changed_paths'] == ['.github/terraform-targets.json']
    assert {'dataset_usage', 'catalog_viewer', 'cron_alert_policy'} <= set(plan['bootstrap_targets'])
    assert set(plan['bootstrap_targets']) <= set(plan['deployments'])
    assert_bootstrap_gate(root, plan, tmp_path, monkeypatch)


def assert_bootstrap_gate(root, plan, tmp_path, monkeypatch):
    from scripts import ci_preflight as preflight
    from test_ci_preflight import jobs_for, results_for
    from workflow_helpers import condition_result

    plan_file, jobs_file, output = (tmp_path / name for name in ('gate-plan.json', 'jobs.json', 'gate-output'))
    plan_file.write_text(json.dumps(plan))
    jobs_file.write_text(json.dumps(jobs_for(plan)))
    for report in results_for(plan):
        directory = tmp_path / 'results' / report['suite']
        directory.mkdir(parents=True)
        (directory / 'result.json').write_text(json.dumps(report))
    monkeypatch.delenv('GITHUB_RUN_ID', raising=False)
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, 'argv', ['ci_preflight.py', 'verify', '--plan', str(plan_file),
        '--results', str(tmp_path / 'results'), '--jobs', str(jobs_file), '--github-output', str(output)])
    assert preflight.main() == 0
    outputs = dict(line.split('=', 1) for line in output.read_text().splitlines())
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    program = """const expression=process.argv[1], needs=JSON.parse(process.argv[2]);
console.log(String(Function('needs','fromJSON','contains','return '+expression)
    (needs,JSON.parse,(values,item)=>values.includes(item))));"""
    for selection in ('dataset_usage', 'catalog_viewer', 'cron_alert_policy'):
        caller = workflow['jobs'][selection.replace('_', '-')]
        expression = caller['with']['sync_iam'].removeprefix('${{').removesuffix('}}').strip()
        expression = expression.replace('needs.ci-ready', 'needs["ci-ready"]').replace('.artifact-registry-iam', '["artifact-registry-iam"]')
        needs = {'ci-ready': {'outputs': outputs}}
        actual = subprocess.check_output(['node', '-e', program, expression, json.dumps(needs)], text=True).strip() == 'true'
        assert actual is (selection in plan.get('bootstrap_targets', []))
        leaf = yaml.safe_load((ROOT / caller['uses'].removeprefix('./')).read_text())
        context = {'github.ref': 'refs/heads/main', 'github.event_name': 'push', 'inputs.sync_iam': actual,
                   'needs.prepare.result': 'success', 'needs.prepare.outputs.proceed': 'true', 'needs.prepare.outputs.claim_signed': 'success'}
        assert condition_result(leaf['jobs']['bootstrap'], context) is actual
        iam_outputs = copy.deepcopy(outputs)
        iam_outputs['delivery'] = json.dumps(json.loads(outputs['delivery']) | {'artifact-registry-iam': True})
        needs['ci-ready']['outputs'] = iam_outputs
        assert (subprocess.check_output(['node', '-e', program, expression, json.dumps(needs)], text=True).strip() == 'true') is actual
