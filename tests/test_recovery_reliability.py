"""Recover the real serialized plan into signed runtime and schedule evidence."""
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import ssl
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import pytest
import yaml

from scripts import ci_contract as ci, deployment_revision as deployment
from scripts import deployment_permissions as permissions, ingestion_targets as ingestion
from scripts import operational_safety_check as safety, wdpa_schedule_readiness as readiness
from scripts.terraform_target_contracts import registry
from terraform_helpers import terraform_executable, load_terraform
from test_deployment_revision import CURRENT, IMAGE, REPO
import test_deployment_revision as fixtures

ROOT = Path(__file__).resolve().parents[1]
CONFIG = 'sha256:' + 'e' * 64


@pytest.mark.parametrize('target', ingestion.DEPLOY_TARGETS)
def test_real_plan_recovery_retains_config_and_execution_through_resume(tmp_path, monkeypatch, target):
    policy = ingestion.DEPLOY_TARGETS[target]
    variable = policy['image_variable']
    terraform = terraform_executable()
    variables = {row['image_variable']: {'default': 'unused-by-reconciliation'} for row in ingestion.DEPLOY_TARGETS.values()}
    variables['wdpa_monthly_image_config_digest'] = {'default': ''}
    output = {'value': {'image': '${var.' + variable + '}', 'config': '${var.wdpa_monthly_image_config_digest}'}}
    if target == 'wdpa-monthly':
        output['precondition'] = [{'condition': '${var.wdpa_monthly_image_config_digest == "' + CONFIG + '"}',
                                   'error_message': 'Original tested WDPA config is required'}]
    (tmp_path / 'main.tf.json').write_text(json.dumps({'variable': variables, 'output': {'worker': output}}))
    def plan(name, values):
        destination = tmp_path / name
        subprocess.run([terraform, f'-chdir={tmp_path}', 'plan', '-input=false', '-no-color', '-out=' + str(destination),
            *(f'-var={key}={value}' for key, value in values.items())], check=True, capture_output=True)
        raw = subprocess.check_output([terraform, f'-chdir={tmp_path}', 'show', '-json', str(destination)], text=True)
        destination.with_suffix('.json').write_text(raw)
        return destination.with_suffix('.json')
    monkeypatch.setenv('CHECKPOINT_DISABLE', '1')
    subprocess.run([terraform, f'-chdir={tmp_path}', 'init', '-backend=false', '-input=false'], check=True, capture_output=True)
    original = plan('original.tfplan', {variable: IMAGE, 'wdpa_monthly_image_config_digest': CONFIG})
    record = fixtures.ReplayTests.record(CURRENT, 42)
    statuses = []
    api = Mock()
    def post(path, payload):
        if path.endswith('/deployments'):
            record.update(json.loads(json.dumps(payload)))
            return record
        status = {**payload, 'id': len(statuses) + 1}
        statuses.insert(0, json.loads(json.dumps(status)))
        return status
    api.post.side_effect = post
    api.get.side_effect = lambda _: json.loads(json.dumps(record))
    api.pages.side_effect = lambda path, **_: statuses if '/statuses?' in path else [json.loads(json.dumps(record))]
    for key, value in {'GITHUB_REPOSITORY': REPO, 'GITHUB_RUN_ID': '13', 'GITHUB_RUN_ATTEMPT': '2',
        'GITHUB_REF': 'refs/heads/main', 'GITHUB_WORKFLOW_REF': f'{REPO}/.github/workflows/deployment-recovery.yml@refs/heads/main',
        'RUNNER_TEMP': str(tmp_path), 'GITHUB_OUTPUT': str(tmp_path / 'outputs')}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(deployment, 'check', lambda *_: {'proceed': 'true'})
    monkeypatch.setattr(deployment, 'receipt_signer', lambda *_: '.github/workflows/ingestion-deploy.yml')
    monkeypatch.setattr(deployment.emissions, 'verify_rehearsal', Mock())
    monkeypatch.setattr(deployment, 'verify_record_identity', lambda *_: deployment.record_payload(record))
    monkeypatch.setattr(deployment, 'verify_receipt', Mock())
    args = SimpleNamespace(target=target, workflow=policy['workflow'], executor_sha=CURRENT,
        source_run_id=13, source_run_attempt=2, artifact=IMAGE, plan_json=str(original),
        plan_scope='\n'.join(policy['targets'] or ['google_storage_bucket.usage']),
        deferred=False, command='recovery-inputs', deployment_id=42, executor_dir=None)
    deployment.start(args, api)
    deployment.recover(args, api)
    emitted = dict(line.split('=', 1) for line in (tmp_path / 'outputs').read_text().splitlines())
    recovered_vars = json.loads(emitted['image_variables'])
    if target == 'wdpa-monthly':
        assert recovered_vars['wdpa_monthly_image_config_digest'] == CONFIG
    refreshed = plan('refreshed.tfplan', recovered_vars)
    worker = json.loads(refreshed.read_text())['planned_values']['outputs']['worker']['value']
    job = {'spec': {'template': {'spec': {'template': {'spec': {'containers': [{
        'image': worker['image'], 'env': [{'name': 'WDPA_BUILD_IMAGE', 'value': worker['image']},
                                       {'name': 'WDPA_BUILD_IMAGE_CONFIG_DIGEST', 'value': worker['config']}],
    }]}}}}}}
    execution = {'metadata': {'name': target + '-abcde', 'creationTimestamp': '2026-11-05T12:00:00Z'},
        'spec': {'template': {'spec': {'containers': [{'image': IMAGE}]}}},
        'status': {'completionTime': '2026-11-05T13:00:00Z', 'succeededCount': 1}}
    args.command, args.plan_json = 'reconcile', str(refreshed)
    with patch.object(deployment.subprocess, 'check_output', side_effect=[CURRENT, json.dumps(job), json.dumps([execution])]):
        deployment.recover(args, api)
    assert statuses[0]['description'] == 'verified'
    assert statuses[0]['environment_url'].endswith('/' + execution['metadata']['name'] + '?project=shared-datasets-1')
    if target == 'wdpa-monthly':
        reader = Mock(return_value=execution)
        readiness.require_ready(api, REPO, job, reader)
        reader.assert_called_once_with(execution['metadata']['name'])
        execution['status']['failedCount'] = 1
        with pytest.raises(deployment.DeploymentError, match='not completed successfully'):
            readiness.require_ready(api, REPO, job, reader)


