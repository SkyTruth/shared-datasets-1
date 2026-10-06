"""The npm trusted publisher identity consumes immutable CI bytes."""
from pathlib import Path

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]


def test_sdk_publisher_keeps_workflow_identity_and_oidc_only_for_mutation():
    workflow = load_workflow(ROOT / '.github/workflows/publish-typescript-sdk.yml')
    assert workflow['name'] == 'Publish TypeScript SDK'
    assert workflow_triggers(workflow) == {'workflow_run': {'workflows': ['CI'], 'branches': ['main'], 'types': ['completed']}}
    assert workflow['permissions'] == {'contents': 'read', 'actions': 'read'}
    candidate, publish = workflow['jobs']['candidate'], workflow['jobs']['publish']
    assert 'id-token' not in candidate.get('permissions', {})
    assert publish['permissions']['id-token'] == 'write'
    assert publish['permissions']['deployments'] == 'write'
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
    assert 'steps.deployment.outputs.proceed' in publish['if']
    assert 'should_publish=false' in steps['Verify registry retained the tested bytes']['run']
    assert workflow['env']['NODE_VERSION'] == '24.13.1'
