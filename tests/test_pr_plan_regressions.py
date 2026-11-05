"""Regression checks runnable unchanged on the pre-feature base revision."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts.terraform_plan_permissions import PROJECT, plan_checks
from scripts.ci_contract import CONTRACT_FILES
from scripts.terraform_target_contracts import registry, callers
from terraform_helpers import load_terraform, resources

ROOT = Path(__file__).resolve().parents[1]


def test_new_planner_and_reusable_workflow_invalidate_evidence():
    assert {'scripts/pr_terraform_plan.py', '.github/workflows/pr-terraform-plan.yml'} <= set(CONTRACT_FILES)

@pytest.mark.parametrize('paths,expected', [([], False), (['docs/ci-deployment.md'], False),
    (['ingestion/eamlis_monthly/run.py'], False), (['terraform/README.md'], False),
    (['terraform/envs/prod/main.tf'], True), (['.github/terraform-targets.json'], True), (None, True)])
def test_select_serializes_real_ci_output(tmp_path, paths, expected):
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'changed_paths': paths, 'deployments': []}))
    output = tmp_path / 'github-output'
    result = subprocess.run([sys.executable, 'scripts/pr_terraform_plan.py', 'select', '--plan', str(plan)],
                            cwd=ROOT, env={**os.environ, 'GITHUB_OUTPUT': str(output)}, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert output.read_text() == f'pr_terraform={str(expected).lower()}\n'


def test_isolated_wif_shape_and_exact_state_read_grant():
    document = load_terraform(ROOT / 'terraform/envs/prod/pr_plan_iam.tf')
    rows = {(kind, name): value for kind, name, value in resources(document)}
    provider = rows['google_iam_workload_identity_pool_provider', 'pr_plan']
    parts = ["assertion.repository == '${var.github_repository}'", "assertion.event_name == 'pull_request'",
             "assertion.ref.matches('^refs/pull/[0-9]+/merge$')",
             "assertion.workflow_ref == '${var.github_repository}/.github/workflows/ci.yml@' + assertion.ref",
             "assertion.job_workflow_ref == '${var.github_repository}/.github/workflows/pr-terraform-plan.yml@' + assertion.ref"]
    source = (ROOT / 'terraform/envs/prod/pr_plan_iam.tf').read_text()
    assert all(part in source for part in parts)
    assert 'google_iam_workload_identity_pool.pr_plan.workload_identity_pool_id' in provider['workload_identity_pool_id']
    binding = rows['google_service_account_iam_member', 'pr_plan_wif']
    assert 'google_iam_workload_identity_pool.pr_plan.name' in binding['member']
    assert 'environment:' not in binding['member'] and 'principalSet:' not in binding['member']
    permissions = rows['google_project_iam_custom_role', 'pr_plan_reader']['permissions']
    assert all(p.endswith(('.get', '.list', '.getIamPolicy', '.getAttestationRules', '.use')) for p in permissions)
    assert not any(p.startswith('secretmanager.') or 'setIamPolicy' in p for p in permissions)
    state = rows['google_project_iam_custom_role', 'pr_plan_state_reader']
    assert state['permissions'] == ['storage.objects.get', 'storage.objects.list']
    condition = rows['google_storage_bucket_iam_member', 'pr_plan_state_reader']['condition'][0]['expression']
    assert condition.endswith('/000-system/terraform/state/prod/default.tfstate\'')
    for output in document['output']:
        assert next(iter(output.values()))['sensitive'] is True


def test_pr_identity_has_new_protected_scope_without_widening_historical_scope():
    targets = registry()
    assert len(targets['Production Terraform state read IAM bootstrap']['resources']) == 2
    scope = targets['PR plan identity provisioning']['resources']
    declared = {f'{kind}.{name}' for kind, name, _ in resources(load_terraform(ROOT / 'terraform/envs/prod/pr_plan_iam.tf'))}
    assert set(scope) | set(targets['PR plan identity IAM bootstrap']['resources']) == declared
    jobs = callers(ROOT)
    assert jobs['terraform-state-read-iam-sync.yml/pr-plan-identity']['needs'] == 'pr-plan-authority'
    assert jobs['terraform-state-read-iam-sync.yml/pr-plan-authority']['with']['block_deletes'] is True


@pytest.mark.parametrize('kind,extra', [('google_iam_workload_identity_pool', {}),
    ('google_iam_workload_identity_pool_provider', {'workload_identity_pool_provider_id': 'ci-pr-plan'})])
def test_new_identity_plan_permissions_are_closed_to_its_pool(kind, extra):
    row = {'type': kind, 'address': kind + '.pr_plan', 'change': {'actions': ['create'],
           'after': {'project': PROJECT, 'workload_identity_pool_id': 'github-pr-plan', **extra}}}
    assert plan_checks({'format_version': '1.2', 'resource_changes': [row]}, project_number='123')
    row['change']['after']['workload_identity_pool_id'] = 'production'
    with pytest.raises(ValueError):
        plan_checks({'format_version': '1.2', 'resource_changes': [row]}, project_number='123')


def test_ci_requires_live_plan_before_ready():
    ci = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    assert 'pr-terraform-plan' in ci['ci-ready']['needs']
    job = ci['pr-terraform-plan']
    assert "github.event_name == 'pull_request'" in job['if']
    assert 'github.event.pull_request.head.repo.full_name == github.repository' in job['if']
    assert "outputs.pr_terraform == 'true'" in job['if']
    gate = next(s for s in ci['ci-ready']['steps'] if s.get('name') == 'Require selected same-repo Terraform plan')
    for required, result, code in [('true', 'success', 0), ('true', 'failure', 1), ('true', 'skipped', 1), ('false', 'skipped', 0)]:
        done = subprocess.run(['bash', '-c', gate['run']], env={'REQUIRED': required, 'RESULT': result})
        assert done.returncode == code
