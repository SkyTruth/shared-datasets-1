"""Paused schedules and terminal deployment records use the shared workflow."""
import pytest
from test_wdpa_monthly_deploy_workflow import run_shell, shared_step


@pytest.mark.parametrize('target', ['eamlis-monthly', 'wdpa-monthly'])
@pytest.mark.parametrize('state,date,ready,success', [
    ('PAUSED', '', 'false', True), ('PAUSED', '2026-11-01', 'true', True),
    ('ENABLED', '', 'true', True), ('', '', None, False), ('UPDATE_FAILED', '', None, False),
])
def test_paused_schedule_defers_before_mutation_and_requires_known_state(tmp_path, target, state, date, ready, success):
    result = run_shell(tmp_path, shared_step('prepare', 'Check runtime verification window')['run'], target=target,
                       extra={'TEST_STATE': state, 'CANARY_RUN_DATE': date}, prefix='''
gcloud() { echo "$TEST_STATE"; }
python() {
  if [[ "$1" == -c ]]; then command python "$@"; else echo ready=true >> "$GITHUB_OUTPUT"; fi
}
''')
    assert (result.returncode == 0) == success, result.stderr
    output = tmp_path / 'outputs'
    assert (output.read_text() if output.exists() else '') == (f'ready={ready}\n' if ready is not None else '')


@pytest.mark.parametrize('job_status,phase,execution,expected', [
    ('success', 'verified', 'projects/p/jobs/job/executions/canary-1', 'verified'),
    ('failure', 'verified', 'canary-2', 'failed'), ('cancelled', '', '', 'failed'),
    ('success', 'verification_pending', 'wdpa-1', 'verification_pending'), ('success', '', '', 'unknown'),
])
def test_record_outcome_retains_actual_execution_and_never_promotes_failure(tmp_path, job_status, phase, execution, expected):
    result = run_shell(tmp_path, shared_step('verify', 'Record deployment outcome')['run'], extra={
        'JOB_STATUS': job_status, 'DEPLOYMENT_PHASE': phase, 'CANARY_EXECUTION': execution,
        'DEPLOYMENT_ID': '42', 'ARGS': str(tmp_path / 'args'),
    }, prefix='python() { printf "%s\\n" "$@" > "$ARGS"; }\n')
    assert result.returncode == 0, result.stderr
    args = (tmp_path / 'args').read_text().splitlines()
    assert args[:4] == ['scripts/deployment_revision.py', 'finish', '--deployment-id', '42']
    assert args[4:6] == ['--phase', expected]
    assert args[6:] == (['--execution', execution.rsplit('/', 1)[-1]] if execution else [])
    assert 'always()' in shared_step('verify', 'Record deployment outcome')['if']
    assert 'always()' in shared_step('verify', 'Attest terminal deployment outcome')['if']


@pytest.mark.parametrize('apply_exit', [0, 1])
def test_apply_failure_propagates_without_claiming_runtime_success(tmp_path, apply_exit):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/terraform_retry.sh').write_text('exit "$TEST_EXIT"\n')
    result = run_shell(tmp_path, shared_step('apply', 'Terraform apply')['run'], extra={
        'GITHUB_WORKSPACE': str(tmp_path), 'TEST_EXIT': str(apply_exit),
    })
    assert result.returncode == apply_exit
    assert not (tmp_path / 'environment').exists()
