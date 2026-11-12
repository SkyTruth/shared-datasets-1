"""Observations cross the real CI/artifact/planning/signing boundaries, never apply."""
from __future__ import annotations

import copy
import io
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from scripts import ci_contract as ci, deployment_readiness as readiness
from scripts import deployment_emission as emissions, deployment_revision as revision
from scripts.deployment_receipt_contracts import readiness_boundary
from test_ci_preflight import fixture_repo, emit_plan, jobs_for, results_for
from test_sdk_release_authorization import fixture as sdk_fixture, archive, REPO, SHA

ROOT = Path(__file__).resolve().parents[1]
REPRESENTATIVES = ['sea_ice', 'pmtiles_cdn', 'catalog_viewer', 'artifact_registry_iam']
SECRET = 'sentinel-provider-state-and-permission-secret'


def gate_fixture(tmp_path, monkeypatch, *, attempt=2, approval='APPROVED', selection=None):
    api, _, _, artifacts, payloads = sdk_fixture(tmp_path, monkeypatch, gate_attempt=1)
    for index, key in ((0, 'evidence.json'), (1, 'plan.json')):
        import zipfile
        with zipfile.ZipFile(io.BytesIO(payloads[index])) as zipped:
            value = json.loads(zipped.read(key))
        if selection is None:
            value['readiness_targets'] = ['sea_ice']
        else:
            value.update({field: selection[field] for field in ('deployments', 'held_deployment_targets', 'readiness_targets')})
        payloads[index] = archive({key: value})
        import hashlib
        artifacts[index]['digest'] = 'sha256:' + hashlib.sha256(payloads[index]).hexdigest()
    repository = {'id': 9, 'full_name': REPO, 'default_branch': 'main'}
    pr = {'number': 27, 'state': 'closed', 'merged': True, 'merged_at': '2026-11-05T00:00:00Z',
          'merge_commit_sha': SHA, 'draft': False, 'user': {'login': 'contributor'},
          'head': {'sha': 'b' * 40, 'repo': repository}, 'base': {'ref': 'main', 'repo': repository}}
    original_get, original_pages = api.get.side_effect, api.pages.side_effect
    api.get.side_effect = lambda endpoint: (repository if endpoint == f'repos/{REPO}' else pr
        if endpoint == f'repos/{REPO}/pulls/27' else original_get(endpoint))
    api.pages.side_effect = lambda endpoint, field=None: ([pr] if '/commits/' in endpoint else
        [{'id': 1, 'pull_request_url': f'https://api.github.com/repos/{REPO}/pulls/27', 'state': approval,
          'user': {'login': 'jonaraphael'}, 'commit_id': 'b' * 40, 'submitted_at': '2026-11-04T00:00:00Z'}]
        if '/reviews?' in endpoint else original_pages(endpoint, field))
    monkeypatch.setattr(readiness.subprocess, 'check_output', lambda args, **kw: SHA if args[-1] == 'HEAD' else 'tree')
    env = {'GITHUB_REPOSITORY': REPO, 'GITHUB_REPOSITORY_ID': '9', 'EXECUTOR_SHA': SHA,
           'SOURCE_RUN_ID': '123', 'SOURCE_RUN_ATTEMPT': str(attempt), 'GITHUB_RUN_ID': '123',
           'GITHUB_RUN_ATTEMPT': str(attempt), 'GITHUB_EVENT_NAME': 'push', 'GITHUB_REF': 'refs/heads/main',
           'GITHUB_WORKFLOW_REF': REPO + '/' + readiness.CALLER + '@refs/heads/main',
           'GITHUB_WORKFLOW_SHA': SHA, 'GITHUB_SHA': SHA}
    return api, env


