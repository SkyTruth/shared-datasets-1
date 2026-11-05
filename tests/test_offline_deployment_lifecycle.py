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
from scripts import deployment_permissions as permissions, slack_incidents as incidents
from scripts import terraform_plan_allowlist as allowlist, terraform_plan_permissions as plan_permissions
from scripts import tested_image_authorization as images, tested_image_bundle as bundle
from test_deployment_emission import verified
from test_tested_image_handoff import docker_tar, zipped
from workflow_helpers import load_workflow, workflow_steps_by_name

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = 'SkyTruth/shared-datasets-1'
TARGET = 'sea-ice-daily'
SIGNER = '.github/workflows/ingestion-deploy.yml'


def wire(value):
    return json.loads(json.dumps(value))


class Services:
    """GitHub persistence, artifact transport, Docker, IAM and attestation service."""
    def __init__(self, plan, image, archive):
        self.plan, self.image = plan, image
        self.run = {'id': 123, 'run_attempt': 1, 'workflow_id': 7, 'path': '.github/workflows/ci.yml',
                    'head_sha': plan['tested_sha'], 'event': 'push', 'head_branch': 'main', 'status': 'in_progress',
                    'repository': {'id': 17, 'full_name': REPOSITORY}, 'head_repository': {'id': 17, 'full_name': REPOSITORY},
                    'referenced_workflows': [{'path': REPOSITORY + '/' + SIGNER + '@refs/heads/main',
                                             'sha': plan['tested_sha'], 'ref': 'refs/heads/main'}]}
        self.jobs = [{'name': name, 'status': 'completed', 'conclusion': 'success', 'started_at': '2026-11-05T01:00:00Z',
                      'completed_at': '2026-11-05T01:01:00Z'} for name in ('ci-ready', 'geospatial-changes', 'production-images (' + TARGET + ')')]
        manifest = {'schema_version': 1, 'tested_sha': plan['tested_sha'], 'images': {TARGET: image}}
        result = {**plan, 'suite': 'production-images', 'image_target': TARGET, 'status': 'success',
                  'tools': ci.expected_tools('production-images'), 'commands': [{'argv': ['fixture-image-probe'], 'exit_code': 0}],
                  'production_images': manifest}
        evidence = {**plan, 'status': 'success', 'plan_artifact': 'ci-validation-plan-attempt1',
                    'suite_artifacts': {'production-images-' + TARGET: 'ci-result-production-images-' + TARGET + '-attempt1'}}
        self.payloads = [zipped({'plan.json': plan}), zipped({'evidence.json': evidence}),
                         zipped({'result.json': result, 'images/manifest.json': manifest}), zipped({image['archive']: archive})]
        names = ['ci-validation-plan-attempt1', 'ci-ready-evidence-attempt1',
                 'ci-result-production-images-' + TARGET + '-attempt1', 'ci-image-' + TARGET + '-attempt1']
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
            assert 'deployments?environment=production-' + TARGET in path
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
def journey(tmp_path, monkeypatch):
    sha = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    selection = ci.resolve_selection(['ingestion/sea_ice_daily/main.py'], ingestion_holds=())
    plan = {'schema_version': 1, 'base': sha, 'head': sha, 'tested_sha': sha,
            'tree': subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=ROOT, text=True).strip(),
            'contract_digest': ci.contract_digest(ROOT), 'changed_paths': ['ingestion/sea_ice_daily/main.py'],
            'source': {'run_id': '123', 'run_attempt': 1}, **selection}
    ci.write_json(tmp_path / 'ci-plan.json', plan)
    plan = json.loads((tmp_path / 'ci-plan.json').read_bytes())
    assert selection['deployments'] == ['sea_ice'] and TARGET in plan['production_image_targets']
    raw, image = docker_tar(TARGET, tag=bundle.source_tag(TARGET, sha))
    image['source_tag'] = bundle.source_tag(TARGET, sha)
    services = Services(plan, image, raw)
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


@pytest.mark.parametrize('defect', [None, 'gate', 'selection', 'image', 'allowlist', 'permission', 'claim', 'apply', 'verify', 'terminal', 'history-race'])
def test_emitted_selection_through_retained_image_saved_plan_signed_apply_and_terminal_receipt(journey, monkeypatch, defect):
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
            images.download(services, REPOSITORY, 123, 1, sha, TARGET, directory / 'loaded', ROOT)
        assert services.records == [] and not any(call[0] == 'docker' for call in services.calls)
        return
    retained = images.download(services, REPOSITORY, 123, 1, sha, TARGET, directory / 'loaded', ROOT)
    assert any(call[:3] == ('docker', 'image', 'load') for call in services.calls)
    image = 'us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/sea-ice-daily@' + retained['config_digest']
    address = allowlist.JOB_IMAGE_TARGETS[TARGET]
    value = {'project': 'shared-datasets-1', 'location': 'us-central1', 'name': TARGET,
             'template': [{'template': [{'service_account': 'sea-ice-daily@shared-datasets-1.iam.gserviceaccount.com',
                                         'containers': [{'image': image}]}]}]}
    plan = {'format_version': '1.2', 'resource_changes': [{'address': address, 'type': 'google_cloud_run_v2_job',
            'mode': 'managed', 'change': {'actions': ['update'], 'before': value, 'after': value}}]}
    if defect == 'allowlist':
        plan['resource_changes'][0]['address'] = 'google_storage_bucket.unrelated'
    plan_path = directory / 'ingestion.tfplan.json'
    plan_path.write_text(json.dumps(plan))
    refused = allowlist.main([str(plan_path), '--job-image-target', TARGET, '--expected-image', image, '--refusal-prefix', 'REFUSED'])
    if defect == 'allowlist':
        assert refused != 0 and services.records == []
        return
    assert refused == 0
    required = plan_permissions.plan_checks(json.loads(plan_path.read_bytes()), project_number='123456789', target=TARGET)
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
    args = SimpleNamespace(workflow=TARGET + '-deploy.yml', target=TARGET, executor_sha=sha, source_run_id=123,
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
    args.phase = 'failed' if applied.returncode else deployment.terminal_execution(wire(execution), image)
    args.execution = 'sea-ice-daily-abcde'
    deployment.finish(args, services)
    terminal = next(path for path in (directory / 'deployment-receipts').glob('*.json') if json.loads(path.read_bytes())['status']['description'] == args.phase)
    services.sign(terminal)
    if defect == 'terminal':
        services.statuses[0]['environment_url'] += '&substituted=true'
        with pytest.raises(AssertionError, match='unsigned subject'):
            incidents.terminal_success(services, 'deployment:' + TARGET)
        return
    if defect == 'history-race':
        original_crypto = services.crypto
        def racing_crypto(command, *other):
            response = original_crypto(command, *other)
            if json.loads(Path(command[3]).read_bytes())['status']['description'] == 'verified':
                services.statuses.insert(0, {**services.statuses[0], 'id': 99, 'state': 'failure', 'description': 'failed'})
            return response
        monkeypatch.setattr(emission, 'verified_output', racing_crypto)
    healthy, memo = incidents.terminal_success(services, 'deployment:' + TARGET)
    if defect in {'apply', 'verify', 'history-race'}:
        assert (healthy, memo) == (None, None)
    else:
        assert healthy['proof']['artifact'] == image and healthy['proof']['status_id'] == services.statuses[0]['id']
        assert memo['evidence_sha256'] == incidents.terminal_evidence_sha256(services.records[0], services.statuses)
    assert args.phase == ('failed' if defect in {'apply', 'verify'} else 'verified')
