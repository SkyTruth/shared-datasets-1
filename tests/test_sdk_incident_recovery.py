"""Reclassify a real candidate-only episode without replacing its Slack parent."""
import copy
import json
from pathlib import Path
from unittest import mock

import pytest
import yaml

from scripts import deployment_emission as emissions
from scripts import deployment_receipt_contracts as contracts
from scripts import slack_incidents as incidents
from test_slack_incidents import FakeSlack, IDENTITY, SHA, observation, signed_record

OLD_SCOPE = 'deployment:typescript-sdk'
SCOPE = incidents.scope_for_job('candidate', {}, 'publish-typescript-sdk.yml')[0]
RUNS = (38496427534, 38497808852, 38504179673)


@pytest.fixture(params=incidents.alerts.WORKFLOW_NAMES['publish-typescript-sdk.yml'])
def history(tmp_path, monkeypatch, request):
    failures = [observation(scope=OLD_SCOPE, label='typescript-sdk', job_name='candidate',
                            run_id=run_id, job_id=100 + index, time=f'2026-10-14T0{index + 1}:00:00Z')
                for index, run_id in enumerate(RUNS)]
    value = None
    for failure in failures:
        value = incidents.fold(value, failure, IDENTITY)
    value['incident']['id'] = 'SD-DA144B216BD5'
    value['incident']['posts'] = {key: {'state': 'delivered', 'ts': f'1791942827.{index:06d}'}
                                  for index, key in enumerate(incidents.failure_posts(value), 1)}
    parent = value['incident']['posts']['parent']['ts']
    value['incident']['permalink'] = f"https://example.slack.com/archives/{IDENTITY['channel']}/p{parent.replace('.', '')}"
    value['incident']['parent_view_sha256'] = incidents.parent_view_sha256(value)
    record = signed_record()
    record['payload']['state'] = value
    records = [record]
    repo = {'id': 9, 'full_name': incidents.REPOSITORY}
    workflow = {'id': 7, 'path': '.github/workflows/publish-typescript-sdk.yml'}
    runs = {item['run_id']: {'id': item['run_id'], 'name': request.param, 'path': workflow['path'],
                            'workflow_id': 7, 'head_sha': SHA, 'head_branch': 'main', 'event': 'workflow_run',
                            'run_attempt': 1, 'status': 'completed', 'conclusion': 'failure', 'updated_at': item['time'],
                            'repository': repo, 'head_repository': repo} for item in failures}
    jobs = {item['run_id']: [
        {'name': 'candidate', 'id': item['job_id'], 'status': 'completed', 'conclusion': item['conclusion'], 'completed_at': item['time']},
        {'name': 'publish', 'id': item['job_id'] + 1000, 'status': 'completed', 'conclusion': 'skipped'}] for item in failures}
    api = mock.Mock()
    def get(path):
        if path == f'repos/{incidents.REPOSITORY}':
            return repo
        if '/workflows/' in path:
            return workflow
        return runs[int(path.split('/runs/')[1].split('/')[0])]
    api.get.side_effect = get
    def pages(path, field=None):
        if '/runs/' in path:
            return jobs[int(path.split('/runs/')[1].split('/')[0])]
        if path.endswith('/statuses?per_page=100'):
            return [{'id': 12, 'state': 'success', 'description': 'applied'}]
        return records
    api.pages.side_effect = pages
    def post(path, payload):
        if path.endswith('/deployments'):
            created = {**copy.deepcopy(payload), 'id': max(item['id'] for item in records) + 1,
                       'sha': payload['ref'], 'creator': {'login': 'github-actions[bot]'}}
            records.append(created)
            return created
        return {**payload, 'id': 12}
    api.post.side_effect = post
    verifier = mock.Mock()
    monkeypatch.setattr(emissions, 'verify', verifier)
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'outputs'))
    ledger = incidents.Ledger(api, {'id': 500, 'run_attempt': 1, 'head_sha': 'b' * 40}, tmp_path)
    return ledger, record, records, jobs, runs, verifier


def healthy(run_id=RUNS[-1] + 1, **changes):
    value = observation(scope=SCOPE, kind='job', label='candidate', job_name='candidate',
                        run_id=run_id, job_id=300, conclusion='success', sha='b' * 40, time='2026-10-14T04:00:00Z')
    value.update(changes)
    return value


