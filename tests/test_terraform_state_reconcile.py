"""Exercise the full-plan producer and empty allowlist through their real shell."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.deployment_revision import TERRAFORM_SYNCS
from scripts.terraform_plan_permissions import plan_checks
from workflow_helpers import load_workflow

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/terraform_targets/state-only-outputs.json'


def caller():
    return load_workflow(ROOT / '.github/workflows/prod-terraform-state-reconcile.yml')['jobs']['reconcile']['with']


def step(name):
    steps = load_workflow(ROOT / '.github/workflows/prod-terraform-target-apply.yml')['jobs']['sync']['steps']
    return next(value['run'] for value in steps if value.get('name') == name)


def guard(directory, plan):
    inputs = caller()
    (directory / 'target-apply.tfplan.json').write_text(json.dumps(plan))
    shell = step('Enforce resource-change allowlist').replace('${{ inputs.delete_only }}', 'false')
    environment = {**os.environ, 'RUNNER_TEMP': str(directory), 'ALLOWED_EXACT': inputs['allowed_exact'],
                   'ALLOWED_PATTERNS': '', 'BLOCK_DELETES': 'false', 'REFUSAL_PREFIX': inputs['refusal_prefix'],
                   'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']}
    return subprocess.run(['bash', '-c', shell], cwd=ROOT, env=environment, text=True, capture_output=True)


def test_registered_state_only_caller_has_no_resource_scope_or_side_effect_preparation():
    inputs = caller()
    assert inputs['targets'] == inputs['allowed_exact'] == ''
    assert not inputs.get('allowed_patterns') and not inputs.get('prepare_monitoring_slack')
    assert inputs['tf_vars'] == '${{ inputs.tf_vars }}'
    assert TERRAFORM_SYNCS[inputs['sync_name']] == inputs['caller_workflow']


def test_actual_plan_shell_has_no_targets_and_keeps_deployment_variables_as_arguments(tmp_path):
    fake = tmp_path / 'terraform'
    recorded = tmp_path / 'arguments.json'
    fake.write_text(f'#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n'
                    f'Path({str(recorded)!r}).write_text(json.dumps(sys.argv[1:]))\n')
    fake.chmod(0o755)
    variables = 'wdpa_monthly_image=example.invalid/image@sha256:' + 'a' * 64 + '\nmonitoring_slack_channel_id=C12345678'
    environment = {**os.environ, 'TERRAFORM_BIN': str(fake), 'GITHUB_WORKSPACE': str(ROOT),
                   'TERRAFORM_DIR': 'terraform/envs/prod', 'RUNNER_TEMP': str(tmp_path),
                   'TARGETS': caller()['targets'], 'TF_VARS': variables}
    result = subprocess.run(['bash', '-c', step('Terraform plan')], cwd=ROOT, env=environment,
                            text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = json.loads(recorded.read_text())
    assert arguments[:2] == ['-chdir=terraform/envs/prod', 'plan']
    assert '-refresh=true' in arguments and not any(value.startswith('-target=') for value in arguments)
    assert [value for value in arguments if value.startswith('-var=')] == ['-var=' + value for value in variables.splitlines()]
    assert '-out=' + str(tmp_path / 'target-apply.tfplan') in arguments


@pytest.mark.parametrize('count', [0, 1, 5])
def test_actual_guard_accepts_noop_and_serialized_output_only_plan(tmp_path, count):
    plan = json.loads(FIXTURE.read_text())
    assert len(plan['output_changes']) == 5
    plan['output_changes'] = dict(list(plan['output_changes'].items())[:count])
    result = guard(tmp_path, plan)
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan_checks(plan, project_number='12695949518') == []


@pytest.mark.parametrize('actions', [['create'], ['update'], ['delete'], ['delete', 'create'], ['create', 'delete']])
@pytest.mark.parametrize('address', ['google_storage_bucket.shared_bucket',
                                     'module.wdpa_monthly_job.google_cloud_run_v2_job.this',
                                     'google_project_iam_member.github_actions_operational_retirement[0]'])
def test_actual_guard_refuses_every_resource_mutation(tmp_path, actions, address):
    plan = copy.deepcopy(json.loads(FIXTURE.read_text()))
    plan['resource_changes'].append({'address': address, 'change': {'actions': actions}})
    result = guard(tmp_path, plan)
    assert result.returncode == 1 and address in result.stdout