@pytest.mark.parametrize('defect', [None, 'event', 'ref', 'caller', 'executor', 'run', 'attempt', 'repository-id', 'approval'])
def test_exact_current_gate_and_acceptance_before_credentials(tmp_path, monkeypatch, defect):
    api, env = gate_fixture(tmp_path, monkeypatch, approval='DISMISSED' if defect == 'approval' else 'APPROVED')
    changes = {'event': ('GITHUB_EVENT_NAME', 'workflow_dispatch'), 'ref': ('GITHUB_REF', 'refs/pull/27/merge'),
               'caller': ('GITHUB_WORKFLOW_REF', REPO + '/' + readiness.LEAF + '@refs/heads/main'),
               'executor': ('EXECUTOR_SHA', 'c' * 40), 'run': ('SOURCE_RUN_ID', '124'),
               'attempt': ('SOURCE_RUN_ATTEMPT', '1'), 'repository-id': ('GITHUB_REPOSITORY_ID', '10')}
    if defect in changes:
        env[changes[defect][0]] = changes[defect][1]
    if defect:
        with pytest.raises((RuntimeError, ValueError)):
            readiness.authorize(api, environment=env)
    else:
        _, context = readiness.authorize(api, environment=env)
        assert context['invocation']['run_attempt'] == 2
        assert context['gate']['run_attempt'] == context['selector']['run_attempt'] == 1
        assert context['targets'] == ['sea_ice'] and len(context['gate_digest']) == 64


@pytest.mark.parametrize('extra,held,deployments,ready', [([], (), [], REPRESENTATIVES),
    (['ingestion/sea_ice_daily/run.py'], (), ['sea_ice'], REPRESENTATIVES[1:]),
    (['ingestion/sea_ice_daily/run.py'], ('sea_ice',), [], REPRESENTATIVES[1:])])
def test_actual_input_consumers_win_before_rollout_holds(extra, held, deployments, ready):
    plan = ci.resolve_selection(['scripts/tested_image_bundle.py', *extra], ingestion_holds=held)
    assert plan['deployments'] == deployments and plan['readiness_targets'] == ready
    assert plan['held_deployment_targets'] == list(held)
    ci.validate_obligations(plan)
    if held:
        assert 'sea-ice-daily' in ci.planned_images(plan)


def test_real_plan_transport_emits_readiness_only_after_successful_main_gate(tmp_path, monkeypatch):
    from scripts import ci_preflight as preflight
    root, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    source = root / 'scripts/deployment_revision.py'
    source.write_text(source.read_text() + '\n# Executor-only change\n')
    git('add', '.')
    git('commit', '-m', 'executor observation')
    plan, _ = emit_plan(root, base, tmp_path / 'selection')
    assert plan['deployments'] == [] and plan['readiness_targets'] == REPRESENTATIVES
    assert set(ci.verify_results(plan, results_for(plan), jobs_for(plan))) == set(plan['suites'])
    assert ci.delivery_outputs(plan, 'push', 'refs/heads/main')['deployment-readiness']
    assert not ci.delivery_outputs(plan, 'pull_request', 'refs/heads/main')['deployment-readiness']
    files = {name: tmp_path / name for name in ('plan.json', 'jobs.json', 'outputs', 'evidence.json')}
    files['plan.json'].write_text(json.dumps(plan))
    files['jobs.json'].write_text(json.dumps(jobs_for(plan)))
    for result in results_for(plan):
        directory = tmp_path / 'results' / result['suite']
        directory.mkdir(parents=True)
        (directory / 'result.json').write_text(json.dumps(result))
    monkeypatch.delenv('GITHUB_RUN_ID', raising=False)
    monkeypatch.setenv('GITHUB_EVENT_NAME', 'push')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.chdir(root)
    monkeypatch.setattr(sys, 'argv', ['ci_preflight.py', 'verify', '--plan', str(files['plan.json']),
        '--results', str(tmp_path / 'results'), '--jobs', str(files['jobs.json']), '--github-output', str(files['outputs']), '--output', str(files['evidence.json'])])
    assert preflight.main() == 0
    values = dict(line.split('=', 1) for line in files['outputs'].read_text().splitlines())
    assert 'readiness' not in values
    assert json.loads(files['evidence.json'].read_text())['readiness_targets'] == REPRESENTATIVES
    assert json.loads(values['delivery'])['deployment-readiness']
    result['status'] = 'failure'
    (directory / 'result.json').write_text(json.dumps(result))
    files['outputs'].unlink()
    files['evidence.json'].unlink()
    assert preflight.main() == 1
    assert not files['outputs'].exists()
    assert not files['evidence.json'].exists()


