"""One-off authoritative closure and protected preparation, without production transport."""
import json
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from requests import HTTPError, Response

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


def choose(fixture):
    return recovery.selection(executions.path(fixture.ctx), fixture.session, fixture.store,
                              IDENTITY, fixture.api, recovery.ReadBudget())


def test_automatic_selection_is_read_only_and_second_sweep_makes_no_slack_call(execution_recovery):
    fixture = execution_recovery
    before = dict(fixture.store.bucket.objects)
    result = choose(fixture)
    assert result['status'] == 'eligible'
    assert fixture.store.bucket.objects == before
    fixture.slack.update.assert_not_called()
    recovery.apply_selection(result, fixture.store, fixture.slack, IDENTITY)
    fixture.slack.update.assert_called_once()
    assert fixture.slack.update.call_args.args[0] == TS
    assert fixture.slack.update.call_args.args[1]['text'].startswith(':white_check_mark: Resolved')
    fixture.slack.reset_mock()
    fixture.session.reset_mock()
    assert choose(fixture)['status'] == 'resolved'
    fixture.session.get.assert_not_called()
    fixture.slack.update.assert_not_called()
    fixture.slack.post.assert_not_called()


@pytest.mark.parametrize('damage', ['open', 'old-deployment', 'old-status', 'pending', 'failed', 'applied', 'unsigned', 'missing-deployment'])
def test_automatic_ineligible_evidence_is_quiet_and_never_writes(execution_recovery, damage):
    fixture = execution_recovery
    if damage == 'open':
        fixture.alert['state'] = 'OPEN'
    elif damage == 'old-deployment':
        fixture.record['created_at'] = '2026-11-16T14:00:00Z'
    elif damage == 'old-status':
        fixture.status['created_at'] = '2026-11-16T14:00:00Z'
    elif damage in {'pending', 'failed', 'applied'}:
        fixture.status.update(state='success' if damage == 'applied' else 'pending' if damage == 'pending' else 'failure',
                              description='applied' if damage == 'applied' else 'verification_pending' if damage == 'pending' else 'failed')
    elif damage == 'unsigned':
        fixture.verifier.side_effect = subprocess.CalledProcessError(1, ['gh', 'attestation', 'verify', 'receipt'],
            stderr='✗ No attestations found for subject sha256:' + 'a' * 64 + '\nError: no attestations found\n')
    elif damage == 'missing-deployment':
        fixture.api.pages.side_effect = lambda *_args, **_kwargs: []
    before = dict(fixture.store.bucket.objects)
    assert choose(fixture)['status'] == 'waiting'
    assert fixture.store.bucket.objects == before
    fixture.slack.update.assert_not_called()
    fixture.slack.post.assert_not_called()


@pytest.mark.parametrize('kind', ['review', 'decision', 'another-open-failure'])
def test_automatic_parent_remains_untouched_while_other_work_is_pending(execution_recovery, kind):
    fixture = execution_recovery
    if kind == 'review':
        executions.report(fixture.store, fixture.slack, IDENTITY, fixture.ctx,
                          review=('request', {'asset_slug': 'sea-ice', 'release': '2026-11-16'}))
    elif kind == 'decision':
        executions.report(fixture.store, fixture.slack, IDENTITY, fixture.ctx,
                          decision=('request', 'Original decision evidence and links', False))
    else:
        other = '0.another'
        executions.monitoring({**fixture.incident, 'incident_id': other},
            {**fixture.alert, 'name': f'projects/{relay.PROJECT}/alerts/{other}'}, fixture.store, fixture.slack, IDENTITY)
        fixture.session.get.side_effect = lambda url, **_kwargs: Mock(json=lambda: {
            **fixture.alert, 'name': url.removeprefix('https://monitoring.googleapis.com/v3/'),
            'state': 'OPEN' if url.endswith(other) else 'CLOSED'})
    fixture.slack.reset_mock()
    before = dict(fixture.store.bucket.objects)
    assert choose(fixture)['status'] == 'waiting'
    assert fixture.store.bucket.objects == before
    fixture.slack.update.assert_not_called()


