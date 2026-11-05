"""No-op receipts and bounded read-only planning at their execution boundaries."""
from __future__ import annotations

import io
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import threading
import time
from types import SimpleNamespace
import urllib.error
from unittest.mock import Mock

import pytest

from scripts import deployment_emission as emission, deployment_revision as deployment
from scripts import pr_terraform_plan as planner
from scripts.deployment_receipt_contracts import ingestion_boundary
from test_deployment_emission import verified
from test_offline_deployment_lifecycle import journey as journey
from workflow_helpers import load_workflow, workflow_steps_by_name

ROOT = Path(__file__).resolve().parents[1]
REPO = planner.REPOSITORY
URL = 'https://policytroubleshooter.googleapis.com/v3/iam:troubleshoot'


@pytest.mark.parametrize('defect', [None, 'empty', 'digest', 'update', 'outputs', 'deferred', 'read', 'failed', 'missing', 'unsigned', 'live-error', 'execution-error'])
def test_signed_noop_preserves_runtime_proof_and_skips_apply_and_canary(journey, monkeypatch, defect):
    services, directory = journey
    image = 'us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/sea-ice-daily@sha256:' + 'a' * 64
    plan = {'resource_changes': [{'address': 'module.sea_ice_daily_job.google_cloud_run_v2_job.this',
                                 'change': {'actions': ['update' if defect == 'update' else 'read' if defect == 'read' else 'no-op']}}]}
    if defect == 'outputs':
        plan['output_changes'] = {'output': {'actions': ['update']}}
    if defect == 'deferred':
        plan['deferred_changes'] = [{'reason': 'unknown'}]
    if defect == 'empty':
        plan['resource_changes'] = []
    path = directory / 'plan.json'
    path.write_text(json.dumps(plan))
    execution = {'metadata': {'name': 'sea-ice-daily-abcde'},
                 'spec': {'template': {'spec': {'containers': [{'image': image}]}}},
                 'status': {'completionTime': 'now', 'succeededCount': 1}}
    if defect == 'failed':
        execution['status'] = {'completionTime': 'now', 'failedCount': 1}
    original = subprocess.check_output

    def external(command, **kwargs):
        if command[:4] == ['gcloud', 'run', 'jobs', 'describe']:
            if defect == 'live-error':
                raise subprocess.CalledProcessError(1, command)
            return (image if defect != 'digest' else image[:-1] + 'b') + '\n'
        if command[:5] == ['gcloud', 'run', 'jobs', 'executions', 'list']:
            if defect == 'execution-error':
                raise subprocess.CalledProcessError(1, command)
            return json.dumps([] if defect == 'missing' else [execution])
        return original(command, **kwargs)

    monkeypatch.setattr(subprocess, 'check_output', external)
    rehearsal = emission.emit(REPO, *emission.rehearsal(services, REPO))
    services.sign(rehearsal)
    args = SimpleNamespace(target='sea-ice-daily', workflow='sea-ice-daily-deploy.yml',
                           executor_sha=services.plan['tested_sha'], source_run_id=123, source_run_attempt=1,
                           artifact=image, plan_json=path)
    if defect in {'live-error', 'execution-error'}:
        with pytest.raises(subprocess.CalledProcessError):
            deployment.start(args, services)
        assert services.records == []
        return
    result = deployment.start(args, services)
    noop = defect in {None, 'unsigned'}
    assert result['noop'] == str(noop).lower()
    claim = directory / 'deployment-receipts' / 'record-71-status-81.json'
    if defect != 'unsigned':
        services.sign(claim)
    terminal = SimpleNamespace(deployment_id=71, phase='verified' if noop else 'applied',
                               execution=result.get('execution', ''))
    if defect == 'unsigned':
        with pytest.raises((AssertionError, deployment.DeploymentError)):
            deployment.finish(terminal, services)
        assert len(services.statuses) == 1
        return
    deployment.finish(terminal, services)
    services.sign(directory / 'deployment-receipts' / 'record-71-status-82.json')
    deployment.verify_record(services, REPO, services.records[0])
    deployment.verify_receipt(services, REPO, services.records[0], services.statuses[0])
    if noop:
        assert services.records[0]['payload']['noop_execution'] == execution['metadata']['name']
        assert services.records[0]['payload']['targets'] == [plan['resource_changes'][0]['address']]
        assert services.statuses[0]['environment_url'].endswith('/sea-ice-daily-abcde?project=shared-datasets-1')
        assert deployment.check(args, services)['proceed'] == 'false'
    steps = workflow_steps_by_name(load_workflow(ROOT / '.github/workflows/ingestion-deploy.yml'), 'apply')
    assert "steps.revision.outputs.noop != 'true'" in steps['Terraform apply']['if']
    assert "steps.claim-receipt.outcome == 'success'" in steps['Terraform apply']['if']
    assert "steps.revision.outputs.noop == 'true'" in steps['Record applied deployment']['run']
    verify = load_workflow(ROOT / '.github/workflows/ingestion-deploy.yml')['jobs']['verify']['if']
    assert "needs.apply.outputs.noop != 'true'" in verify and "needs.apply.result != 'success'" in verify


