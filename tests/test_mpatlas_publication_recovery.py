from copy import deepcopy
from dataclasses import asdict
import base64
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

from ingestion.common import publication as p
from ingestion.mpatlas_monthly import publication_recovery as recovery
from scripts import reviewed_dataset_plan as plans, dataset_mutation_authorization as auth
from scripts import install_feature_id_reset as installer
from scripts import mpatlas_publication_recovery as bootstrap
from tests.test_source_field_publication import published as published, attempt
from tests.test_publication import LostResponse, MemoryStore

BASE_SHA = '3e5e03ea20759a24ca53d38d72254643d9807e8d'
EXECUTION = 'mpatlas-monthly-abc123'
IMAGE = 'us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/mpatlas-monthly@sha256:' + 'd' * 64


class Reader:
    def __init__(self):
        self.value = {'name': f'{recovery.JOB}/executions/{EXECUTION}', 'job': recovery.JOB,
            'completionTime': '2026-11-15T22:00:00Z', 'failedCount': 1,
            'template': {'containers': [{'image': IMAGE, 'env': [{'name': 'GOOGLE_CLOUD_PROJECT', 'value': 'shared-datasets-1'}, {'name': 'SHARED_DATASETS_BUCKET', 'value': recovery.BUCKET}, {'name': 'SHARED_DATASETS_SLACK_WEBHOOK_URL', 'valueSource': {'secretKeyRef': {'secret': 'hold-notice', 'version': 'latest'}}}]}]}}
        self.paused = True

    def read(self, url):
        assert url == f'https://run.googleapis.com/v2/{recovery.JOB}/executions/{EXECUTION}'
        return deepcopy(self.value)

    def check_job_quiescent(self, job):
        assert job == 'mpatlas-monthly'
        p.require(self.paused, 'pause and drain the original job')


@pytest.fixture
def interrupted(published, tmp_path):
    publisher, store, baseline = published
    publication, args = attempt(publisher, store, baseline, tmp_path / 'attempt', execution_id=EXECUTION, executor_sha=BASE_SHA)
    store.write_bytes(recovery.CONTROL, recovery.source_quality.encode({'schema_version': 1, 'asset_slug': recovery.SLUG, 'owner': EXECUTION, 'hold': None}),
        0, {'purpose': 'source-quality'}, 'application/json', recovery.source_quality.NO_CACHE)

    def fail(uri):
        if uri == publication.context.receipt_uri:
            store.fail_after = len(store.events) + 1
        else:
            store.before_write = fail
    store.before_write = fail
    with pytest.raises(LostResponse):
        publication.publish(**args)
    store.fail_after = None
    reader = Reader()
    plan = recovery.prepare(publisher, store, reader, execution_name=EXECUTION, proposal_id='recover-fixture', verify_image_executor=lambda image, sha: p.require((image, sha) == (IMAGE, BASE_SHA), 'image binding changed'))
    # The handoff crosses the actual immutable document serializer/parser.
    document = plans.normalize_document({'plan_version': 1, 'finalization_version': plans.FINALIZATION_VERSION, 'publish': plan})
    plan = plans.normalize_document(p.strict_json(plans.canonical_bytes(document)))['publish']
    store.events = []
    return publisher, store, reader, plan, publication.context


def resume(publisher, store, reader, plan):
    return recovery.recover(publisher, store, plan, reader, check_authority_and_jobs=lambda: reader.check_job_quiescent('mpatlas-monthly'), verify_image_executor=lambda image, sha: p.require((image, sha) == (IMAGE, BASE_SHA), 'image binding changed'))


def test_original_reviewed_context_finishes_without_local_inputs_or_new_transaction(interrupted):
    publisher, store, reader, plan, context = interrupted
    transaction = plan['publication_recovery']['captured_receipt']['transaction_id']
    result = resume(publisher, store, reader, plan)
    assert result['status'] == 'completed'
    receipt = store.read_json(context.receipt_uri).value
    assert receipt['transaction_id'] == transaction
    assert receipt['intent'] == plan['publication_recovery']['captured_receipt']['intent']
    assert store.read_json(context.state_uri).value['active'] is None
    assert store.read_json(recovery.CONTROL).value['owner'] is None
    control = store.read_json(recovery.CONTROL).version
    assert dict(control.metadata) == {'purpose': 'source-quality'}
    assert control.cache_control == recovery.source_quality.NO_CACHE
    assert all('/publications/' in uri or uri.startswith(recovery.ROOT + '/') or uri.startswith(f'gs://{recovery.BUCKET}/_catalog/releases/')
               or uri == recovery.CONTROL for uri in store.events)


