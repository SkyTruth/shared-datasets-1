"""The npm trusted publisher identity consumes immutable CI bytes."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]


def test_sdk_publisher_keeps_workflow_identity_and_oidc_only_for_mutation():
    workflow = load_workflow(ROOT / '.github/workflows/publish-typescript-sdk.yml')
    assert workflow['name'] == 'Publish TypeScript SDK'
    trigger = workflow_triggers(workflow)
    assert set(trigger) == {'workflow_run', 'workflow_dispatch'}
    assert trigger['workflow_run'] == {'workflows': ['CI'], 'branches': ['main'], 'types': ['completed']}
    assert set(trigger['workflow_dispatch']['inputs']) == {'executor_sha', 'source_run_id', 'source_run_attempt'}
    for name, field in trigger['workflow_dispatch']['inputs'].items():
        assert field['required'] and field['type'] == 'string'
        assert '${{ inputs.' + name in workflow['env'][name.upper()]
    assert workflow['permissions'] == {'contents': 'read', 'actions': 'read', 'deployments': 'read', 'attestations': 'read'}
    candidate, publish = workflow['jobs']['candidate'], workflow['jobs']['publish']
    assert 'id-token' not in candidate.get('permissions', {})
    assert publish['permissions']['id-token'] == 'write'
    assert publish['permissions']['deployments'] == 'write'
    assert publish['permissions']['attestations'] == 'write'
    assert publish['needs'] == 'candidate'
    assert publish['if'] == "needs.candidate.outputs.release_needed == 'true'"
    assert publish['concurrency'] == {'group': 'publish-typescript-sdk', 'queue': 'max', 'cancel-in-progress': False}


def test_sdk_release_does_not_rebuild_and_registry_verifies_tested_bytes():
    workflow = load_workflow(ROOT / '.github/workflows/publish-typescript-sdk.yml')
    steps = workflow_steps_by_name(workflow, 'publish')
    commands = '\n'.join(step.get('run', '') for step in steps.values())
    for forbidden in ('npm ci', 'npm test', 'npm run test:pack', 'npm version', 'git push', 'git commit'):
        assert forbidden not in commands
    assert 'sdk_release_authorization.py' in steps['Verify tested SDK candidate and reviewed version']['run']
    assert steps['Check out exact tested revision']['with']['ref'] == '${{ env.EXECUTOR_SHA }}'
    assert not steps['Check out exact tested revision']['with']['persist-credentials']
    publish = steps['Publish exact tested package']
    assert publish['env']['TARBALL'] == '${{ steps.candidate.outputs.tarball }}'
    assert '--ignore-scripts' in publish['run']
    assert publish['if'] == "steps.deployment.outputs.proceed == 'true' && steps.claim-receipt.outcome == 'success' && steps.registry.outputs.should_publish == 'true'"
    assert list(steps).index('Record serialized SDK deployment attempt') < list(steps).index('Attest serialized deployment claim before mutation') < list(steps).index('Publish exact tested package')
    receipt = steps['Attest serialized deployment claim before mutation']
    assert receipt['id'] == 'claim-receipt'
    assert receipt['uses'] == './.github/actions/deployment-receipt'
    assert receipt['if'] == "${{ steps.deployment.outputs.proceed == 'true' }}"
    assert receipt['with'] == {'mode': 'receipt', 'receipt-path': '${{ steps.deployment.outputs.receipt_path }}'}
    assert 'should_publish=false' in steps['Verify registry retained the tested bytes']['run']
    assert workflow['env']['NODE_VERSION'] == '24.13.1'


def test_completed_sdk_revision_skips_expiring_artifacts_before_publish_allocation():
    workflow = load_workflow(ROOT / '.github/workflows/publish-typescript-sdk.yml')
    for job in ('candidate', 'publish'):
        steps = workflow_steps_by_name(workflow, job)
        names = list(steps)
        assert names.index('Check already completed SDK publication') < names.index('Verify tested SDK candidate and reviewed version')
        assert '--target typescript-sdk' in steps['Check already completed SDK publication']['run']
        assert steps['Verify tested SDK candidate and reviewed version']['if'] == "steps.replay.outputs.proceed == 'true'"


def test_verified_nonready_ci_stops_before_strict_authorization_and_mutation():
    workflow = load_workflow(ROOT / '.github/workflows/publish-typescript-sdk.yml')
    for job in ('candidate', 'publish'):
        steps = workflow_steps_by_name(workflow, job)
        names = list(steps)
        assert names.index('Check out trusted deployment verifier') < names.index('Admit verified CI completion') < names.index('Verify trusted tested source authority')
        assert '--admit-source' in steps['Admit verified CI completion']['run']
        for name in ('Verify trusted tested source authority', 'Check out exact tested revision', 'Check already completed SDK publication'):
            assert steps[name]['if'] == "steps.source.outputs.source_eligible == 'true'"
    steps = workflow_steps_by_name(workflow, 'publish')
    for name in ('Check registry version and exact tarball integrity', 'Record serialized SDK deployment attempt'):
        assert "steps.candidate.outputs.release_needed == 'true'" in steps[name]['if']


@pytest.mark.parametrize(('visible_after', 'error', 'succeeds', 'expected_elapsed'), [
    (0, '', True, 0),
    (90, '', True, 90),
    (600, '', True, 600),
    (None, '', False, 600),
    (0, 'different or missing integrity', False, 0),
    (0, 'ECONNRESET', False, 0),
])
def test_registry_confirmation_waits_only_for_missing_version(
    tmp_path, visible_after, error, succeeds, expected_elapsed,
):
    """Run the workflow shell and real verifier with virtual npm transport/time."""
    workflow = load_workflow(ROOT / '.github/workflows/publish-typescript-sdk.yml')
    step = workflow_steps_by_name(workflow, 'publish')['Verify registry retained the tested bytes']
    state_path = tmp_path / 'registry.json'
    state_path.write_text(json.dumps({'elapsed': 0, 'checks': 0}))
    package = {'name': '@skytruth/shared-datasets', 'version': '0.9.0'}
    (tmp_path / 'package.json').write_text(json.dumps(package))
    (tmp_path / 'package-lock.json').write_text(json.dumps({**package, 'packages': {'': package}}))
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/release-policy.mjs').write_bytes(
        (ROOT / 'api/typescript/scripts/release-policy.mjs').read_bytes(),
    )
    tarball = tmp_path / 'tested.tgz'
    tarball.write_bytes(b'exact tested package')
    integrity = 'sha512-' + base64.b64encode(hashlib.sha512(tarball.read_bytes()).digest()).decode()
    (tmp_path / 'candidate.json').write_text(json.dumps({
        **package, 'tarball': str(tarball), 'integrity': integrity,
    }))
    transport = f'#!{sys.executable}\n' + '''
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ['REGISTRY_STATE'])
state = json.loads(state_path.read_text())
if Path(sys.argv[0]).name == 'sleep':
    state['elapsed'] += int(sys.argv[1])
    state_path.write_text(json.dumps(state))
else:
    args = sys.argv[1:]
    assert args[0] == 'view'
    assert '--prefer-online' in args
    assert os.environ['GITHUB_OUTPUT'] == ''
    if args[2] == 'versions':
        state['checks'] += 1
        state_path.write_text(json.dumps(state))
    if os.environ['REGISTRY_ERROR'] == 'ECONNRESET':
        print(os.environ['REGISTRY_ERROR'], file=sys.stderr)
        sys.exit(1)
    if args[2] == 'versions':
        visible_after = json.loads(os.environ['VISIBLE_AFTER'])
        pending = visible_after is None or state['elapsed'] < visible_after
        print(json.dumps(['0.8.0'] if pending else ['0.8.0', '0.9.0']))
    else:
        assert args[1:3] == ['@skytruth/shared-datasets@0.9.0', 'dist.integrity']
        print(json.dumps('wrong integrity' if os.environ['REGISTRY_ERROR'] else os.environ['INTEGRITY']))
'''
    for name in ('npm', 'sleep'):
        executable = tmp_path / name
        executable.write_text(transport)
        executable.chmod(0o755)
    result = subprocess.run(
        ['bash', '-e', '-c', step['run']], cwd=tmp_path,
        capture_output=True, text=True, timeout=30,
        env={**os.environ, 'PATH': f'{tmp_path}{os.pathsep}{os.environ["PATH"]}',
             'CANDIDATE': str(tmp_path / 'candidate.json'), 'REGISTRY_STATE': str(state_path),
             'VISIBLE_AFTER': json.dumps(visible_after), 'REGISTRY_ERROR': error,
             'INTEGRITY': integrity},
    )
    assert (result.returncode == 0) == succeeds, result.stdout + result.stderr
    state = json.loads(state_path.read_text())
    assert state['elapsed'] == expected_elapsed
    if error:
        assert state['checks'] == 1
        assert error in result.stderr
    if visible_after is None:
        assert 'Registry has not confirmed the exact tested package integrity' in result.stderr