@pytest.mark.parametrize('damage', ['schema', 'source', 'alias', 'read', 'verification-read', 'signature', 'corrupt-deployment', 'generation', 'slack'])
def test_automatic_internal_faults_propagate(execution_recovery, damage):
    fixture = execution_recovery
    if damage in {'schema', 'source'}:
        path = executions.path(fixture.ctx)
        generation, raw = fixture.store.bucket.objects[path]
        value = json.loads(raw)
        if damage == 'schema':
            value['schema'] = 'corrupt'
        else:
            value['source']['execution'] = 'sea-ice-daily-other'
        fixture.store.bucket.objects[path] = generation + 1, json.dumps(value).encode()
    elif damage == 'alias':
        fixture.store.save(INCIDENT_ID, {'schema': 'foreign'}, fixture.store.load(INCIDENT_ID)[1])
    elif damage == 'read':
        fixture.session.get.side_effect = OSError('Monitoring unavailable')
    elif damage == 'verification-read':
        fixture.api.pages.side_effect = OSError('GitHub unavailable')
    elif damage == 'signature':
        fixture.verifier.side_effect = subprocess.CalledProcessError(1, ['gh', 'attestation', 'verify', 'receipt'], stderr='Error: HTTP 503: unavailable')
    elif damage == 'corrupt-deployment':
        fixture.verifier.side_effect = deployments.DeploymentError('incompatible deployment receipt')
    elif damage == 'generation':
        fixture.store.bucket.fail_receipt = True
    else:
        fixture.slack.update.side_effect = OSError('Slack unavailable')
    with pytest.raises((ValueError, OSError, RuntimeError, subprocess.CalledProcessError)):
        result = choose(fixture)
        recovery.apply_selection(result, fixture.store, fixture.slack, IDENTITY)
    fixture.slack.post.assert_not_called()


def test_automatic_retry_after_new_deployment_keeps_recorded_proof_without_verification(execution_recovery):
    fixture = execution_recovery
    fixture.slack.update.side_effect = OSError('interrupted')
    with pytest.raises(OSError):
        recovery.apply_selection(choose(fixture), fixture.store, fixture.slack, IDENTITY)
    recorded = executions.load(fixture.store, fixture.ctx, IDENTITY)[0]['recovery']
    fixture.record['id'] = 20
    fixture.status['id'] = 22
    fixture.session.reset_mock()
    fixture.api.reset_mock()
    fixture.verifier.reset_mock()
    fixture.slack.update.side_effect = None
    recovery.apply_selection(choose(fixture), fixture.store, fixture.slack, IDENTITY)
    assert choose(fixture)['status'] == 'resolved'
    assert executions.load(fixture.store, fixture.ctx, IDENTITY)[0]['recovery'] == recorded
    assert fixture.store.load(INCIDENT_ID)[0]['closed'] is True
    fixture.session.get.assert_not_called()
    fixture.api.get.assert_not_called()
    fixture.api.pages.assert_not_called()
    fixture.verifier.assert_not_called()


def automatic_environment():
    return {'GITHUB_REF': 'refs/heads/main', 'GITHUB_REPOSITORY': 'SkyTruth/shared-datasets-1',
            'GITHUB_WORKFLOW_REF': 'SkyTruth/shared-datasets-1/.github/workflows/resolve-workflow-alert.yml@refs/heads/main',
            'GITHUB_EVENT_NAME': 'workflow_dispatch', 'GITHUB_ACTOR': 'github-actions[bot]'}


@pytest.mark.parametrize('key,value', [('GITHUB_REF', 'refs/heads/other'), ('GITHUB_REPOSITORY', 'foreign/repo'),
    ('GITHUB_WORKFLOW_REF', 'foreign/main'), ('GITHUB_EVENT_NAME', 'pull_request'), ('INCIDENT_ID', INCIDENT_ID),
    ('RECOVERY_DEPLOYMENT_ID', '10'), ('ORIGINAL_RUN_ID', '11'), ('ORIGINAL_MESSAGE_TS', TS)])
def test_automatic_gate_rejects_untrusted_context_and_caller_ids(key, value):
    recovery.automatic_authority(automatic_environment())
    with pytest.raises(ValueError):
        recovery.automatic_authority({**automatic_environment(), key: value})


@pytest.mark.parametrize('actor,ref', [('someone', 'refs/heads/main'), ('jonaraphael', 'refs/heads/other')])
def test_manual_cli_retains_owner_and_main_gates(monkeypatch, actor, ref):
    monkeypatch.setattr(recovery.sys, 'argv', ['monitoring_slack_reconcile.py', INCIDENT_ID])
    monkeypatch.setenv('GITHUB_ACTOR', actor)
    monkeypatch.setenv('GITHUB_REF', ref)
    with pytest.raises(ValueError, match='owner-only'):
        recovery.main()