def test_recovery_itself_resumes_after_every_durable_write(interrupted):
    publisher, original, reader, plan, context = interrupted
    complete = deepcopy(original)
    resume(publisher, complete, reader, plan)
    for fail_after in range(1, len(complete.events) + 1):
        store = deepcopy(original)
        store.fail_after = fail_after
        with pytest.raises(LostResponse):
            resume(publisher, store, reader, plan)
        store.fail_after = None
        resume(publisher, store, reader, plan)
        assert store.read_json(context.state_uri).value['active'] is None
        assert store.read_json(recovery.CONTROL).value['owner'] is None
        for operation in plan['publication_recovery']['captured_receipt']['intent']['operations']:
            assert store.events.count(operation['destination']) == 1


@pytest.mark.parametrize('field', ['state', 'receipt', 'control', 'captured_state', 'captured_receipt', 'execution', 'executor_sha', 'image'])
def test_changed_reviewed_evidence_never_authorizes_a_write(interrupted, field):
    publisher, store, reader, plan, _context = interrupted
    changed = deepcopy(plan)
    value = changed['publication_recovery'][field]
    if field in {'state', 'receipt', 'control'}:
        value['generation'] += 1
    elif field.startswith('captured_'):
        value['schema_version'] = 123
    else:
        changed['publication_recovery'][field] = 'bad'
    with pytest.raises((p.PublicationError, KeyError, ValueError)):
        resume(publisher, store, reader, changed)
    assert not store.events


@pytest.mark.parametrize('case', ['running', 'successful', 'foreign_executor', 'foreign_configuration', 'unpaused'])
def test_live_execution_and_pause_evidence_is_mandatory(interrupted, case):
    publisher, store, reader, plan, _context = interrupted
    if case == 'running':
        reader.value.pop('completionTime')
    elif case == 'successful':
        reader.value['succeededCount'] = 1
    elif case == 'foreign_executor':
        reader.value['template']['containers'][0]['env'].append({'name': 'SHARED_DATASETS_EXECUTOR_SHA', 'value': 'e' * 40})
    elif case == 'foreign_configuration':
        reader.value['template']['containers'][0]['env'].append({'name': 'MPATLAS_EXTRA', 'value': 'changed'})
    else:
        reader.paused = False
    with pytest.raises(p.PublicationError):
        resume(publisher, store, reader, plan)
    assert not store.events


def test_foreign_control_generation_is_not_cleared(interrupted):
    publisher, store, reader, plan, context = interrupted
    live = store.read_json(recovery.CONTROL)
    store.write_json(recovery.CONTROL, {**live.value, 'owner': 'another-execution'}, live.version.generation)
    store.events = []
    with pytest.raises(p.PublicationError, match='control changed'):
        resume(publisher, store, reader, plan)
    assert not store.events
    assert store.read_json(context.receipt_uri).value == plan['publication_recovery']['captured_receipt']


@pytest.mark.parametrize('race', ['before_clear_snapshot', 'after_clear_snapshot'])
def test_final_clear_never_adopts_a_new_claim(interrupted, race):
    publisher, store, reader, plan, context = interrupted
    original_read = store.read_json
    final_reads = 0

    def replace_owner():
        control = original_read(recovery.CONTROL)
        store.write_bytes(recovery.CONTROL, recovery.source_quality.encode({**control.value, 'owner': 'newer-execution'}),
            control.version.generation, dict(control.version.metadata), control.version.content_type, control.version.cache_control)

    def read(uri):
        nonlocal final_reads
        if uri == recovery.CONTROL and original_read(context.state_uri).value['active'] is None:
            receipt = original_read(context.receipt_uri).value
            if len(receipt['results']) == len(receipt['intent']['operations']):
                final_reads += 1
                if final_reads == 2 and race == 'before_clear_snapshot':
                    replace_owner()
        return original_read(uri)

    def before_write(uri):
        if uri == recovery.CONTROL:
            replace_owner()
        else:
            store.before_write = before_write

    store.read_json = Mock(side_effect=read)
    if race == 'after_clear_snapshot':
        store.before_write = before_write
    with pytest.raises(p.PublicationError, match='control changed|generation mismatch'):
        resume(publisher, store, reader, plan)
    assert original_read(recovery.CONTROL).value['owner'] == 'newer-execution'
    assert store.events.count(recovery.CONTROL) == 1  # The competing write only.
    assert original_read(context.state_uri).value['active'] is None


