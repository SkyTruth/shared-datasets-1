"""Exercise trusted bootstrap and target mutation ordering contracts."""
import os
from pathlib import Path
import subprocess

import pytest

from scripts.deployment_artifact import fingerprint
from workflow_helpers import load_workflow, workflow_steps_by_name

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [('publish-typescript-sdk.yml', 'publish', 'Check out exact tested revision'), ('pmtiles-cdn-sync.yml', 'sync', 'Check out repository'), ('catalog-viewer-deploy.yml', 'deploy', 'Check out repository'), ('catalog-web-deploy.yml', 'deploy', 'Check out repository')]


@pytest.mark.parametrize('filename,job,checkout', TARGETS)
def test_authority_runs_from_trusted_main_before_candidate_checkout(filename, job, checkout):
    workflow = load_workflow(ROOT / '.github/workflows' / filename)
    steps = workflow_steps_by_name(workflow, job)
    names = list(steps)
    assert names.index('Require trusted main workflow') < names.index('Check out trusted deployment verifier') < names.index(checkout)
    trusted = steps['Check out trusted deployment verifier']['with']
    assert trusted == {'ref': '${{ github.workflow_sha }}', 'fetch-depth': 0, 'persist-credentials': False}
    proof = 'Verify trusted catalog source authority' if filename == 'catalog-web-deploy.yml' else 'Verify trusted tested source authority'
    assert '--bootstrap' in steps[proof]['run']
    assert names.index(proof) < names.index(checkout)
    assert workflow['env']['BOOTSTRAP_WORKFLOW_SHA'] == '${{ github.workflow_sha }}'
    guard = steps['Require trusted main workflow']['run']
    for ref, caller, valid in [('refs/heads/main', f'SkyTruth/shared-datasets-1/.github/workflows/{filename}@refs/heads/main', True), ('refs/pull/10/merge', f'SkyTruth/shared-datasets-1/.github/workflows/{filename}@refs/heads/main', False), ('refs/heads/main', 'evil/fork/.github/workflows/ci.yml@refs/heads/main', False), ('refs/heads/main', 'SkyTruth/shared-datasets-1/.github/workflows/ci.yml@refs/heads/feature', False)]:
        result = subprocess.run(['bash', '-c', guard], env={**os.environ, 'GITHUB_REPOSITORY': 'SkyTruth/shared-datasets-1', 'GITHUB_REF': ref, 'GITHUB_WORKFLOW_REF': caller}, capture_output=True)
        assert (result.returncode == 0) is valid


def test_bundle_fingerprint_changes_with_bytes_names_and_refuses_symlinks(tmp_path):
    folder = tmp_path / 'bundle'
    folder.mkdir()
    file = folder / 'catalog.json'
    file.write_text('first')
    first = fingerprint([folder])
    file.write_text('second')
    assert fingerprint([folder]) != first
    file.write_text('first')
    file.rename(folder / 'index.html')
    assert fingerprint([folder]) != first
    (folder / 'linked').symlink_to(folder / 'index.html')
    with pytest.raises(ValueError, match='symlinks'):
        fingerprint([folder])


@pytest.mark.parametrize('filename,job,mutation,record', [('pmtiles-cdn-sync.yml', 'sync', 'Terraform apply PMTiles managed-folder IAM bootstrap', 'Record serialized CDN deployment attempt'), ('catalog-viewer-deploy.yml', 'deploy', 'Push tested catalog-viewer image', 'Record serialized viewer deployment attempt'), ('catalog-web-deploy.yml', 'deploy', 'Publish catalog web bundle', 'Record serialized catalog deployment attempt')])
def test_first_mutation_requires_record_under_noncancelling_serialization(filename, job, mutation, record):
    workflow = load_workflow(ROOT / '.github/workflows' / filename)
    steps = workflow_steps_by_name(workflow, job)
    assert workflow['jobs'][job]['concurrency']['queue'] == 'max'
    assert not workflow['jobs'][job]['concurrency']['cancel-in-progress']
    names = list(steps)
    assert names.index(record) < names.index(mutation)
    assert steps[mutation]['if'] == "steps.deployment.outputs.proceed == 'true'"


def test_missing_required_evidence_cannot_be_omitted_from_fingerprint(tmp_path):
    present = tmp_path / 'plan.tfplan'
    present.write_bytes(b'saved plan')
    with pytest.raises(ValueError, match='missing'):
        fingerprint([present, tmp_path / 'missing.lock'])


@pytest.mark.parametrize('filename,job,allowlist,check,mutation,plan,target,guard', [
    ('pmtiles-cdn-sync.yml', 'sync', 'Enforce PMTiles managed-folder IAM bootstrap allowlist', 'Verify permissions required by the saved CDN bootstrap plan', 'Record serialized CDN deployment attempt', 'pmtiles-managed-folder-bootstrap.tfplan.json', 'pmtiles-cdn-bootstrap', 'replay'),
    ('pmtiles-cdn-sync.yml', 'sync', 'Enforce PMTiles resource-change allowlist', 'Verify permissions required by the saved CDN plan', 'Terraform apply', 'pmtiles-cdn-sync.tfplan.json', 'pmtiles-cdn', 'deployment'),
    ('catalog-viewer-deploy.yml', 'deploy', 'Enforce Secret Manager IAM bootstrap allowlist', 'Verify permissions required by the saved viewer bootstrap plan', 'Terraform apply Secret Manager IAM bootstrap', 'catalog-viewer-secret-manager-iam-bootstrap.tfplan.json', 'iam-bootstrap', 'deployment'),
    ('catalog-viewer-deploy.yml', 'deploy', 'Enforce catalog-viewer resource-change allowlist', 'Verify permissions required by the saved viewer plan', 'Terraform apply', 'catalog-viewer.tfplan.json', 'catalog-viewer', 'deployment'),
])
def test_actual_saved_plan_permissions_are_verified_before_mutation(filename, job, allowlist, check, mutation, plan, target, guard):
    steps = workflow_steps_by_name(load_workflow(ROOT / '.github/workflows' / filename), job)
    names = list(steps)
    assert names.index(allowlist) < names.index(check) < names.index(mutation)
    assert steps[check]['if'] == f"steps.{guard}.outputs.proceed == 'true'"
    assert f'scripts/deployment_permissions.py --target {target} --plan-json "${{RUNNER_TEMP}}/{plan}"' in steps[check]['run']
