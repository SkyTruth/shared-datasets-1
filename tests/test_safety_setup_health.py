"""Setup outages and missing heartbeat series use the retained health contract."""
import json
import base64
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import zipfile
from unittest import mock

import pytest

from scripts import incident_health_artifacts as native, operational_safety_check as safety, slack_notify, workflow_failure_alert as alerts
from test_operational_safety_check import NOW, OperationalAPI
from workflow_helpers import load_workflow, workflow_steps_by_name


@pytest.mark.parametrize('failure', ['dependencies', 'verifier', 'authentication'])
def test_setup_outages_deduplicate_changes_and_daily_reminders_without_probe_dependencies(tmp_path, monkeypatch, no_ambient_credentials, failure):
    monkeypatch.setattr('scripts.catalog_drift_guard.get_client', lambda: None)
    monkeypatch.setattr(safety, 'freshness', lambda *_args, **_kwargs: [])
    api = OperationalAPI()
    monkeypatch.setattr(safety, 'GitHub', lambda: api)
    monkeypatch.setattr(safety.alerts, 'context', lambda *_a, **_k: api.current)
    monkeypatch.setattr(safety, 'audit_freshness', lambda *_a, **_k: [])
    monkeypatch.setattr(safety, 'policy_warnings', lambda _: ([], []))
    monkeypatch.setenv('SAFETY_PROBE_OUTCOME', 'skipped')
    monkeypatch.setenv('SAFETY_SETUP_OUTCOMES', failure + '=failure')
    monkeypatch.setattr(sys, 'argv', ['safety', '--health', '--work-dir', str(tmp_path)])
    monkeypatch.setattr(safety, 'isolated_setup_failure', lambda *_: False)  # Outages after an earlier failed run.
    previous = {'problems': [], 'warnings': [], 'notified_at': None}
    with mock.patch.object(slack_notify, 'notify') as notify, mock.patch.object(safety, 'heartbeat_series') as heartbeat:
        for hour, count in [(0, 1), (3, 1), (24, 2)]:
            class Clock(type(NOW)):
                @classmethod
                def now(cls, _zone):
                    from datetime import timedelta
                    return NOW + timedelta(hours=hour)
            monkeypatch.setattr(safety, 'datetime', Clock)
            monkeypatch.setattr(safety, 'previous_health', lambda *_: previous)
            safety.main()
            previous = json.loads((tmp_path / 'health/cache.json').read_bytes())
            assert notify.call_count == count
            assert any(failure + '=failure' in item for item in previous['problems'])
            assert previous['scope'] == safety.HEALTH_SCOPE
        monkeypatch.setenv('SAFETY_SETUP_OUTCOMES', 'different setup=failure')
        safety.main()
        assert notify.call_count == 3  # A changed setup cause is immediately visible.
        heartbeat.assert_not_called()


def test_health_entrypoint_and_imports_work_without_site_packages_and_fallback_defers_to_retained_report(tmp_path):
    workflow = load_workflow(safety.ROOT / '.github/workflows/operational-safety-check.yml')
    steps = workflow_steps_by_name(workflow, 'safety')
    command = steps['Check freshness and stalled operations']['run']
    assert '"$interpreter" scripts/operational_safety_check.py --health' in command
    assert 'uv ' not in command
    for name in ('Install locked probe dependencies', 'Install pinned durable receipt verifier', 'Authenticate heartbeat writer and catalog reader'):
        identifier = steps[name]['id']
        assert 'steps.' + identifier + '.outcome' in steps['Check freshness and stalled operations']['env']['SAFETY_SETUP_OUTCOMES']
    result = subprocess.run([sys.executable, '-S', str(Path(safety.__file__)), '--help'], cwd=tmp_path,
                            env={**os.environ, 'PYTHONPATH': ''}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    fallback = steps['Report safety failure directly']['if']
    assert "steps.probe.outcome == 'success'" in fallback
    assert "steps.health.outcome != 'success'" in fallback and "steps.health-report.outcome != 'success'" in fallback


@pytest.mark.parametrize('dependencies', [False, True])
def test_health_workflow_uses_synced_interpreter_when_present_and_bootstrap_python_otherwise(tmp_path, dependencies):
    command = workflow_steps_by_name(load_workflow(safety.ROOT / '.github/workflows/operational-safety-check.yml'), 'safety')['Check freshness and stalled operations']['run']
    for relative, label in [('bin/python', 'bootstrap'), ('.venv/bin/python', 'synced')]:
        if label == 'synced' and not dependencies:
            continue
        executable = tmp_path / relative
        executable.parent.mkdir(parents=True)
        executable.write_text('#!/bin/sh\nprintf "' + label + '%s\\n" "$*" > "$CAPTURE"\n')
        executable.chmod(0o755)
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', command], cwd=tmp_path, capture_output=True, text=True,
                            env={**os.environ, 'PATH': str(tmp_path / 'bin') + os.pathsep + os.environ['PATH'],
                                 'CAPTURE': str(tmp_path / 'capture'), 'RUNNER_TEMP': str(tmp_path)})
    assert result.returncode == 0, result.stderr
    assert (tmp_path / 'capture').read_text().startswith(('synced' if dependencies else 'bootstrap') + 'scripts/operational_safety_check.py --health')