def test_deployed_execution_shape_uses_verified_image_identity(interrupted):
    publisher, store, reader, plan, _context = interrupted
    env = reader.value['template']['containers'][0]['env']
    assert 'SHARED_DATASETS_EXECUTOR_SHA' not in {item['name'] for item in env}
    assert any('valueSource' in item for item in env)
    verifier = Mock(side_effect=p.PublicationError('unsigned image'))
    with pytest.raises(p.PublicationError, match='unsigned image'):
        recovery.prepare(publisher, store, reader, execution_name=EXECUTION, proposal_id='refuse-unsigned', verify_image_executor=verifier)
    verifier.assert_called_once_with(IMAGE, BASE_SHA)
    with pytest.raises(p.PublicationError, match='unsigned image'):
        recovery.recover(publisher, store, plan, reader, check_authority_and_jobs=lambda: None, verify_image_executor=verifier)
    assert not store.events


@pytest.mark.parametrize('case', ['verified', 'missing', 'unsigned', 'changed_image', 'foreign_executor'])
def test_executor_binding_requires_attested_tested_image(case, tmp_path, monkeypatch):
    from scripts import deployment_revision as deployments, deployment_emission as emissions
    from tests.test_deployment_emission import record, status, run, verified, REPO
    claim = record()
    claim.update(sha=BASE_SHA, ref=BASE_SHA, environment='production-mpatlas-monthly')
    claim['payload'].update(target='mpatlas-monthly', artifact=IMAGE)
    phase = status()
    emitter = {**run(), 'head_sha': BASE_SHA}
    signed = verified(emissions.receipt(REPO, claim, phase), emitter, '.github/workflows/mpatlas-monthly-deploy.yml')
    if case == 'changed_image':
        claim['payload']['artifact'] = IMAGE.replace('d' * 64, 'e' * 64)
    elif case == 'foreign_executor':
        emitter['head_sha'] = 'e' * 40
    api = Mock()
    def get(path):
        if path == f'repos/{REPO}':
            return {'id': 17, 'full_name': REPO}
        if '/actions/workflows/' in path:
            return {'id': 12, 'path': emitter['path']}
        return emitter
    def pages(path, field=None):
        if '/deployments?' in path:
            assert path == f'repos/{REPO}/deployments?sha={BASE_SHA}&environment=production-mpatlas-monthly&per_page=100'
            return [] if case == 'missing' else [claim]
        if '/statuses?' in path:
            return [phase]
        return [{'name': 'ci-ready', 'status': 'completed', 'conclusion': 'success'}]
    api.get.side_effect = get
    api.pages.side_effect = pages
    if case == 'verified':
        monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
        monkeypatch.delenv('RUNNER_TEMP', raising=False)
        monkeypatch.setenv('SHARED_DATASETS_WORKDIR', str(tmp_path / 'repo-work'))
    else:
        monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    with patch.object(emissions, 'verified_output', return_value=json.dumps([] if case == 'unsigned' else signed)) as verifier:
        if case == 'verified':
            bootstrap.verify_image_executor(IMAGE, BASE_SHA, api=api)
            verifier.assert_called_once()
            assert verifier.call_args.args[0][:3] == ['gh', 'attestation', 'verify']
            assert Path(os.environ['RUNNER_TEMP']).is_relative_to(tmp_path / 'repo-work')
        else:
            with pytest.raises((p.PublicationError, deployments.DeploymentError)):
                bootstrap.verify_image_executor(IMAGE, BASE_SHA, api=api)
    api.post.assert_not_called()


def test_recovery_review_body_is_bounded_without_truncating_evidence(interrupted, monkeypatch):
    _publisher, _store, _reader, plan, _context = interrupted
    document = bootstrap.proposal_document(plan)
    size = len(plans.render_document(document))
    monkeypatch.setattr(bootstrap, 'MAX_REVIEW_BODY_CHARACTERS', size)
    assert bootstrap.proposal_document(plan) == document
    monkeypatch.setattr(bootstrap, 'MAX_REVIEW_BODY_CHARACTERS', size - 1)
    with pytest.raises(p.PublicationError, match='full release index and recorded results.*bounded-reference'):
        bootstrap.proposal_document(plan)
    assert document['publish'] == plan


def test_large_release_history_is_rejected_before_proposal_output(interrupted):
    _publisher, _store, _reader, plan, _context = interrupted
    grown = deepcopy(plan)
    capture = grown['publication_recovery']
    receipt = capture['captured_receipt']
    index = next(operation for operation in receipt['intent']['operations'] if operation['id'] == 'release-index')['source']['parameters']['index']
    for year in range(2010, 2026):
        entry = deepcopy(index['releases'][0])
        entry['date'] = f'{year}-01-01'
        index['releases'].append(entry)
    receipt['transaction_id'] = p.digest(p.canonical(receipt['intent']))
    capture['receipt']['sha256'] = p.digest(p.canonical(receipt))
    if capture['captured_state']['active'] is not None:
        capture['captured_state']['active']['transaction_id'] = receipt['transaction_id']
        capture['state']['sha256'] = p.digest(p.canonical(capture['captured_state']))
    with pytest.raises(p.PublicationError, match='recovery review body is.*Do not truncate'):
        bootstrap.proposal_document(grown)