@pytest.mark.parametrize('error', [ssl.SSLError('handshake timeout'), ConnectionResetError('reset'), TimeoutError('read timeout'),
    URLError(TimeoutError('connect timeout'))])
def test_google_permission_read_retries_transient_transport_at_client_boundary(error):
    response = io.BytesIO(b'{"permissions":["run.jobs.get"]}')
    with patch.object(permissions.urllib.request, 'urlopen', side_effect=[error, response]) as read, \
         patch.object(permissions.time, 'sleep') as sleep:
        assert permissions.request('https://run.googleapis.com/v2/projects/p:testIamPermissions', ['run.jobs.get'], 'test-token') == []
    assert read.call_count == 2
    sleep.assert_called_once_with(5)


@pytest.mark.parametrize('error', [ssl.SSLCertVerificationError('invalid certificate'),
    URLError(ssl.SSLCertVerificationError('invalid certificate')), HTTPError('https://example.test', 403, 'denied', {}, io.BytesIO(b'{}')),
    ValueError('invalid JSON')])
def test_google_read_does_not_retry_auth_certificate_or_malformed_data(error):
    with patch.object(permissions.urllib.request, 'urlopen', side_effect=error) as read, patch.object(permissions.time, 'sleep') as sleep:
        with pytest.raises((ssl.SSLCertVerificationError, URLError, RuntimeError, ValueError)):
            permissions.request('https://run.googleapis.com/v2/projects/p:testIamPermissions', ['run.jobs.get'], 'test-token')
    assert read.call_count == 1
    sleep.assert_not_called()


def test_google_read_retry_budget_is_bounded():
    with patch.object(permissions.urllib.request, 'urlopen', side_effect=ssl.SSLError('handshake timeout')) as read, \
         patch.object(permissions.time, 'sleep') as sleep:
        with pytest.raises(ssl.SSLError):
            permissions.request('https://run.googleapis.com/v2/projects/p:testIamPermissions', [], 'test-token')
    assert read.call_count == 3
    assert [row.args[0] for row in sleep.call_args_list] == [5, 10]


def test_every_tracked_terraform_file_has_a_selection_or_reasoned_boundary():
    paths = ci.deployment_paths()
    exceptions = registry(section='selection_exceptions')
    files = subprocess.check_output(['git', 'ls-files', 'terraform'], cwd=ROOT, text=True).splitlines()
    unowned = [path for path in files if path.endswith(('.tf', '.tfvars', '.hcl')) and path not in paths
        and not any(ci.matches(path, {pattern}) and row.get('reason', '').strip() for pattern, row in exceptions.items())]
    assert unowned == []
    lock = ci.select_deployments(['terraform/envs/prod/.terraform.lock.hcl'])
    assert set(lock) == set(registry(section='deployments'))
    for name in ('pr_plan_iam.tf', 'terraform_state_read_iam.tf'):
        assert ci.select_deployments(['terraform/envs/prod/' + name]) == []


@pytest.mark.parametrize('job', ['geospatial-changes', 'ci-ready'])
def test_gate_only_job_edits_select_every_validation_suite(tmp_path, job):
    before = (ROOT / '.github/workflows/ci.yml').read_bytes()
    workflow = yaml.safe_load(before)
    workflow['jobs'][job]['timeout-minutes'] = 99
    target = tmp_path / '.github/workflows/ci.yml'
    target.parent.mkdir(parents=True)
    target.write_text(yaml.safe_dump(workflow))
    with patch.object(ci, 'repository_files', return_value={'.github/workflows/ci.yml': before}):
        assert ci.changed_ci_suites('base', tmp_path) == set(ci.SUITES)


