"""Explicit composite inputs survive transport without ambient caller settings."""
import os
from pathlib import Path
import subprocess

from scripts import terraform_target_contracts as contracts

ROOT = Path(__file__).resolve().parents[1]


def test_terraform_bootstrap_declares_caller_inputs_and_ignores_conflicting_ambient_values(tmp_path):
    from scripts.deployment_receipt_contracts import expanded_steps
    value = contracts.workflow(ROOT, 'prod-terraform-target-apply.yml')
    for name in ('prepare', 'sync'):
        caller = next(s for s in value['jobs'][name]['steps'] if s.get('uses') == './.github/actions/deployment-bootstrap/terraform')
        assert caller['with']['sync-name'] == '${{ inputs.sync_name }}'
        assert caller['with']['workload-identity-provider'] == '${{ env.TERRAFORM_WORKLOAD_IDENTITY_PROVIDER }}'
        assert caller['with']['service-account'] == '${{ env.TERRAFORM_SERVICE_ACCOUNT }}'
    inputs = {'workflow': 'artifact-registry-iam-sync.yml', 'sync-name': 'Artifact Registry writer binding sync',
              'executor-sha': 'a' * 40, 'source-run-id': '13', 'source-run-attempt': '2',
              'workload-identity-provider': 'reviewed-provider', 'service-account': 'reviewed-account'}
    steps = list(expanded_steps(ROOT, {'steps': [{'uses': caller['uses'], 'with': inputs}]}))
    auth = next(s for s in steps if str(s.get('uses', '')).startswith('google-github-actions/auth@'))
    assert auth['with'] == {'workload_identity_provider': 'reviewed-provider', 'service_account': 'reviewed-account'}
    resolve = next(s for s in steps if s.get('name') == 'Resolve registered Terraform target')
    result = subprocess.run(['bash', '-eu', '-c', resolve['run']], cwd=ROOT, capture_output=True, text=True,
        env={**os.environ, 'GITHUB_ENV': str(tmp_path / 'env'), 'SYNC_NAME': 'foreign', 'CALLER_WORKFLOW': 'foreign.yml', **resolve['env']})
    assert result.returncode == 0, result.stderr
    assert 'google_artifact_registry_repository_iam_member.github_actions_artifact_registry_writer' in (tmp_path / 'env').read_text()
