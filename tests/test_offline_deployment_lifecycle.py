"""One serialized deployment journey; only remote services/CLIs are substituted."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from scripts import ci_contract as ci, deployment_emission as emission, deployment_revision as deployment
from scripts import dataset_usage_deploy as usage
from scripts.ingestion_targets import DEPLOY_TARGETS, environment as target_environment
from scripts import deployment_permissions as permissions, slack_incidents as incidents
from scripts import terraform_plan_allowlist as allowlist, terraform_plan_permissions as plan_permissions
from scripts import tested_image_authorization as images, tested_image_bundle as bundle
from test_deployment_emission import verified
from test_tested_image_handoff import docker_tar, zipped
from workflow_helpers import load_workflow, workflow_steps_by_name

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'SkyTruth/shared-datasets-1'
SIGNER = '.github/workflows/ingestion-deploy.yml'


def wire(value):
    return json.loads(json.dumps(value))


class Services:
    """GitHub persistence, artifact transport, Docker, IAM and attestation service."""
    def __init__(self, plan, image, archive, target):
        self.target = target
        self.plan, self.image = plan, image
        self.run = {'id': 123, 'run_attempt': 1, 'workflow_id': 7, 'path': '.github/workflows/ci.yml',
                    'head_sha': plan['tested_sha'], 'event': 'push', 'head_branch': 'main', 'status': 'in_progress',
                    'repository': {'id': 17, 'full_name': REPOSITORY}, 'head_repository': {'id': 17, 'full_name': REPOSITORY},
                    'referenced_workflows': [{'path': REPOSITORY + '/' + SIGNER + '@refs/heads/main',
                                             'sha': plan['tested_sha'], 'ref': 'refs/heads/main'}]}
        self.jobs = [{'name': name, 'status': 'completed', 'conclusion': 'success', 'started_at': '2026-11-05T01:00:00Z',
                      'completed_at': '2026-11-05T01:01:00Z'} for name in ('ci-ready', 'geospatial-changes', 'production-images (' + self.target + ')')]
        manifest = {'schema_version': 1, 'tested_sha': plan['tested_sha'], 'images': {self.target: image}}
        result = {**plan, 'suite': 'production-images', 'image_target': self.target, 'status': 'success',
                  'tools': ci.expected_tools('production-images'), 'commands': [{'argv': ['fixture-image-probe'], 'exit_code': 0}],
                  'production_images': manifest}
        evidence = {**plan, 'status': 'success', 'plan_artifact': 'ci-validation-plan-attempt1',
                    'suite_artifacts': {'production-images-' + self.target: 'ci-result-production-images-' + self.target + '-attempt1'}}
        self.payloads = [zipped({'plan.json': plan}), zipped({'evidence.json': evidence}),
                         zipped({'result.json': result, 'images/manifest.json': manifest}), zipped({image['archive']: archive})]
        names = ['ci-validation-plan-attempt1', 'ci-ready-evidence-attempt1',
                 'ci-result-production-images-' + self.target + '-attempt1', 'ci-image-' + self.target + '-attempt1']
        self.artifacts = [{'id': index, 'name': name, 'expired': False, 'workflow_run': {'id': 123},
                           'size_in_bytes': len(raw), 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest(),
                           'created_at': '2026-11-05T01:00:30Z'} for index, (name, raw) in enumerate(zip(names, self.payloads), 1)]
        self.records, self.statuses, self.signed = [], [], {}
        self.bad_signature = False
        self.calls = []

    def get(self, path):
        if path == 'repos/' + REPOSITORY:
            return wire(self.run['repository'])
        if '/actions/workflows/' in path:
            return {'id': 7, 'path': self.run['path']}
        if '/actions/runs/' in path:
            return wire(self.run)
        if '/deployments/' in path:
            return wire(self.records[0])
        raise AssertionError(path)

    def pages(self, path, field=None):
        if '/jobs?' in path:
            value = self.jobs
        elif '/artifacts?' in path:
            value = self.artifacts
        elif '/statuses?' in path:
            value = self.statuses
        else:
            assert 'deployments?environment=production-' + self.target in path
            value = self.records
        return wire(value)

    def archive(self, repository, identifier):
        assert repository == REPOSITORY
        return self.payloads[identifier - 1]

    def archive_to(self, repository, identifier, path):
        path.write_bytes(self.archive(repository, identifier))

    def post(self, path, value):
        self.calls.append(('POST', path))
        if path.endswith('/deployments'):
            record = {**wire(value), 'id': 71, 'sha': value['ref'], 'creator': {'login': 'github-actions[bot]', 'type': 'Bot'}}
            self.records.append(record)
            return wire(record)
        status = {**wire(value), 'id': 81 + len(self.statuses), 'created_at': '2026-11-05T01:05:00Z'}
        self.statuses.insert(0, status)
        return wire(status)

    def sign(self, path):
        value = json.loads(path.read_bytes())
        self.signed[hashlib.sha256(emission.canonical(value)).hexdigest()] = value

    def crypto(self, command, *_):
        value = json.loads(Path(command[3]).read_bytes())
        digest = hashlib.sha256(emission.canonical(value)).hexdigest()
        assert digest in self.signed, 'fixture refused an unsigned subject'
        result = verified(self.signed[digest], emitter=self.run, signer=SIGNER)
        if self.bad_signature:
            result[0]['verificationResult']['signature']['certificate']['buildSignerDigest'] = '0' * 40
        return json.dumps(result)


@pytest.fixture
def journey(tmp_path, monkeypatch, target):
    config = DEPLOY_TARGETS[target]
    source = "ingestion/" + target.replace("-", "_") + "/run.py"
    assert (ROOT / source).is_file()
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    selection = ci.resolve_selection([source], ingestion_holds=())
    plan = {'schema_version': 1, 'base': sha, 'head': sha, 'tested_sha': sha,
            'tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=ROOT, text=True).strip(),
            'contract_digest': ci.contract_digest(ROOT), 'changed_paths': [source],
            'source': {'run_id': '123', 'run_attempt': 1}, **selection}
    ci.write_json(tmp_path / 'ci-plan.json', plan)
    plan = json.loads((tmp_path / 'ci-plan.json').read_bytes())
    assert config['selection'] in selection['deployments'] and target in plan['production_image_targets']
    raw, image = docker_tar(target, tag=bundle.source_tag(target, sha))
    image['source_tag'] = bundle.source_tag(target, sha)
    services = Services(plan, image, raw, target)
    original_run, original_output = subprocess.run, subprocess.check_output

    def external_run(command, **kwargs):
        if command[0] == 'docker' or command[:2] == ['git', 'merge-base']:
            services.calls.append(tuple(command))
            return subprocess.CompletedProcess(command, 0)
        return original_run(command, **kwargs)

    def external_output(command, **kwargs):
        if command[:3] == ['docker', 'image', 'inspect']:
            return json.dumps([{'Id': image['config_digest'], 'Os': 'linux', 'Architecture': 'amd64', 'RepoTags': [image['source_tag']]}]).encode()
        if command == ['gh', '--version']:
            return 'gh version ' + incidents.verifier.VERSION + ' (fixture)\n'
        return original_output(command, **kwargs)

    monkeypatch.setattr(subprocess, 'run', external_run)
    monkeypatch.setattr(subprocess, 'check_output', external_output)
    monkeypatch.setattr(emission, 'verified_output', services.crypto)
    for key, value in {'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ID': '123',
                       'GITHUB_RUN_ATTEMPT': '1', 'RUNNER_TEMP': str(tmp_path), 'GITHUB_OUTPUT': str(tmp_path / 'outputs')}.items():
        monkeypatch.setenv(key, value)
    return services, tmp_path


@pytest.mark.parametrize('target,defect', [(target, defect) for target in DEPLOY_TARGETS
    for defect in [None, 'gate', 'selection', 'image', 'allowlist', 'permission', 'claim', 'apply', 'verify', 'pending', 'terminal', 'history-race',
                   *(['activation'] if target == 'dataset-usage' else [])]])
def test_emitted_selection_through_retained_image_saved_plan_signed_apply_and_terminal_receipt(journey, monkeypatch, target, defect):
    config = DEPLOY_TARGETS[target]
    services, directory = journey
    sha = services.plan['tested_sha']
    if defect == 'gate':
        services.jobs[0]['conclusion'] = 'failure'
    if defect == 'selection':
        services.payloads[0] = zipped({'plan.json': {**services.plan, 'production_image_targets': []}})
        services.artifacts[0]['digest'] = 'sha256:' + hashlib.sha256(services.payloads[0]).hexdigest()
    if defect == 'image':
        services.payloads[-1] += b'substituted bytes'
    if defect in {'gate', 'selection', 'image'}:
        with pytest.raises(deployment.DeploymentError):
            images.download(services, REPOSITORY, 123, 1, sha, target, directory / 'loaded', ROOT)
        assert services.records == [] and not any(call[0] == 'docker' for call in services.calls)
        return
    retained = images.download(services, REPOSITORY, 123, 1, sha, target, directory / 'loaded', ROOT)
    assert any(call[:3] == ('docker', 'image', 'load') for call in services.calls)
    image = 'us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/' + target + '@' + retained['config_digest']
    address = "module." + target.replace("-", "_") + "_job.google_cloud_run_v2_job.this"
    value = {'project': 'shared-datasets-1', 'location': 'us-central1', 'name': target,
             'template': [{'template': [{'service_account': target + '@shared-datasets-1.iam.gserviceaccount.com',
                                         'containers': [{'image': image}]}]}]}
    task = value['template'][0]['template'][0]
    if config['plan_guard'] == 'wdpa':
        task.update(timeout='43200s', volumes=[{'name': 'work', 'empty_dir': [{'medium': 'DISK', 'size_limit': '100Gi'}]}])
        task['containers'][0].update(resources=[{'limits': {'cpu': '4', 'memory': '8Gi'}}],
            volume_mounts=[{'name': 'work', 'mount_path': '/work'}], env=[
                {'name': 'TMPDIR', 'value': '/work/tmp'}, {'name': 'SHARED_DATASETS_WORKDIR', 'value': '/work/shared-datasets-1'},
                {'name': 'WDPA_BUILD_IMAGE', 'value': image},
                {'name': 'WDPA_BUILD_IMAGE_CONFIG_DIGEST', 'value': retained['config_digest']}])
    if target == 'dataset-usage':
        task.update(timeout='1800s', max_retries=0)
        task['containers'][0]['resources'] = [{'limits': {'cpu': '1', 'memory': '1Gi'}}]
    plan = {'format_version': '1.2', 'resource_changes': [{'address': address, 'type': 'google_cloud_run_v2_job',
            'mode': 'managed', 'change': {'actions': ['update'], 'before': value, 'after': value}}]}
    if target == 'dataset-usage':
        verified = usage.validate_rollout(ROOT)
        plan['resource_changes'].append({'address': 'module.dataset_usage_scheduler.google_cloud_scheduler_job.this',
            'type': 'google_cloud_scheduler_job', 'mode': 'managed',
            'change': {'actions': ['update'], 'before': {'project': 'shared-datasets-1', 'region': 'us-central1', 'name': target},
                'after': {'project': 'shared-datasets-1', 'region': 'us-central1', 'name': target,
                'paused': False if defect == 'activation' else not verified,
                'schedule': '0 9 * * *', 'time_zone': 'UTC', 'retry_config': [{'retry_count': 0}]}}})
    if defect == 'allowlist':
        plan['resource_changes'][0]['address'] = 'google_storage_bucket.unrelated'
    plan_path = directory / 'ingestion.tfplan.json'
    plan_path.write_text(json.dumps(plan))
    if target == 'dataset-usage':
        if defect in {'allowlist', 'activation'}:
            with pytest.raises(ValueError):
                usage.validate_plan(json.loads(plan_path.read_bytes()), image, verified=verified)
            assert services.records == []
            return
        usage.validate_plan(json.loads(plan_path.read_bytes()), image, verified=verified)
    else:
        refused = allowlist.main([str(plan_path), '--job-image-target', target, '--expected-image', image,
                                 '--expected-config', retained['config_digest'], '--block-deletes', '--refusal-prefix', 'REFUSED'])
        if defect == 'allowlist':
            assert refused != 0 and services.records == []
            return
        assert refused == 0
    required = plan_permissions.plan_checks(json.loads(plan_path.read_bytes()), project_number='123456789', target=target)
    probes = []
    def iam(url, needed):
        probes.append((url, needed))
        return ['run.jobs.update'] if defect == 'permission' and 'run.jobs.update' in needed else []
    if defect == 'permission':
        with pytest.raises(RuntimeError, match='not ready'):
            permissions.verify_checks(required, iam, attempts=1)
        assert services.records == []
        return
    permissions.verify_checks(required, iam, attempts=1)
    assert any('run.jobs.update' in needed for _, needed in probes)
    # The signing service accepts only explicit emitted subjects, including rehearsal.
    rehearsal = emission.emit(REPOSITORY, *emission.rehearsal(services, REPOSITORY))
    services.sign(rehearsal)
    args = SimpleNamespace(workflow=target + '-deploy.yml', target=target, executor_sha=sha, source_run_id=123,
                           source_run_attempt=1, artifact=image, plan_json=plan_path)
    result = deployment.start(args, services)
    args.deployment_id = int(result['deployment_id'])
    claim = next(path for path in (directory / 'deployment-receipts').glob('*.json') if json.loads(path.read_bytes())['environment'] != 'receipt-rehearsal')
    services.sign(claim)
    services.bad_signature = defect == 'claim'
    if defect == 'claim':
        with pytest.raises(deployment.DeploymentError, match='exact record, phase and protected emitter'):
            deployment.verify_record(services, REPOSITORY, services.records[0])
        assert len(services.statuses) == 1
        return
    deployment.verify_record(services, REPOSITORY, services.records[0])
    # Execute the real saved-plan apply shell with an external Terraform service double.
    terraform = directory / 'terraform'
    terraform.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$APPLY_ARGS"\nexit ' + ('1' if defect == 'apply' else '0') + '\n')
    terraform.chmod(0o755)
    workflow = load_workflow(ROOT / '.github/workflows/ingestion-deploy.yml')
    apply_step = workflow_steps_by_name(workflow, 'apply')['Terraform apply']
    assert "steps.claim-receipt.outcome == 'success'" in apply_step['if']
    saved = directory / 'ingestion.tfplan'
    saved.write_bytes(b'opaque saved provider plan')
    applied = subprocess.run(['bash', '-euo', 'pipefail', '-c', apply_step['run']], cwd=ROOT,
                             env={**os.environ, 'GITHUB_WORKSPACE': str(ROOT), 'TERRAFORM_BIN': str(terraform),
                                  'TMPDIR': str(directory), 'APPLY_ARGS': str(directory / 'apply-args')}, capture_output=True)
    assert (directory / 'apply-args').read_text().splitlines() == ['-chdir=terraform/envs/prod', 'apply', '-input=false', str(saved)]
    execution = {'spec': {'template': {'spec': {'containers': [{'image': image}]}}},
                 'status': {'completionTime': '2026-11-05T01:05:00Z', 'succeededCount': 1}}
    if defect == 'verify':
        execution['status'].update(succeededCount=0, failedCount=1)
    if defect == 'pending':
        execution['status'].pop('completionTime')
    execution_path = directory / 'execution.json'
    execution_path.write_text(json.dumps(execution))
    gcloud = directory / 'gcloud'
    gcloud.write_text('#!/bin/sh\ncat "$EXECUTION_JSON"\n')
    gcloud.chmod(0o755)
    watch = workflow_steps_by_name(workflow, 'verify')['Watch ingestion canary']
    environment = directory / 'canary-env'
    watched = subprocess.run(['bash', '-euo', 'pipefail', '-c', 'sleep() { :; }\n' + watch['run']], cwd=ROOT,
        env={**os.environ, 'PATH': str(directory) + os.pathsep + str(Path(os.sys.executable).parent) + os.pathsep + os.environ['PATH'],
             'IMAGE': image, 'CANARY_EXECUTION': target + '-abcde', 'REGION': 'us-central1',
             'GOOGLE_CLOUD_PROJECT': 'shared-datasets-1', 'EXECUTION_JSON': str(execution_path), 'GITHUB_ENV': str(environment),
             **{key: str(value).lower() if isinstance(value, bool) else str(value)
                for key, value in target_environment(config).items()}}, capture_output=True, text=True)
    args.phase = 'failed' if applied.returncode or watched.returncode else environment.read_text().strip().split('=', 1)[1]
    args.execution = target + '-abcde'
    deployment.finish(args, services)
    terminal = next(path for path in (directory / 'deployment-receipts').glob('*.json') if json.loads(path.read_bytes())['status']['description'] == args.phase)
    services.sign(terminal)
    if defect == 'terminal':
        services.statuses[0]['environment_url'] += '&substituted=true'
        with pytest.raises(AssertionError, match='unsigned subject'):
            incidents.terminal_success(services, 'deployment:' + target)
        return
    if defect == 'history-race':
        original_crypto = services.crypto
        def racing_crypto(command, *other):
            response = original_crypto(command, *other)
            if json.loads(Path(command[3]).read_bytes())['status']['description'] == 'verified':
                services.statuses.insert(0, {**services.statuses[0], 'id': 99, 'state': 'failure', 'description': 'failed'})
            return response
        monkeypatch.setattr(emission, 'verified_output', racing_crypto)
    healthy, memo = incidents.terminal_success(services, 'deployment:' + target)
    if defect in {'apply', 'verify', 'pending', 'history-race'}:
        assert (healthy, memo) == (None, None)
    else:
        assert healthy['proof']['artifact'] == image and healthy['proof']['status_id'] == services.statuses[0]['id']
        assert memo['evidence_sha256'] == incidents.terminal_evidence_sha256(services.records[0], services.statuses)
    expected = 'verification_pending' if defect == 'pending' and config['canary_mode'] == 'deferred' else (
        'failed' if defect in {'apply', 'verify', 'pending'} else 'verified')
    assert args.phase == expected
