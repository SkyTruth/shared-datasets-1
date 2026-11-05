"""Declared deployment boundaries, including the complete 120-PR replay."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import ci_contract as ci, ingestion_targets as ingestion, production_image_contracts as images
from scripts import deployment_revision as revision, deployment_emission as emission
from scripts.terraform_target_contracts import registry
from workflow_helpers import load_workflow
from test_ci_deployments import condition_result
from test_deployment_emission import record, run, status, verified, REPO, SHA, IMAGE

ROOT = Path(__file__).resolve().parents[1]
REPLAY = json.loads((ROOT / 'tests/fixtures/ci-selection-replay.json').read_text())


@pytest.mark.parametrize('row', REPLAY, ids=lambda row: str(row['pr']))
def test_complete_historical_replay_preserves_selection(row):
    assert ci.resolve_selection(row['paths']) == row['expected']


def test_registry_declaration_selects_new_input_without_another_python_table(tmp_path, monkeypatch):
    value = json.loads((ROOT / '.github/terraform-targets.json').read_text())
    value['deployments']['catalog_viewer']['inputs'].append('scripts/new-viewer-input.py')
    (tmp_path / '.github').mkdir()
    (tmp_path / '.github/terraform-targets.json').write_text(json.dumps(value))
    monkeypatch.setattr(ci, 'registry', lambda root=None, **kwargs: registry(tmp_path, **kwargs))
    ci.deployment_paths.cache_clear()
    try:
        assert ci.select_deployments(['scripts/new-viewer-input.py']) == ['catalog_viewer']
        assert ci.select_deployments(['scripts/unregistered-input.py']) == []
    finally:
        ci.deployment_paths.cache_clear()


def parse_environment(text):
    return dict(re.findall(r'([A-Z_]+)<<INGESTION_POLICY\n([^\n]*)\nINGESTION_POLICY\n', text))


@pytest.mark.parametrize('target', ingestion.DEPLOY_TARGETS)
def test_executor_reads_exact_typed_emission_for_every_target(tmp_path, target):
    workflow = load_workflow(ROOT / '.github/workflows/ingestion-deploy.yml')
    static = set(workflow['env']) | {'GITHUB_WORKSPACE', 'GITHUB_ENV'}
    steps = [step for job in workflow['jobs'].values() for step in job.get('steps', [])]
    reads = set(re.findall(r'env\.([A-Z_]+)', json.dumps(workflow))) - static
    key_types = dict(ingestion.ExecutorEnvironment.__annotations__)
    assert reads <= set(key_types)
    command = next(step['run'] for step in steps if step.get('id') == 'target')
    environment = {**os.environ, 'TARGET': target, 'WORKFLOW_FILE': target + '-deploy.yml',
                   'GITHUB_ENV': str(tmp_path / 'environment'), 'STAGE': 'all'}
    result = subprocess.run(['bash', '-e', '-c', command], cwd=ROOT, env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    emitted = parse_environment((tmp_path / 'environment').read_text())
    assert set(emitted) == set(key_types)
    assert emitted['ROLLOUT_GATE'] == str(ingestion.DEPLOY_TARGETS[target]['rollout_gate']).lower()
    assert emitted['ASSET_ROOT'] == ingestion.DEPLOY_TARGETS[target]['asset_root']
    assert all(emitted[key] in {'true', 'false'} for key, kind in key_types.items() if kind == 'bool')
    for missing in ('ROLLOUT_GATE', 'ASSET_ROOT'):
        output = tmp_path / missing
        failed = subprocess.run(['bash', '-e', '-c', command.replace(missing, missing + '_RENAMED')], cwd=ROOT,
                                env=environment | {'GITHUB_ENV': str(output)}, text=True, capture_output=True)
        assert failed.returncode != 0
        assert not output.exists()


@pytest.mark.parametrize('stage,target,identifier,image,accepted', [
    ('all', 'dataset-usage', '', '', True), ('apply', 'dataset-usage', '', '', True),
    ('verify', 'dataset-usage', '71', IMAGE, True), ('verify', 'wdpa-monthly', '71', IMAGE, False),
    ('invalid', 'dataset-usage', '', '', False), ('verify', 'dataset-usage', '', IMAGE, False),
    ('verify', 'dataset-usage', '71', 'mutable:latest', False), ('all', 'dataset-usage', '71', IMAGE, False),
])
def test_split_stage_is_closed_before_authentication(tmp_path, stage, target, identifier, image, accepted):
    workflow = load_workflow(ROOT / '.github/workflows/ingestion-deploy.yml')
    command = next(step['run'] for step in workflow['jobs']['verify']['steps'] if step.get('id') == 'target')
    env = {**os.environ, 'TARGET': target, 'WORKFLOW_FILE': target + '-deploy.yml', 'GITHUB_ENV': str(tmp_path / 'env'),
           'STAGE': stage, 'VERIFY_DEPLOYMENT_ID': identifier, 'VERIFY_IMAGE': image}
    result = subprocess.run(['bash', '-e', '-c', command], cwd=ROOT, env=env, text=True, capture_output=True)
    assert (result.returncode == 0) is accepted, result.stderr
    assert (tmp_path / 'env').exists() is accepted


def test_import_and_literal_executor_closure_restores_python_and_javascript(tmp_path):
    root = tmp_path / 'repository'
    (root / 'scripts').mkdir(parents=True)
    (root / '.github').mkdir()
    (root / '.github/terraform-targets.json').write_text('{}')
    policy = root / 'api/typescript/scripts/release-policy.mjs'
    policy.parent.mkdir(parents=True)
    policy.write_text('console.log("trusted javascript");\n')
    (root / 'scripts/dataset_usage_deploy.py').write_text('print("trusted telemetry")\n')
    (root / 'scripts/deployment_revision.py').write_text('''import subprocess, sys
from pathlib import Path
subprocess.run([sys.executable, 'scripts/dataset_usage_deploy.py'], check=True)
policy = Path.cwd() / 'api/typescript/scripts/release-policy.mjs'
subprocess.run(['node', str(policy)], check=True)
''')
    for name in ('terraform_plan_allowlist', 'install_deployment_verifier'):
        (root / f'scripts/{name}.py').write_text('')
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE, text=True).strip()
    git('init')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org', 'commit', '-m', 'trusted executor')
    snapshot = runpy.run_path(str(ROOT / '.github/actions/deployment-bootstrap/restore.py'))['snapshot']
    archive = tmp_path / 'trusted.tar'
    snapshot(git('rev-parse', 'HEAD'), archive, root=root)
    for path in (policy, root / 'scripts/dataset_usage_deploy.py'):
        path.write_text('candidate bytes must not execute!')
    with tarfile.open(archive) as saved:
        saved.extractall(root, filter='data')
    result = subprocess.run([sys.executable, 'scripts/deployment_revision.py'], cwd=root, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ['trusted telemetry', 'trusted javascript']
    # Evidence invalidation must include the snapshot program itself.
    assert '.github/actions/deployment-bootstrap/restore.py' in ci.CONTRACT_FILES


@pytest.mark.parametrize('defect', ['valid', 'empty', 'unknown-mode', 'unknown-placeholder'])
def test_image_checks_are_registered_argv_and_reject_incomplete_declarations(monkeypatch, defect):
    checks = copy.deepcopy(registry(section='image_checks'))
    checks['dataset-usage']['container'].append(['{image}', '--new-registered-probe'])
    if defect == 'empty':
        checks['dataset-usage'] = {}
    elif defect == 'unknown-mode':
        checks['dataset-usage']['unregistered'] = [['{image}']]
    elif defect == 'unknown-placeholder':
        checks['dataset-usage']['container'] = [['{missing_binding}']]
    monkeypatch.setattr(images, 'registry', lambda **kwargs: checks)
    if defect == 'valid':
        commands = images.commands('dataset-usage', 'a' * 40, image_id='sha256:' + 'b' * 64)
        assert commands[-1][-2:] == ['sha256:' + 'b' * 64, '--new-registered-probe']
        assert set(checks) == set(images.BUILD_TARGETS)
    else:
        with pytest.raises((ValueError, KeyError)):
            images.commands('dataset-usage', 'a' * 40)


def test_viewer_and_cdn_pass_all_registered_image_placeholders_and_fail_before_plan(tmp_path):
    expected = ingestion.image_variables('', 'unused-by-reconciliation')
    for filename in ('catalog-viewer-deploy.yml', 'pmtiles-cdn-sync.yml'):
        workflow = load_workflow(ROOT / '.github/workflows' / filename)
        plans = [step['run'] for job in workflow['jobs'].values() for step in job.get('steps', [])
                 if 'image_vars_text=' in step.get('run', '')]
        assert len(plans) == 2
        for index, command in enumerate(plans):
            calls = tmp_path / f'{filename}-{index}'
            env = {**os.environ, 'GITHUB_WORKSPACE': str(ROOT), 'RUNNER_TEMP': str(tmp_path), 'CATALOG_VIEWER_IMAGE': IMAGE, 'CALLS': str(calls)}
            prefix = 'bash() { printf "%s\\n" "$@" > "$CALLS"; }\n'
            for accepted in (True, False):
                result = subprocess.run(['bash', '-eu', '-c', prefix + ('' if accepted else 'python() { return 7; }\n') + command],
                                        cwd=ROOT, env=env, text=True, capture_output=True)
                if accepted:
                    assert result.returncode == 0, result.stderr
                    variables = dict(arg[5:].split('=', 1) for arg in calls.read_text().splitlines() if arg.startswith('-var='))
                    assert expected.items() <= variables.items()
                    calls.unlink()
                else:
                    assert result.returncode != 0
                    assert not calls.exists()


def step_condition(step, values):
    expression = step.get('if', 'True').removeprefix('${{').removesuffix('}}').strip()
    expression = expression.replace('always()', 'True').replace('steps.', 'needs.')
    return condition_result({'if': '!cancelled() && (' + expression + ')'},
                            {key.replace('steps.', 'needs.'): value for key, value in values.items()})


@pytest.mark.parametrize('event,upload,allowed', [('pull_request', 'skipped', True), ('push', 'success', True),
                                                  ('push', 'skipped', False), ('push', 'failure', False)])
def test_pr_skips_unconsumed_tars_but_keeps_gate_evidence(event, upload, allowed):
    job = load_workflow(ROOT / '.github/workflows/ci.yml')['jobs']['production-images']
    retain = next(step for step in job['steps'] if step.get('id') == 'retained-image')
    assert step_condition(retain, {'github.event_name': event}) is (event != 'pull_request')
    gate = next(step for step in job['steps'] if step.get('name') == 'Upload compact gate result')
    assert step_condition(gate, {'github.event_name': event, 'steps.retained-results.outcome': 'success',
                                   'steps.retained-image.outcome': upload}) is allowed


def test_usage_apply_unblocks_iam_independently_of_canary():
    jobs = load_workflow(ROOT / '.github/workflows/ci.yml')['jobs']
    assert jobs['dataset-usage']['with']['stage'] == 'apply'
    verify = jobs['dataset-usage-verify']
    assert verify['concurrency'] == {'group': 'deployment-dataset-usage', 'queue': 'max', 'cancel-in-progress': False}
    assert verify['with']['stage'] == 'verify'
    assert verify['with']['deployment_id'] == '${{ needs.dataset-usage.outputs.deployment_id }}'
    for name in ('ingestion-iam', 'scratch-cleanup-iam'):
        assert 'dataset-usage' in jobs[name]['needs'] and 'dataset-usage-verify' not in jobs[name]['needs']
        values = {'github.event_name': 'push', 'github.ref': 'refs/heads/main', 'needs.ci-ready.result': 'success',
                  'needs.geospatial-changes.outputs.dataset_usage': 'true', 'needs.dataset-usage.result': 'success',
                  'needs.geospatial-changes.outputs.' + name.replace('-', '_'): 'true'}
        assert condition_result(jobs[name], values)
        assert not condition_result(jobs[name], values | {'needs.dataset-usage.result': 'failure'})


@pytest.mark.parametrize('defect', ['valid', 'image', 'target', 'executor', 'source-attempt', 'execution-attempt',
                                  'unsigned', 'wrong-leaf', 'failed', 'unapplied', 'later-outcome'])
def test_split_verification_consumes_exact_signed_applied_receipt(tmp_path, monkeypatch, defect):
    claim = record()
    claim['environment'] = 'production-dataset-usage'
    claim['payload']['target'] = 'dataset-usage'
    phase = status('applied')
    if defect in {'failed', 'unapplied', 'later-outcome'}:
        phase = status({'failed': 'failed', 'unapplied': 'started', 'later-outcome': 'verified'}[defect])
    emitter = run()
    emitter['referenced_workflows'] = [{'path': f'{REPO}/.github/workflows/ingestion-deploy.yml@refs/heads/main', 'sha': SHA, 'ref': 'refs/heads/main'}]
    api = Mock()
    def get(path):
        if '/deployments/' in path:
            return claim
        if path == 'repos/' + REPO:
            return {'id': 17, 'full_name': REPO}
        if '/actions/workflows/' in path:
            return {'id': 12, 'path': emitter['path']}
        return emitter
    api.get.side_effect = get
    api.pages.side_effect = lambda path, field=None: [phase] if "/statuses?" in path else [{"name": "ci-ready", "status": "completed", "conclusion": "success"}]
    env = dict(GITHUB_REPOSITORY=REPO, GITHUB_RUN_ID='13', GITHUB_RUN_ATTEMPT='2', TARGET='dataset-usage', EXECUTOR_SHA=SHA,
               SOURCE_RUN_ID='13', SOURCE_RUN_ATTEMPT='2', IMAGE=IMAGE, RUNNER_TEMP=str(tmp_path), STAGE='verify')
    field = {'image': 'IMAGE', 'target': 'TARGET', 'executor': 'EXECUTOR_SHA', 'source-attempt': 'SOURCE_RUN_ATTEMPT', 'execution-attempt': 'GITHUB_RUN_ATTEMPT'}.get(defect)
    if field:
        env[field] = '3' if field.endswith('ATTEMPT') else 'wrong'
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    value = emission.receipt(REPO, claim, phase)
    proof = verified(value, emitter=emitter, signer=revision.INGESTION_SIGNER)
    if defect == 'wrong-leaf':
        proof[0]['verificationResult']['signature']['certificate']['buildSignerURI'] = 'https://github.com/' + REPO + '/.github/workflows/unrelated.yml@refs/heads/main'
    def attest(command, **kwargs):
        if defect == 'unsigned':
            raise subprocess.CalledProcessError(1, command)
        return json.dumps(proof)
    monkeypatch.setattr(emission.subprocess, 'check_output', attest)
    if defect == 'valid':
        revision.verify_applied(SimpleNamespace(deployment_id=71), api)
    else:
        with pytest.raises((revision.DeploymentError, subprocess.CalledProcessError)):
            revision.verify_applied(SimpleNamespace(deployment_id=71), api)
        if field:
            with pytest.raises(revision.DeploymentError):
                revision.finish(SimpleNamespace(deployment_id=71, phase='failed', execution=''), api)
    api.post.assert_not_called()


@pytest.mark.parametrize('change,error', [
    ({'plan_guard': 'unknown'}, 'unknown plan guard'),
    ({'canary_mode': 'unknown'}, 'unknown canary mode'),
    ({'rollout_gate': 'true'}, 'gates must be booleans'),
    ({'targets': ['owned', 'owned']}, 'resource targets must be distinct'),
    ({'after_verify': 'arbitrary shell'}, 'unknown verification hook'),
])
def test_registry_reports_the_named_invalid_policy(change, error):
    values = dict(selection='example', asset_root='100-category/110-family/example',
                  targets=['module.example_job.google_cloud_run_v2_job.this'])
    with pytest.raises(ValueError, match=error):
        ingestion.target('example', **(values | change))
