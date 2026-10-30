"""Real Git identities and ZIP transport exercise the PR/main reuse boundary."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import zipfile

import pytest

from scripts import ci_reuse as reuse
from scripts import ci_preflight as preflight
from scripts import sdk_release_authorization as release
from scripts.ci_contract import CONTRACT_FILES, contract_digest, expected_tools, select_production_images, verify_results
from scripts.deployment_revision import DeploymentError
from scripts.tested_image_bundle import source_tag

REPOSITORY = 'SkyTruth/shared-datasets-1'
REFERENCE = {'pr_number': 42, 'run_id': 123, 'run_attempt': 1}
MAIN_SOURCE = {'run_id': '456', 'run_attempt': 1}


class SourceAPI:
    def __init__(self, root, main, source, head, suite):
        self.responses = {}
        self.repo = {'id': 9, 'full_name': REPOSITORY}
        self.workflow = {'id': 7, 'path': '.github/workflows/ci.yml'}
        self.pr = {'number': 42, 'merged': True, 'state': 'closed', 'merge_commit_sha': main['tested_sha'],
                   'base': {'ref': 'main', 'sha': main['base'], 'repo': self.repo.copy()},
                   'head': {'sha': head, 'repo': self.repo.copy()}}
        self.run = {'id': 123, 'run_attempt': 1, 'event': 'pull_request', 'head_sha': head,
                    'repository': self.repo.copy(), 'head_repository': self.repo.copy(),
                    'workflow_id': 7, 'path': self.workflow['path'], 'status': 'completed', 'conclusion': 'success'}
        self.runs = [self.run]
        self.main_run = {**self.run, 'id': 456, 'event': 'push', 'head_sha': main['tested_sha'], 'head_branch': 'main'}
        self.main_jobs = [{'name': suite, 'status': 'in_progress', 'started_at': '2026-10-30T10:00:00Z'}]
        self.jobs = [{'name': name, 'status': 'completed', 'conclusion': 'success',
                      'started_at': '2026-10-29T10:00:00Z', 'completed_at': '2026-10-29T10:01:00Z'}
                     for name in ('ci-ready', 'geospatial-changes', suite)]
        self.commit = {'sha': source, 'tree': {'sha': main['tree']}, 'parents': [{'sha': main['base']}, {'sha': head}]}
        identity = {**main, 'head': head, 'tested_sha': source, 'source': {'run_id': '123', 'run_attempt': 1}}
        self.plan = identity
        self.evidence = {**identity, 'status': 'success', 'plan_artifact': 'ci-validation-plan-attempt1',
                         'suite_artifacts': {suite: f'ci-result-{suite}-attempt1'}}
        self.result = {key: identity[key] for key in reuse.IDENTITY}
        self.result.update(source=identity['source'], suite=suite, status='success',
                           tools=expected_tools(suite), commands=[{'argv': ['real-suite-entrypoint'], 'exit_code': 0}])
        self.documents = {'ci-validation-plan-attempt1': {'plan.json': self.plan},
                          'ci-ready-evidence-attempt1': {'evidence.json': self.evidence},
                          f'ci-result-{suite}-attempt1': {'result.json': self.result}}
        if suite == 'production-images':
            targets = select_production_images(main['changed_paths'])
            manifest = {'schema_version': 1, 'tested_sha': source, 'images': {
                target: {'config_digest': 'sha256:' + 'a' * 64, 'source_tag': source_tag(target, source),
                         'archive': target + '.docker.tar', 'archive_sha256': 'b' * 64, 'archive_size': 1, 'platform': 'linux/amd64'}
                for target in targets}}
            self.result['production_images'] = manifest
            self.documents[f'ci-result-{suite}-attempt1']['images/manifest.json'] = manifest
            for target in targets:
                self.documents[f'ci-image-{target}-attempt1'] = {'image.docker.tar': b'image'}
        self.pack()

    def pack(self):
        self.payloads, self.artifacts = {}, []
        for index, (name, documents) in enumerate(self.documents.items(), 1):
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                for member, value in documents.items():
                    archive.writestr(member, value if isinstance(value, bytes) else json.dumps(value))
            raw = buffer.getvalue()
            self.payloads[index] = raw
            self.artifacts.append({'id': index, 'name': name, 'expired': False, 'workflow_run': {'id': 123},
                                   'created_at': '2026-10-29T10:00:30Z', 'expires_at': '2026-11-28T10:00:30Z', 'digest': 'sha256:' + hashlib.sha256(raw).hexdigest()})

    def get(self, path):
        if path in self.responses:
            return self.responses[path]
        if '/actions/runs/456/attempts/' in path:
            return self.main_run
        if path == f'repos/{REPOSITORY}':
            return self.repo
        if '/workflows/' in path:
            return self.workflow
        if '/pulls/' in path:
            return self.pr
        if '/git/commits/' in path:
            return self.commit
        if '/actions/runs/' in path:
            return self.run
        raise AssertionError(path)

    def pages(self, path, field=None):
        if field == 'workflow_runs':
            return self.runs
        if field == 'jobs' and '/actions/runs/456/' in path:
            return self.main_jobs
        if field == 'jobs':
            return self.jobs
        if field == 'artifacts':
            return self.artifacts
        if '/commits/' in path and '/pulls?' in path:
            return [{'number': 42}]
        raise AssertionError(path)

    def archive(self, repository, artifact_id):
        assert repository == REPOSITORY
        return self.payloads[artifact_id]


@pytest.fixture(scope='module')
def histories(tmp_path_factory):
    root = tmp_path_factory.mktemp('ci-reuse-history')

    def git(*args, input=None):
        return subprocess.check_output(['git', *args], cwd=root, input=input, text=True, stderr=subprocess.DEVNULL).strip()

    git('init', '-b', 'main')
    git('config', 'user.email', 'test@example.com')
    git('config', 'user.name', 'CI reuse test')
    source_root = Path(__file__).resolve().parents[1]
    for filename in CONTRACT_FILES:
        path = root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((source_root / filename).read_bytes())
    git('add', '.')
    git('commit', '-m', 'base')
    base = git('rev-parse', 'HEAD')
    cached = {}
    current = None

    def build(paths):
        git('checkout', '--detach', base)
        for filename in paths:
            path = root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('new fixture bytes\n')
        git('add', '.')
        git('commit', '-m', 'feature')
        head, tree = git('show', '-s', '--format=%H%n%T', 'HEAD').splitlines()
        source = git('commit-tree', tree, '-p', base, '-p', head, input='PR tested merge\n')
        main = git('commit-tree', tree, '-p', base, input='squash merged feature\n')
        git('checkout', '--detach', main)
        plan = {'schema_version': 1, 'base': base, 'head': main, 'tested_sha': main, 'tree': tree,
                'contract_digest': contract_digest(root), 'suites': ['lint', 'tests', 'browser'],
                'changed_paths': paths, 'selection_reason': 'complete path classification'}
        return root, plan, source, head

    def factory(*, suite='browser', paths=None):
        nonlocal current
        paths = tuple(paths or ['tests/browser/fixture.txt'])
        if paths not in cached:
            cached[paths] = build(paths)
            current = cached[paths][1]['tested_sha']
        checkout, original, source, head = cached[paths]
        if current != original['tested_sha']:
            git('checkout', '--detach', original['tested_sha'])
            current = original['tested_sha']
        plan = {**original, 'suites': ['lint', 'tests', suite], 'changed_paths': list(paths)}
        return checkout, plan, SourceAPI(checkout, plan, source, head, suite)

    return factory


@pytest.mark.parametrize('suite', ['browser', 'geospatial-integration'])
def test_original_exact_tree_result_is_not_rewritten_for_main(histories, suite):
    root, plan, api = histories(suite=suite)
    verified = reuse.discover(api, REPOSITORY, plan, root, suite, main_source=MAIN_SOURCE)
    assert verified.reference == REFERENCE
    assert verified.result['tested_sha'] != plan['tested_sha']
    assert verified.result['tested_sha'] == verified.plan['tested_sha']
    assert verified.suite_artifact == f'ci-result-{suite}-attempt1'


@pytest.mark.parametrize('paths', [['ingestion/mpatlas_monthly/run.py'],
                                  ['services/catalog_viewer/run.py', 'ingestion/dataset_usage/run.py']])
def test_whole_selected_image_set_retains_original_artifacts(histories, paths):
    root, plan, api = histories(suite='production-images', paths=paths)
    verified = reuse.verify_source(api, REPOSITORY, plan, root, 'production-images', REFERENCE, main_source=MAIN_SOURCE)
    assert set(verified.result['production_images']['images']) == set(select_production_images(paths))
    assert verified.result['production_images']['tested_sha'] != plan['tested_sha']


@pytest.mark.parametrize('paths', [['ingestion/wdpa_monthly/run.py'], ['ingestion/sea_ice_daily/run.py'], ['unknown.py']])
def test_executor_dependent_or_unknown_images_run_fresh(histories, paths):
    root, plan, _ = histories()
    plan['changed_paths'] = paths
    plan['suites'].append('production-images')
    assert not reuse.eligible(root, plan, 'production-images')


@pytest.mark.parametrize('defect', ['fork', 'base', 'head', 'tree', 'contract', 'attempt', 'workflow', 'run-repository',
                                   'missing-job', 'failed-job', 'tamper', 'expired', 'missing-artifact', 'duplicate-artifact',
                                   'reused-result', 'reused-gate', 'failed-command', 'tools', 'merge-parent'])
def test_selected_invalid_source_never_passes(histories, defect):
    root, plan, api = histories()
    if defect == 'fork':
        api.pr['head']['repo']['id'] += 1
    elif defect == 'base':
        api.pr['base']['sha'] = 'a' * 40
    elif defect == 'head':
        api.pr['head']['sha'] = 'a' * 40
    elif defect == 'tree':
        api.commit['tree']['sha'] = 'a' * 40
    elif defect == 'contract':
        api.plan['contract_digest'] = 'bad'
    elif defect == 'attempt':
        api.run['run_attempt'] = 2
    elif defect == 'workflow':
        api.run['workflow_id'] = 12
    elif defect == 'run-repository':
        api.run['head_repository']['id'] += 1
    elif defect == 'missing-job':
        api.jobs.pop()
    elif defect == 'failed-job':
        api.jobs[-1]['conclusion'] = 'failure'
    elif defect == 'reused-result':
        api.result['reused_from'] = REFERENCE
    elif defect == 'reused-gate':
        api.evidence['reused_suites'] = {'browser': REFERENCE}
    elif defect == 'failed-command':
        api.result['commands'][0]['exit_code'] = 1
    elif defect == 'tools':
        api.result['tools'] = {}
    elif defect == 'merge-parent':
        api.commit['parents'].reverse()
    api.pack()
    if defect == 'tamper':
        api.payloads[1] += b'corrupt'
    elif defect == 'expired':
        api.artifacts[0]['expired'] = True
    elif defect == 'missing-artifact':
        api.artifacts.pop(0)
    elif defect == 'duplicate-artifact':
        api.artifacts.append(api.artifacts[0].copy())
    with pytest.raises(DeploymentError):
        reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE, main_source=MAIN_SOURCE)


def test_latest_failed_run_cannot_fall_back_to_older_success(histories):
    root, plan, api = histories()
    api.runs.append({**api.run, 'id': 124, 'conclusion': 'failure'})
    assert reuse.discover(api, REPOSITORY, plan, root, 'browser', main_source=MAIN_SOURCE) is None
    with pytest.raises(DeploymentError, match='latest'):
        reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE, main_source=MAIN_SOURCE)


@pytest.mark.parametrize('missing', [True, False])
def test_discovery_retention_miss_runs_fresh_but_selected_reference_fails(histories, missing):
    root, plan, api = histories()
    if missing:
        api.artifacts.pop(0)
    else:
        api.artifacts[0]['expired'] = True
    assert reuse.discover(api, REPOSITORY, plan, root, 'browser', main_source=MAIN_SOURCE) is None
    with pytest.raises(DeploymentError):
        reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE, main_source=MAIN_SOURCE)


def test_changed_contract_since_base_is_ineligible(histories):
    root, plan, _ = histories(paths=[CONTRACT_FILES[0]])
    assert not reuse.eligible(root, plan, 'browser')


def test_discovery_does_not_hide_digest_failure(histories):
    root, plan, api = histories()
    api.payloads[1] += b'corrupt'
    with pytest.raises(DeploymentError, match='digest'):
        reuse.discover(api, REPOSITORY, plan, root, 'browser', main_source=MAIN_SOURCE)


def test_missing_gate_upload_in_execution_window_is_retention_miss(histories):
    root, plan, api = histories()
    api.artifacts[1]['created_at'] = '2026-10-29T09:00:00Z'
    assert reuse.discover(api, REPOSITORY, plan, root, 'browser', main_source=MAIN_SOURCE) is None
    with pytest.raises(reuse.UnavailableRetention):
        reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE, main_source=MAIN_SOURCE)


def install_transport(monkeypatch, api):
    """Replace only remote requests; production discovery and verifiers run."""
    monkeypatch.setattr(release.GitHub, 'get', lambda self, path: api.get(path))
    monkeypatch.setattr(release.GitHub, 'pages', lambda self, path, field=None: api.pages(path, field))
    monkeypatch.setattr(release.GitHub, 'archive', lambda self, repository, artifact: api.archive(repository, artifact))
    for key, value in {'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
                       'GITHUB_REPOSITORY': REPOSITORY, 'GITHUB_RUN_ID': '456', 'GITHUB_RUN_ATTEMPT': '1'}.items():
        monkeypatch.setenv(key, value)


def gate_results(plan, wrapper):
    """Ordinary main lint/tests evidence accompanies the serialized wrapper."""
    ordinary = []
    for suite in ('lint', 'tests'):
        result = {key: plan[key] for key in reuse.IDENTITY}
        result.update(suite=suite, status='success', source={'run_id': '456', 'run_attempt': 1},
                      tools=expected_tools(suite), commands=[{'argv': [suite], 'exit_code': 0}])
        ordinary.append(result)
    return [*ordinary, wrapper]


@pytest.mark.parametrize('suite,paths', [('browser', None), ('geospatial-integration', None),
                                       ('production-images', ['ingestion/mpatlas_monthly/run.py'])])
def test_reuse_entrypoint_serializes_main_wrapper_and_gate_reverifies_original_source(histories, tmp_path, monkeypatch, suite, paths):
    root, plan, api = histories(suite=suite, paths=paths)
    install_transport(monkeypatch, api)
    output = tmp_path / 'main-result'
    assert preflight.reuse_suite(root, plan, suite, output)
    wrapper = json.loads((output / 'result.json').read_bytes())
    assert wrapper['source'] == {'run_id': '456', 'run_attempt': 1}
    assert wrapper['tested_sha'] == plan['tested_sha'] != api.result['tested_sha']
    assert wrapper['reused_from'] == REFERENCE
    assert 'production_images' not in wrapper
    results = gate_results(plan, wrapper)
    with pytest.raises(ValueError, match='independently verified source'):
        verify_results(plan, results)
    proofs = {1: {'lint', 'tests', suite}}
    verified = []

    def reverify(result):
        source = reuse.verify_source(api, REPOSITORY, plan, root, result['suite'], result['reused_from'], main_source=MAIN_SOURCE)
        verified.append(source)

    chosen = verify_results(plan, results, source_proofs=proofs, run_id='456', run_attempt=1, reuse_verifier=reverify)
    assert chosen[suite] == wrapper
    assert len(verified) == 1 and verified[0].result == api.result
    # The wrapper cannot preserve an earlier pass after the remote source proof
    # becomes invalid. The gate fetches and checks the original ZIP again.
    api.payloads[1] += b'tampered after wrapper execution'
    with pytest.raises(DeploymentError, match='digest'):
        verify_results(plan, results, source_proofs=proofs, run_id='456', run_attempt=1, reuse_verifier=reverify)


def test_reuse_entrypoint_rejects_selected_bad_evidence_without_writing_success(histories, tmp_path, monkeypatch):
    root, plan, api = histories()
    install_transport(monkeypatch, api)
    api.payloads[1] += b'corrupt'
    output = tmp_path / 'invalid-result'
    with pytest.raises(DeploymentError, match='digest'):
        preflight.reuse_suite(root, plan, 'browser', output)
    assert not (output / 'result.json').exists()


@pytest.mark.parametrize('identity', ['latest', 'current', 'producer'])
def test_each_run_identity_is_independently_verified(histories, identity):
    root, plan, api = histories()
    altered = {**api.run, 'workflow_id': 999}
    if identity == 'latest':
        api.runs = [altered]
    elif identity == 'current':
        api.responses[f'repos/{REPOSITORY}/actions/runs/123'] = altered
    else:
        api.responses[f'repos/{REPOSITORY}/actions/runs/123/attempts/1'] = altered
    with pytest.raises(DeploymentError, match='workflow or repository mismatch'):
        reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE, main_source=MAIN_SOURCE)


@pytest.mark.parametrize('artifact', ['ci-validation-plan-attempt1', 'ci-ready-evidence-attempt1',
                                    'ci-result-production-images-attempt1', 'ci-image-mpatlas-monthly-attempt1'])
def test_every_source_dependency_covers_main_wrapper_recovery_lifetime(histories, artifact):
    root, plan, api = histories(suite='production-images', paths=['ingestion/mpatlas_monthly/run.py'])
    # Source bytes still exist today, but would expire before this main suite's
    # complete recovery window. Fresh validation must retain new dependencies.
    next(item for item in api.artifacts if item['name'] == artifact)['expires_at'] = '2026-11-14T09:59:59Z'
    assert reuse.discover(api, REPOSITORY, plan, root, 'production-images', main_source=MAIN_SOURCE) is None
    with pytest.raises(reuse.UnavailableRetention, match='retention is too short'):
        reuse.verify_source(api, REPOSITORY, plan, root, 'production-images', REFERENCE, main_source=MAIN_SOURCE)


def test_selective_main_rerun_anchors_lifetime_to_actual_producing_attempt(histories):
    root, plan, api = histories()
    for artifact in api.artifacts:
        artifact['expires_at'] = '2026-11-14T10:00:00Z'
    plan['source'] = MAIN_SOURCE.copy()
    assert reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE, main_source=MAIN_SOURCE)
    newer_source = {'run_id': '456', 'run_attempt': 2}
    api.main_run['run_attempt'] = 2
    api.main_jobs[0]['started_at'] = '2026-11-01T10:00:00Z'
    with pytest.raises(reuse.UnavailableRetention, match='retention is too short'):
        reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE, main_source=newer_source)
    # An older main plan is compatible with a later execution of the same run;
    # the later wrapper must independently have enough source retention.
    for artifact in api.artifacts:
        artifact['expires_at'] = '2026-11-16T10:00:00Z'
    assert reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE, main_source=newer_source)


def test_later_pr_failure_does_not_revoke_already_authorized_main_bytes(histories):
    root, plan, api = histories()
    api.runs = [{**api.run, 'id': 124, 'conclusion': 'failure'}]
    api.responses[f'repos/{REPOSITORY}/actions/runs/123'] = {**api.run, 'run_attempt': 2, 'conclusion': 'failure'}
    with pytest.raises(DeploymentError, match='latest'):
        reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE, main_source=MAIN_SOURCE)
    assert reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE,
                               main_source=MAIN_SOURCE, require_latest=False)
    api.jobs[-1]['conclusion'] = 'failure'
    with pytest.raises(DeploymentError, match='producing job'):
        reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE,
                            main_source=MAIN_SOURCE, require_latest=False)


@pytest.mark.parametrize('defect', ['invalid-id', 'wrong-plan-run', 'wrong-attempt', 'wrong-head', 'wrong-event',
                                   'wrong-repository', 'missing-job', 'duplicate-job'])
def test_main_producer_identity_is_proven_before_reuse(histories, defect):
    root, plan, api = histories()
    source = MAIN_SOURCE.copy()
    if defect == 'invalid-id':
        source['run_id'] = '0'
    elif defect == 'wrong-plan-run':
        plan['source'] = {'run_id': '789', 'run_attempt': 1}
    elif defect == 'wrong-attempt':
        api.main_run['run_attempt'] = 2
    elif defect == 'wrong-head':
        api.main_run['head_sha'] = 'a' * 40
    elif defect == 'wrong-event':
        api.main_run['event'] = 'pull_request'
    elif defect == 'wrong-repository':
        api.main_run['repository'] = {'id': 1, 'full_name': REPOSITORY}
    elif defect == 'missing-job':
        api.main_jobs.clear()
    else:
        api.main_jobs.append(api.main_jobs[0].copy())
    with pytest.raises(DeploymentError):
        reuse.verify_source(api, REPOSITORY, plan, root, 'browser', REFERENCE, main_source=source)


def test_main_authorized_consumer_loads_original_pr_bytes_and_rejects_changed_tar(histories, tmp_path, monkeypatch):
    from scripts import tested_image_authorization as consumer
    from scripts import tested_image_bundle as bundle
    from test_tested_image_handoff import docker_tar, inspected

    target = 'mpatlas-monthly'
    root, plan, api = histories(suite='production-images', paths=['ingestion/mpatlas_monthly/run.py'])
    raw, image = docker_tar(target, tag=source_tag(target, api.plan['tested_sha']))
    image['source_tag'] = source_tag(target, api.plan['tested_sha'])
    api.result['production_images']['images'][target] = image
    image_name = f'ci-image-{target}-attempt1'
    api.documents[image_name] = {image['archive']: raw}
    api.pack()
    for artifact in api.artifacts:
        artifact['size_in_bytes'] = len(api.payloads[artifact['id']])
    install_transport(monkeypatch, api)
    assert preflight.reuse_suite(root, plan, 'production-images', tmp_path / 'wrapper')
    wrapper = json.loads((tmp_path / 'wrapper/result.json').read_bytes())
    main_plan = {**plan, 'source': MAIN_SOURCE}
    main_evidence = {**main_plan, 'status': 'success', 'plan_artifact': 'ci-validation-plan-attempt1',
                     'suite_artifacts': {'production-images': 'ci-result-production-images-attempt1'},
                     'reused_suites': {'production-images': REFERENCE}}
    main_artifacts = []
    main_documents = {'ci-validation-plan-attempt1': {'plan.json': main_plan},
                      'ci-ready-evidence-attempt1': {'evidence.json': main_evidence},
                      'ci-result-production-images-attempt1': {'result.json': wrapper}}

    def zip_bytes(documents):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for name, value in documents.items():
                archive.writestr(name, value if isinstance(value, bytes) else json.dumps(value))
        return output.getvalue()

    for identity, (name, documents) in enumerate(main_documents.items(), 100):
        payload = api.payloads[identity] = zip_bytes(documents)
        main_artifacts.append({'id': identity, 'name': name, 'workflow_run': {'id': 456}, 'expired': False,
                               'created_at': '2026-10-30T10:00:30Z', 'digest': 'sha256:' + hashlib.sha256(payload).hexdigest()})
    api.main_jobs = [{**job, 'started_at': '2026-10-30T10:00:00Z', 'completed_at': '2026-10-30T10:01:00Z'} for job in api.jobs]
    pages = api.pages
    api.pages = lambda path, field=None: main_artifacts if '/runs/456/' in path and field == 'artifacts' else pages(path, field)
    api.archive_to = lambda repository, identity, path: path.write_bytes(api.archive(repository, identity))
    original_output, original_run = subprocess.check_output, subprocess.run
    docker_calls = []

    def command_output(args, **kwargs):
        if args[0] != 'docker':
            return original_output(args, **kwargs)
        assert args[:3] == ['docker', 'image', 'inspect']
        return inspected(image)

    def command_run(args, **kwargs):
        if args[0] != 'docker':
            return original_run(args, **kwargs)
        assert args[:3] == ['docker', 'image', 'load']
        docker_calls.append(args)

    monkeypatch.setattr(subprocess, 'check_output', command_output)
    monkeypatch.setattr(subprocess, 'run', command_run)

    def download(directory):
        return consumer.download(api, REPOSITORY, 456, 1, plan['tested_sha'], target, tmp_path / directory, root)

    result = download('loaded')
    assert result['tested_sha'] == api.plan['tested_sha'] != plan['tested_sha']
    assert result['authorized_sha'] == plan['tested_sha']
    assert result['source_tag'] == image['source_tag']
    assert Path(result['archive']).read_bytes() == raw
    api.runs = [{**api.run, 'id': 124, 'conclusion': 'failure'}]
    api.responses[f'repos/{REPOSITORY}/actions/runs/123'] = {**api.run, 'run_attempt': 2, 'conclusion': 'failure'}
    assert download('recovered')['config_digest'] == image['config_digest']
    retained = next(artifact for artifact in api.artifacts if artifact['name'] == image_name)
    changed = zip_bytes({image['archive']: raw[:-1] + bytes([raw[-1] ^ 1])})
    api.payloads[retained['id']] = changed
    retained.update(digest='sha256:' + hashlib.sha256(changed).hexdigest(), size_in_bytes=len(changed))
    with pytest.raises(bundle.ImageError, match='archive digest or size mismatch'):
        download('corrupt')
    assert len(docker_calls) == 2
