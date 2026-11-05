"""PR transport, live IAM and leakage boundaries; external services only are faked."""
from __future__ import annotations

import ast
import io
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
import yaml

from scripts import pr_terraform_plan as pr
from scripts.terraform_plan_permissions import PROJECT, PROJECT_URL

ROOT = Path(__file__).resolve().parents[1]
DEPLOYER = f"deployer@{PROJECT}.iam.gserviceaccount.com"
SECRET = "state-value-that-must-not-appear"
BUCKET = 'https://storage.googleapis.com/storage/v1/b/fixture-bucket/iam/testPermissions?permissions=storage.buckets.update'
SA = f'https://iam.googleapis.com/v1/projects/{PROJECT}/serviceAccounts/{DEPLOYER}:testIamPermissions'


def event(fork=False):
    return {'pull_request': {'head': {'repo': {'full_name': 'other/fork' if fork else pr.REPOSITORY}},
                             'base': {'repo': {'full_name': pr.REPOSITORY}}}}


def test_same_repo_only_and_required_ci_gate():
    assert pr.same_repo(event()) and not pr.same_repo(event(True)) and not pr.same_repo({})
    ci = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    job = ci['pr-terraform-plan']
    assert "github.event_name == 'pull_request'" in job['if']
    assert 'github.event.pull_request.head.repo.full_name == github.repository' in job['if']
    assert "outputs.pr_terraform == 'true'" in job['if']
    assert job['permissions'] == {'contents': 'read', 'id-token': 'write'}
    assert 'environment' not in job and 'secrets' not in job
    job = yaml.safe_load((ROOT / '.github/workflows/pr-terraform-plan.yml').read_text())['jobs']['plan']
    steps = job['steps']
    assert all(s.get('with', {}).get('persist-credentials') is False for s in steps if s.get('uses', '').startswith('actions/checkout'))
    assert not any(s.get('uses', '').startswith('actions/upload-artifact') for s in steps)
    auth = next(s for s in steps if s.get('uses', '').startswith('google-github-actions/auth'))
    assert auth['with']['service_account'] == '${{ vars.GCP_PR_PLAN_SERVICE_ACCOUNT }}'
    assert 'pr-terraform-plan' in ci['ci-ready']['needs']
    gate = next(s for s in ci['ci-ready']['steps'] if s.get('name') == 'Require selected same-repo Terraform plan')
    for required, result, code in [('true', 'success', 0), ('true', 'failure', 1), ('true', 'skipped', 1), ('false', 'skipped', 0)]:
        done = subprocess.run(['bash', '-c', gate['run']], env={'REQUIRED': required, 'RESULT': result})
        assert done.returncode == code


@pytest.mark.parametrize('command', ['apply', 'import', 'state', 'destroy', 'taint', 'force-unlock'])
def test_mutation_commands_cannot_reach_subprocess(command, tmp_path):
    with patch.object(pr.subprocess, 'run') as run, pytest.raises(ValueError, match='not read-only'):
        pr.terraform(command, directory=tmp_path)
    run.assert_not_called()


def test_read_only_command_inventory_and_workflow_reachable_steps():
    source = (ROOT / 'scripts/pr_terraform_plan.py').read_text()
    tree = ast.parse(source)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == 'terraform']
    assert {n.args[0].value for n in calls} == {'version', 'init', 'show', 'plan'} == pr.TF_COMMANDS
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {'run', 'check_output'}]
    assert len(calls) == 3  # Read-only git show, Terraform allowlist, gcloud auth.
    workflow = yaml.safe_load((ROOT / '.github/workflows/pr-terraform-plan.yml').read_text())['jobs']['plan']
    actions = [s['uses'] for s in workflow['steps'] if 'uses' in s]
    assert all(a.startswith(('actions/checkout@', 'actions/download-artifact@', 'google-github-actions/auth@',
                              'google-github-actions/setup-gcloud@', './.github/actions/ci-tools')) for a in actions)
    assert 'terraform apply' not in source and 'testIamPermissions",' not in source