@pytest.mark.parametrize('probe,health,upload,direct', [('skipped', 'success', 'success', False),
    ('failure', 'success', 'success', False), ('success', 'success', 'success', True),
    ('skipped', 'failure', 'success', True), ('failure', 'success', 'failure', True)])
def test_direct_fallback_keeps_dispatch_and_delivery_failure_coverage(probe, health, upload, direct):
    steps = workflow_steps_by_name(load_workflow(safety.ROOT / '.github/workflows/operational-safety-check.yml'), 'safety')
    expression = steps['Report safety failure directly']['if'].removeprefix('${{').removesuffix('}}').replace('steps.health-report', 'steps["health-report"]')
    result = subprocess.run(['node', '-e', 'const {expression,steps}=JSON.parse(process.argv[1]); '
        'console.log(new Function("steps","failure","cancelled",`return (${expression})`)(steps,()=>true,()=>false))',
        json.dumps({'expression': expression, 'steps': {'trust': {'outcome': 'success'}, 'probe': {'outcome': probe},
            'health': {'outcome': health}, 'health-report': {'outcome': upload}}})], check=True, capture_output=True, text=True)
    assert json.loads(result.stdout) is direct


HEALTH_RUNNER = r'''
import io,json,os,runpy,sys,urllib.request
class Response(io.BytesIO):
    status=200
    def getcode(self): return 200
def webhook(request, **kwargs):
    assert request.full_url == 'https://example.invalid/webhook'
    with open(os.environ['MESSAGES'],'a') as stream: stream.write(request.data.decode()+'\n')
    return Response(b'ok')
urllib.request.urlopen=webhook
sys.argv=[os.environ['SAFETY_SCRIPT'],'--health','--work-dir',os.environ['HEALTH_DIR']]
runpy.run_path(sys.argv[0],run_name='__main__')
'''