def test_prepare_inventories_control_and_input_blockers_together(interrupted):
    publisher, store, reader, plan, _context = interrupted
    control = store.read_json(recovery.CONTROL)
    store.write_json(recovery.CONTROL, {**control.value, 'owner': 'another-execution'}, control.version.generation)
    operation = next(item for item in plan['publication_recovery']['captured_receipt']['intent']['operations'] if item['source']['kind'] == 'gcs')
    del store.history[(operation['source']['path'], operation['source']['generation'])]
    store.events = []
    with pytest.raises(p.PublicationError) as error:
        recovery.prepare(publisher, store, reader, execution_name=EXECUTION, proposal_id='blocked', verify_image_executor=lambda image, sha: None)
    assert 'foreign claim' in str(error.value)
    assert 'captured input is missing' in str(error.value)
    assert not store.events


def test_only_the_existing_reviewed_control_installer_can_execute_the_plan(interrupted):
    _publisher, _store, _reader, plan, _context = interrupted
    document = {'plan_version': 1, 'finalization_version': plans.FINALIZATION_VERSION, 'publish': plan}
    assert auth.plan_outputs(document) == {'has_publish_plan': False, 'has_delete_plan': False, 'has_identity_reset_plan': True}
    with patch.object(auth, 'require_production_context'), patch.object(auth, 'verified_envelope', return_value={'outcome': 'mutation', 'document': document}):
        assert installer.authorized_plan(Path('authorization'), 'f' * 64, api=Mock(), env={})[1] == plan
    with pytest.raises(plans.PlanValidationError):
        plans.normalize_document({**document, 'publish': {**plan, 'promotions': [{'extra': 'effect'}]}})


def test_control_authority_selects_pinned_evidence_verifier(interrupted, tmp_path, monkeypatch):
    from workflow_helpers import load_workflow
    _publisher, _store, _reader, plan, _context = interrupted
    output = tmp_path / 'outputs'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    with patch.object(installer, 'authorized_plan', return_value=({}, plan)):
        assert installer.main(['--directory', str(tmp_path), '--expected-sha256', 'f' * 64, '--check-only']) == 0
    assert output.read_text() == 'requires_image_evidence=true\n'
    job = load_workflow(Path(__file__).resolve().parents[1] / '.github/workflows/publish-dataset.yml')['jobs']['install-approved-identity-reset']
    assert job['permissions']['deployments'] == job['permissions']['attestations'] == 'read'
    authority = next(step for step in job['steps'] if step.get('id') == 'control-authority')
    verifier = next(step for step in job['steps'] if step.get('name') == 'Install pinned image evidence verifier')
    execute = next(step for step in job['steps'] if step.get('name') == 'Recheck live controls and install reset')
    assert '--check-only' in authority['run']
    assert verifier['if'] == "steps.control-authority.outputs.requires_image_evidence == 'true'"
    assert verifier['run'] == 'python scripts/install_deployment_verifier.py'
    assert job['steps'].index(authority) < job['steps'].index(verifier) < job['steps'].index(execute)