@pytest.mark.parametrize('url,expected', [(PROJECT_URL, '//cloudresourcemanager.googleapis.com/projects/123'),
    (BUCKET, '//storage.googleapis.com/projects/_/buckets/fixture-bucket'),
    (SA, f'//iam.googleapis.com/projects/{PROJECT}/serviceAccounts/{DEPLOYER}')])
def test_permission_probe_to_troubleshooter_resource(url, expected):
    assert pr.full_resource(url, '123') == expected


def fixture_transport(request, **kwargs):
    assert kwargs == {'timeout': 60}
    assert 'setIamPolicy' not in request.full_url and 'testIamPermissions' not in request.full_url
    if 'iam:troubleshoot' in request.full_url:
        body = json.loads(request.data)
        assert body['accessTuple']['principal'] == DEPLOYER
        assert request.get_method() == 'POST'
        return io.BytesIO(json.dumps({'overallAccessState': 'CAN_ACCESS',
                    'allowPolicyExplanation': {'allowAccessState': 'ALLOW_ACCESS_STATE_GRANTED'}}).encode())
    if '/roles/' in request.full_url:
        payload = {'includedPermissions': ['storage.buckets.update', 'iam.serviceAccounts.actAs']}
    else:
        payload = {'bindings': [{'role': 'roles/fixture', 'members': ['serviceAccount:' + DEPLOYER]}]}
    return io.BytesIO(json.dumps(payload).encode())


def test_real_iam_http_serialization_and_production_principal(capsys):
    with patch.object(pr.urllib.request, 'urlopen', side_effect=fixture_transport):
        pr.PolicyReader('fixture-token').verify([(BUCKET, ['storage.buckets.update']), (SA, ['iam.serviceAccounts.actAs'])], DEPLOYER, '123')
    assert capsys.readouterr().out.splitlines() == ['storage.buckets.update granted', 'iam.serviceAccounts.actAs granted']


@pytest.mark.parametrize('verdict', [{'overallAccessState': 'CANNOT_ACCESS'}, {'overallAccessState': 'UNKNOWN_INFO'}, {},
    {'overallAccessState': 'UNKNOWN_CONDITIONAL'}, {'overallAccessState': 'CAN_ACCESS'},
    {'overallAccessState': 'CAN_ACCESS', 'allowPolicyExplanation': {'allowAccessState': 'ALLOW_ACCESS_STATE_UNKNOWN_CONDITIONAL'}}])
def test_allow_grant_cannot_override_deny_missing_or_unknown_permission(verdict):
    def transport(request, **kwargs):
        if 'iam:troubleshoot' in request.full_url:
            return io.BytesIO(json.dumps(verdict).encode())
        return fixture_transport(request, **kwargs)
    with patch.object(pr.urllib.request, 'urlopen', side_effect=transport), pytest.raises(ValueError, match='permission storage.buckets.update'):
        pr.PolicyReader('fixture-token').verify([(BUCKET, ['storage.buckets.update'])], DEPLOYER, '123')


def state():
    image = 'us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:' + 'a' * 64
    return {'values': {'root_module': {'child_modules': [{'resources': [
        {'address': 'module.wdpa_monthly_job.google_cloud_run_v2_job.this', 'type': 'google_cloud_run_v2_job',
         'values': {'template': [{'template': [{'containers': [{'image': image, 'env': [{'name': 'WDPA_BUILD_IMAGE_CONFIG_DIGEST', 'value': 'sha256:' + 'b' * 64}]}]}]}]}}]}]}}}