def run_health_cli(tmp_path, api, identifier, cause, prior):
    """Run the real health CLI with no site packages against gh JSON/ZIP routes.

    The prior run, when given, publishes the current health/cache.json as its artifact.
    Returns the completed process, this run's source record and the webhook messages sent so far.
    """
    executable = tmp_path / 'gh'
    executable.write_text('#!' + sys.executable + '\nimport base64,json,os,sys\n'
                          'routes=json.load(open(os.environ["RESPONSES"]))\n'
                          'key=sys.argv[-1].split("?",1)[0]\nvalue=routes[key]\n'
                          'if isinstance(value,dict) and "binary" in value: sys.stdout.buffer.write(base64.b64decode(value["binary"]))\n'
                          'else: print(json.dumps([value] if "--slurp" in sys.argv else value))\n')
    executable.chmod(0o755)
    responses, messages = tmp_path / 'responses.json', tmp_path / 'messages.jsonl'
    current = api.source(identifier, 'operational-safety-check.yml', status='in_progress', conclusion=None)
    prefix = 'repos/' + safety.REPOSITORY
    routes = {prefix: api.repository,
              prefix + '/actions/runs/' + str(identifier): current,
              prefix + '/actions/runs/' + str(identifier) + '/attempts/1': current,
              prefix + '/actions/workflows/operational-safety-check.yml/runs': {'total_count': int(prior is not None), 'workflow_runs': [prior] if prior else []}}
    for filename, definition in api.definitions.items():
        routes[prefix + '/actions/workflows/' + filename] = definition
        routes[prefix + '/actions/workflows/' + str(definition['id'])] = definition
        if filename != 'operational-safety-check.yml':
            routes[prefix + '/actions/workflows/' + filename + '/runs'] = {'total_count': 0, 'workflow_runs': []}
    routes[prefix + '/actions/workflows/scratch-cleanup-audit.yml'] = {'id': 18, 'path': '.github/workflows/scratch-cleanup-audit.yml'}
    routes[prefix + '/actions/workflows/scratch-cleanup-audit.yml/runs'] = {'total_count': 0, 'workflow_runs': []}
    if prior:
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            archive.writestr('cache.json', (tmp_path / 'health/cache.json').read_bytes())
        raw = output.getvalue()
        routes[prefix + '/actions/runs/' + str(prior['id'])] = prior
        routes[prefix + '/actions/runs/' + str(prior['id']) + '/attempts/1/jobs'] = {'total_count': 1, 'jobs': [
            {'name': safety.JOB, 'started_at': prior['created_at'], 'completed_at': prior['updated_at'],
             'steps': [{'name': 'Retain safety health report', 'status': 'completed', 'conclusion': 'success'}]}]}
        routes[prefix + '/actions/runs/' + str(prior['id']) + '/artifacts'] = {'total_count': 1, 'artifacts': [
            {'id': 9, 'name': f"safety-health-{prior['id']}-1", 'expired': False, 'size_in_bytes': len(raw),
             'created_at': prior['updated_at'], 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
             'workflow_run': {'id': prior['id'], 'head_sha': prior['head_sha'], 'head_branch': 'main'}}]}
        routes[prefix + '/actions/artifacts/9/zip'] = {'binary': base64.b64encode(raw).decode()}
    responses.write_text(json.dumps(routes))
    result = subprocess.run([sys.executable, '-S', '-c', HEALTH_RUNNER], cwd=tmp_path, text=True, capture_output=True,
        env={**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'], 'PYTHONPATH': '',
             'GITHUB_REPOSITORY': safety.REPOSITORY, 'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ID': str(identifier),
             'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_WORKFLOW_REF': safety.REPOSITORY + '/' + safety.WORKFLOW + '@refs/heads/main',
             'SAFETY_PROBE_OUTCOME': 'skipped', 'SAFETY_SETUP_OUTCOMES': cause, 'RESPONSES': str(responses),
             'SAFETY_SCRIPT': safety.__file__, 'HEALTH_DIR': str(tmp_path), 'MESSAGES': str(messages),
             'SHARED_DATASETS_SLACK_WEBHOOK_URL': 'https://example.invalid/webhook'})
    sent = len(messages.read_text().splitlines()) if messages.exists() else 0
    return result, current, sent


def test_dependency_free_health_cli_restores_authenticated_failed_setup_report_before_resending(tmp_path):
    """The real CLI traverses gh JSON/ZIP and webhook bytes, with no site packages."""
    api = OperationalAPI()
    prior = None
    for identifier, cause, sends in ((300, 'dependencies=failure', 1), (301, 'dependencies=failure', 1), (302, 'authentication=failure', 2)):
        result, current, sent = run_health_cli(tmp_path, api, identifier, cause, prior)
        assert result.returncode == 0, result.stderr
        assert sent == sends
        report = json.loads((tmp_path / 'health/cache.json').read_bytes())
        assert any(cause in problem for problem in report['problems'])
        prior = {**current, 'status': 'completed', 'conclusion': 'failure'}


