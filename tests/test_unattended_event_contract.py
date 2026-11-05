"""Declared producers/events cover every automatic workflow dispatch."""
import json
import re

import pytest

from scripts import slack_incidents as incidents, workflow_failure_alert as alerts
from test_failure_alert_routing import ROOT, event, source_api
from workflow_helpers import load_workflow, workflow_triggers


def test_every_workflow_dispatch_producer_has_an_alert_and_replay_route():
    edges = set()
    subscriptions = workflow_triggers(load_workflow(ROOT / '.github/workflows/unattended-workflow-alert.yml'))['workflow_run']['workflows']
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        text = path.read_text()
        targets = re.findall(r'gh\s+workflow\s+run\s+([\w-]+\.yml)', text)
        targets += re.findall(r'actions/workflows/([\w-]+\.yml)/dispatches', text)
        for target in targets:
            edges.add((path.name, target))
            assert 'workflow_dispatch' in workflow_triggers(load_workflow(path.parent / target))
            for name in alerts.WORKFLOW_NAMES[target]:
                payload = event(name=name, event='workflow_dispatch', run_attempt=1, head_sha='a' * 40)
                assert alerts.alert_for_run(payload) is not None, (path.name, target, name)
                # Safety and incident workers deliberately avoid native self-recursion;
                # the safety replay owns those completions instead.
                assert name in subscriptions or target in {'operational-safety-check.yml', 'unattended-workflow-alert.yml'}
                api = source_api(payload['workflow_run'])
                api.pages.return_value = []  # Even startup failures without jobs are covered.
                payload['workflow_run']['updated_at'] = '2026-11-05T01:00:00Z'
                assert incidents.source_observations(api, json.loads(json.dumps(payload)), {})
    assert ('ci.yml', 'publish-typescript-sdk.yml') in edges
    assert len(edges) == 4


@pytest.mark.parametrize('name', alerts.WORKFLOW_NAMES['publish-typescript-sdk.yml'])
@pytest.mark.parametrize('defect', [None, 'fork', 'branch', 'path', 'obsolete', 'pull_request'])
def test_sdk_dispatch_reaches_exact_job_scope_and_rejects_untrusted_sources(name, defect):
    payload = event(name=name, event='workflow_dispatch', run_attempt=1, head_sha='a' * 40)
    updates = {'path': '.github/workflows/ci.yml'} if defect == 'path' else {'run_attempt': 2} if defect == 'obsolete' else {}
    if defect == 'fork':
        updates['head_repository'] = {'id': 42, 'full_name': 'other/fork'}
    if defect == 'branch':
        updates['head_branch'] = 'feature'
    if defect == 'pull_request':
        payload['workflow_run']['event'] = 'pull_request'
    api = source_api(payload['workflow_run'], **updates)
    api.pages.return_value = [{'id': 456, 'name': 'candidate', 'status': 'completed', 'conclusion': 'failure',
                              'completed_at': '2026-11-05T01:00:00Z'}]
    if defect in {'fork', 'branch', 'path'}:
        with pytest.raises(ValueError, match='trusted main source'):
            incidents.source_observations(api, payload, {})
    else:
        observed = incidents.source_observations(api, payload, {})
        assert bool(observed) is (defect is None)
        if observed:
            assert observed[0]['kind'] == 'job' and observed[0]['job_id'] == 456
            assert observed[0]['scope'] != 'deployment:typescript-sdk'