def test_real_episode_migrates_after_verification_and_resolves_only_its_original_parent(history, tmp_path):
    ledger, original, records, _, _, verifier = history
    untouched = copy.deepcopy(original)
    migrated = ledger.load()[SCOPE]
    assert verifier.call_args.args[2] == untouched
    assert original == untouched
    assert migrated['incident']['id'] == 'SD-DA144B216BD5'
    assert migrated['incident']['posts'] == untouched['payload']['state']['incident']['posts']
    prepared = incidents.prepare(ledger, IDENTITY, [healthy()], ancestor=lambda *_: True, success=lambda *_: None)
    assert len(prepared) == 1
    assert list((tmp_path / 'claims').glob('*.json'))
    slack = FakeSlack()
    incidents.deliver(ledger, prepared, slack)
    assert [call[0] for call in slack.calls] == ['update']
    assert slack.calls[0][2]['ts'] == migrated['incident']['posts']['parent']['ts']
    assert 'Resolved' in slack.calls[0][1]['text']
    assert list((tmp_path / 'outcomes').glob('*.json'))
    # APIs may return JSON strings for historical payloads.
    original['payload'] = json.dumps(original['payload'])
    assert list(ledger.load()) == [SCOPE]
    assert incidents.prepare(ledger, IDENTITY, [healthy()], ancestor=lambda *_: True, success=lambda *_: None) == []
    assert len(records) == 3


def test_unsigned_original_cannot_be_reclassified(history):
    ledger, _, _, _, _, verifier = history
    verifier.side_effect = emissions.EmissionError('forged')
    with mock.patch.object(incidents, 'migrate_sdk_candidate') as migration:
        with pytest.raises(emissions.EmissionError, match='forged'):
            ledger.load()
    migration.assert_not_called()


@pytest.mark.parametrize('defect', ['publish-ran', 'publish-missing', 'wrong-job', 'wrong-sha', 'mixed'])
def test_historical_publication_or_incompatible_source_cannot_be_reclassified(history, defect):
    ledger, record, _, jobs, runs, _ = history
    if defect == 'publish-ran':
        jobs[RUNS[0]][1]['conclusion'] = 'failure'
    elif defect == 'publish-missing':
        jobs[RUNS[0]].pop()
    elif defect == 'wrong-job':
        jobs[RUNS[0]][0]['id'] = 999
    elif defect == 'wrong-sha':
        runs[RUNS[0]]['head_sha'] = 'f' * 40
    else:
        record['payload']['state']['incident']['failures'][-1]['job_name'] = 'publish'
    with pytest.raises(incidents.IncidentError):
        ledger.load()
    ledger.api.post.assert_not_called()


@pytest.mark.parametrize('defect', ['parent', 'different-incident'])
def test_canonical_scope_collision_cannot_replace_the_original_parent(history, defect):
    ledger, _, records, _, _, _ = history
    canonical = copy.deepcopy(records[0])
    canonical['id'] += 1
    canonical['payload']['state'] = ledger.load()[SCOPE]
    if defect == 'parent':
        canonical['payload']['state']['incident']['posts']['parent']['ts'] = '1791942827.999999'
        canonical['payload']['state']['incident']['permalink'] = ''
    else:
        canonical['payload']['state']['incident']['id'] = 'SD-AAAAAAAAAAAA'
    records.append(canonical)
    with pytest.raises(incidents.IncidentError, match='conflict|collid'):
        ledger.load()


def test_signed_resolved_migration_does_not_resurrect_during_a_later_episode(history):
    ledger, _, _, _, _, _ = history
    prepared = incidents.prepare(ledger, IDENTITY, [healthy()], ancestor=lambda *_: True, success=lambda *_: None)
    incidents.deliver(ledger, prepared, FakeSlack())
    later = healthy(run_id=999, conclusion='failure', time='2026-10-14T05:00:00Z')
    incidents.prepare(ledger, IDENTITY, [later], ancestor=lambda *_: True, success=lambda *_: None)
    states = ledger.load()
    assert list(states) == [SCOPE]
    assert states[SCOPE]['incident']['id'] != 'SD-DA144B216BD5'