@pytest.mark.parametrize('error', [TimeoutError('private'), urllib.error.URLError('private'),
    *[urllib.error.HTTPError(URL, code, 'private', {}, None) for code in (500, 502, 503, 504)]])
def test_policy_reads_retry_transients_and_cache_success(monkeypatch, error):
    transport = Mock(side_effect=[error, io.BytesIO(b'{"ok":true}')])
    monkeypatch.setattr(planner.urllib.request, 'urlopen', transport)
    pause = Mock()
    monkeypatch.setattr(time, 'sleep', pause)
    reader = planner.PolicyReader('private')
    assert reader.read(URL, '{}') == reader.read(URL, '{}') == {'ok': True}
    assert transport.call_count == 2
    pause.assert_called_once_with(5)


@pytest.mark.parametrize('code', [400, 401, 403, 404, 408, 429, 503])
def test_policy_denial_diagnostics_name_resource_permission_and_bound_retries(monkeypatch, code):
    transport = Mock(side_effect=urllib.error.HTTPError(URL, code, 'private', {}, io.BytesIO(b'private')))
    monkeypatch.setattr(planner.urllib.request, 'urlopen', transport)
    pause = Mock()
    monkeypatch.setattr(time, 'sleep', pause)
    with pytest.raises(planner.PlanFailure) as error:
        planner.PolicyReader('private').verify([(planner.PROJECT_URL, ['run.jobs.update'])],
                                               'deployer@shared-datasets-1.iam.gserviceaccount.com', '123')
    assert f'HTTP {code}' in str(error.value) and 'run.jobs.update' in str(error.value)
    assert '//cloudresourcemanager.googleapis.com/projects/123' in str(error.value)
    assert 'private' not in str(error.value)
    assert transport.call_count == (3 if code == 503 else 1)
    assert [call.args for call in pause.call_args_list] == ([(5,), (10,)] if code == 503 else [])


def test_planner_runs_four_scopes_concurrently_with_private_isolated_files(tmp_path, monkeypatch):
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': {'head': {'repo': {'full_name': REPO}}, 'base': {'repo': {'full_name': REPO}}}}))
    for key, value in {'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_EVENT_PATH': str(event), 'RUNNER_TEMP': str(tmp_path),
                       'PRODUCTION_TERRAFORM_SERVICE_ACCOUNT': 'deployer@shared-datasets-1.iam.gserviceaccount.com'}.items():
        monkeypatch.setenv(key, value)
    scopes = [dict(name=f'scope-{i}', resources=['google_project_iam_custom_role.fixture'], exact={'google_project_iam_custom_role.fixture'},
                   patterns=[], variables={'fixture': i}, required=[], block_deletes=False, delete_only=False, job_image=None) for i in range(9)]
    monkeypatch.setattr(planner, 'scopes', lambda *args: scopes)
    monkeypatch.setattr(planner, 'deployed_variables', lambda state: {})
    monkeypatch.setattr(planner.PolicyReader, 'read', lambda *args: {'projectNumber': '123'})
    monkeypatch.setattr(planner.subprocess, 'check_output', lambda *args, **kwargs: 'token')
    lock, barrier, active, peak, paths = threading.Lock(), threading.Barrier(4), 0, 0, []

    def terraform(command, *arguments, **kwargs):
        nonlocal active, peak
        if command == 'version':
            return '{"terraform_version":"1.8.5"}'
        if command == 'init':
            return ''
        if command == 'show':
            return '{"format_version":"1.2","resource_changes":[]}'
        assert command == 'plan' and '-lock=false' in arguments
        path = Path(next(arg.removeprefix('-out=') for arg in arguments if arg.startswith('-out=')))
        variables = Path(next(arg.removeprefix('-var-file=') for arg in arguments if arg.startswith('-var-file=')))
        with lock:
            paths.append(path)
            active += 1
            peak = max(peak, active)
            first_batch = len(paths) <= 4
        if first_batch:
            barrier.wait(timeout=5)
        assert json.loads(variables.read_text())['fixture'] in range(9)
        path.write_text('{}')
        with lock:
            active -= 1
        return ''

    monkeypatch.setattr(planner, 'terraform', terraform)
    planner.run({'changed_paths': ['terraform/envs/prod/main.tf'], 'deployments': []})
    assert peak == 4 and len(set(paths)) == len(scopes)
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize('workflow,job', [('ingestion-schedule-control.yml', 'control'), ('release-index-rebuild.yml', 'rebuild')])
def test_jobs_without_ancestry_or_diff_reads_use_shallow_checkouts(workflow, job):
    steps = load_workflow(ROOT / '.github/workflows' / workflow)['jobs'][job]['steps']
    assert all(step.get('with', {}).get('fetch-depth', 1) == 1 for step in steps if step.get('uses', '').startswith('actions/checkout@'))