@pytest.mark.parametrize('remove', [False, True])
def test_narrowed_historical_obligations_survive_real_plan_and_gate_authorization(tmp_path, monkeypatch, remove):
    root, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    registry_path = root / '.github/terraform-targets.json'
    value = json.loads(registry_path.read_text())
    rules = value['selection_exceptions']
    if remove:
        del rules['scripts/tested_image_authorization.py']
    else:
        del rules['scripts/tested_image_authorization.py']['readiness_targets']['cron_alert_policy']
    registry_path.write_text(json.dumps(value))
    git('add', '.')
    git('commit', '-m', 'narrow executor readiness policy')
    selection, _ = emit_plan(root, base, tmp_path / 'selection')
    assert 'cron_alert_policy' in selection['readiness_targets'] and selection['deployments'] == []
    assert 'cron_alert_policy' not in {name for row in ci.executor_rules(value).values() for name in row}
    api, env = gate_fixture(tmp_path, monkeypatch, selection=selection)
    _, context = readiness.authorize(api, environment=env, root=root)
    assert context['targets'] == selection['readiness_targets']
    assert 'Cron alert policy sync' in {row['name'] for row in readiness.selected_scopes(context, root)}


@pytest.mark.parametrize('reverse', [False, True])
def test_executor_and_explicit_delivery_reclassification_keeps_the_declared_owner(tmp_path, reverse):
    root, git = fixture_repo(tmp_path)
    path = root / '.github/terraform-targets.json'
    value = json.loads(path.read_text())
    executor = value['selection_exceptions']['scripts/deployment_emission.py']
    desired_input = {'kind': 'include', 'reason': 'Explicit desired-state consumer', 'deployments': {'sea_ice': 'Owned input'}}
    value['selection_exceptions']['scripts/deployment_emission.py'] = desired_input if reverse else executor
    path.write_text(json.dumps(value))
    git('add', '.')
    git('commit', '-m', 'initial input policy')
    base = git('rev-parse', 'HEAD')
    value['selection_exceptions']['scripts/deployment_emission.py'] = executor if reverse else desired_input
    path.write_text(json.dumps(value))
    git('add', '.')
    git('commit', '-m', 'reclassify desired-state input')
    selection, _ = emit_plan(root, base, tmp_path / 'selection')
    assert selection['deployments'] == ['sea_ice']
    assert 'sea_ice' not in selection['readiness_targets']


@pytest.mark.parametrize('rename', [False, True])
def test_copied_executor_input_keeps_its_real_consumer_across_removal(tmp_path, rename):
    root, git = fixture_repo(tmp_path)
    recipe = root / 'ingestion/mpatlas_monthly/Dockerfile'
    helper = root / 'scripts/fixture_executor.py'
    helper.write_text('VALUE = 1\n')
    registry_path = root / '.github/terraform-targets.json'
    value = json.loads(registry_path.read_text())
    value['selection_exceptions']['scripts/fixture_executor.py'] = {
        'kind': 'executor', 'reason': 'Fixture executor is also a runtime image input',
        'readiness_targets': dict.fromkeys(REPRESENTATIVES, 'Representative observation')}
    registry_path.write_text(json.dumps(value))
    recipe.write_text(recipe.read_text() + '\nCOPY scripts/fixture_executor.py /app/scripts/\n')
    git('add', '.')
    git('commit', '-m', 'runtime helper consumer')
    base = git('rev-parse', 'HEAD')
    helper.write_text(helper.read_text() + '\n# Changed runtime input\n')
    selected = ci.resolve_selection(['scripts/fixture_executor.py'], base=base, root=root)
    assert 'mpatlas' in selected['deployments'] and 'mpatlas-monthly' in selected['production_image_targets']
    if rename:
        helper.rename(root / 'scripts/renamed_executor.py')
        recipe.write_text(recipe.read_text().replace('fixture_executor.py', 'renamed_executor.py'))
    else:
        helper.unlink()
        recipe.write_text(recipe.read_text().replace('COPY scripts/fixture_executor.py /app/scripts/\n', ''))
    git('add', '.')
    git('commit', '-m', 'remove previous input')
    plan, _ = emit_plan(root, base, tmp_path / 'selection')
    assert 'mpatlas' in plan['deployments'] and 'mpatlas-monthly' in plan['production_image_targets']


