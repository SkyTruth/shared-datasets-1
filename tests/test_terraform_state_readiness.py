"""Reproduce the full-plan read boundary missed by the IAM bootstrap hint."""
from __future__ import annotations

import io
import json
from pathlib import Path
import re
from unittest.mock import patch

import pytest

from scripts import deployment_permissions as live
from scripts.ci_contract import select_deployments
from scripts.deployment_revision import TERRAFORM_SYNCS
from scripts.terraform_target_contracts import callers, declared
from workflow_helpers import load_workflow

ROOT = Path(__file__).resolve().parents[1]
FAILURE = json.loads((ROOT / 'tests/fixtures/ci_failures/terraform_state_read_permissions.json').read_text())
PROJECT_URL = f'https://cloudresourcemanager.googleapis.com/v1/projects/{live.PROJECT}:testIamPermissions'
MISSING = {(PROJECT_URL, permission) for permission in FAILURE['project_permissions']}


def response(missing):
    def open_url(request, **kwargs):
        assert request.get_method() == 'POST'
        payload = json.loads(request.data)
        assert set(payload) == {'permissions'}
        assert kwargs == {'timeout': 30}
        granted = [permission for permission in payload['permissions']
                   if (request.full_url, permission) not in missing]
        return io.BytesIO(json.dumps({'permissions': granted}).encode())
    return open_url


def readiness_cli(missing):
    inputs = load_workflow(ROOT / '.github/workflows/prod-terraform-state-reconcile.yml')['jobs']['reconcile']['with']
    verify = live.verify_checks
    # Keep the real CLI, HTTP serialization, response parsing and permission
    # comparison; only external transport and propagation waiting are fixtures.
    with (patch('sys.argv', ['deployment_permissions.py', '--target', inputs['readiness_target']]),
          patch.object(live.subprocess, 'check_output', return_value='fixture-token'),
          patch.object(live.urllib.request, 'urlopen', side_effect=response(missing)),
          patch.object(live, 'verify_checks', side_effect=lambda required, probe: verify(required, probe, attempts=1))):
        live.main()


@pytest.mark.parametrize('missing', [MISSING, *({item} for item in sorted(MISSING))])
def test_previous_bootstrap_hint_passed_but_real_state_read_cli_refuses_missing_reads(missing, capsys):
    with patch.object(live.urllib.request, 'urlopen', side_effect=response(missing)):
        live.verify('iam-bootstrap', lambda url, required: live.request(url, required, 'fixture-token'), attempts=1)
    with pytest.raises(RuntimeError, match='deployment identity is not ready') as error:
        readiness_cli(missing)
    for _, permission in missing:
        assert permission in str(error.value)
    assert capsys.readouterr().out == ''


def test_shared_project_read_prerequisites_pass_cli_without_foreign_access_or_mutation(capsys):
    assert dict(live.checks('terraform-state-read')) == {
        PROJECT_URL: tuple(FAILURE['project_permissions']),
    }
    readiness_cli(set())
    assert capsys.readouterr().out == 'terraform-state-read: deployment permission prerequisites verified.\n'


def test_new_role_grants_only_five_shared_project_metadata_reads():
    source = (ROOT / 'terraform/envs/prod/terraform_state_read_iam.tf').read_text()
    permissions = set(re.findall(r'"([a-zA-Z0-9.]+)"', re.search(r'permissions\s*=\s*\[(.*?)\]', source, re.S)[1]))
    assert permissions == set(FAILURE['project_permissions'])
    assert all(permission.endswith('.get') for permission in permissions)
    assert set(re.findall(r'resource "([^"]+)" "([^"]+)"', source)) == {
        ('google_project_iam_custom_role', 'terraform_state_reader'),
        ('google_project_iam_member', 'github_actions_terraform_state_reader'),
    }
    assert len(re.findall(r'project\s*=\s*var.project_id', source)) == 2
    assert 'member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"' in source
    assert 'role    = google_project_iam_custom_role.terraform_state_reader.name' in source
    assert 'x30' not in source and 'condition' not in source


def test_manual_bootstrap_and_state_only_scopes_remain_separate():
    bootstrap = callers(ROOT)['terraform-state-read-iam-sync.yml/bootstrap']
    inputs = bootstrap['with']
    scope = {'google_project_iam_custom_role.terraform_state_reader',
             'google_project_iam_member.github_actions_terraform_state_reader'}
    assert declared(inputs['targets']) == declared(inputs['allowed_exact']) == scope
    assert inputs['block_deletes'] is True and inputs['readiness_target'] == 'iam-bootstrap'
    assert TERRAFORM_SYNCS[inputs['sync_name']] == inputs['caller_workflow']
    assert not inputs.get('allowed_patterns') and not inputs.get('prepare_monitoring_slack')
    state = callers(ROOT)['prod-terraform-state-reconcile.yml/reconcile']
    assert not state.get('needs')
    assert state['with']['targets'] == state['with']['allowed_exact'] == ''
    assert state['with']['readiness_target'] == 'terraform-state-read'
    assert select_deployments(['terraform/envs/prod/terraform_state_read_iam.tf',
                               '.github/workflows/terraform-state-read-iam-sync.yml',
                               'scripts/deployment_permissions.py']) == []
