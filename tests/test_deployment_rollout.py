"""Round-seven deploy regressions at the workflow and HTTP boundaries."""
from __future__ import annotations

import copy
import io
import os
from pathlib import Path
import subprocess
import urllib.error
from unittest.mock import Mock

import pytest

from scripts import deployment_permissions as permissions
from scripts import terraform_plan_allowlist as allowlist
from workflow_helpers import load_workflow

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://cloudresourcemanager.googleapis.com/v1/projects/shared-datasets-1:testIamPermissions'


def test_usage_two_runs_keep_target_ownership_until_exact_image_verification(tmp_path):
    ci = load_workflow(ROOT / '.github/workflows/ci.yml')['jobs']
    wrapper = load_workflow(ROOT / '.github/workflows/dataset-usage-deploy.yml')
    shared = load_workflow(ROOT / '.github/workflows/ingestion-deploy.yml')['jobs']
    # Schedule B at every A job boundary, including the apply/verify boundary.
    # A split caller releases the wrapper queue there and admits B's apply.
    call = ci['dataset-usage']
    phases = ['apply'] if call.get('with', {}).get('stage') == 'apply' else ['apply', 'verify']
    assert wrapper['concurrency'] == {'group': 'deployment-dataset-usage', 'queue': 'max', 'cancel-in-progress': False}
    trace, live = [], ''
    images = {run: 'image@sha256:' + run.lower() * 64 for run in ('A', 'B')}
    fake = tmp_path / 'gcloud'
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$LIVE_IMAGE"\n')
    fake.chmod(0o755)
    guard = next(s['run'] for s in shared['verify']['steps'] if s.get('name') == 'Confirm deployed digest')
    schedule = [(run, phase) for run in ('A', 'B') for phase in phases]
    if phases == ['apply']:
        schedule += [('A', 'verify'), ('B', 'verify')]
    for run, phase in schedule:
        trace.append(f'{run}.{phase}')
        if phase == 'apply':
            live = images[run]
        else:
            result = subprocess.run(['bash', '-euo', 'pipefail', '-c', guard], cwd=ROOT,
                                    env={**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
                                         'LIVE_IMAGE': live, 'IMAGE': images[run], 'TARGET': 'dataset-usage',
                                         'REGION': 'us-central1', 'GOOGLE_CLOUD_PROJECT': 'shared-datasets-1'},
                                    capture_output=True, text=True)
            assert result.returncode == 0, (trace, result.stderr)
    assert trace == ['A.apply', 'A.verify', 'B.apply', 'B.verify']
    # IAM reads the existing bucket independently; the rollout keeps its queue.
    assert call['uses'] == './.github/workflows/dataset-usage-deploy.yml'
    assert 'dataset-usage-rollout' not in ci
    assert 'concurrency' not in call and 'runs-on' not in call
    for name in ('ingestion-iam', 'scratch-cleanup-iam'):
        assert 'dataset-usage' not in ci[name]['needs']
        assert 'needs.dataset-usage.result' not in ci[name]['if']
    # A foreign live image still fails: serialization must not weaken the guard.
    rejected = subprocess.run(['bash', '-euo', 'pipefail', '-c', guard], cwd=ROOT,
                              env={**os.environ, 'PATH': str(tmp_path) + os.pathsep + os.environ['PATH'],
                                   'LIVE_IMAGE': images['B'], 'IMAGE': images['A'], 'TARGET': 'dataset-usage',
                                   'REGION': 'us-central1', 'GOOGLE_CLOUD_PROJECT': 'shared-datasets-1'}, capture_output=True)
    assert rejected.returncode != 0