def test_policy_migration_does_not_manufacture_desired_state_changes():
    current = json.loads((ROOT / '.github/terraform-targets.json').read_text())
    previous = copy.deepcopy(current)
    for row in previous['selection_exceptions'].values():
        if row['kind'] == 'executor':
            row['kind'] = 'exclude'
            row['deployments'] = row.pop('readiness_targets')
    assert ci.registry_deployment_targets(json.dumps(previous), json.dumps(current)) == set()
    changed = copy.deepcopy(current)
    changed['targets']['Artifact Registry IAM sync']['post_apply_wait_seconds'] += 1
    assert ci.registry_deployment_targets(json.dumps(current), json.dumps(changed)) == {'artifact_registry_iam'}


def test_current_delivery_images_cannot_silently_lose_their_copy_inputs(tmp_path):
    root, _ = fixture_repo(tmp_path)
    path = root / '.github/terraform-targets.json'
    value = json.loads(path.read_text())
    value['workflows']['mpatlas-monthly-deploy.yml']['image'] = 'unregistered-image'
    path.write_text(json.dumps(value))
    with pytest.raises(KeyError, match='unregistered-image'):
        ci.resolve_selection(['.github/terraform-targets.json'], root=root)


@pytest.mark.parametrize('change,expected', [('edit', {'sea_ice'}), ('add', {'eamlis'}), ('remove', {'sea_ice'})])
def test_include_rule_edits_change_only_the_affected_co_owner(change, expected):
    before = json.loads((ROOT / '.github/terraform-targets.json').read_text())
    rule = before['selection_exceptions']['terraform/envs/prod/.terraform.lock.hcl']
    rule['deployments'] = {'sea_ice': 'Sea ice input', 'mpatlas': 'MPAtlas input'}
    after = copy.deepcopy(before)
    owners = after['selection_exceptions']['terraform/envs/prod/.terraform.lock.hcl']['deployments']
    if change == 'edit':
        owners['sea_ice'] = 'Updated sea ice obligation'
    elif change == 'add':
        owners['eamlis'] = 'EAMLIS input'
    else:
        del owners['sea_ice']
    assert ci.registry_deployment_targets(json.dumps(before), json.dumps(after)) == expected


def test_authorization_refusal_keeps_its_safe_diagnostic(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', ['deployment_readiness.py', 'authorize', '--context', str(tmp_path / 'context.json'),
                                     '--result', str(tmp_path / 'result.json')])
    with patch.object(readiness, 'authorize', side_effect=ValueError('Exact-head owner approval is missing')), pytest.raises(SystemExit):
        readiness.main()
    assert 'Exact-head owner approval is missing' in capsys.readouterr().err


def test_cli_redacts_provider_or_permission_failures(tmp_path, monkeypatch, capsys):
    context, result = tmp_path / 'context.json', tmp_path / 'result.json'
    context.write_text('{}')
    monkeypatch.setattr(sys, 'argv', ['deployment_readiness.py', 'check', '--context', str(context), '--result', str(result)])
    for name in ('terraform', 'runtime', 'observer'):
        monkeypatch.setenv('READINESS_TOKEN_' + name.upper(), 'token')
    monkeypatch.setenv('TERRAFORM_SERVICE_ACCOUNT', 'deployer@shared-datasets-1.iam.gserviceaccount.com')
    with patch.object(readiness, 'check', side_effect=RuntimeError(SECRET)), pytest.raises(SystemExit):
        readiness.main()
    assert SECRET not in capsys.readouterr().err and not result.exists()


@pytest.mark.parametrize('defect', ['missing', 'unknown', 'duplicate', 'overlap', 'held-overlap', 'ordering'])
def test_obligation_partition_refuses_malformed_transport(defect):
    plan = dict(deployments=[], held_deployment_targets=[], readiness_targets=REPRESENTATIVES[:])
    if defect == 'missing':
        del plan['readiness_targets']
    elif defect == 'unknown':
        plan['readiness_targets'] += ['invented']
    elif defect == 'duplicate':
        plan['readiness_targets'] += ['sea_ice']
    elif defect == 'overlap':
        plan['deployments'] = ['sea_ice']
    elif defect == 'held-overlap':
        plan['held_deployment_targets'] = ['sea_ice']
    else:
        plan['readiness_targets'].reverse()
    with pytest.raises(ValueError):
        ci.validate_obligations(plan)