def test_state_images_use_stored_immutable_digests_and_reject_mutable_tags():
    value = state()
    assert pr.deployed_variables(value)['wdpa_monthly_image'].endswith('a' * 64)
    assert pr.deployed_variables(value)['wdpa_monthly_image_config_digest'] == 'sha256:' + 'b' * 64
    value['values']['root_module']['child_modules'][0]['resources'][0]['values']['template'][0]['template'][0]['containers'][0]['image'] = 'mutable:latest'
    with pytest.raises(ValueError, match='immutable'):
        pr.deployed_variables(value)


def test_manual_terraform_owners_are_selected_even_without_automatic_deployments():
    scopes = pr.scopes({'changed_paths': ['terraform/envs/prod/pr_plan_iam.tf'], 'deployments': []})
    assert {s['name'] for s in scopes} == {'PR plan identity IAM bootstrap', 'PR plan identity provisioning'}
    assert all(not any('terraform_state_reader' in r for r in s['resources']) for s in scopes)


@pytest.mark.parametrize('stage', ['positive', 'provider', 'allowlist', 'permission', 'fork'])
def test_complete_runner_through_saved_plan_and_http_boundaries(tmp_path, capsys, stage):
    event_path = tmp_path / 'event.json'
    event_path.write_text(json.dumps(event(stage == 'fork')))
    plan = {'changed_paths': ['terraform/envs/prod/terraform_state_read_iam.tf'], 'deployments': ['eamlis']}
    saved = {'format_version': '1.2', 'resource_changes': [{'address': 'google_project_iam_custom_role.' + ('foreign' if stage == 'allowlist' else 'terraform_state_reader'),
        'type': 'google_project_iam_custom_role', 'change': {'actions': ['update'], 'before': {},
            'after': {'project': PROJECT, 'role_id': 'fixture', 'description': SECRET}}}]}
    calls = []
    def process(command, **kwargs):
        calls.append(command)
        assert kwargs['capture_output'] is True
        assert command[0] == 'terraform'
        cmd = command[2]
        if cmd == 'version':
            stdout = json.dumps({'terraform_version': '1.8.5'})
        elif cmd == 'show':
            stdout = json.dumps(saved if command[-1].endswith('.tfplan') else state())
        else:
            stdout = SECRET
        if cmd == 'plan':
            assert '-lock=false' in command and '-input=false' in command
            assert all(not k.startswith(('TF_LOG', 'TF_CLI_ARGS', 'TF_VAR_')) for k in kwargs['env'])
        return subprocess.CompletedProcess(command, 1 if cmd == 'plan' and stage == 'provider' else 0, stdout, SECRET)
    def transport(request, **kwargs):
        if request.full_url.endswith('/projects/' + PROJECT):
            return io.BytesIO(b'{"projectNumber":"123"}')
        if stage == 'permission' and 'iam:troubleshoot' in request.full_url:
            return io.BytesIO(b'{"overallAccessState":"CANNOT_ACCESS"}')
        return fixture_transport(request, **kwargs)
    with (patch.dict(os.environ, {'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_EVENT_PATH': str(event_path),
             'RUNNER_TEMP': str(tmp_path), 'PRODUCTION_TERRAFORM_SERVICE_ACCOUNT': DEPLOYER,
             'TF_LOG': 'TRACE', 'TF_CLI_ARGS_plan': '-refresh=false', 'TF_VAR_secret': SECRET}),
          patch.object(pr.subprocess, 'run', side_effect=process),
          patch.object(pr.subprocess, 'check_output', return_value='fixture-token'),
          patch.object(pr.urllib.request, 'urlopen', side_effect=transport)):
        if stage == 'positive':
            pr.run(plan)
        else:
            with pytest.raises(ValueError):
                pr.run(plan)
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert not list((tmp_path / 'pr-terraform').rglob('*.tfplan'))
    if stage == 'fork':
        assert calls == []
    else:
        assert {c[2] for c in calls} <= pr.TF_COMMANDS
        assert sum(c[2] == 'plan' for c in calls) == 1
        assert '::notice::Live PR plan excluded: eamlis-monthly:' in captured.out
