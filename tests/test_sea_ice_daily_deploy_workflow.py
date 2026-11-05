"""The shared canary retains identity before polling and resumes only after validation."""
import json

import pytest
from test_wdpa_monthly_deploy_workflow import REPO_ROOT, run_shell, shared_step, shared_workflow


@pytest.mark.parametrize('target', ['wdpa-monthly', 'mpatlas-monthly', 'eamlis-monthly', 'sea-ice-daily'])
@pytest.mark.parametrize('date', ['', '2026-10-01', 'invalid; exit 0'])
def test_canary_date_is_one_argument_and_execution_is_recorded_before_watch(tmp_path, target, date):
    result = run_shell(tmp_path, shared_step('verify', 'Execute ingestion canary')['run'], target=target, extra={
        'CANARY_RUN_DATE': date, 'ARGS': str(tmp_path / 'args'), 'GITHUB_SERVER_URL': 'https://github.com',
        'GITHUB_REPOSITORY': 'SkyTruth/shared-datasets-1', 'GITHUB_RUN_ID': '123',
    }, prefix='gcloud() { printf "%s\\n" "$@" > "$ARGS"; echo canary-123; }\n')
    if date.startswith('invalid'):
        assert result.returncode != 0
        assert not (tmp_path / 'args').exists()
        return
    assert result.returncode == 0, result.stderr
    args = (tmp_path / 'args').read_text().splitlines()
    assert args[:4] == ['run', 'jobs', 'execute', target]
    assert '--async' in args
    overrides = [a for a in args if a.startswith('--update-env-vars=')]
    assert ('RUN_DATE=2026-10-01' in ''.join(overrides)) == bool(date)
    assert ('SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL=' in ''.join(overrides)) == (target != 'wdpa-monthly')
    assert (tmp_path / 'environment').read_text() == 'CANARY_EXECUTION=canary-123\n'


@pytest.mark.parametrize('target', ['eamlis-monthly', 'sea-ice-daily'])
@pytest.mark.parametrize('status,wrong_image,phase,success', [
    ({'succeededCount': 1, 'completionTime': 'now'}, False, 'verified', True),
    ({'succeededCount': 1}, False, None, False), ({'cancelledCount': 1}, False, 'failed', False),
    ({'failedCount': 1}, False, 'failed', False), ({'completionTime': 'now'}, False, 'unknown', False),
    ({'succeededCount': 1, 'completionTime': 'now'}, True, None, False),
])
def test_short_canary_requires_terminal_success_of_exact_digest(tmp_path, target, status, wrong_image, phase, success):
    image = 'registry/job@sha256:' + 'a' * 64
    payload = {'spec': {'template': {'spec': {'containers': [{'image': 'wrong' if wrong_image else image}]}}}, 'status': status}
    result = run_shell(tmp_path, shared_step('verify', 'Watch ingestion canary')['run'], target=target,
                       extra={'IMAGE': image, 'CANARY_EXECUTION': 'canary-123', 'EXECUTION_JSON': json.dumps(payload)},
                       prefix='gcloud() { echo "$EXECUTION_JSON"; }\nsleep() { :; }\n')
    assert (result.returncode == 0) == success, result.stderr
    output = tmp_path / 'environment'
    assert (output.read_text() if output.exists() else '') == (f'DEPLOYMENT_PHASE={phase}\n' if phase else '')


@pytest.mark.parametrize('running,cancel,inventory_ok,success', [('', 'false', True, True), ('old-canary 1', 'false', True, False), ('old-canary 1', 'true', True, True), ('', 'false', False, False)])
def test_active_canary_is_never_cancelled_without_explicit_opt_in(tmp_path, running, cancel, inventory_ok, success):
    result = run_shell(tmp_path, shared_step('verify', 'Resolve in-flight canary')['run'], extra={
        'RUNNING': running, 'CANCEL_RUNNING_CANARY': cancel, 'INVENTORY_EXIT': '0' if inventory_ok else '1',
        'CALLS': str(tmp_path / 'calls'),
    }, prefix='''gcloud() {
  echo "$*" >> "$CALLS"
  if [[ "$4" == list ]]; then echo "$RUNNING"; return "$INVENTORY_EXIT"; fi
}
''')
    assert (result.returncode == 0) == success, result.stderr
    assert ('executions cancel old-canary' in (tmp_path / 'calls').read_text()) == (bool(running) and cancel == 'true')


def test_canary_validation_and_hygiene_precede_scheduler_resume(tmp_path):
    names = [s.get('name') for s in shared_workflow()['jobs']['verify']['steps']]
    order = ['Require successful signed apply', 'Confirm deployed digest', 'Execute ingestion canary', 'Watch ingestion canary',
             'Validate latest release contract', 'Run asset bucket hygiene audit', 'Resume sea-ice-daily scheduler', 'Record deployment outcome']
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    resume = shared_step('verify', 'Resume sea-ice-daily scheduler')
    assert resume['if'] == "inputs.target == 'sea-ice-daily' && inputs.resume_scheduler"
    for published in (True, False):
        calls = tmp_path / 'calls'
        calls.write_text('')
        result = run_shell(tmp_path, resume['run'], target='sea-ice-daily', extra={'PUBLISHED': '0' if published else '1', 'CALLS': str(calls)}, prefix='''
uv() { return "$PUBLISHED"; }
gcloud() { echo "$*" >> "$CALLS"; }
''')
        assert (result.returncode == 0) == published
        assert ('scheduler jobs resume' in calls.read_text()) == published
    assert 'COPY catalog/feature-identity-resolutions ./catalog/feature-identity-resolutions' in (REPO_ROOT / 'ingestion/sea_ice_daily/Dockerfile').read_text()