def test_readiness_workflow_cannot_become_a_mutation_adapter(tmp_path):
    assert readiness_boundary(ROOT) == []
    ci_workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    caller = ci_workflow['deployment-readiness']
    assert caller['if'] == ci.delivery_condition('deployment-readiness')
    assert caller['needs'] == ['ci-ready', 'artifact-registry-iam'] and 'secrets' not in caller
    assert all('deployment-readiness' not in job.get('needs', []) for job in ci_workflow.values())
    value = yaml.safe_load((ROOT / readiness.LEAF).read_text())
    directory = tmp_path / '.github/workflows'
    directory.mkdir(parents=True)
    # A direct inserted mutation must fail the dedicated workflow boundary.
    value['jobs']['readiness']['steps'].append({'run': 'terraform apply retained.tfplan'})
    (directory / Path(readiness.LEAF).name).write_text(yaml.safe_dump(value))
    (tmp_path / '.github/actions').symlink_to(ROOT / '.github/actions', target_is_directory=True)
    assert readiness_boundary(tmp_path)


@pytest.mark.parametrize('target', ['sea_ice', 'cron_alert_policy'])
@pytest.mark.parametrize('denied,selected_tag', [(False, False), (True, False), (False, True)])
def test_real_read_only_planner_and_principal_http_probes(tmp_path, monkeypatch, capsys, denied, selected_tag, target):
    image = 'us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/test@sha256:' + 'a' * 64
    if selected_tag:
        image = 'unusable:tag'
    container = {'image': image, 'env': []}
    if target == 'sea_ice':
        resource, kind = 'module.sea_ice_daily_job.google_cloud_run_v2_job.this', 'google_cloud_run_v2_job'
        after = {'template': [{'template': [{'containers': [container]}]}]}
    else:
        resource, kind = 'google_cloud_run_v2_service.monitoring_slack', 'google_cloud_run_v2_service'
        container['env'] = [{'name': 'SHARED_DATASETS_SLACK_CHANNEL_ID', 'value': 'channel'},
            {'name': 'SHARED_DATASETS_SLACK_BOT_TOKEN', 'value_source': [{'secret_key_ref': [{'version': '1'}]}]}]
        after = {'template': [{'containers': [container]}]}
    # An unrelated historical tag is legitimate state, not an input to these scopes.
    state = {'values': {'root_module': {'resources': [{'address': resource, 'type': kind, 'values': after},
        {'address': 'module.wdpa_monthly_job.google_cloud_run_v2_job.this', 'type': 'google_cloud_run_v2_job',
         'values': {'template': [{'template': [{'containers': [{'image': 'held:historical', 'env': []}]}]}]}}]}}}
    saved = {'format_version': '1.2', 'resource_changes': [{'address': resource, 'type': kind,
             'change': {'actions': ['no-op'], 'before': after, 'after': {**after, 'description': SECRET}}}]}
    calls, principals, probes = [], [], []
    def process(command, **kwargs):
        calls.append(command)
        assert command[0] == 'terraform' and command[2] in {'version', 'init', 'show', 'plan'}
        assert not any(k.startswith(('TF_LOG', 'TF_CLI_ARGS', 'TF_VAR_', 'READINESS_TOKEN_')) for k in kwargs['env'])
        assert kwargs['env']['TF_WORKSPACE'] == 'default'
        if command[2] == 'plan':
            assert '-lock=false' in command
        payload = {'terraform_version': '1.8.5'} if command[2] == 'version' else saved if command[-1].endswith('.tfplan') else state
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), SECRET)
    def transport(req, **kwargs):
        token = req.headers['Authorization'].removeprefix('Bearer ')
        principals.append(token)
        if req.full_url.endswith('/projects/shared-datasets-1'):
            return io.BytesIO(b'{"projectNumber":"123"}')
        if req.get_method() == 'GET':
            assert req.full_url.startswith('https://storage.googleapis.com/storage/v1/b/') and '/iam/testPermissions?' in req.full_url
            permissions = parse_qs(urlsplit(req.full_url).query)['permissions']
        else:
            assert req.get_method() == 'POST' and req.full_url.endswith((':testIamPermissions', '/testIamPermissions'))
            permissions = json.loads(req.data)['permissions']
        probes.append((token, permissions))
        denied_operation = token == 'runtime-token' if target == 'sea_ice' else token == 'terraform-token' and 'artifactregistry.repositories.uploadArtifacts' in permissions
        if denied and denied_operation:
            raise RuntimeError(SECRET)
        return io.BytesIO(json.dumps({'permissions': permissions}).encode())
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    monkeypatch.setenv('TF_LOG', SECRET)
    monkeypatch.setenv('TF_CLI_ARGS_plan', '-destroy')
    monkeypatch.setenv('READINESS_TOKEN_RUNTIME', 'runtime-token')
    context = {'targets': [target]}
    with patch.object(readiness.subprocess, 'run', side_effect=process), patch.object(readiness.urllib.request, 'urlopen', side_effect=transport):
        if selected_tag:
            with pytest.raises(ValueError, match='not immutable'):
                readiness.check(context, dict(terraform='terraform-token', runtime='runtime-token', observer='observer-token'),
                                'deployer@shared-datasets-1.iam.gserviceaccount.com')
            assert not any(command[2] == 'plan' for command in calls)
        elif denied:
            with pytest.raises(RuntimeError):
                readiness.check(context, dict(terraform='terraform-token', runtime='runtime-token', observer='observer-token'),
                                'deployer@shared-datasets-1.iam.gserviceaccount.com')
        else:
            value = readiness.check(context, dict(terraform='terraform-token', runtime='runtime-token', observer='observer-token'),
                                    'deployer@shared-datasets-1.iam.gserviceaccount.com')
            assert value['schema'] == readiness.SCHEMA and value['status'] == 'ready'
            assert SECRET not in readiness.canonical(value).decode()
            if target == 'sea_ice':
                assert {'runtime-token', 'observer-token', 'terraform-token'} <= set(principals)
                runtime = {permission for token, permissions in probes if token == 'runtime-token' for permission in permissions}
                assert {'run.jobs.list', 'run.executions.cancel'} <= runtime
            else:
                assert any(token == 'terraform-token' and 'artifactregistry.repositories.uploadArtifacts' in permissions for token, permissions in probes)
            with pytest.raises(RuntimeError, match='unrecognized deployment record'):
                revision.record_payload(value)
    assert SECRET not in capsys.readouterr().out
    assert calls and not list(tmp_path.glob('pr-terraform-*'))