def test_shared_rollout_has_no_split_verification_and_attests_apply_before_signalling_success():
    document = load_workflow(ROOT / '.github/workflows/ingestion-deploy.yml')
    inputs = document['on']['workflow_call']['inputs']
    assert not {'stage', 'deployment_id', 'image'} & set(inputs)
    verify = document['jobs']['verify']
    assert verify['needs'] == ['prepare', 'apply']
    assert verify['env']['IMAGE'] == '${{ needs.prepare.outputs.image }}'
    barrier = next(s for s in verify['steps'] if s.get('name') == 'Require successful signed apply')
    assert barrier['run'].strip() == 'test "$APPLY_RESULT" = success && test "$CLAIM_SIGNED" = success'
    apply = document['jobs']['apply']['steps']
    assert apply[-1]['name'] == 'Attest applied deployment'
    assert apply[-1]['uses'] == './.github/actions/deployment-receipt'


@pytest.mark.parametrize('error', [urllib.error.URLError('TLS handshake timeout'), TimeoutError('read timeout'),
                                 *[urllib.error.HTTPError(URL, code, 'transient', {}, io.BytesIO(b'{}'))
                                   for code in (500, 502, 503, 504)]])
def test_http_probe_retries_transient_reads_then_validates_permissions(monkeypatch, error):
    response = Mock()
    response.__enter__ = Mock(return_value=io.BytesIO(b'{"permissions":["read"]}'))
    response.__exit__ = Mock(return_value=False)
    transport = Mock(side_effect=[error, response])
    monkeypatch.setattr(permissions.urllib.request, 'urlopen', transport)
    pause = Mock()
    monkeypatch.setattr(permissions.time, 'sleep', pause)
    assert permissions.request(URL, ('read', 'write'), 'private-token') == ['write']
    assert transport.call_count == 2
    pause.assert_called_once_with(5)
    assert all(call.kwargs['timeout'] == 30 for call in transport.call_args_list)


@pytest.mark.parametrize('code', [400, 401, 403, 404, 408, 429])
def test_http_probe_never_retries_client_or_authorization_errors(monkeypatch, code):
    error = urllib.error.HTTPError(URL, code, 'denied', {}, io.BytesIO(b'{"error":{"message":"private-token"}}'))
    transport, pause = Mock(side_effect=error), Mock()
    monkeypatch.setattr(permissions.urllib.request, 'urlopen', transport)
    monkeypatch.setattr(permissions.time, 'sleep', pause)
    with pytest.raises(RuntimeError, match=f'HTTP {code}') as raised:
        permissions.request(URL, ('read',), 'private-token')
    assert 'private-token' not in str(raised.value)
    assert '[REDACTED]' in str(raised.value)
    assert transport.call_count == 1
    pause.assert_not_called()


@pytest.mark.parametrize('error', [urllib.error.URLError('TLS timeout'), TimeoutError('timeout'),
                                 urllib.error.HTTPError(URL, 503, 'unavailable', {}, io.BytesIO(b'{}'))])
def test_http_probe_exhaustion_is_bounded(monkeypatch, error):
    transport, pause = Mock(side_effect=error), Mock()
    monkeypatch.setattr(permissions.urllib.request, 'urlopen', transport)
    monkeypatch.setattr(permissions.time, 'sleep', pause)
    with pytest.raises((RuntimeError, urllib.error.URLError, TimeoutError)):
        permissions.request(URL, ('read',), 'private-token')
    assert transport.call_count == 3
    assert [call.args for call in pause.call_args_list] == [(5,), (10,)]


@pytest.mark.parametrize('filename', ['ingestion-deploy.yml', 'catalog-viewer-deploy.yml', 'pmtiles-cdn-sync.yml',
                                    'prod-terraform-target-apply.yml', 'deployment-recovery.yml'])
def test_every_production_queue_runner_has_explicit_bounded_timeout(filename):
    document = load_workflow(ROOT / '.github/workflows' / filename)
    for name, job in document['jobs'].items():
        # Shared leaves inherit a target queue from their wrapper; their setup
        # and verification jobs must be bounded as well as state-lock owners.
        if 'runs-on' in job:
            assert isinstance(job.get('timeout-minutes'), int), (filename, name)
            assert 1 <= job['timeout-minutes'] <= (150 if name == 'verify' else 30)
            if job.get('concurrency', {}).get('group') == 'prod-terraform-state':
                assert job['timeout-minutes'] <= 30


