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

import pytest

from scripts import ci_contract as ci, ingestion_targets as ingestion, production_image_contracts as images
from scripts.terraform_target_contracts import registry
from workflow_helpers import load_workflow
from workflow_helpers import condition_result
from test_deployment_emission import IMAGE

ROOT = Path(__file__).resolve().parents[1]
REPLAY = json.loads((ROOT / 'tests/fixtures/ci-selection-replay.json').read_text())


@pytest.mark.parametrize('row', REPLAY, ids=lambda row: str(row['pr']))
def test_complete_historical_replay_preserves_selection(row):
    assert ci.resolve_selection(row['paths']) == row['expected']


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
                   'GITHUB_ENV': str(tmp_path / 'environment')}
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
    snapshot = runpy.run_path(str(ROOT / 'scripts/tested_image_bundle.py'))['snapshot_trusted']
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
    assert 'scripts/tested_image_bundle.py' in ci.CONTRACT_FILES


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
    calls = [step for step in job['steps'] if step.get('uses') == './.github/actions/ci-image-result']
    assert len(calls) == 7
    assert all("github.event_name == 'push'" in step['with']['retain-image']
               and 'deploy_image_targets' in step['with']['retain-image'] for step in calls)
    steps = load_workflow(ROOT / '.github/actions/ci-image-result/action.yml')['runs']['steps']
    retain = next(step for step in steps if step.get('id') == 'retained-image')
    assert retain['if'] == "inputs.retain-image == 'true'"
    assert retain['with']['retention-days'] == 2
    gate = next(step for step in steps if step.get('name') == 'Upload compact gate result')
    expression = copy.deepcopy(gate)
    expression['if'] = expression['if'].replace('inputs.retain-image', 'github.retain_image')
    assert step_condition(expression, {'github.retain_image': 'true' if event == 'push' else 'false',
                                   'steps.retained-results.outcome': 'success',
                                   'steps.retained-image.outcome': upload}) is allowed


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