@pytest.mark.parametrize('defect', [None, 'unsigned', 'subject', 'digest', 'leaf', 'sha', 'repository', 'attempt', 'runner', 'timestamp', 'multiple'])
def test_signed_result_is_exact_and_never_a_deployment_receipt(tmp_path, monkeypatch, defect):
    api, env = gate_fixture(tmp_path, monkeypatch)
    run, context = readiness.authorize(api, environment=env)
    value = {'schema': readiness.SCHEMA, 'status': 'ready', 'context': context, 'scopes': [{'scope': 'sea-ice-daily'}]}
    path = tmp_path / 'readiness-123-attempt2.json'
    path.write_bytes(readiness.canonical(value))
    expected = emissions.run_policy(REPO, 9, run, readiness.LEAF)
    import hashlib
    subject = {'name': path.name, 'digest': {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}
    result = {'verificationResult': {'signature': {'certificate': copy.deepcopy(expected)}, 'verifiedTimestamps': ['verified'],
              'statement': {'_type': 'https://in-toto.io/Statement/v1', 'predicateType': emissions.PREDICATE, 'subject': [subject]}}}
    verified = result['verificationResult']
    if defect in {'leaf', 'sha', 'repository', 'attempt', 'runner'}:
        field = {'leaf': 'buildSignerURI', 'sha': 'sourceRepositoryDigest', 'repository': 'sourceRepositoryIdentifier',
                 'attempt': 'runInvocationURI', 'runner': 'runnerEnvironment'}[defect]
        verified['signature']['certificate'][field] = 'wrong'
    elif defect == 'timestamp':
        verified['verifiedTimestamps'] = []
    elif defect == 'multiple':
        verified['statement']['subject'].append({'name': 'another'})
    elif defect == 'subject':
        verified['statement']['subject'][0] = {'name': 'another'}
    elif defect == 'digest':
        verified['statement']['subject'][0] = {**subject, 'digest': {'sha256': '0' * 64}}
    monkeypatch.setattr(emissions, 'verified_output', lambda *args: json.dumps([] if defect == 'unsigned' else [result]))
    if defect:
        with pytest.raises(RuntimeError):
            readiness.verify_result(value, context, run, path)
    else:
        readiness.verify_result(value, context, run, path)
        with pytest.raises(RuntimeError):
            revision.record_payload(value)