@pytest.mark.parametrize('workflow', ['ingestion-deploy.yml', 'catalog-viewer-deploy.yml', 'pmtiles-cdn-sync.yml',
    'ci.yml', 'pr-terraform-plan.yml', 'identity-decision-reconcile.yml', 'unattended-workflow-alert.yml', 'repo-functionality-alert.yml'])
def test_history_consumers_retain_full_checkout_history(workflow):
    jobs = load_workflow(ROOT / '.github/workflows' / workflow)['jobs']
    checkouts = [step for job in jobs.values() for step in job.get('steps', []) if step.get('uses', '').startswith('actions/checkout@')]
    assert checkouts and all(step.get('with', {}).get('fetch-depth') == 0 for step in checkouts)


@pytest.mark.parametrize('target', ['eamlis_monthly', 'mpatlas_monthly'])
def test_short_monthly_jobs_use_four_cpu_without_reducing_memory(target):
    from scripts.terraform_target_contracts import resource_index
    job = resource_index()[f'terraform/envs/prod/{target}.tf'][f'module.{target}_job']
    assert job['cpu'] == '4' and job['memory'] == {'eamlis_monthly': '4Gi', 'mpatlas_monthly': '8Gi'}[target]


@pytest.mark.parametrize('defect', [None, 'unsigned', 'unbound-noop', 'hidden-failure'])
def test_noop_contract_requires_exact_signed_output_and_keeps_failure_verification(defect):
    workflow = copy.deepcopy(load_workflow(ROOT / '.github/workflows/ingestion-deploy.yml'))
    jobs = workflow['jobs']
    if defect == 'unsigned':
        step = workflow_steps_by_name(workflow, 'apply')['Terraform apply']
        step['if'] = step['if'].replace(" && steps.claim-receipt.outcome == 'success'", '')
    if defect == 'unbound-noop':
        jobs['apply']['outputs']['noop'] = 'true'
    if defect == 'hidden-failure':
        jobs['verify']['if'] = jobs['verify']['if'].replace(" || needs.apply.result != 'success'", '')
    assert bool(ingestion_boundary(workflow)) == (defect is not None)