def test_sweep_selects_newest_parents_without_state_and_continues_after_fault(execution_recovery, monkeypatch, caplog):
    fixture = execution_recovery
    paths = [f'executions/{index:064x}.json' for index in range(recovery.PARENT_LIMIT + 3)]
    listing = Mock(return_value=[SimpleNamespace(name=path, updated=datetime(2026, 11, 16, tzinfo=timezone.utc)
        + timedelta(seconds=index)) for index, path in enumerate(paths)])
    fixture.store.bucket.list_blobs = listing
    seen = []
    def select(path, *_args, **_kwargs):
        seen.append(path)
        if path == paths[-1]:
            raise ValueError('corrupt receipt')
        return {'execution': path, 'status': 'resolved', 'reason': 'already resolved'}
    monkeypatch.setattr(recovery, 'selection', select)
    before = dict(fixture.store.bucket.objects)
    with caplog.at_level('INFO'), pytest.raises(ExceptionGroup, match='execution recovery faults'):
        recovery.sweep(fixture.session, fixture.store, fixture.slack, IDENTITY, fixture.api)
    assert seen == list(reversed(paths))[:recovery.PARENT_LIMIT]
    listing.assert_called_once_with(prefix='executions/')
    assert fixture.store.bucket.objects == before
    assert '3' in caplog.text and 'parent limit' in caplog.text
    fixture.slack.update.assert_not_called()


def test_empty_sweep_and_failure_budget(execution_recovery):
    fixture = execution_recovery
    fixture.store.bucket.list_blobs = Mock(return_value=[])
    recovery.sweep(fixture.session, fixture.store, fixture.slack, IDENTITY, fixture.api)
    budget = recovery.ReadBudget()
    for _ in range(recovery.FAILURE_LIMIT):
        budget.take()
    with pytest.raises(recovery.FailureBudgetExhausted):
        budget.take()


@pytest.mark.parametrize('stderr', [
    '✗ No attestations found for subject sha256:' + 'a' * 64 + '\nError: HTTP 503: unavailable',
    'Error: no attestations found',
    '✗ No attestations found for subject sha256:' + 'a' * 64 + '\nError: no attestations found\nError: invalid signature',
    'Error: HTTP 404: Not Found',
])
def test_unsigned_sentinel_never_masks_failed_verification_reads(execution_recovery, stderr):
    fixture = execution_recovery
    fixture.verifier.side_effect = subprocess.CalledProcessError(1, ['gh', 'attestation', 'verify', 'receipt'], stderr=stderr)
    with pytest.raises(subprocess.CalledProcessError):
        choose(fixture)
    fixture.slack.update.assert_not_called()


def test_automatic_sweep_authenticates_only_when_it_can_edit(execution_recovery):
    fixture = execution_recovery
    fixture.store.bucket.list_blobs = Mock(return_value=[listed_parent(fixture.ctx)])
    fixture.slack.channel = IDENTITY['channel']
    fixture.slack.identity.return_value = IDENTITY
    fixture.alert['state'] = 'OPEN'
    recovery.sweep(fixture.session, fixture.store, fixture.slack, None, fixture.api)
    fixture.slack.identity.assert_not_called()
    fixture.slack.update.assert_not_called()
    fixture.alert['state'] = 'CLOSED'
    recovery.sweep(fixture.session, fixture.store, fixture.slack, None, fixture.api)
    fixture.slack.identity.assert_called_once()
    fixture.slack.update.assert_called_once()
    fixture.slack.reset_mock()
    recovery.sweep(fixture.session, fixture.store, fixture.slack, None, fixture.api)
    fixture.slack.identity.assert_not_called()
    fixture.slack.update.assert_not_called()


def test_automatic_rechecks_live_bot_identity_before_edit(execution_recovery):
    fixture = execution_recovery
    fixture.store.bucket.list_blobs = Mock(return_value=[listed_parent(fixture.ctx)])
    fixture.slack.channel = IDENTITY['channel']
    fixture.slack.identity.return_value = {**IDENTITY, 'user': 'UOTHER123'}
    with pytest.raises(ExceptionGroup):
        recovery.sweep(fixture.session, fixture.store, fixture.slack, None, fixture.api)
    fixture.slack.update.assert_not_called()
    fixture.slack.post.assert_not_called()


def listed_parent(ctx, age=0):
    return SimpleNamespace(name=executions.path(ctx), updated=datetime(2026, 11, 16, tzinfo=timezone.utc)
                           - timedelta(seconds=age))


