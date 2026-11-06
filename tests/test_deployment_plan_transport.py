"""Registry facts and serialized CI plans survive dependency-free execution."""
import json
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
    plan = {'deployments': list(ci_contract.DEPLOYMENTS), 'suites': list(ci_contract.SUITES)}
    assert set(ci_contract.delivery_outputs(plan, 'push', 'refs/heads/main')) == callers
    assert all(ci_contract.delivery_outputs(plan, 'push', 'refs/heads/main').values())
    for event, ref in [('pull_request', 'refs/heads/main'), ('workflow_dispatch', 'refs/heads/main'), ('push', 'refs/heads/topic')]:
        assert not any(ci_contract.delivery_outputs(plan, event, ref).values())
    assert jobs['ci-ready']['outputs']['delivery'] == "${{ job.status == 'success' && github.event_name == 'push' && github.ref == 'refs/heads/main' && steps.verify.outputs.delivery || '{}' }}"
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
        key, raw = output.read_text().strip().split('=', 1)
        assert key == 'delivery'
        assert json.loads(raw) == ci_contract.delivery_outputs(plan, 'push', 'refs/heads/main')
    else:
        assert not output.exists()