def test_setup_outage_after_a_successful_run_stays_quiet_until_the_next_run_also_fails(tmp_path):
    """Release CDN 504s clear within minutes; a second consecutive setup failure still alerts."""
    api = OperationalAPI()
    (tmp_path / 'health').mkdir()
    healthy = native.canonical({'scope': safety.HEALTH_SCOPE, 'problems': ['retained finding'], 'warnings': [],
                                'notified_at': '2026-05-01T00:00:00+00:00'})
    (tmp_path / 'health/cache.json').write_bytes(healthy)
    succeeded = {**api.source(299, 'operational-safety-check.yml'), 'conclusion': 'success'}
    result, first, sent = run_health_cli(tmp_path, api, 300, 'verifier=failure', succeeded)
    assert result.returncode == 0, result.stderr
    assert sent == 0
    assert 'Slack waits for a second consecutive failure' in result.stdout
    assert (tmp_path / 'health/cache.json').read_bytes() == healthy  # The last delivered state carries forward.
    result, _, sent = run_health_cli(tmp_path, api, 301, 'verifier=failure', {**first, 'status': 'completed', 'conclusion': 'failure'})
    assert result.returncode == 0, result.stderr
    assert sent == 1
    report = json.loads((tmp_path / 'health/cache.json').read_bytes())
    assert any('verifier=failure' in problem for problem in report['problems'])


@pytest.mark.parametrize('prior', [None, {'conclusion': 'cancelled'}, {'conclusion': 'failure'}, {'conclusion': 'success', 'event': 'push'}])
def test_setup_outage_alerts_at_once_without_a_trusted_successful_previous_run(prior, monkeypatch):
    api = OperationalAPI()
    rows = [] if prior is None else [{**api.source(299, 'operational-safety-check.yml'), **prior}]
    monkeypatch.setattr(safety, 'prior_runs', lambda *_: rows)
    monkeypatch.setattr(safety.alerts, 'verify_source_event', lambda *_: True)
    assert safety.isolated_setup_failure(api, api.current) is False


def test_isolated_setup_failure_requires_authenticated_previous_success(monkeypatch):
    api = OperationalAPI()
    row = {**api.source(299, 'operational-safety-check.yml'), 'conclusion': 'success'}
    monkeypatch.setattr(safety, 'prior_runs', lambda *_: [row])
    for verified, quiet in ((True, True), (False, False)):
        monkeypatch.setattr(safety.alerts, 'verify_source_event', lambda *_: verified)
        assert safety.isolated_setup_failure(api, api.current) is quiet
    monkeypatch.setattr(safety.alerts, 'verify_source_event', mock.Mock(side_effect=ValueError('forged run')))
    with pytest.raises(ValueError, match='forged run'):
        safety.isolated_setup_failure(api, api.current)  # The health step fails, so the direct fallback alerts.


def test_safety_toolchain_cache_matches_setup_pins_and_precedes_every_download():
    from scripts import install_deployment_verifier as installer
    from scripts.ci_toolchain import TOOLCHAIN
    workflow = load_workflow(safety.ROOT / '.github/workflows/operational-safety-check.yml')
    steps = {step.get('name') or step['uses']: step for step in workflow['jobs']['safety']['steps']}
    names = list(steps)
    cache, key = steps['Restore pinned toolchain downloads'], steps['Select toolchain cache key']['run']
    assert cache['uses'] == 'actions/cache@v4'
    assert names.index('Restore pinned toolchain downloads') < min(names.index('actions/setup-python@v5'), names.index('astral-sh/setup-uv@v6'),
                                                                  names.index('Install pinned durable receipt verifier'))
    python = steps['actions/setup-python@v5']['with']['python-version']
    uv = steps['astral-sh/setup-uv@v6']['with']['version']
    assert (python, uv) == (TOOLCHAIN['python'], TOOLCHAIN['uv'])
    assert cache['with']['path'].splitlines() == ['${{ runner.tool_cache }}/Python/' + python, '${{ runner.tool_cache }}/uv/' + uv,
                                             '${{ runner.temp }}/deployment-receipts/tools/' + installer.ASSET]
    assert cache['with']['key'] == '${{ steps.toolchain.outputs.key }}'
    for part in ('${ImageOS}', '${RUNNER_ARCH}', 'python' + python, 'uv' + uv, 'gh' + installer.VERSION):
        assert part in key