@pytest.mark.parametrize('days,healthy', [(37, True), (38, False), (44, False)])
def test_monthly_freshness_is_maximum_month_cadence_plus_seven_days(days, healthy):
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    payload = {'latest_run': {'date': (now - timedelta(days=days)).date().isoformat(), 'status': 'success'}}
    assert safety.fresh_activity(payload, 'monthly', now=now) is healthy


def test_wdpa_task_timeout_retains_at_least_fifty_percent_observed_headroom():
    config = load_terraform(ROOT / 'terraform/envs/prod/wdpa_monthly.tf')
    job = next(row['wdpa_monthly_job'] for row in config['module'] if 'wdpa_monthly_job' in row)
    assert int(job['timeout'].removesuffix('s')) >= (8 * 3600 + 21 * 60) * 1.5


@pytest.mark.parametrize('state', ['missing', 'pending', 'failed', 'applied', 'auth_failed', 'untrusted'])
def test_recovery_identity_health_uses_signed_manual_provisioning_and_latest_auth(state):
    import hashlib
    target = 'terraform-' + hashlib.sha256(b'PR plan identity provisioning').hexdigest()[:16]
    record = {'id': 42, 'environment': 'production-' + target}
    run = {'id': 90, 'run_attempt': 2}
    api = Mock()
    api.pages.side_effect = lambda path, **_: ([] if state == 'missing' else [record]) if '/deployments?' in path else [run] if '/runs?' in path else [
        {'steps': [{'name': 'Authenticate read-only recovery', 'conclusion': 'failure' if state == 'auth_failed' else 'success'}]}]
    status = {'state': 'success' if state in {'applied', 'auth_failed', 'untrusted'} else 'failure',
              'description': 'applied' if state in {'applied', 'auth_failed', 'untrusted'} else state}
    with patch.object(deployment, 'verify_record', return_value={'target': target}), \
         patch.object(deployment, 'verified_statuses', return_value=[status]), \
         patch.object(safety.alerts, 'verify_source_event', side_effect=ValueError('untrusted') if state == 'untrusted' else None, return_value=True):
        if state != 'applied':
            with pytest.raises((ValueError, RuntimeError), match='untrusted' if state == 'untrusted' else '[Rr]ecovery'):
                safety.recovery_identity(api)
        else:
            findings = safety.recovery_identity(api)
            assert findings == []
            assert all('recovery' in finding.lower() for finding in findings)
    api.post.assert_not_called()


def test_heartbeat_doc_matches_delivered_reader_failure_contract():
    text = (ROOT / 'docs/alert-routing.md').read_text()
    assert 'health-read failure remains in the deduplicated report and emits no' not in text
    assert 'Reader failures remain in the delivered health report' in text


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
def test_shared_deployment_reader_refuses_mutations_before_transport(method):
    request = permissions.urllib.request.Request('https://run.googleapis.com/v2/projects/p/jobs/job', method=method)
    with patch.object(permissions.urllib.request, 'urlopen') as transport:
        with pytest.raises(ValueError, match='cannot execute a mutation'):
            permissions.read_json(request, timeout=30)
    transport.assert_not_called()


@pytest.mark.parametrize('client', ['permission', 'policy'])
def test_deployment_read_clients_retry_in_clean_hosted_interpreter_with_mismatched_virtualenv(tmp_path, client):
    import sys
    code = """
import io, ssl, sys
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
from scripts import deployment_permissions as p, pr_terraform_plan as planner
with patch.object(p.urllib.request, 'urlopen', side_effect=[ssl.SSLError('TLS handshake timeout'), io.BytesIO(b'{"permissions": []}')]) as read, patch.object(p.time, 'sleep'):
    if sys.argv[2] == 'permission':
        assert p.request('https://run.googleapis.com/v2/projects/p:testIamPermissions', [], 'fixture') == []
    else:
        assert planner.PolicyReader('fixture').read('https://policytroubleshooter.googleapis.com/v3/iam:troubleshoot', '{}') == {'permissions': []}
    assert read.call_count == 2
"""
    environment = {**deployment.os.environ, 'VIRTUAL_ENV': str(tmp_path / '.venv-mismatch')}
    result = subprocess.run([sys.executable, '-S', '-c', code, str(ROOT), client], cwd=tmp_path,
                            env=environment, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('config', [None, '', 'sha256:bad'])
def test_wdpa_claim_requires_original_tested_config_before_record_creation(tmp_path, monkeypatch, config):
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'variables': {'wdpa_monthly_image_config_digest': {'value': config}}}))
    args = SimpleNamespace(target='wdpa-monthly', artifact=IMAGE, plan_json=str(plan),
        executor_sha=CURRENT, source_run_id=13, source_run_attempt=2)
    monkeypatch.setenv('GITHUB_REPOSITORY', REPO)
    monkeypatch.setenv('GITHUB_RUN_ID', '13')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    api = Mock()
    with patch.object(deployment, 'check', return_value={'proceed': 'true'}), \
         patch.object(deployment, 'receipt_signer', return_value='.github/workflows/ingestion-deploy.yml'), \
         patch.object(deployment.emissions, 'verify_rehearsal'):
        with pytest.raises((TypeError, ValueError), match='digest|str'):
            deployment.start(args, api)
    api.post.assert_not_called()