def add_parent(fixture, suffix, *, job='sea-ice-daily', resolved=False):
    ctx = {**fixture.ctx, 'job': job, 'execution': job + '-' + suffix}
    key = '0.' + suffix
    alert = {**fixture.alert, 'name': f'projects/{relay.PROJECT}/alerts/{key}',
             'resource': {'type': 'cloud_run_job', 'labels': {'project_id': relay.PROJECT,
                 'location': 'us-central1', 'job_name': job}}, 'log': {'extractedLabels': {'execution': ctx['execution']}}}
    executions.monitoring({**fixture.incident, 'incident_id': key}, alert, fixture.store, fixture.slack, IDENTITY)
    if resolved:
        executions.report(fixture.store, fixture.slack, IDENTITY, ctx, failure=(key, True, ''),
                          recovery={'deployment_id': 10, 'status_id': 12, 'run_id': 11, 'attempt': 1})
        alias, generation = fixture.store.load(key)
        fixture.store.save(key, {**alias, 'closed': True}, generation)
    return ctx, alert


def test_sweep_memoizes_latest_proof_once_per_job_and_reads_each_parent_once(execution_recovery):
    fixture = execution_recovery
    other, alert = add_parent(fixture, 'second')
    fixture.store.bucket.list_blobs = Mock(return_value=[listed_parent(fixture.ctx), listed_parent(other)])
    fixture.session.get.side_effect = lambda url, **_: Mock(json=lambda: alert if url.endswith('0.second') else fixture.alert)
    fixture.slack.reset_mock()
    read = Mock(wraps=fixture.store.read)
    fixture.store.read = read
    recovery.sweep(fixture.session, fixture.store, fixture.slack, IDENTITY, fixture.api)
    assert fixture.verifier.call_count == 1
    assert fixture.api.get.call_count == 1
    assert fixture.api.pages.call_count == 3
    assert fixture.slack.update.call_count == 2
    # One discovery read and one generation-checked reporter read for each parent.
    for ctx in (fixture.ctx, other):
        assert sum(call.args[0] == executions.path(ctx) for call in read.call_args_list) == 2


def test_sweep_proof_memo_keeps_jobs_independent(execution_recovery, monkeypatch):
    fixture = execution_recovery
    other, alert = add_parent(fixture, 'other', job='mpatlas-monthly')
    fixture.store.bucket.list_blobs = Mock(return_value=[listed_parent(fixture.ctx), listed_parent(other)])
    fixture.session.get.side_effect = lambda url, **_: Mock(json=lambda: alert if url.endswith('0.other') else fixture.alert)
    other_record = {**fixture.record, 'id': 20}
    other_status = {**fixture.status, 'id': 22}
    def pages(path):
        if '/statuses?' in path:
            return [other_status if '/20/' in path else fixture.status]
        return [other_record if 'mpatlas-monthly' in path else fixture.record]
    fixture.api.pages.side_effect = pages
    fixture.api.get.side_effect = lambda path: other_record if path.endswith('/20') else fixture.record
    monkeypatch.setattr(deployments, 'record_payload', lambda record: {
        **fixture.payload, 'target': 'mpatlas-monthly' if record['id'] == 20 else fixture.ctx['job']})
    fixture.slack.reset_mock()
    recovery.sweep(fixture.session, fixture.store, fixture.slack, IDENTITY, fixture.api)
    assert fixture.verifier.call_count == 2
    assert executions.load(fixture.store, fixture.ctx, IDENTITY)[0]['recovery']['deployment_id'] == 10
    assert executions.load(fixture.store, other, IDENTITY)[0]['recovery']['deployment_id'] == 20


