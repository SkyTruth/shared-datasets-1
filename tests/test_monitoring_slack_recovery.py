"""One-off authoritative closure and protected preparation, without production transport."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import monitoring_slack_prepare as prepare
from scripts import monitoring_slack_reconcile as recovery
from scripts.slack_incident_api import Slack
from services.monitoring_slack import run as relay
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
    assert job['environment'] == 'shared-datasets-production'
    assert job['permissions'] == {'contents': 'read', 'id-token': 'write'}
    assert '${{ inputs.' not in job['steps'][-1]['run']


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
