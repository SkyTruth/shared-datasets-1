"""Replay and legacy resolution consume the immutable source caller graph."""
import copy
import json
import subprocess
from datetime import datetime, timezone
from unittest import mock

import pytest
import yaml

from scripts import slack_incidents as incidents, workflow_failure_alert as alerts
from test_slack_incidents import IDENTITY, MemoryLedger, FakeSlack

BASE = subprocess.check_output(['git', 'rev-parse', '7bd972e'], text=True).strip()


def source(filename):
    return yaml.safe_load(subprocess.check_output(['git', 'show', BASE + ':.github/workflows/' + filename], text=True))


@pytest.mark.parametrize('target,caller', [('wdpa-monthly','wdpa'), ('mpatlas-monthly','mpatlas'),
                                         ('eamlis-monthly','eamlis'), ('sea-ice-daily','sea-ice')])
@pytest.mark.parametrize('entrypoint', ['ci', 'manual'])
def test_replay_and_legacy_keep_historical_recovery_identity(target, caller, entrypoint):
    filename = target + '-deploy.yml'
    name = source(filename)['jobs']['deploy'].get('name', 'deploy')
    if entrypoint == 'ci':
        name = source('ci.yml')['jobs'][caller].get('name', caller) + ' / ' + name
        filename = 'ci.yml'
    repository = {'id': 17, 'full_name': incidents.REPOSITORY}
    run = {'id': 41701840527, 'run_attempt': 1, 'head_sha': BASE, 'workflow_id': 7,
           'name': alerts.WORKFLOW_NAMES[filename][0], 'path': '.github/workflows/' + filename,
           'head_branch': 'main', 'event': 'push' if entrypoint == 'ci' else 'workflow_run',
           'repository': repository, 'head_repository': repository, 'status': 'completed',
           'conclusion': 'failure', 'updated_at': '2026-11-03T10:00:00Z'}
    failure = {'id': 10, 'name': name, 'status': 'completed', 'conclusion': 'failure',
               'completed_at': run['updated_at']}
    jobs = [failure, {**failure, 'id': 11, 'name': 'ci-ready', 'conclusion': 'success'}] if entrypoint == 'ci' else [failure]
    api = mock.Mock()
    api.get.side_effect = lambda path: repository if path == 'repos/' + incidents.REPOSITORY else (
        {'id': 7, 'path': run['path']} if '/actions/workflows/' in path else copy.deepcopy(run))
    api.pages.side_effect = lambda path, **_: [run] if '/actions/runs?' in path else jobs
    observations = incidents.replay_observations(api, '2026-11-03T00:00:00Z',
                                              now=datetime(2026,11,4,tzinfo=timezone.utc))
    failed = [item for item in observations if item['conclusion'] == 'failure']
    assert len(failed) == 1 and failed[0]['scope'] == 'deployment:' + target
    recovery = {**failed[0], 'run_id': 41701840528, 'job_id': 0, 'conclusion': 'success',
                'time': '2026-11-03T11:00:00Z',
                'proof': {'record_id': 20, 'status_id': 21, 'artifact': 'image@sha256:' + 'f'*64}}
    ledger, slack = MemoryLedger(), FakeSlack()
    records = incidents.prepare(ledger, IDENTITY, failed, success=lambda *_: (None, None))
    incidents.deliver(ledger, records, slack)
    records = incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True, success=lambda *_: (recovery, None))
    incidents.deliver(ledger, records, slack)
    assert ledger.states['deployment:' + target]['incident']['resolution'] == recovery
    with mock.patch.dict(incidents.verified_recovery.__kwdefaults__, success=lambda *_: (recovery, None)), \
         mock.patch.object(incidents.deployments, 'git_ancestor', return_value=True):
        alerts.resolve_legacy(api, slack, run['id'], '1791270000.000001')
    assert 'Verified ' + target + ' recovery' in json.dumps(slack.calls[-1])


def test_source_graph_rejects_unknown_revision_and_unregistered_paths():
    with pytest.raises(incidents.IncidentError, match='revision'):
        incidents.job_targets(revision='main', workflow='ci.yml')
    with pytest.raises(subprocess.CalledProcessError):
        incidents.job_targets(revision='f'*40, workflow='ci.yml')
    targets = incidents.job_targets(revision=BASE, workflow='ci.yml')
    assert incidents.scope_for_job('unknown / Deploy tested WDPA ingestion image', targets, 'ci.yml')[1] == 'job'