@pytest.mark.parametrize('absence', ['missing', 'unsigned', 'fault'])
def test_sweep_memoizes_absence_and_faults_without_repeating_proof_lookup(execution_recovery, absence):
    fixture = execution_recovery
    other, alert = add_parent(fixture, 'second')
    fixture.store.bucket.list_blobs = Mock(return_value=[listed_parent(fixture.ctx), listed_parent(other)])
    fixture.session.get.side_effect = lambda url, **_: Mock(json=lambda: alert if url.endswith('0.second') else fixture.alert)
    if absence == 'missing':
        fixture.api.pages.side_effect = lambda *_args, **_kwargs: []
    else:
        fixture.verifier.side_effect = subprocess.CalledProcessError(1, ['gh', 'attestation', 'verify', 'receipt'],
            stderr='✗ No attestations found for subject sha256:' + 'a' * 64 + '\nError: no attestations found\n'
            if absence == 'unsigned' else 'Error: HTTP 503: unavailable')
    fixture.slack.reset_mock()
    if absence == 'fault':
        with pytest.raises(ExceptionGroup):
            recovery.sweep(fixture.session, fixture.store, fixture.slack, IDENTITY, fixture.api)
    else:
        recovery.sweep(fixture.session, fixture.store, fixture.slack, IDENTITY, fixture.api)
    assert fixture.api.pages.call_count == (1 if absence == 'missing' else 2)
    assert fixture.verifier.call_count == (0 if absence == 'missing' else 1)
    fixture.slack.update.assert_not_called()


def test_failure_budget_defers_remaining_parents_quietly(execution_recovery, monkeypatch, caplog):
    fixture = execution_recovery
    other, alert = add_parent(fixture, 'second')
    fixture.store.bucket.list_blobs = Mock(return_value=[listed_parent(other, 1), listed_parent(fixture.ctx)])
    fixture.session.get.side_effect = lambda url, **_: Mock(json=lambda: alert if url.endswith('0.second') else fixture.alert)
    monkeypatch.setattr(recovery, 'FAILURE_LIMIT', 1)
    fixture.slack.reset_mock()
    fixture.store.read = Mock(wraps=fixture.store.read)
    with caplog.at_level('INFO'):
        recovery.sweep(fixture.session, fixture.store, fixture.slack, IDENTITY, fixture.api)
    assert fixture.session.get.call_count == 1
    assert fixture.slack.update.call_count == 1
    assert '1' in caplog.text and 'failure read budget' in caplog.text
    assert not any(call.args[0] == executions.path(other) for call in fixture.store.read.call_args_list)
    assert executions.load(fixture.store, other, IDENTITY)[0].get('recovery') is None


def test_failure_budget_never_applies_partially_verified_parent(execution_recovery, monkeypatch):
    fixture = execution_recovery
    other = '0.second'
    executions.monitoring({**fixture.incident, 'incident_id': other},
        {**fixture.alert, 'name': f'projects/{relay.PROJECT}/alerts/{other}'}, fixture.store, fixture.slack, IDENTITY)
    fixture.store.bucket.list_blobs = Mock(return_value=[listed_parent(fixture.ctx)])
    fixture.session.get.side_effect = lambda url, **_: Mock(json=lambda: {
        **fixture.alert, 'name': url.removeprefix('https://monitoring.googleapis.com/v3/')})
    monkeypatch.setattr(recovery, 'FAILURE_LIMIT', 1)
    fixture.slack.reset_mock()
    before = dict(fixture.store.bucket.objects)
    recovery.sweep(fixture.session, fixture.store, fixture.slack, IDENTITY, fixture.api)
    fixture.slack.update.assert_not_called()
    assert fixture.store.bucket.objects == before


def test_render_drift_obeys_edit_cap_without_monitoring_or_github_and_next_sweep_continues(execution_recovery, monkeypatch, caplog):
    fixture = execution_recovery
    # Discard the original unresolved fixture from this inventory.
    parents = [add_parent(fixture, 'resolved' + str(i), resolved=True)[0] for i in range(recovery.EDIT_LIMIT + 3)]
    fixture.store.bucket.list_blobs = Mock(return_value=[listed_parent(ctx, i) for i, ctx in enumerate(parents)])
    render = executions.render
    monkeypatch.setattr(executions, 'render', lambda value: {**render(value), 'text': render(value)['text'] + '\nUpdated wording'})
    fixture.slack.reset_mock()
    fixture.slack.channel = IDENTITY['channel']
    fixture.slack.identity.return_value = IDENTITY
    with caplog.at_level('INFO'):
        recovery.sweep(fixture.session, fixture.store, fixture.slack, None, fixture.api)
    assert fixture.slack.update.call_count == recovery.EDIT_LIMIT
    assert 'edit limit' in caplog.text
    recovery.sweep(fixture.session, fixture.store, fixture.slack, None, fixture.api)
    assert fixture.slack.update.call_count == len(parents)
    fixture.session.get.assert_not_called()
    fixture.api.get.assert_not_called()
    fixture.api.pages.assert_not_called()
    for ctx in parents:
        parent = executions.load(fixture.store, ctx, IDENTITY)[0]
        assert parent['recovery']['deployment_id'] == 10
        assert parent['view'] == recovery.digest(executions.render(parent))