@pytest.mark.parametrize("all_queues", [False, True])
def test_all_production_target_and_state_queues_bound_their_reusable_leaf_jobs(all_queues):
    seen = set()

    def visit(path, inherited=False):
        if (path, inherited) in seen:
            return
        seen.add((path, inherited))
        document = load_workflow(path)

        def held(concurrency):
            group = concurrency.get('group', '') if isinstance(concurrency, dict) else concurrency
            return bool(group) if all_queues else group == 'prod-terraform-state' or group.startswith(('deployment-', 'ingestion-'))

        for name, job in document['jobs'].items():
            locked = inherited or held(document.get('concurrency', {})) or held(job.get('concurrency', {}))
            if 'runs-on' in job and locked:
                assert isinstance(job.get('timeout-minutes'), int), (path.name, name)
                assert 1 <= job['timeout-minutes'] <= (360 if all_queues else 150)
            elif job.get('uses', '').startswith('./.github/workflows/'):
                visit(ROOT / job['uses'].removeprefix('./'), locked)

    for path in (ROOT / '.github/workflows').glob('*.yml'):
        visit(path)


@pytest.mark.parametrize('field,value', [('cpu', '6'), ('memory', '16Gi'), ('timeout', '50400s')])
def test_wdpa_processing_limits_follow_the_single_terraform_source(tmp_path, monkeypatch, field, value):
    source = (ROOT / 'terraform/envs/prod/wdpa_monthly.tf').read_text()
    old = {'cpu': '4', 'memory': '8Gi', 'timeout': '46800s'}
    source = source.replace(f'"{old[field]}"', f'"{value}"')
    module = tmp_path / 'terraform/envs/prod/wdpa_monthly.tf'
    module.parent.mkdir(parents=True)
    module.write_text(source)
    monkeypatch.setattr(allowlist, 'ROOT', tmp_path)
    values = old | {field: value}
    task = {'timeout': values['timeout'], 'containers': [{'resources': [{'limits': {k: values[k] for k in ('cpu', 'memory')}}]}]}
    after = {'template': [{'template': [task]}]}
    assert 'unexpected WDPA processing limits' not in allowlist.wdpa_errors(after, 'image', 'config')
    broken = copy.deepcopy(after)
    if field == 'timeout':
        broken['template'][0]['template'][0]['timeout'] = old[field]
    else:
        broken['template'][0]['template'][0]['containers'][0]['resources'][0]['limits'][field] = old[field]
    assert 'unexpected WDPA processing limits' in allowlist.wdpa_errors(broken, 'image', 'config')


def test_wdpa_nonliteral_processing_limits_fail_closed(tmp_path, monkeypatch):
    module = tmp_path / 'terraform/envs/prod/wdpa_monthly.tf'
    module.parent.mkdir(parents=True)
    module.write_text((ROOT / 'terraform/envs/prod/wdpa_monthly.tf').read_text().replace('"46800s"', 'var.timeout'))
    monkeypatch.setattr(allowlist, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='WDPA.*literal'):
        allowlist.wdpa_errors({}, 'image', 'config')


@pytest.mark.parametrize('actions', [['create'], ['delete'], ['delete', 'create']])
def test_wdpa_companion_policy_stays_update_only(actions):
    address = 'google_iam_deny_policy.canonical_destructive_actions[0]'
    blocked = allowlist.blocked_changes(
        {'resource_changes': [{'address': address, 'change': {'actions': actions}}]},
        allowed_exact={address}, allowed_patterns=[], block_deletes=False,
        job_image=(allowlist.JOB_IMAGE_TARGETS['wdpa-monthly'], 'image'),
    )
    assert blocked == [f'{"/".join(actions)} {address}']
