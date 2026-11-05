"""Recovery consumes executor data through trusted code and read-only identity."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from scripts import deployment_revision as deployment
from terraform_helpers import load_terraform, resources

ROOT = Path(__file__).resolve().parents[1]


def recovery_steps():
    return yaml.safe_load((ROOT / '.github/workflows/deployment-recovery.yml').read_text())['jobs']['reconcile']['steps']


def test_recovery_preserves_trusted_checkout_and_never_authenticates_as_deployer():
    steps = recovery_steps()
    checkouts = [s['with'] for s in steps if s.get('uses') == 'actions/checkout@v4']
    assert len(checkouts) == 2
    assert checkouts[0]['ref'] == '${{ github.workflow_sha }}'
    assert checkouts[1] == {'ref': '${{ steps.original.outputs.executor_sha }}', 'path': 'original-executor',
                            'fetch-depth': 0, 'persist-credentials': False}
    auth = [s for s in steps if s.get('uses') == 'google-github-actions/auth@v2']
    assert len(auth) == 1
    assert auth[0]['with']['service_account'] == 'shared-datasets-observer@shared-datasets-1.iam.gserviceaccount.com'
    assert auth[0]['with']['workload_identity_provider'] == '${{ vars.GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER }}'
    assert steps.index(auth[0]) > next(i for i, s in enumerate(steps) if s.get('id') == 'original')


def test_recovery_grants_reuse_readonly_roles_and_only_the_exact_default_state():
    rows = {f'{kind}.{name}': body for path in (ROOT / 'terraform/envs/prod').glob('*.tf')
            for kind, name, body in resources(load_terraform(path))}
    metadata = rows['google_project_iam_member.recovery_reader']
    state = rows['google_storage_bucket_iam_member.recovery_state_reader']
    assert metadata['member'] == state['member'] == '${google_service_account.github_job["observer"].member}'
    assert metadata['role'] == '${google_project_iam_custom_role.pr_plan_reader.name}'
    assert state['role'] == '${google_project_iam_custom_role.pr_plan_state_reader.name}'
    assert state['condition'][0]['expression'] == rows['google_storage_bucket_iam_member.pr_plan_state_reader']['condition'][0]['expression']
    assert rows['google_project_iam_custom_role.pr_plan_state_reader']['permissions'] == ['storage.objects.get', 'storage.objects.list']
    assert all(p.endswith(('.get', '.list', '.getIamPolicy', '.getAttestationRules', '.use'))
               for p in rows['google_project_iam_custom_role.pr_plan_reader']['permissions'])
    policy = json.loads((ROOT / 'terraform/envs/prod/github-workflow-identities.json').read_text())
    assert 'deployment-recovery.yml' in policy['observer']
    assert 'deployment-recovery.yml' not in policy['terraform']
    registry = json.loads((ROOT / '.github/terraform-targets.json').read_text())
    assert {'google_project_iam_member.recovery_reader', 'google_storage_bucket_iam_member.recovery_state_reader',
            'google_project_iam_custom_role.pr_plan_reader', 'google_project_iam_custom_role.pr_plan_state_reader'} <= set(registry['targets']['GitHub workflow identity sync']['resources'])


def test_refresh_uses_original_config_trusted_retry_and_a_lock_free_refreshed_plan(tmp_path):
    workspace = tmp_path / 'trusted checkout'
    original = workspace / 'original-executor'
    (original / 'terraform/envs/prod').mkdir(parents=True)
    (original / 'scripts').mkdir()
    (original / 'scripts/terraform_retry.sh').write_text('exit 99\n')
    (workspace / 'scripts').symlink_to(ROOT / 'scripts', target_is_directory=True)
    binary = tmp_path / 'bin/terraform'
    binary.parent.mkdir()
    log = tmp_path / 'terraform-calls'
    binary.write_text(f'#!{sys.executable}\nimport json,os,sys\n'
                      'with open(os.environ["CALLS"],"a") as out: out.write(json.dumps(sys.argv[1:])+"\\n")\n'
                      'if "show" in sys.argv: print("{\\"resource_changes\\": []}")\n')
    binary.chmod(0o755)
    refresh = next(s['run'] for s in recovery_steps() if s.get('name') == 'Refresh original target scope without applying')
    environment = {**os.environ, 'PATH': str(binary.parent) + os.pathsep + os.environ['PATH'],
                   'GITHUB_WORKSPACE': str(workspace), 'RUNNER_TEMP': str(tmp_path), 'CALLS': str(log),
                   'TARGETS_JSON': '["module.reviewed"]', 'IMAGE_VARIABLES': '{"job_image":"image@sha256:abc"}',
                   'VIRTUAL_ENV': str(tmp_path / '.venv-mismatch')}
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', refresh], cwd=workspace, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(call[0] == '-chdir=original-executor/terraform/envs/prod' for call in calls)
    assert len(calls) == 3
    assert '-lockfile=readonly' in calls[0]
    assert {'plan', '-lock=false', '-refresh=true', '-target=module.reviewed', '-var=job_image=image@sha256:abc'} <= set(calls[1])
    assert 'apply' not in str(calls)
    assert json.loads((tmp_path / 'reconciliation.json').read_text()) == {'resource_changes': []}


@pytest.mark.parametrize('kind', ['sdk', 'terraform', 'ingestion'])
def test_real_reconcile_step_runs_trusted_policy_with_separate_executor_data(tmp_path, kind):
    workspace = tmp_path / 'trusted checkout'
    original = workspace / 'original-executor'
    original.mkdir(parents=True)
    (workspace / 'scripts').symlink_to(ROOT / 'scripts', target_is_directory=True)
    (original / 'scripts').mkdir()
    (original / 'scripts/deployment_revision.py').write_text('raise RuntimeError("executor code ran")\n')
    subprocess.run(['git', 'init', '-q', str(original)], check=True)
    subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                    '-C', str(original), 'commit', '--allow-empty', '-qm', 'Executor fixture'], check=True)
    sha = subprocess.check_output(['git', '-C', str(original), 'rev-parse', 'HEAD'], text=True).strip()
    target = 'typescript-sdk' if kind == 'sdk' else next(iter(deployment.TERRAFORM_WORKFLOWS)) if kind == 'terraform' else 'sea-ice-daily'
    image = 'image@sha256:' + 'a' * 64
    claim = {'id': 1, 'sha': sha, 'ref': sha, 'environment': 'production-' + target,
             'creator': {'login': 'github-actions[bot]', 'type': 'Bot'},
             'payload': {'schema': deployment.SCHEMA, 'target': target, 'artifact': image,
                         'ci_run_id': 1, 'ci_run_attempt': 1, 'execution_run_id': 1, 'execution_run_attempt': 1}}
    (tmp_path / 'reconciliation.json').write_text('{"resource_changes": []}')
    harness = tmp_path / 'trusted-harness.py'
    harness.write_text('import json,os,pathlib,runpy,sys\n'
                       f'assert pathlib.Path.cwd() == pathlib.Path({str(workspace)!r})\n'
                       'args=sys.argv[1:]; args=[a for a in args if a not in ("-I","-S")]\n'
                       'script=pathlib.Path(args.pop(0)).resolve()\n'
                       f'assert script == pathlib.Path({str(ROOT / "scripts/deployment_revision.py")!r})\n'
                       'module=runpy.run_path(str(script)); scope=module["recover"].__globals__\n'
                       f'claim={claim!r}\n'
                       'class API:\n'
                       ' def pages(self,*args): return [claim]\n'
                       ' def post(self,*args): return {"id":2}\n'
                       'scope["GitHub"]=API\n'
                       'scope["recovery_record"]=lambda *args: (claim,claim["payload"])\n'
                       'scope["post_status"]=lambda api,repo,record,status: pathlib.Path(os.environ["RESULT"]).write_text(json.dumps(status))\n'
                       f'def sdk(record,payload,root):\n assert root == pathlib.Path({str(original)!r})\n'
                       'scope["reconcile_sdk"]=sdk\n'
                       'sys.argv=[str(script),*args]; module["main"]()\n')
    execution = {'metadata': {'name': 'execution', 'creationTimestamp': '2026-11-05T00:00:00Z'},
                 'spec': {'template': {'spec': {'containers': [{'image': image}]}}},
                 'status': {'completionTime': '2026-11-05T00:01:00Z', 'conditions': [{'type': 'Completed', 'status': 'True'}], 'succeededCount': 1}}
    binary = tmp_path / 'bin/gcloud'
    binary.parent.mkdir()
    job = {'spec': {'template': {'spec': {'template': execution['spec']['template']}}}}
    binary.write_text(f'#!{sys.executable}\nimport json,sys\nprint(json.dumps({[execution]!r} if "executions" in sys.argv else {job!r}))\n')
    binary.chmod(0o755)
    step = next(s['run'] for s in recovery_steps() if s.get('id') == 'deployment-outcome')
    wrapper = 'python() { "$PYTHON_BIN" -I -S "$HARNESS" "$@"; }\n'
    environment = {**os.environ, 'PATH': str(binary.parent) + os.pathsep + os.environ['PATH'],
                   'GITHUB_WORKSPACE': str(workspace), 'RUNNER_TEMP': str(tmp_path), 'DEPLOYMENT_ID': '1',
                   'KIND': kind, 'GITHUB_REPOSITORY': 'fixture/recovery', 'RESULT': str(tmp_path / 'result'),
                   'HARNESS': str(harness), 'PYTHON_BIN': sys.executable, 'VIRTUAL_ENV': str(tmp_path / '.venv-mismatch')}
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', wrapper + step], cwd=workspace, env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads((tmp_path / 'result').read_text())['description'] == ('applied' if kind == 'terraform' else 'verified')


def test_schedule_runbook_uses_protected_control_for_every_registered_publisher():
    from scripts.ingestion_targets import TARGETS
    text = (ROOT / '.claude/skills/deploy-scheduled-ingestion/references/job-operations.md').read_text()
    assert 'gcloud scheduler jobs pause' not in text
    assert 'ingestion-schedule-control.yml' in text
    assert all(target in text for target in TARGETS)
