"""Automatic recovery dispatch, protected authority and independent outcomes."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from scripts import slack_incidents as incidents, workflow_failure_alert as alerts
from workflow_helpers import load_workflow, workflow_steps_by_name

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github/workflows'
REQUEST = 'Request automatic execution recovery'


def condition(expression, **context):
    program = "const {expression, context}=JSON.parse(require('fs').readFileSync(0,'utf8')); console.log(new Function('github','inputs',`return (${expression});`)(context.github,context.inputs));"
    return subprocess.check_output(['node', '-e', program],
        input=json.dumps({'expression': expression, 'context': context}), text=True).strip() == 'true'


def test_automatic_worker_is_protected_main_reconciler_without_ids():
    workflow = load_workflow(WORKFLOWS / 'resolve-workflow-alert.yml')
    auto = workflow['jobs']['automatic']
    assert auto['environment'] == 'shared-datasets-production'
    assert auto['permissions'] == {'contents': 'read', 'actions': 'read', 'deployments': 'read',
                                   'attestations': 'read', 'id-token': 'write'}
    auth = next(step for step in auto['steps'] if step.get('uses', '').startswith('google-github-actions/auth@'))
    assert auth['with']['service_account'] == 'shared-datasets-reconciler@shared-datasets-1.iam.gserviceaccount.com'
    assert auto['steps'][-1]['run'].endswith('scripts/monitoring_slack_reconcile.py --automatic')
    assert auto['concurrency'] == {'group': 'monitoring-execution-recovery', 'queue': 'max', 'cancel-in-progress': False}
    inputs = dict(automatic=True, monitoring_incident_id='', recovery_deployment_id='', run_id='', message_ts='')
    for actor in ('github-actions[bot]', 'someone', 'jonaraphael'):
        context = {'github': {'actor': actor, 'ref': 'refs/heads/main'}, 'inputs': inputs}
        assert condition(auto['if'], **context)
        for key in ('monitoring_incident_id', 'recovery_deployment_id', 'run_id', 'message_ts'):
            assert not condition(auto['if'], **{**context, 'inputs': {**inputs, key: '123'}})
        assert not condition(auto['if'], **{**context, 'github': {'actor': actor, 'ref': 'refs/heads/other'}})
    assert "github.actor == 'jonaraphael'" in workflow['jobs']['monitoring']['if']
    assert "github.actor == 'jonaraphael'" in workflow['jobs']['resolve']['if']
    assert '!inputs.automatic' in workflow['jobs']['resolve']['if']


@pytest.mark.parametrize('filename', ['ingestion-deploy.yml', 'deployment-verification.yml'])
def test_dispatch_follows_successful_signed_outcome_and_cannot_rewrite_it(filename):
    job = load_workflow(WORKFLOWS / filename)['jobs']['verify']
    steps = workflow_steps_by_name({'jobs': {'verify': job}}, 'verify')
    request, signature = steps[REQUEST], steps['Attest terminal deployment outcome' if filename == 'ingestion-deploy.yml' else 'Attest verified execution outcome']
    assert signature['id'] == 'terminal-receipt'
    assert job['steps'].index(request) > job['steps'].index(signature)
    assert request['timeout-minutes'] == 1
    assert request['continue-on-error'] is True
    assert 'success()' in request['if'] and "steps.terminal-receipt.outcome == 'success'" in request['if']
    assert job['permissions']['actions'] == 'write'
    assert 'gh workflow run resolve-workflow-alert.yml --ref main -f automatic=true' in request['run']
    if filename == 'ingestion-deploy.yml':
        assert "env.PLAN_GUARD != 'usage'" in request['if']
        assert '"${DEPLOYMENT_PHASE:-}" == verified' in request['run']
    else:
        assert "steps.deployment-outcome.outputs.receipt_path != ''" in signature['if']


def test_daily_request_runs_after_health_even_when_probe_fails_and_tolerates_dispatch_fault():
    workflow = load_workflow(WORKFLOWS / 'operational-safety-check.yml')
    job = workflow['jobs']['safety']
    steps = workflow_steps_by_name(workflow, 'safety')
    request = steps[REQUEST]
    assert request['timeout-minutes'] == 1
    assert 'always()' in request['if'] and "steps.trust.outcome == 'success'" in request['if']
    assert job['steps'].index(request) > job['steps'].index(steps['Retain safety health report'])
    assert job['steps'].index(request) > job['steps'].index(steps['Report safety failure directly'])
    assert request['continue-on-error'] is True


@pytest.mark.parametrize('phase,expected', [('verified', True), ('verification_pending', False), ('failed', False), ('applied', False), ('', False)])
def test_ingestion_request_executes_literal_main_route_only_for_verified_phase(tmp_path, phase, expected):
    capture = tmp_path / 'args'
    gh = tmp_path / 'gh'
    gh.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$CAPTURE"\n')
    gh.chmod(0o755)
    request = workflow_steps_by_name(load_workflow(WORKFLOWS / 'ingestion-deploy.yml'), 'verify')[REQUEST]
    subprocess.run(['bash', '-euo', 'pipefail', '-c', request['run']], check=True, env={**os.environ,
        'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'], 'DEPLOYMENT_PHASE': phase, 'CAPTURE': str(capture)})
    assert capture.exists() is expected
    if expected:
        assert capture.read_text().splitlines() == ['workflow', 'run', 'resolve-workflow-alert.yml', '--ref', 'main', '-f', 'automatic=true']


def test_automatic_resolver_faults_use_existing_alert_route_without_retrigger_loop():
    resolver = load_workflow(WORKFLOWS / 'resolve-workflow-alert.yml')
    router = load_workflow(WORKFLOWS / 'unattended-workflow-alert.yml')
    assert resolver['name'] in router.get('on', router.get(True))['workflow_run']['workflows']
    assert set(resolver.get('on', resolver.get(True))) == {'workflow_dispatch'}
    event = {'repository': {'full_name': alerts.REPOSITORY}, 'workflow_run': {'id': 123,
        'name': resolver['name'], 'event': 'workflow_dispatch', 'status': 'completed', 'conclusion': 'failure',
        'head_branch': 'main', 'head_repository': {'full_name': alerts.REPOSITORY}}}
    assert alerts.alert_for_run(event)
    # The existing ledger classifies this as an ordinary job incident, never a deployment.
    from unittest.mock import Mock
    api = Mock()
    api.pages.return_value = [{'id': 99, 'name': 'automatic', 'status': 'completed',
                              'conclusion': 'failure', 'completed_at': '2026-11-16T18:00:00Z'}]
    run = {**event['workflow_run'], 'run_attempt': 1, 'head_sha': 'a' * 40}
    observation = incidents.from_job(run, api.pages.return_value[0], incidents.job_targets())
    assert observation['kind'] == 'job' and observation['scope'].startswith('job:')
    assert json.loads(json.dumps(observation)) == observation


@pytest.mark.parametrize('manual', ['resolve', 'monitoring'])
def test_owner_dispatched_resolver_failures_stay_supervised(manual):
    from unittest.mock import Mock, patch
    resolver = load_workflow(WORKFLOWS / 'resolve-workflow-alert.yml')
    # The filter keys on job names; a rename must fail here, not silently alert or go quiet.
    assert set(resolver['jobs']) == {'resolve', 'monitoring', 'automatic'}
    assert all('name' not in job for job in resolver['jobs'].values())
    run = {'id': 123, 'run_attempt': 1, 'head_sha': 'a' * 40, 'name': resolver['name'], 'event': 'workflow_dispatch',
           'status': 'completed', 'conclusion': 'failure', 'updated_at': '2026-11-16T18:00:00Z',
           'head_branch': 'main', 'head_repository': {'full_name': alerts.REPOSITORY}}
    event = {'repository': {'full_name': alerts.REPOSITORY}, 'workflow_run': run}

    def jobs(failed):
        return [{'id': index, 'name': name, 'status': 'completed', 'completed_at': run['updated_at'],
                 'conclusion': 'failure' if name == failed else 'skipped'}
                for index, name in enumerate(('resolve', 'monitoring', 'automatic'), 1)]

    api = Mock()
    with patch.object(incidents.alerts, 'verify_source_event', return_value=True):
        # An owner-dispatched job failed: no alert and no ledger observation.
        api.pages.return_value = jobs(manual)
        assert alerts.alert_for_run(event, jobs=jobs(manual)) is None
        assert incidents.source_observations(api, event, {}) == []
        # The unattended sweep failed: exactly its own job incident.
        api.pages.return_value = jobs('automatic')
        assert alerts.alert_for_run(event, jobs=jobs('automatic')) is not None
        observed = incidents.source_observations(api, event, {})
        assert [(item['job_name'], item['conclusion']) for item in observed] == [('automatic', 'failure')]
        # A failed run with no failed job (startup failure) still reaches the workflow scope.
        api.pages.return_value = []
        assert alerts.alert_for_run(event, jobs=[]) is not None
        assert [item['kind'] for item in incidents.source_observations(api, event, {})] == ['workflow']