def test_apply_noop_cli_runs_in_clean_hosted_stdlib_interpreter(journey):
    services, directory = journey
    rehearsal = emission.emit(REPO, *emission.rehearsal(services, REPO))
    attestation = verified(json.loads(rehearsal.read_bytes()), emitter=services.run,
                           signer='.github/workflows/ingestion-deploy.yml')
    image = 'image@sha256:' + 'a' * 64
    execution = {'metadata': {'name': 'sea-ice-daily-abcde'},
                 'spec': {'template': {'spec': {'containers': [{'image': image}]}}},
                 'status': {'completionTime': 'now', 'succeededCount': 1}}
    data = directory / 'services.json'
    data.write_text(json.dumps({'run': services.run, 'attestation': attestation, 'execution': execution, 'image': image}))
    plan = directory / 'no-change.json'
    plan.write_text(json.dumps({'format_version': '1.2', 'resource_changes': [{
        'address': 'module.sea_ice_daily_job.google_cloud_run_v2_job.this', 'change': {'actions': ['no-op']}}]}))
    driver = directory / 'hosted.py'
    driver.write_text(textwrap.dedent('''\
        import json, os, pathlib, subprocess, sys
        sys.path.insert(0, sys.argv.pop(1))
        from scripts import deployment_revision as d
        data = json.loads(pathlib.Path(sys.argv.pop(1)).read_text())
        class API:
            def get(self, path):
                if path == 'repos/SkyTruth/shared-datasets-1':
                    return data['run']['repository']
                if '/actions/workflows/' in path:
                    return {'id': data['run']['workflow_id'], 'path': data['run']['path']}
                return data['run']
            def pages(self, path, field=None):
                return [{'name':'ci-ready', 'status':'completed', 'conclusion':'success'}] if field == 'jobs' else []
            def post(self, path, value):
                return dict(value, id=71, sha=value['ref']) if path.endswith('/deployments') else dict(value, id=81)
        original_run, original_output = subprocess.run, subprocess.check_output
        def run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0) if command[:2] == ['git', 'merge-base'] else original_run(command, **kwargs)
        def output(command, **kwargs):
            if command[:4] == ['gcloud','run','jobs','describe']:
                return data['image'] + '\\n'
            if command[:5] == ['gcloud','run','jobs','executions','list']:
                return json.dumps([data['execution']])
            return original_output(command, **kwargs)
        subprocess.run, subprocess.check_output = run, output
        d.GitHub = API
        d.emissions.verified_output = lambda *args: json.dumps(data['attestation'])
        d.main()
        assert 'yaml' not in sys.modules and 'hcl2' not in sys.modules
    '''))
    output = directory / 'hosted-output'
    result = subprocess.run([sys.executable, '-I', '-S', str(driver), str(ROOT), str(data), 'start', '--target', 'sea-ice-daily',
        '--workflow', 'sea-ice-daily-deploy.yml', '--executor-sha', services.run['head_sha'], '--source-run-id', '123',
        '--source-run-attempt', '1', '--artifact', image, '--plan-json', str(plan)], cwd=ROOT,
        env={**os.environ, 'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'noop=true' in output.read_text() and 'execution=sea-ice-daily-abcde' in output.read_text()


def test_pr_planner_cli_in_declared_locked_hosted_environment(tmp_path):
    workflow = load_workflow(ROOT / '.github/workflows/pr-terraform-plan.yml')['jobs']['plan']['steps']
    sync = next(step for step in workflow if step.get('run', '').startswith('uv sync'))
    assert sync['run'] == 'uv sync --locked'
    # Default sync installs project + dev, exactly the environment pytest uses;
    # -I removes PYTHONPATH/user-site influence and executes scope discovery too.
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps({'changed_paths': ['terraform/envs/prod/eamlis_monthly.tf'],
                                'deployments': ['eamlis'], 'base': subprocess.check_output(
                                    ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}))
    output = tmp_path / 'output'
    result = subprocess.run([sys.executable, '-I', str(ROOT / 'scripts/pr_terraform_plan.py'), 'select', '--plan', str(path)],
                            cwd=ROOT, env={**os.environ, 'GITHUB_OUTPUT': str(output)}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert output.read_text() == 'pr_terraform=true\n'
    path.write_text(json.dumps({'changed_paths': ['terraform/envs/prod/pr_plan_iam.tf'], 'deployments': []}))
    binary = tmp_path / 'bin'
    binary.mkdir()
    terraform = binary / 'terraform'
    terraform.write_text(f'#!{sys.executable}\n' + textwrap.dedent('''\
        import json, pathlib, sys
        command = sys.argv[2]
        if command == 'version':
            print('{"terraform_version":"1.8.5"}')
        elif command == 'show':
            print('{"format_version":"1.2","resource_changes":[]}' if sys.argv[-1].endswith('.tfplan') else '{"values":{"root_module":{}}}')
        elif command == 'plan':
            output = pathlib.Path(next(arg.removeprefix('-out=') for arg in sys.argv if arg.startswith('-out=')))
            output.write_text('private-plan')
        else:
            assert command == 'init'
    '''))
    terraform.chmod(0o755)
    gcloud = binary / 'gcloud'
    gcloud.write_text('#!/bin/sh\nprintf "%s\\n" fixture-token\n')
    gcloud.chmod(0o755)
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': {'head': {'repo': {'full_name': REPO}}, 'base': {'repo': {'full_name': REPO}}}}))
    driver = tmp_path / 'planner.py'
    driver.write_text(textwrap.dedent('''\
        import io, json, runpy, sys, urllib.request
        def transport(request, **kwargs):
            return io.BytesIO(json.dumps({'projectNumber':'123', 'overallAccessState':'CAN_ACCESS',
                'allowPolicyExplanation': {'allowAccessState':'ALLOW_ACCESS_STATE_GRANTED'}}).encode())
        urllib.request.urlopen = transport
        sys.argv.pop(0)
        runpy.run_path(sys.argv[0], run_name='__main__')
    '''))
    result = subprocess.run([sys.executable, '-I', str(driver), str(ROOT / 'scripts/pr_terraform_plan.py'), 'run', '--plan', str(path)],
                            cwd=ROOT, env={**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                                'GITHUB_EVENT_NAME': 'pull_request', 'GITHUB_EVENT_PATH': str(event), 'RUNNER_TEMP': str(tmp_path),
                                'PRODUCTION_TERRAFORM_SERVICE_ACCOUNT': 'deployer@shared-datasets-1.iam.gserviceaccount.com'},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.glob('pr-terraform-*'))