def test_recorded_recovery_closes_new_failure_under_first_proof_in_one_edit(execution_recovery):
    fixture = execution_recovery
    recovery.apply_selection(choose(fixture), fixture.store, fixture.slack, IDENTITY)
    original = executions.load(fixture.store, fixture.ctx, IDENTITY)[0]['recovery']
    other = '0.second'
    executions.monitoring({**fixture.incident, 'incident_id': other},
        {**fixture.alert, 'name': f'projects/{relay.PROJECT}/alerts/{other}'}, fixture.store, fixture.slack, IDENTITY)
    fixture.record['id'] = 20
    fixture.status['id'] = 22
    fixture.session.get.side_effect = lambda url, **_: Mock(json=lambda: {
        **fixture.alert, 'name': url.removeprefix('https://monitoring.googleapis.com/v3/')})
    fixture.slack.reset_mock()
    recovery.apply_selection(choose(fixture), fixture.store, fixture.slack, IDENTITY)
    parent = executions.load(fixture.store, fixture.ctx, IDENTITY)[0]
    assert parent['recovery'] == original
    assert all(item['closed'] for item in parent['failures'].values())
    assert fixture.store.load(other)[0]['closed'] is True
    fixture.slack.update.assert_called_once()
    assert fixture.session.get.call_count == 2  # One first recovery, then only the new failure.


def test_new_parent_multiple_failures_has_one_slack_edit(execution_recovery):
    fixture = execution_recovery
    other = '0.second'
    executions.monitoring({**fixture.incident, 'incident_id': other},
        {**fixture.alert, 'name': f'projects/{relay.PROJECT}/alerts/{other}'}, fixture.store, fixture.slack, IDENTITY)
    fixture.session.get.side_effect = lambda url, **_: Mock(json=lambda: {
        **fixture.alert, 'name': url.removeprefix('https://monitoring.googleapis.com/v3/')})
    fixture.slack.reset_mock()
    recovery.apply_selection(choose(fixture), fixture.store, fixture.slack, IDENTITY)
    fixture.slack.update.assert_called_once()
    assert fixture.slack.update.call_args.args[1]['text'].startswith(':white_check_mark: Resolved')
    assert fixture.verifier.call_count == 1


def test_batch_closure_rejects_unknown_failure_without_writes(execution_recovery):
    fixture = execution_recovery
    before = dict(fixture.store.bucket.objects)
    with pytest.raises(ValueError, match='original execution failure'):
        executions.report(fixture.store, fixture.slack, IDENTITY, fixture.ctx,
                          closed_failures=[INCIDENT_ID, '0.unknown'])
    assert fixture.store.bucket.objects == before
    fixture.slack.update.assert_not_called()


def test_unmanaged_job_is_ignored_without_monitoring_or_github_but_manual_rejects(execution_recovery):
    fixture = execution_recovery
    ctx, alert = add_parent(fixture, 'usage', job='dataset-usage')
    result = recovery.selection(executions.path(ctx), fixture.session, fixture.store, IDENTITY, fixture.api, recovery.ReadBudget())
    assert result['status'] == 'ignored' and 'managed' in result['reason']
    fixture.session.get.assert_not_called()
    fixture.api.pages.assert_not_called()
    fixture.session.get.return_value.json.return_value = alert
    fixture.slack.reset_mock()
    with pytest.raises(ValueError, match='exact managed execution failure'):
        recovery.reconcile('0.usage', fixture.session, fixture.store, fixture.slack, IDENTITY, deployment_id='10', api=fixture.api)
    fixture.slack.update.assert_not_called()


@pytest.mark.parametrize('status', [404, 403, 429, 500])
def test_monitoring_retention_404_is_quiet_only_in_automatic_mode(execution_recovery, status):
    fixture = execution_recovery
    response = Response()
    response.status_code = status
    fixture.session.get.return_value = response
    fixture.slack.reset_mock()
    if status == 404:
        result = choose(fixture)
        assert result['status'] == 'waiting' and '404' in result['reason']
    else:
        with pytest.raises(HTTPError):
            choose(fixture)
    fixture.api.pages.assert_not_called()
    with pytest.raises(HTTPError):
        recovery.reconcile(INCIDENT_ID, fixture.session, fixture.store, fixture.slack, IDENTITY, deployment_id='10', api=fixture.api)
    fixture.slack.update.assert_not_called()
