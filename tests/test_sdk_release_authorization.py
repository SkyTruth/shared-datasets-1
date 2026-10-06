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
    result = {**identity, 'source': {'run_id': '123', 'run_attempt': sdk_attempt}, 'suite': 'sdk-node24', 'status': 'success', 'tools': release.expected_tools('sdk-node24'), 'commands': [{'argv': ['npm', 'test'], 'exit_code': 0}]}
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
