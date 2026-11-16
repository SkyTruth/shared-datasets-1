"""One-off authoritative closure and protected preparation, without production transport."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import monitoring_slack_prepare as prepare, deployment_revision as deployments
from scripts import monitoring_slack_reconcile as recovery
from scripts.repo_guardrails import has_guarded_main_workflow_checkout
from scripts.slack_incident_api import Slack
from services.monitoring_slack import run as relay, executions
from test_monitoring_slack import Bucket
from workflow_helpers import load_workflow, workflow_triggers

INCIDENT_ID = '0.oe73n791vhsd'
IDENTITY = {'team': 'T12345678', 'user': 'U12345678', 'channel': 'C12345678'}
TS = '1792830681.000001'


@pytest.mark.parametrize('state,known', [('OPEN', True), ('CLOSED', False), ('CLOSED', True)])
def test_one_closure_read_never_creates_parent_or_marks_open_incident_closed(state, known):
    bucket = Bucket()
    store = relay.Store(bucket)
    if known:
        store.save(INCIDENT_ID, {'schema': relay.SCHEMA, 'identity': IDENTITY, 'ts': TS, 'closed': False}, 0)
    session = Mock()
    session.get.return_value.json.return_value = {
        'name': f'projects/{relay.PROJECT}/alerts/{INCIDENT_ID}', 'state': state,
        'closeTime': '2026-10-24T11:11:50Z', 'policy': {'displayName': 'WDPA observer'},
    }
    slack = Mock(spec=Slack)
    if state == 'CLOSED' and known:
        recovery.reconcile(INCIDENT_ID, session, store, slack, IDENTITY)
        slack.update.assert_called_once()
        assert slack.update.call_args.args[0] == TS
        assert store.load(INCIDENT_ID)[0]['closed'] is True
    else:
        with pytest.raises(ValueError):
            recovery.reconcile(INCIDENT_ID, session, store, slack, IDENTITY)
        slack.update.assert_not_called()
    slack.post.assert_not_called()
    session.get.assert_called_once_with(f'https://monitoring.googleapis.com/v3/projects/{relay.PROJECT}/alerts/{INCIDENT_ID}', timeout=30)


def test_monitoring_resolution_is_manual_owner_only_main_and_protected():
    workflow = load_workflow(Path('.github/workflows/resolve-workflow-alert.yml'))
    assert set(workflow_triggers(workflow)) == {'workflow_dispatch'}
    job = workflow['jobs']['monitoring']
    assert job['if'] == "github.actor == 'jonaraphael' && github.ref == 'refs/heads/main' && inputs.monitoring_incident_id != ''"
    assert has_guarded_main_workflow_checkout(workflow, '.github/workflows/resolve-workflow-alert.yml')
    assert job['environment'] == 'shared-datasets-production'
    assert job['permissions'] == {'contents': 'read', 'actions': 'read', 'deployments': 'read',
                                 'attestations': 'read', 'id-token': 'write'}
    steps = job['steps']
    assert next(step for step in steps if step.get('uses', '').startswith('actions/checkout'))['with']['fetch-depth'] == 0
    assert any(step.get('run') == 'python scripts/install_deployment_verifier.py' for step in steps)
    assert steps[-1]['env']['RECOVERY_DEPLOYMENT_ID'] == '${{ inputs.recovery_deployment_id }}'
    assert '${{ inputs.' not in job['steps'][-1]['run']


@pytest.fixture
def execution_recovery(monkeypatch):
    from types import SimpleNamespace
    from scripts import slack_incidents as incidents

    store, slack, api, session = relay.Store(Bucket()), Mock(spec=Slack), Mock(), Mock()
    slack.post.return_value = TS
    ctx = {'project': relay.PROJECT, 'job': 'sea-ice-daily', 'execution': 'sea-ice-daily-abcde'}
    alert = {'name': f'projects/{relay.PROJECT}/alerts/{INCIDENT_ID}', 'state': 'CLOSED',
             'openTime': '2026-11-16T15:02:16Z', 'closeTime': '2026-11-16T16:02:16Z',
             'policy': {'displayName': executions.FAILURE_POLICY},
             'resource': {'type': 'cloud_run_job', 'labels': {'project_id': relay.PROJECT,
                 'location': 'us-central1', 'job_name': ctx['job']}},
             'log': {'extractedLabels': {'execution': ctx['execution']}}}
    incident = {'incident_id': INCIDENT_ID, 'state': 'open', 'policy_name': executions.FAILURE_POLICY}
    executions.monitoring(incident, alert, store, slack, IDENTITY, cause='subprocess.TimeoutExpired: GDAL version probe')
    slack.reset_mock()
    record = {'id': 10, 'sha': 'a' * 40, 'created_at': '2026-11-16T16:05:00Z'}
    payload = {'target': ctx['job'], 'artifact': 'image@sha256:' + 'f' * 64}
    status = {'id': 12, 'state': 'success', 'description': 'verified', 'created_at': '2026-11-16T16:18:44Z',
              'log_url': 'https://github.com/SkyTruth/shared-datasets-1/actions/runs/11/attempts/1'}
    api.pages.side_effect = lambda path: [status] if '/statuses?' in path else [record]
    api.get.return_value = record
    session.get.return_value.json.return_value = alert
    monkeypatch.setattr(deployments, 'record_payload', Mock(return_value=payload))
    monkeypatch.setattr(deployments, 'verify_record', Mock(return_value=payload))
    monkeypatch.setattr(incidents, 'terminal_contract_sha256', Mock(return_value='c' * 64))
    verifier = Mock()
    monkeypatch.setattr(deployments, 'verify_receipt', verifier)
    return SimpleNamespace(store=store, slack=slack, api=api, session=session, ctx=ctx,
                           alert=alert, incident=incident, record=record, status=status, payload=payload, verifier=verifier)


def test_signed_verified_recovery_updates_original_parent_and_survives_replayed_open(execution_recovery):
    fixture = execution_recovery
    for _ in range(2):
        recovery.reconcile(INCIDENT_ID, fixture.session, fixture.store, fixture.slack, IDENTITY,
                           deployment_id='10', api=fixture.api)
    fixture.slack.post.assert_not_called()
    for call in fixture.slack.update.call_args_list:
        ts, payload = call.args
        assert ts == TS and payload['text'].startswith(':white_check_mark: Resolved')
        assert 'subprocess.TimeoutExpired: GDAL version probe' in payload['text']
        assert 'Exact execution logs' in payload['text'] and 'Verified recovery' in payload['text']
    assert fixture.verifier.call_count == 2
    stored, _ = executions.load(fixture.store, fixture.ctx, IDENTITY)
    assert stored['schema'] == executions.RECOVERED_SCHEMA
    assert stored['recovery'] == {'deployment_id': 10, 'status_id': 12, 'run_id': 11, 'attempt': 1}
    executions.monitoring(fixture.incident, fixture.alert, fixture.store, fixture.slack, IDENTITY)
    assert fixture.slack.update.call_args.args[1]['text'].startswith(':white_check_mark: Resolved')


@pytest.mark.parametrize('damage', ['open', 'missing-parent', 'foreign-execution', 'wrong-job', 'unsigned',
    'applied', 'failed', 'newer-deployment', 'old-deployment', 'old-status', 'unknown-job', 'unconfirmed-parent'])
def test_unverified_or_unrelated_recovery_never_updates_slack(execution_recovery, damage):
    fixture = execution_recovery
    if damage == 'open':
        fixture.alert['state'] = 'OPEN'
    elif damage == 'missing-parent':
        fixture.store.bucket.objects.clear()
    elif damage == 'foreign-execution':
        fixture.alert['log']['extractedLabels']['execution'] = 'sea-ice-daily-other'
    elif damage == 'wrong-job':
        fixture.payload['target'] = 'mpatlas-monthly'
    elif damage == 'unsigned':
        fixture.verifier.side_effect = deployments.DeploymentError('invalid signature')
    elif damage == 'applied':
        fixture.status['description'] = 'applied'
    elif damage == 'failed':
        fixture.status.update(state='failure', description='failed')
    elif damage == 'newer-deployment':
        fixture.record['id'] = 11
    elif damage == 'old-deployment':
        fixture.record['created_at'] = '2026-11-16T14:00:00Z'
    elif damage == 'old-status':
        fixture.status['created_at'] = '2026-11-16T14:00:00Z'
    elif damage == 'unknown-job':
        fixture.alert['policy']['displayName'] = 'Unrelated policy'
    elif damage == 'unconfirmed-parent':
        name = executions.path(fixture.ctx)
        generation, raw = fixture.store.bucket.objects[name]
        value = json.loads(raw)
        value['ts'] = None
        fixture.store.bucket.objects[name] = generation + 1, json.dumps(value).encode()
    with pytest.raises((ValueError, RuntimeError)):
        recovery.reconcile(INCIDENT_ID, fixture.session, fixture.store, fixture.slack, IDENTITY,
                           deployment_id='10', api=fixture.api)
    fixture.slack.post.assert_not_called()
    fixture.slack.update.assert_not_called()


def test_interrupted_verified_update_reuses_original_parent(execution_recovery):
    fixture = execution_recovery
    fixture.slack.update.side_effect = [OSError('transport interrupted'), None]
    with pytest.raises(OSError):
        recovery.reconcile(INCIDENT_ID, fixture.session, fixture.store, fixture.slack, IDENTITY,
                           deployment_id='10', api=fixture.api)
    recovery.reconcile(INCIDENT_ID, fixture.session, fixture.store, fixture.slack, IDENTITY,
                       deployment_id='10', api=fixture.api)
    assert [call.args[0] for call in fixture.slack.update.call_args_list] == [TS, TS]
    fixture.slack.post.assert_not_called()


def test_pending_identity_review_survives_verified_recovery(execution_recovery):
    fixture = execution_recovery
    original = '*Decision needed:* keep all original evidence and review links'
    executions.report(fixture.store, fixture.slack, IDENTITY, fixture.ctx, decision=('request', original, False))
    fixture.slack.reset_mock()
    recovery.reconcile(INCIDENT_ID, fixture.session, fixture.store, fixture.slack, IDENTITY,
                       deployment_id='10', api=fixture.api)
    text = fixture.slack.update.call_args.args[1]['text']
    assert text.startswith(':warning:') and original in text and 'Decision needed' in text
    assert 'Resolved' not in text
    fixture.slack.post.assert_not_called()


@pytest.mark.parametrize('matches', [False, True])
def test_preparation_checks_registry_bytes_and_keeps_bot_secret_out_of_state_and_arguments(tmp_path, monkeypatch, matches):
    image = 'sha256:' + 'a' * 64
    output = tmp_path / 'github-env'
    monkeypatch.setenv('CALLER_WORKFLOW', 'cron-alert-policy-sync.yml')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.setenv('SHARED_DATASETS_SLACK_BOT_TOKEN', 'fixture-private-token')
    monkeypatch.setenv('SHARED_DATASETS_SLACK_CHANNEL_ID', 'C12345678')
    monkeypatch.setenv('TESTED_IMAGE_ID', image)
    monkeypatch.setenv('TESTED_IMAGE_CONFIG', image)
    monkeypatch.setenv('EXECUTOR_SHA', 'b' * 40)
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_ENV', str(output))
    monkeypatch.setattr(prepare, 'Slack', Mock())
    run = Mock()
    run.return_value.stdout = b'projects/123456789/secrets/shared-datasets-slack-bot-token/versions/1\n'
    monkeypatch.setattr(prepare.subprocess, 'run', run)
    raw = json.dumps({'config': {'digest': image if matches else 'sha256:' + 'c' * 64}}).encode()
    monkeypatch.setattr(prepare.subprocess, 'check_output', Mock(side_effect=[raw, 'sha256:' + 'd' * 64]))
    if matches:
        prepare.main()
        assert output.read_text().startswith('TF_VAR_monitoring_slack_image=')
        assert 'fixture-private-token' not in output.read_text()
        assert run.call_args.kwargs['input'] == b'fixture-private-token'
        assert '--data-file=-' in run.call_args.args[0]
        assert 'TF_VAR_monitoring_slack_token_version=1' in output.read_text()
    else:
        with pytest.raises(ValueError, match='tested bytes'):
            prepare.main()
        assert not output.exists()
        assert not any('secrets' in call.args[0] for call in run.call_args_list)
    assert all('fixture-private-token' not in str(call.args) for call in run.call_args_list)