@pytest.mark.parametrize('failed_setup', ['Install locked probe dependencies', 'Install pinned durable receipt verifier', 'Authenticate read-only catalog probe', 'Authenticate heartbeat writer and catalog reader'])
@pytest.mark.parametrize('broken_boundary', [None, 'health', 'upload', 'dispatch'])
def test_retained_setup_reports_own_replay_but_never_hide_delivery_or_dispatch_failure(failed_setup, broken_boundary):
    steps = [{'name': failed_setup, 'conclusion': 'failure'}, {'name': 'Check operational work', 'conclusion': 'skipped'},
             {'name': 'Check freshness and stalled operations', 'conclusion': 'failure' if broken_boundary == 'health' else 'success'},
             {'name': 'Retain safety health report', 'conclusion': 'failure' if broken_boundary == 'upload' else 'success'}]
    if broken_boundary == 'dispatch':
        steps.append({'name': 'Request catalog refresh', 'conclusion': 'failure'})
    assert alerts.safety_health_only({'name': 'Operations / Safety check'}, [{'steps': steps}]) is (broken_boundary is None)


@pytest.mark.parametrize('series', [[], [{'metric': {'type': 'logging.googleapis.com/user/operational-safety-heartbeat'}}]])
def test_heartbeat_acceptance_reads_metric_time_series_instead_of_assuming_log_write_establishes_it(series):
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    with mock.patch.object(google.auth, 'default', return_value=(object(), None)), mock.patch.object(AuthorizedSession, 'get') as get:
        get.return_value.json.return_value = {'timeSeries': series}
        findings = safety.heartbeat_series(NOW)
        assert bool(findings) is (not series)
        assert get.call_args.args[0] == 'https://monitoring.googleapis.com/v3/projects/shared-datasets-1/timeSeries'
        query = get.call_args.kwargs['params']
        assert query['view'] == 'HEADERS' and query['pageSize'] == 1
        assert 'operational-safety-heartbeat' in query['filter']
        assert 'resource.type="global"' in query['filter']
        assert query['interval.startTime'] < query['interval.endTime']
        get.return_value.raise_for_status.assert_called_once()


def test_heartbeat_read_failure_cannot_claim_established_monitoring_and_iam_grants_only_read():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    import hcl2
    with mock.patch.object(google.auth, 'default', return_value=(object(), None)), mock.patch.object(AuthorizedSession, 'get') as get:
        get.return_value.raise_for_status.side_effect = RuntimeError('Monitoring denied')
        with pytest.raises(RuntimeError, match='Monitoring denied'):
            safety.heartbeat_series(NOW)
    config = hcl2.loads((safety.ROOT / 'terraform/envs/prod/monitoring.tf').read_text(),
                       serialization_options=hcl2.utils.SerializationOptions(strip_string_quotes=True))
    role = next(group['google_project_iam_custom_role']['operational_safety_reader'] for group in config['resource']
                if 'operational_safety_reader' in group.get('google_project_iam_custom_role', {}))
    assert role['permissions'] == ['logging.logEntries.create']
    from terraform_helpers import load_terraform, evaluate
    values = load_terraform(safety.ROOT / 'terraform/envs/prod/github_job_identities.tf')['locals'][0]
    values['github_workflow_identities'] = json.loads((safety.ROOT / 'terraform/envs/prod/github-workflow-identities.json').read_text())
    permissions = evaluate(values, 'local.github_job_permissions', variables={'github_repository': 'SkyTruth/shared-datasets-1'})['heartbeat']
    assert {'cloudscheduler.jobs.get', 'run.executions.get', 'monitoring.timeSeries.list'} <= set(permissions)
    assert all(permission.endswith(('.get', '.list')) for permission in permissions)


@pytest.mark.parametrize('series', [None, True, 'present', {'metric': 'forged'}])
def test_malformed_heartbeat_series_never_establishes_absence_detection(series):
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    with mock.patch.object(google.auth, 'default', return_value=(object(), None)), mock.patch.object(AuthorizedSession, 'get') as get:
        get.return_value.json.return_value = {'timeSeries': series}
        with pytest.raises(RuntimeError, match='invalid heartbeat time-series'):
            safety.heartbeat_series(NOW)
