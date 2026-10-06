"""Release fixtures bind selective reruns, tested bytes and producing jobs."""
import base64
import hashlib
import io
import json
from pathlib import Path
from unittest.mock import Mock
import zipfile

import pytest

from scripts import sdk_release_authorization as release
from scripts.deployment_revision import DeploymentError

SHA = 'a' * 40
REPO = 'SkyTruth/shared-datasets-1'


def archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as zipped:
        for name, value in files.items():
            zipped.writestr(name, json.dumps(value) if isinstance(value, dict) else value)
    return output.getvalue()


def fixture(tmp_path, monkeypatch, *, sdk_attempt=1):
    package = b'exact tested package bytes'
    manifest = {'name': '@skytruth/shared-datasets', 'version': '0.12.0'}
    path = tmp_path / 'api/typescript/package.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(manifest))
    candidate = {**manifest, 'tested_sha': SHA, 'tarball': 'sdk.tgz', 'sha256': hashlib.sha256(package).hexdigest(), 'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(package).digest()).decode()}
    identity = {'schema_version': 1, 'base': 'b' * 40, 'head': SHA, 'tested_sha': SHA, 'tree': 'tree', 'contract_digest': 'contract'}
    plan = {**identity, 'suites': ['lint', 'tests', 'sdk-node22', 'sdk-node24'], 'source': {'run_id': '123', 'run_attempt': 1}, 'changed_paths': ['api/typescript/src/index.ts'], 'selection_reason': 'complete path classification'}
    evidence = {**plan, 'status': 'success', 'source': {'run_id': '123', 'run_attempt': 2}, 'plan_artifact': 'ci-validation-plan-attempt1', 'suite_artifacts': {'sdk-node24': f'ci-result-sdk-node24-attempt{sdk_attempt}'}}
    result = {**identity, 'source': {'run_id': '123', 'run_attempt': sdk_attempt}, 'suite': 'sdk-node24', 'status': 'success', 'tools': release.expected_tools('sdk-node24'), 'commands': [{'argv': ['npm', 'test'], 'exit_code': 0}], 'package': candidate}
    payloads = [archive({'evidence.json': evidence}), archive({'plan.json': plan}), archive({'result.json': result, 'package/candidate.json': candidate, 'package/sdk.tgz': package, 'logs/arbitrary-script.py': b'not executed or extracted'})]
    artifacts = [{'id': index, 'name': name, 'expired': False, 'workflow_run': {'id': 123}, 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest()} for index, (name, raw) in enumerate(zip(['ci-ready-evidence-attempt2', 'ci-validation-plan-attempt1', f'ci-result-sdk-node24-attempt{sdk_attempt}'], payloads), 1)]
    run = {'id': 123, 'workflow_id': 7, 'repository': {'id': 9, 'full_name': REPO}, 'head_repository': {'id': 9, 'full_name': REPO}, 'event': 'push', 'head_branch': 'main', 'head_sha': SHA, 'path': '.github/workflows/ci.yml', 'run_attempt': 1}
    api = Mock()
    api.archive.side_effect = lambda repo, artifact: payloads[artifact - 1]
    api.get.side_effect = lambda endpoint: {**run, 'run_attempt': int(endpoint.rsplit('/', 1)[-1])}
    api.pages.side_effect = lambda endpoint, field=None: artifacts if field == 'artifacts' else [{'name': 'geospatial-changes', 'status': 'completed', 'conclusion': 'success'}, {'name': 'sdk-validation (Node 24)', 'status': 'completed', 'conclusion': 'success'}]
    monkeypatch.setattr(release, 'verify_ci', lambda *args: run)
    monkeypatch.setattr(release, 'contract_digest', lambda root: 'contract')
    monkeypatch.setattr(release.subprocess, 'check_output', lambda args, **kwargs: 'release_needed=true' if args[0] == 'node' else 'tree')
    return api, candidate, package, artifacts, payloads


def test_selective_retry_releases_original_successful_sdk_bytes(tmp_path, monkeypatch):
    api, candidate, package, *_ = fixture(tmp_path, monkeypatch)
    outputs = release.download(api, REPO, 123, 2, SHA, tmp_path / 'output', tmp_path)
    assert Path(outputs['tarball']).read_bytes() == package
    assert json.loads(Path(outputs['candidate']).read_text())['integrity'] == candidate['integrity']
    assert outputs['artifact'] == 'sdk@sha256:' + candidate['sha256']
    assert set((tmp_path / 'output').iterdir()) == {Path(outputs['tarball']), Path(outputs['candidate'])}


def test_consumer_accepts_the_actual_preflight_package_producer(tmp_path, monkeypatch):
    from scripts import ci_preflight

    api, candidate, package, artifacts, payloads = fixture(tmp_path, monkeypatch)
    with zipfile.ZipFile(io.BytesIO(payloads[1])) as zipped:
        plan = json.loads(zipped.read('plan.json'))
    output = tmp_path / 'producer'
    smoke = output / 'work/_scratch/sdk-package-smoke-produced'
    smoke.mkdir(parents=True)
    tarball = smoke / 'sdk.tgz'
    tarball.write_bytes(package)
    (smoke / 'candidate.json').write_text(json.dumps({**candidate, 'tarball': str(tarball)}))
    monkeypatch.setenv('GITHUB_RUN_ID', '123')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    monkeypatch.setattr(ci_preflight, 'validate_checkout', lambda *_: None)
    monkeypatch.setattr(ci_preflight, 'probe_tools', lambda *_: release.expected_tools('sdk-node24'))
    monkeypatch.setattr(ci_preflight, 'suite_commands', lambda *_: [(['npm', 'test'], tmp_path)])
    monkeypatch.setattr(ci_preflight.subprocess, 'run', lambda *args, **kwargs: Mock(returncode=0))
    result = ci_preflight.run_suite(tmp_path, plan, 'sdk-node24', output)
    assert result['status'] == 'success'
    payloads[2] = archive({'result.json': (output / 'result.json').read_bytes(), 'package/candidate.json': (output / 'package/candidate.json').read_bytes(), 'package/sdk.tgz': (output / 'package/sdk.tgz').read_bytes()})
    artifacts[2]['digest'] = 'sha256:' + hashlib.sha256(payloads[2]).hexdigest()
    actual = release.download(api, REPO, 123, 2, SHA, tmp_path / 'consumer', tmp_path)
    assert Path(actual['tarball']).read_bytes() == package


@pytest.mark.parametrize('change', ['tarball', 'integrity', 'sha256', 'tested_sha', 'version'])
def test_candidate_tampering_is_rejected(tmp_path, monkeypatch, change):
    _, candidate, package, *_ = fixture(tmp_path, monkeypatch)
    candidate[change] = 'tampered'
    with pytest.raises(DeploymentError):
        release.verify_candidate(candidate, package, {'name': '@skytruth/shared-datasets', 'version': '0.12.0'}, SHA)


def test_passing_receipt_cannot_hide_failed_producing_job(tmp_path, monkeypatch):
    api, *_ = fixture(tmp_path, monkeypatch)
    original = api.pages.side_effect
    api.pages.side_effect = lambda endpoint, field=None: original(endpoint, field) if field == 'artifacts' else [{'name': 'geospatial-changes', 'status': 'completed', 'conclusion': 'success'}, {'name': 'sdk-validation (Node 24)', 'status': 'completed', 'conclusion': 'failure'}]
    with pytest.raises(DeploymentError, match='producing job'):
        release.download(api, REPO, 123, 2, SHA, tmp_path / 'output', tmp_path)


@pytest.mark.parametrize('attempt', [0, 3])
def test_receipt_cannot_select_nonexistent_or_future_attempt(tmp_path, monkeypatch, attempt):
    api, *_ = fixture(tmp_path, monkeypatch, sdk_attempt=attempt)
    with pytest.raises(DeploymentError, match='artifact attempt'):
        release.download(api, REPO, 123, 2, SHA, tmp_path / 'output', tmp_path)


def test_archive_digest_and_safe_member_paths_are_required(tmp_path, monkeypatch):
    api, _, _, artifacts, payloads = fixture(tmp_path, monkeypatch)
    artifacts[0]['digest'] = 'sha256:' + '0' * 64
    with pytest.raises(DeploymentError, match='digest'):
        release.artifact_files(api, REPO, 123, artifacts, artifacts[0]['name'])
    raw = archive({'../execute.py': b'injected'})
    payloads[0] = raw
    artifacts[0]['digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
    with pytest.raises(DeploymentError, match='unsafe'):
        release.artifact_files(api, REPO, 123, artifacts, artifacts[0]['name'])


def test_candidate_must_match_recorded_test_result_even_with_consistent_new_hashes(tmp_path, monkeypatch):
    api, candidate, _, artifacts, payloads = fixture(tmp_path, monkeypatch)
    with zipfile.ZipFile(io.BytesIO(payloads[2])) as zipped:
        result = json.loads(zipped.read('result.json'))
    new_bytes = b'different untested package'
    replacement = {**candidate, 'sha256': hashlib.sha256(new_bytes).hexdigest(), 'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(new_bytes).digest()).decode()}
    payloads[2] = archive({'result.json': result, 'package/candidate.json': replacement, 'package/sdk.tgz': new_bytes})
    artifacts[2]['digest'] = 'sha256:' + hashlib.sha256(payloads[2]).hexdigest()
    with pytest.raises(DeploymentError, match='recorded tested package'):
        release.download(api, REPO, 123, 2, SHA, tmp_path / 'output', tmp_path)


def admission_fixture(*, ready='success', conclusion='failure', current_attempt=1):
    repository = {'id': 9, 'full_name': REPO}
    workflow = {'id': 7, 'path': '.github/workflows/ci.yml'}
    run = {'id': 123, 'run_attempt': 1, 'workflow_id': 7, 'path': workflow['path'], 'name': 'CI',
           'event': 'push', 'head_branch': 'main', 'head_sha': SHA, 'status': 'completed', 'conclusion': conclusion,
           'repository': repository, 'head_repository': repository}
    api = Mock()
    api.get.side_effect = lambda endpoint: repository if endpoint == f'repos/{REPO}' else workflow if endpoint.endswith('/workflows/ci.yml') else run if '/attempts/' in endpoint else {**run, 'run_attempt': current_attempt}
    api.pages.return_value = [] if ready is None else [{'name': 'ci-ready', 'status': 'completed', 'conclusion': ready}]
    event = {'workflow_run': dict(run), 'repository': repository}
    return api, event


@pytest.mark.parametrize('ready', ['failure', 'cancelled', 'skipped', None])
def test_verified_validation_failure_does_not_authorize_or_fail_an_sdk_release(ready):
    api, event = admission_fixture(ready=ready)
    assert not release.admit_source(api, REPO, 123, 1, SHA, event)
    api.pages.assert_called_once()
    api.archive.assert_not_called()


def test_failed_post_validation_deployment_keeps_tested_sdk_eligible():
    api, event = admission_fixture()
    assert release.admit_source(api, REPO, 123, 1, SHA, event)


@pytest.mark.parametrize('conclusion,current_attempt', [('cancelled', 1), ('success', 2)])
def test_cancelled_or_verified_obsolete_ci_completion_is_an_sdk_noop(conclusion, current_attempt):
    api, event = admission_fixture(conclusion=conclusion, current_attempt=current_attempt)
    assert not release.admit_source(api, REPO, 123, 1, SHA, event)
    api.pages.assert_called_once()


@pytest.mark.parametrize('change', ['head_sha', 'workflow_id', 'path', 'event', 'repository', 'run_attempt'])
def test_sdk_admission_rejects_forged_event_identity_even_when_validation_failed(change):
    api, event = admission_fixture(ready='failure')
    event['workflow_run'][change] = {'id': 100, 'full_name': 'evil/fork'} if change == 'repository' else 'forged'
    with pytest.raises(DeploymentError):
        release.admit_source(api, REPO, 123, 1, SHA, event)


def test_sdk_admission_does_not_hide_incomplete_job_enumeration_or_duplicate_gate():
    api, event = admission_fixture(ready='failure')
    api.pages.side_effect = DeploymentError('incomplete API enumeration')
    with pytest.raises(DeploymentError, match='incomplete'):
        release.admit_source(api, REPO, 123, 1, SHA, event)
    api.pages.side_effect = None
    api.pages.return_value *= 2
    with pytest.raises(DeploymentError, match='ambiguous'):
        release.admit_source(api, REPO, 123, 1, SHA, event)


@pytest.mark.parametrize('admission_only', [False, True])
def test_verified_failed_ci_completes_candidate_detection_without_release(tmp_path, monkeypatch, admission_only):
    api, event = admission_fixture(ready='failure')
    event_path = tmp_path / 'event.json'
    event_path.write_text(json.dumps(event))
    github_output = tmp_path / 'github-output'
    monkeypatch.setenv('GITHUB_REPOSITORY', REPO)
    monkeypatch.setenv('GITHUB_EVENT_PATH', str(event_path))
    monkeypatch.setenv('GITHUB_OUTPUT', str(github_output))
    monkeypatch.setattr(release, 'GitHub', lambda: api)
    strict = Mock(side_effect=AssertionError('non-ready CI must not enter deployment authorization'))
    monkeypatch.setattr(release, 'verify_context', strict)
    args = ['sdk_release_authorization.py', '--executor-sha', SHA, '--source-run-id', '123', '--source-run-attempt', '1']
    args += ['--admit-source'] if admission_only else ['--output', str(tmp_path / 'package')]
    monkeypatch.setattr(release.sys, 'argv', args)
    release.main()
    assert github_output.read_text() == 'source_eligible=false\nrelease_needed=false\n'
    assert not (tmp_path / 'package').exists()
    strict.assert_not_called()
