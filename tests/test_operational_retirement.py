"""Exercise the protected retirement's actual shell guard and saved-plan bytes."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.terraform_plan_permissions import plan_checks
from workflow_helpers import load_workflow

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/terraform_targets/operational-retirement.json'
NUMBER = '12695949518'


def retirement_jobs():
    return load_workflow(ROOT / '.github/workflows/scratch-cleanup-iam-sync.yml')['jobs']


def test_workflow_scope_matches_the_independent_audited_resource_inventory():
    inputs = retirement_jobs()['retire-operational-infrastructure']['with']
    inventory = {row['address'] for row in json.loads(FIXTURE.read_text())['resource_changes']}
    targets = inputs['targets'].splitlines()
    allowed = inputs['allowed_exact'].splitlines()
    assert len(targets) == len(allowed) == len(inventory) == 20
    assert set(targets) == set(allowed) == inventory
    assert not inputs.get('allowed_patterns')


def guard(tmp_path, plan):
    inputs = retirement_jobs()['retire-operational-infrastructure']['with']
    steps = load_workflow(ROOT / '.github/workflows/prod-terraform-target-apply.yml')['jobs']['sync']['steps']
    shell = next(step['run'] for step in steps if step.get('name') == 'Enforce resource-change allowlist')
    shell = shell.replace('${{ inputs.delete_only }}', str(inputs['delete_only']).lower())
    (tmp_path / 'target-apply.tfplan.json').write_text(json.dumps(plan))
    environment = {**os.environ, 'RUNNER_TEMP': str(tmp_path), 'ALLOWED_EXACT': inputs['allowed_exact'],
                   'ALLOWED_PATTERNS': inputs.get('allowed_patterns', ''), 'BLOCK_DELETES': 'false',
                   'REFUSAL_PREFIX': inputs['refusal_prefix'],
                   'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']}
    return subprocess.run(['bash', '-c', shell], cwd=ROOT, env=environment, capture_output=True, text=True)


@pytest.mark.parametrize('count', [0, 1, 20])
def test_actual_workflow_guard_and_permission_contract_accept_complete_partial_and_noop_retirement(tmp_path, count):
    plan = json.loads(FIXTURE.read_text())
    plan['resource_changes'] = plan['resource_changes'][:count]
    accepted = guard(tmp_path, plan)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    required = plan_checks(json.loads((tmp_path / 'target-apply.tfplan.json').read_text()), project_number=NUMBER)
    if count == 0:
        assert required == []
    if count == 20:
        permissions = {permission for _, values in required for permission in values}
        assert permissions == {
            'iam.roles.get', 'iam.roles.delete', 'iam.serviceAccounts.get', 'iam.serviceAccounts.delete',
            'iam.workloadIdentityPoolProviders.get', 'iam.workloadIdentityPoolProviders.delete',
            'logging.views.get', 'logging.views.delete', 'monitoring.metricDescriptors.get',
            'monitoring.metricDescriptors.delete', 'monitoring.alertPolicies.get',
            'monitoring.alertPolicies.delete', 'logging.notificationRules.delete',
        }


@pytest.mark.parametrize('actions', [['create'], ['update'], ['delete', 'create'], ['create', 'delete']])
def test_actual_workflow_refuses_resurrection_update_and_replacement_of_retired_resources(tmp_path, actions):
    plan = json.loads(FIXTURE.read_text())
    plan['resource_changes'][0]['change']['actions'] = actions
    rejected = guard(tmp_path, plan)
    assert rejected.returncode == 1
    assert plan['resource_changes'][0]['address'] in rejected.stdout


@pytest.mark.parametrize('address', ['google_storage_bucket.shared_bucket',
                                     'module.wdpa_monthly_job.google_cloud_run_v2_job.this',
                                     'google_cloud_run_v2_service.monitoring_slack',
                                     'google_iam_workload_identity_pool_provider.github'])
def test_actual_workflow_refuses_deletion_of_active_resources(tmp_path, address):
    plan = json.loads(FIXTURE.read_text())
    plan['resource_changes'][0]['address'] = address
    rejected = guard(tmp_path, plan)
    assert rejected.returncode == 1 and address in rejected.stdout


@pytest.mark.parametrize('kind', ['google_logging_log_view', 'google_monitoring_metric_descriptor',
                                  'google_iam_workload_identity_pool_provider'])
def test_retirement_permission_reads_reject_foreign_identity_and_non_deletion(kind):
    row = copy.deepcopy(next(row for row in json.loads(FIXTURE.read_text())['resource_changes'] if row['type'] == kind))
    before = row['change']['before']
    if kind == 'google_logging_log_view':
        before['id'] = before['id'].replace('projects/shared-datasets-1/', 'projects/other-project/')
    else:
        before['project'] = 'other-project'
    with pytest.raises(ValueError, match='foreign|outside production'):
        plan_checks({'format_version': '1.2', 'resource_changes': [row]}, project_number=NUMBER)
    row = copy.deepcopy(next(row for row in json.loads(FIXTURE.read_text())['resource_changes'] if row['type'] == kind))
    row['change'].update(actions=['update'], after=row['change']['before'])
    with pytest.raises(ValueError, match='only retired'):
        plan_checks({'format_version': '1.2', 'resource_changes': [row]}, project_number=NUMBER)


@pytest.mark.parametrize('grant', ['success', 'failure', 'cancelled', 'skipped'])
@pytest.mark.parametrize('retirement', ['success', 'failure', 'cancelled', 'skipped'])
def test_revocation_runs_after_any_grant_attempt_including_partial_failure(grant, retirement):
    job = retirement_jobs()['revoke-operational-retirement']
    assert set(job['needs']) == {'operational-retirement-grant', 'retire-operational-infrastructure'}
    expression = job['if'].removeprefix('${{').removesuffix('}}').strip()
    for original, value in {
        'always()': 'True', 'github.ref': repr('refs/heads/main'), 'github.event_name': repr('push'),
        'needs.operational-retirement-grant.result': repr(grant),
        'needs.retire-operational-infrastructure.result': repr(retirement),
    }.items():
        expression = expression.replace(original, value)
    assert eval(expression.replace('&&', ' and ').replace('||', ' or '), {'__builtins__': {}}) is (grant != 'skipped')
    assert job['with']['delete_only'] is True
    assert 'operational_retirement_authority_enabled=false' in job['with']['tf_vars']
