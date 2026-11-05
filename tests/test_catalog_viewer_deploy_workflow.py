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
    prepare, deploy = (workflow['jobs'][name] for name in ('prepare', 'deploy'))
    assert 'concurrency' not in prepare
    assert deploy['environment'] == prepare['environment'] == 'shared-datasets-production'
    assert deploy['concurrency'] == {'group': 'prod-terraform-state', 'queue': 'max', 'cancel-in-progress': False}
    assert workflow['concurrency'] == {'group': 'deployment-catalog-viewer', 'queue': 'max', 'cancel-in-progress': False}
    assert deploy['needs'] == 'prepare' and deploy['if'] == "needs.prepare.outputs.proceed == 'true'"
    assert deploy['env'] == {'CATALOG_VIEWER_IMAGE': '${{ needs.prepare.outputs.image }}',
                             'CATALOG_VIEWER_LOCAL_DIGEST': '${{ needs.prepare.outputs.config_digest }}'}
    assert not any('docker ' in s.get('run', '') or 'sleep 30' in s.get('run', '') for s in deploy['steps'])
    steps = workflow_steps_by_name(workflow, 'deploy')
    assert terraform_targets(steps['Terraform plan Secret Manager IAM bootstrap']['run']) == BOOTSTRAP
    assert terraform_targets(steps['Terraform plan']['run']) == RESOURCES
    assert 'catalog_viewer_image=${CATALOG_VIEWER_IMAGE}' in steps['Terraform plan']['run']
    assert all('-refresh=false' in steps[name]['run'] for name in ('Terraform plan', 'Terraform plan Secret Manager IAM bootstrap'))
    names = list(steps)
    ordered = ['Attest serialized deployment claim before mutation', 'Terraform apply Secret Manager IAM bootstrap',
               'Verify catalog-viewer live dependencies', 'Terraform plan', 'Enforce catalog-viewer resource-change allowlist',
               'Verify permissions required by the saved viewer plan', 'Terraform apply', 'Verify ready revision uses tested image']
    assert [names.index(name) for name in ordered] == sorted(names.index(name) for name in ordered)


@pytest.mark.parametrize('bootstrap', [True, False])
@pytest.mark.parametrize('foreign', [True, False])
@pytest.mark.parametrize('actions', [['create'], ['update'], ['delete'], ['no-op']])
def test_actual_viewer_allowlists_preserve_exact_scope(tmp_path, bootstrap, foreign, actions):
    steps = workflow_steps_by_name(load_workflow(WORKFLOW), 'deploy')
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