def test_unprivileged_bootstrap_validates_the_real_serialized_plan_without_cloud_dependencies(interrupted, tmp_path):
    _publisher, _store, _reader, plan, _context = interrupted
    path = tmp_path / 'document.json'
    path.write_text(json.dumps({'plan_version': 1, 'finalization_version': plans.FINALIZATION_VERSION, 'publish': plan}))
    root = Path(__file__).resolve().parents[1]
    code = "import json,sys;sys.path.insert(0,sys.argv[1]);from scripts import reviewed_dataset_plan as p;p.normalize_document(json.load(open(sys.argv[2])))"
    result = subprocess.run([sys.executable, '-I', '-S', '-c', code, str(root), str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_signed_image_and_current_authority_are_checked_before_loading_old_code(tmp_path):
    payload = tmp_path / 'plan.json'
    payload.write_text('{}')
    with patch.object(bootstrap.subprocess, 'run', side_effect=RuntimeError('verification refused')) as command, patch.object(bootstrap, 'load_runtime') as load:
        with pytest.raises(RuntimeError, match='verification refused'):
            bootstrap.main(['resume', '--directory', str(tmp_path), '--expected-sha256', 'f' * 64,
                '--executor-root', str(tmp_path / 'old-code'), '--plan', str(payload)])
    assert command.call_args.args[0][3] == 'verify'
    load.assert_not_called()


def test_all_known_input_and_destination_blockers_are_reported_before_a_write(interrupted):
    publisher, store, reader, plan, _context = interrupted
    operations = plan['publication_recovery']['captured_receipt']['intent']['operations']
    inputs = [op for op in operations if op['source']['kind'] == 'gcs']
    missing = inputs[0]['source']
    del store.history[(missing['path'], missing['generation'])]
    data = [op for op in operations if op['phase'] == 'data']
    for op in data[:2]:
        store.write_bytes(op['destination'], b'foreign', op['expected_generation'], {}, op['content_type'], op['cache_control'])
    store.events = []
    with pytest.raises(p.PublicationError) as error:
        resume(publisher, store, reader, plan)
    assert 'captured input is missing' in str(error.value)
    for op in data[:2]:
        assert op['id'] + ': original destination expectation conflicts' in str(error.value)
    assert not store.events


def test_prepare_rejects_missing_retained_authority_before_a_proposal(interrupted):
    publisher, store, reader, _plan, context = interrupted
    adoption = store.read_json(context.state_uri).value['adoption_receipt']
    del store.objects[adoption]
    with pytest.raises(p.PublicationError):
        recovery.prepare(publisher, store, reader, execution_name=EXECUTION, proposal_id='cannot-prepare', verify_image_executor=lambda image, sha: None)
    assert not store.events


def test_original_archived_executor_and_frozen_dependencies_resume_the_serialized_transaction(interrupted, tmp_path):
    """Actual isolated old-code process; only Cloud Run/GCS transport is replaced."""
    publisher, store, reader, plan, context = interrupted
    root = Path(__file__).resolve().parents[1]
    executor = tmp_path / 'original'
    executor.mkdir()
    import io
    import tarfile
    baseline = '3e5e03ea20759a24ca53d38d72254643d9807e8d'
    with tarfile.open(fileobj=io.BytesIO(subprocess.check_output(['git', 'archive', baseline], cwd=root))) as archive:
        archive.extractall(executor, filter='data')
    snapshot = tmp_path / 'store.json'
    snapshot.write_text(json.dumps({'plan': plan, 'execution': reader.value, 'serial': store.serial,
        'history': [{'version': asdict(version), 'data': base64.b64encode(data).decode()}
                    for version, data in store.history.values()],
        'current': {uri: version.generation for uri, version in store.objects.items()}}))
    driver = tmp_path / 'driver.py'
    driver.write_text('''import base64, importlib.util, json, pathlib, sys
from types import SimpleNamespace
sys.path.insert(0, sys.argv[1])
from ingestion.common import publication as p
from ingestion.common import source_field_publication as original
assert pathlib.Path(original.__file__).resolve().is_relative_to(pathlib.Path(sys.argv[1]).resolve())
spec=importlib.util.spec_from_file_location('reviewed_recovery',sys.argv[2])
recovery=importlib.util.module_from_spec(spec);spec.loader.exec_module(recovery)
class LostResponse(RuntimeError): pass
''' + inspect.getsource(MemoryStore) + '''
snapshot=json.loads(pathlib.Path(sys.argv[3]).read_text())
store=MemoryStore();store.serial=snapshot['serial']
for item in snapshot['history']:
    value=item['version'];value['metadata']=tuple(tuple(pair) for pair in value['metadata'])
    version=p.ObjectVersion(**value)
    store.history[(version.path,version.generation)]=(version,base64.b64decode(item['data']))
for uri,generation in snapshot['current'].items(): store.objects[uri]=store.history[(uri,generation)][0]
reader=SimpleNamespace(read=lambda url:snapshot['execution'])
publisher=SimpleNamespace(bucket=SimpleNamespace(name=recovery.BUCKET))
result=recovery.recover(publisher,store,snapshot['plan'],reader,check_authority_and_jobs=lambda:None,verify_image_executor=lambda image,sha:None)
assert result['status']=='completed'
print('Original archived publication modules + frozen base dependencies: complete')
''')
    result = subprocess.run(['uv', 'run', '--project', str(executor), '--frozen', '--no-dev', '--python', '3.12.12',
        'python', '-I', str(driver), str(executor), str(root / 'ingestion/mpatlas_monthly/publication_recovery.py'), str(snapshot)],
        cwd=root, env={**os.environ, 'UV_PROJECT_ENVIRONMENT': str(executor / '.venv')}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'Original archived publication modules' in result.stdout