def test_migration_preserves_uncertain_retry_and_blocks_automatic_resend(history):
    ledger, original, _, _, _, _ = history
    state = original['payload']['state']
    key = list(state['incident']['posts'])[-1]
    state['incident']['posts'][key] = {'state': 'claimed', 'ts': None}
    prepared = incidents.prepare(ledger, IDENTITY, [healthy()], ancestor=lambda *_: True, success=lambda *_: None)
    slack = FakeSlack()
    incidents.deliver(ledger, prepared, slack)
    assert [call[0] for call in slack.calls] == ['update']
    assert ledger.load()[SCOPE]['incident']['posts'][key] == {'state': 'claimed', 'ts': None}


def test_unrelated_revision_does_not_resolve_migrated_candidate(history):
    ledger, _, _, _, _, _ = history
    incidents.prepare(ledger, IDENTITY, [healthy()], ancestor=lambda *_: False, success=lambda *_: None)
    assert ledger.load()[SCOPE]['incident']['resolution'] is None


def test_migration_accepts_later_confirmed_delivery_metadata_without_changing_known_identity(history):
    ledger, original, records, _, _, _ = history
    state = original['payload']['state']
    state['incident'].update(permalink='', parent_view_sha256=None,
                             posts={'parent': {'state': 'claimed', 'ts': None}})
    migrated = ledger.load()[SCOPE]
    migrated['incident']['posts']['parent'] = {'state': 'delivered', 'ts': '1791942827.000001'}
    migrated['incident']['permalink'] = f"https://example.slack.com/archives/{IDENTITY['channel']}/p1791942827000001"
    ledger.save(migrated, 'claims')
    assert ledger.load()[SCOPE] == migrated
    assert len(records) == 2


def test_candidate_success_cannot_resolve_a_real_sdk_publication_failure(history):
    ledger, original, _, _, _, _ = history
    for failure in original['payload']['state']['incident']['failures']:
        failure['job_name'] = 'publish'
    incidents.prepare(ledger, IDENTITY, [healthy()], ancestor=lambda *_: True, success=lambda *_: None)
    assert ledger.load()[OLD_SCOPE]['incident']['resolution'] is None


@pytest.mark.parametrize('step_conclusion', ['success', 'skipped', 'failure', None])
def test_only_executed_successful_candidate_verification_can_supply_recovery(history, step_conclusion):
    ledger, _, _, jobs, runs, _ = history
    run = runs[RUNS[0]]
    run['conclusion'] = 'success'
    job = jobs[RUNS[0]][0]
    job['conclusion'] = 'success'
    job['steps'] = [] if step_conclusion is None else [
        {'name': incidents.SDK_CANDIDATE_STEP, 'status': 'completed', 'conclusion': step_conclusion}]
    result = incidents.source_observations(ledger.api, {'workflow_run': run}, incidents.job_targets())
    assert any(item['scope'] == SCOPE and item['kind'] == 'job' for item in result) == (step_conclusion == 'success')
    assert incidents.scope_for_job('publish', incidents.job_targets(), incidents.SDK_WORKFLOW)[1] == 'deployment'


@pytest.mark.parametrize('defect', [None, 'write-token', 'oidc', 'publish-command', 'missing-verification', 'extra-writer'])
def test_candidate_read_only_workflow_boundary(defect):
    path = Path(__file__).resolve().parents[1] / '.github/workflows/publish-typescript-sdk.yml'
    workflow = yaml.safe_load(path.read_text())
    candidate = workflow['jobs']['candidate']
    if defect == 'write-token':
        candidate['permissions'] = {'contents': 'write'}
    elif defect == 'oidc':
        candidate['permissions'] = {'id-token': 'write'}
    elif defect == 'publish-command':
        candidate['steps'].append({'run': 'npm publish exact.tgz'})
    elif defect == 'missing-verification':
        candidate['steps'] = [step for step in candidate['steps'] if step.get('id') != 'candidate']
    elif defect == 'extra-writer':
        workflow['jobs']['other'] = {'steps': [{'run': 'npm publish exact.tgz'}]}
    assert bool(contracts.sdk_candidate_read_only(workflow)) == (defect is not None)
