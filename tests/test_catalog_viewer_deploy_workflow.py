"""Viewer scopes and image handoff through the actual workflow shell."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from workflow_helpers import load_workflow, terraform_targets, workflow_steps_by_name, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/catalog-viewer-deploy.yml'
BOOTSTRAP = {'google_project_iam_member.github_actions_pmtiles_cdn_secret_iam_policy_manager'}
RESOURCES = {
    'module.catalog_viewer_service_account.google_service_account.this',
    'google_cloud_run_v2_service.catalog_viewer',
    'google_cloud_run_v2_service_iam_member.catalog_viewer_iap_invoker',
    'google_iap_web_cloud_run_service_iam_member.catalog_viewer_accessors',
    'google_project_iam_custom_role.catalog_viewer_sign_blob',
    'google_secret_manager_secret_iam_member.pmtiles_cdn_catalog_viewer_signer',
    'google_service_account_iam_member.catalog_viewer_self_sign_blob',
    'google_storage_bucket_iam_member.catalog_viewer_object_viewer',
    'google_storage_bucket.catalog_comparisons',
    'google_storage_bucket_iam_member.catalog_comparisons_worker',
}


def test_viewer_preserves_protected_scopes_and_transports_only_prepared_image():
    workflow = load_workflow(WORKFLOW)
    triggers = workflow_triggers(workflow)
    assert set(triggers) == {'workflow_call', 'workflow_dispatch'}
    for name in ('executor_sha', 'source_run_id', 'source_run_attempt'):
        assert triggers['workflow_call']['inputs'][name]['required']
        assert triggers['workflow_call']['inputs'][name]['type'] == triggers['workflow_dispatch']['inputs'][name]['type'] == 'string'
        assert triggers['workflow_dispatch']['inputs'][name]['required']
    jobs = workflow['jobs']
    assert set(jobs) == {'prepare', 'bootstrap', 'ready', 'deploy', 'verify'}
    assert all(job['environment'] == 'shared-datasets-production' for job in jobs.values())
    assert [key for key, job in jobs.items() if 'concurrency' in job] == ['bootstrap', 'deploy']
    assert workflow['concurrency'] == {'group': 'deployment-catalog-viewer', 'queue': 'max', 'cancel-in-progress': False}
    bootstrap = workflow_steps_by_name(workflow, 'bootstrap')
    deploy = workflow_steps_by_name(workflow, 'deploy')
    assert terraform_targets(bootstrap['Terraform plan Secret Manager IAM bootstrap']['run']) == BOOTSTRAP
    assert terraform_targets(deploy['Terraform plan']['run']) == RESOURCES
    assert 'catalog_viewer_image=${CATALOG_VIEWER_IMAGE}' in deploy['Terraform plan']['run']
    assert '-refresh=false' in deploy['Terraform plan']['run']
    assert '-refresh=false' in bootstrap['Terraform plan Secret Manager IAM bootstrap']['run']
    assert 'Verify catalog-viewer live dependencies' in workflow_steps_by_name(workflow, 'ready')
    assert 'Verify ready revision uses tested image' in workflow_steps_by_name(workflow, 'verify')
    from scripts.deployment_receipt_contracts import viewer_boundary
    assert viewer_boundary(workflow) == []
    names = list(deploy)
    order = ['Require successful signed preparation', 'Terraform plan', 'Enforce catalog-viewer resource-change allowlist',
             'Verify permissions required by the saved viewer plan', 'Terraform apply']
    assert [names.index(name) for name in order] == sorted(names.index(name) for name in order)


@pytest.mark.parametrize('bootstrap', [True, False])
@pytest.mark.parametrize('foreign', [True, False])
@pytest.mark.parametrize('actions', [['create'], ['update'], ['delete'], ['no-op']])
def test_actual_viewer_allowlists_preserve_exact_scope(tmp_path, bootstrap, foreign, actions):
    steps = workflow_steps_by_name(load_workflow(WORKFLOW), 'bootstrap' if bootstrap else 'deploy')
    name = 'Enforce Secret Manager IAM bootstrap allowlist' if bootstrap else 'Enforce catalog-viewer resource-change allowlist'
    plan = 'catalog-viewer-secret-manager-iam-bootstrap' if bootstrap else 'catalog-viewer'
    for address in BOOTSTRAP if bootstrap else RESOURCES:
        if address.endswith('.catalog_viewer_accessors'):
            address += '["user:reviewer@example.org"]'
        proposed = 'google_storage_bucket.unrelated' if foreign else address
        (tmp_path / (plan + '.tfplan.json')).write_text(json.dumps({'resource_changes': [
            {'address': proposed, 'change': {'actions': actions}},
        ]}))
        result = subprocess.run(['bash', '-e', '-c', steps[name]['run']], cwd=ROOT,
                                env={**os.environ, 'RUNNER_TEMP': str(tmp_path)}, text=True, capture_output=True)
        assert (result.returncode == 0) == (not foreign or actions == ['no-op']), result.stdout + result.stderr


@pytest.mark.parametrize('sync,event,claim,expected', [
    (False, 'push', 'success', False), (True, 'push', 'success', True),
    (False, 'workflow_dispatch', 'success', True), (True, 'push', 'failure', False),
])
def test_viewer_bootstrap_enters_state_queue_only_for_signed_iam_changes(sync, event, claim, expected):
    from workflow_helpers import condition_result
    job = load_workflow(WORKFLOW)['jobs']['bootstrap']
    assert condition_result(job, {'needs.prepare.result': 'success', 'needs.prepare.outputs.proceed': 'true',
        'needs.prepare.outputs.claim_signed': claim, 'inputs.sync_iam': sync, 'github.event_name': event}) is expected


@pytest.mark.parametrize('bootstrap,prepare,claim,expected', [
    ('skipped', 'success', 'success', True), ('success', 'success', 'success', True),
    ('failure', 'success', 'success', False), ('cancelled', 'success', 'success', False),
    ('skipped', 'failure', 'success', False), ('skipped', 'success', 'failure', False),
])
def test_viewer_readiness_accepts_intentional_bootstrap_skip_and_rejects_failure(bootstrap, prepare, claim, expected):
    from workflow_helpers import condition_result
    workflow = load_workflow(WORKFLOW)
    assert condition_result(workflow['jobs']['ready'], {'needs.prepare.result': prepare,
        'needs.prepare.outputs.deployment_id': '42', 'needs.prepare.outputs.claim_signed': claim,
        'needs.bootstrap.result': bootstrap, 'github.event_name': 'push', 'inputs.sync_iam': False}) is expected
    assert workflow['jobs']['verify']['if'] == "${{ always() && needs.prepare.outputs.deployment_id != '' }}"


@pytest.mark.parametrize('sync,event', [(True, 'push'), (False, 'workflow_dispatch')])
def test_viewer_required_bootstrap_cannot_be_bypassed_by_a_skipped_result(sync, event):
    from workflow_helpers import condition_result
    job = load_workflow(WORKFLOW)['jobs']['ready']
    assert not condition_result(job, {'needs.prepare.result': 'success',
        'needs.prepare.outputs.deployment_id': '42', 'needs.prepare.outputs.claim_signed': 'success',
        'needs.bootstrap.result': 'skipped', 'github.event_name': event, 'inputs.sync_iam': sync})
