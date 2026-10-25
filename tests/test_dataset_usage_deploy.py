from __future__ import annotations

import copy
import datetime as dt
import json
import re
from pathlib import Path
from unittest.mock import Mock

import pytest

from ingestion.dataset_usage.run import COST_COMPONENTS, TRAFFIC_CHECKS
from scripts import dataset_usage_deploy
from scripts.dataset_usage_deploy import validate_plan, validate_rollout, verify_reader_ready
from scripts.terraform_plan_permissions import plan_checks
from workflow_helpers import load_workflow

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/dataset-usage@sha256:' + 'a' * 64


def test_writer_requires_latest_verified_reader_and_matching_bytes(monkeypatch):
    api = Mock()
    latest = {'id': 2, 'sha': 'a' * 40}
    api.pages.return_value = [{'id': 1, 'sha': 'b' * 40}, latest]
    verify = Mock(return_value={'target': 'catalog-viewer'})
    monkeypatch.setattr(dataset_usage_deploy.deployment_revision, 'verify_record', verify)
    statuses = Mock(return_value=[{'state': 'success', 'description': 'verified'}])
    monkeypatch.setattr(dataset_usage_deploy.deployment_revision, 'verified_statuses', statuses)
    contents = [(ROOT / path).read_bytes() for path in dataset_usage_deploy.READER_FILES]
    reader = Mock(side_effect=contents)
    monkeypatch.setattr(dataset_usage_deploy.subprocess, 'check_output', reader)
    verify_reader_ready(ROOT, api, 'SkyTruth/shared-datasets-1')
    verify.assert_called_once_with(api, 'SkyTruth/shared-datasets-1', latest)
    statuses.return_value = [{'state': 'failure', 'description': 'failed'}]
    with pytest.raises(ValueError, match='not verified'):
        verify_reader_ready(ROOT, api, 'SkyTruth/shared-datasets-1')
    statuses.return_value = [{'state': 'success', 'description': 'verified'}]
    reader.side_effect = [b'old reader']
    with pytest.raises(ValueError, match='current compatible'):
        verify_reader_ready(ROOT, api, 'SkyTruth/shared-datasets-1')
    api.pages.return_value = []
    with pytest.raises(ValueError, match='before the usage writer'):
        verify_reader_ready(ROOT, api, 'SkyTruth/shared-datasets-1')


def resource(address, after, before=None, actions=None):
    return {'address': address, 'mode': 'managed', 'type': address.split('.')[-2], 'change': {'actions': actions or ['update'], 'before': before, 'after': after, 'after_unknown': {}}}


def test_shared_bucket_logging_update_cannot_change_iam_retention_cors_or_security():
    before = {'name': 'skytruth-shared-datasets-1', 'project': 'shared-datasets-1', 'uniform_bucket_level_access': True, 'public_access_prevention': 'inherited', 'retention_policy': [], 'cors': [{'origin': ['example']}], 'lifecycle_rule': [], 'logging': []}
    after = {**before, 'logging': [{'log_bucket': 'skytruth-shared-datasets-1-usage-raw', 'log_object_prefix': 'storage-usage'}]}
    raw = {'name': 'skytruth-shared-datasets-1-usage-raw', 'project': 'shared-datasets-1'}
    plan = {'resource_changes': [resource('google_storage_bucket.shared_bucket', after, before), resource('google_storage_bucket.dataset_usage_raw', raw, raw, ['no-op'])]}
    validate_plan(plan, IMAGE, verified=False)
    for field, value in [('uniform_bucket_level_access', False), ('public_access_prevention', 'enforced'), ('retention_policy', [{}]), ('cors', []), ('lifecycle_rule', [{}])]:
        changed = copy.deepcopy(plan)
        changed['resource_changes'][0]['change']['after'][field] = value
        with pytest.raises(ValueError, match='outside logging'):
            validate_plan(changed, IMAGE, verified=False)
    changed = copy.deepcopy(plan)
    changed['resource_changes'][0]['change']['after_unknown']['cors'] = True
    with pytest.raises(ValueError, match='Unknown shared bucket'):
        validate_plan(changed, IMAGE, verified=False)


def test_logging_activation_requires_already_provisioned_exact_raw_bucket():
    shared = {'name': 'skytruth-shared-datasets-1', 'logging': [{'log_bucket': 'skytruth-shared-datasets-1-usage-raw', 'log_object_prefix': 'storage-usage'}]}
    raw = {'name': 'skytruth-shared-datasets-1-usage-raw', 'public_access_prevention': 'enforced', 'uniform_bucket_level_access': True, 'force_destroy': False, 'lifecycle_rule': [{'action': [{'type': 'Delete'}], 'condition': [{'age': 7}]}]}
    plan = {'resource_changes': [resource('google_storage_bucket.shared_bucket', shared, shared, ['no-op']), resource('google_storage_bucket.dataset_usage_raw', raw, actions=['create'])]}
    with pytest.raises(ValueError, match='collection disabled'):
        validate_plan(plan, IMAGE, verified=False)
    plan['resource_changes'][1]['change']['actions'] = ['no-op']
    validate_plan(plan, IMAGE, verified=False)
    for mutation in ('foreign-name', 'missing-raw', 'foreign-logging', 'wrong-prefix'):
        changed = copy.deepcopy(plan)
        if mutation == 'missing-raw':
            changed['resource_changes'].pop()
        elif mutation == 'foreign-name':
            changed['resource_changes'][1]['change']['after']['name'] = 'unreviewed-bucket'
        else:
            changed['resource_changes'][0]['change']['after']['logging'][0]['log_bucket' if mutation == 'foreign-logging' else 'log_object_prefix'] = 'unreviewed'
        with pytest.raises(ValueError, match='exact reviewed usage bucket'):
            validate_plan(changed, IMAGE, verified=False)
    # Initial bootstrap can create its private bucket while logging is disabled.
    plan['resource_changes'][0]['change']['after']['logging'] = []
    plan['resource_changes'][1]['change']['actions'] = ['create']
    validate_plan(plan, IMAGE, verified=False)


@pytest.mark.parametrize('logging,unknown', [
    (None, True),
    ([], True),
    ([{'log_bucket': None, 'log_object_prefix': 'storage-usage'}], [{'log_bucket': True}]),
    ([{'log_bucket': 'skytruth-shared-datasets-1-usage-raw', 'log_object_prefix': None}], [{'log_object_prefix': True}]),
])
def test_unknown_logging_cannot_bypass_bucket_activation_guard(logging, unknown):
    shared = {'name': 'skytruth-shared-datasets-1', 'logging': logging}
    raw = {'name': 'skytruth-shared-datasets-1-usage-raw', 'public_access_prevention': 'enforced', 'uniform_bucket_level_access': True, 'force_destroy': False}
    plan = {'resource_changes': [resource('google_storage_bucket.shared_bucket', shared, shared), resource('google_storage_bucket.dataset_usage_raw', raw, actions=['create'])]}
    plan['resource_changes'][0]['change']['after_unknown']['logging'] = unknown
    with pytest.raises(ValueError, match='Unknown shared bucket logging'):
        validate_plan(plan, IMAGE, verified=False)


@pytest.mark.parametrize('address', ['google_storage_bucket.shared_bucket', 'google_storage_bucket.dataset_usage_raw'])
def test_logging_activation_rejects_unknown_bucket_identity(address):
    shared = {'name': 'skytruth-shared-datasets-1', 'logging': [{'log_bucket': 'skytruth-shared-datasets-1-usage-raw', 'log_object_prefix': 'storage-usage'}]}
    raw = {'name': 'skytruth-shared-datasets-1-usage-raw'}
    plan = {'resource_changes': [resource('google_storage_bucket.shared_bucket', shared, shared, ['no-op']), resource('google_storage_bucket.dataset_usage_raw', raw, raw, ['no-op'])]}
    next(row for row in plan['resource_changes'] if row['address'] == address)['change']['after_unknown']['name'] = True
    with pytest.raises(ValueError, match='Unknown usage bucket logging identity'):
        validate_plan(plan, IMAGE, verified=False)


@pytest.mark.parametrize('actions', [['delete'], ['delete', 'create'], ['create', 'delete']])
def test_no_destructive_usage_operations_or_unrelated_resources(actions):
    with pytest.raises(ValueError, match='destructive'):
        validate_plan({'resource_changes': [resource('google_storage_bucket.dataset_usage_raw', {}, actions=actions)]}, IMAGE, verified=False)
    with pytest.raises(ValueError, match='Unrelated'):
        validate_plan({'resource_changes': [resource('module.sea_ice_daily_job.google_cloud_run_v2_job.this', {})]}, IMAGE, verified=False)


def test_shared_security_auditing_cannot_be_changed_by_usage_deployment():
    before = {'project': 'shared-datasets-1', 'service': 'storage.googleapis.com', 'audit_log_config': [{'log_type': 'DATA_WRITE', 'exempted_members': []}]}
    validate_plan({'resource_changes': [resource('google_project_iam_audit_config.storage_data_write', before, before)]}, IMAGE, verified=False)
    after = {**before, 'audit_log_config': [*before['audit_log_config'], {'log_type': 'DATA_READ', 'exempted_members': []}]}
    with pytest.raises(ValueError, match='must remain unchanged'):
        validate_plan({'resource_changes': [resource('google_project_iam_audit_config.storage_data_write', after, before)]}, IMAGE, verified=False)
    after['audit_log_config'] = [{'log_type': 'DATA_READ'}]
    with pytest.raises(ValueError, match='Write auditing'):
        validate_plan({'resource_changes': [resource('google_project_iam_audit_config.storage_data_write', after, before)]}, IMAGE, verified=False)


def test_unverified_schedule_and_public_telemetry_refused():
    after = {'paused': False, 'schedule': '0 9 * * *', 'time_zone': 'UTC', 'retry_config': [{'retry_count': 0}]}
    with pytest.raises(ValueError, match='Unverified'):
        validate_plan({'resource_changes': [resource('module.dataset_usage_scheduler.google_cloud_scheduler_job.this', after)]}, IMAGE, verified=False)
    after['paused'] = True
    validate_plan({'resource_changes': [resource('module.dataset_usage_scheduler.google_cloud_scheduler_job.this', after)]}, IMAGE, verified=False)
    with pytest.raises(ValueError, match='never be public'):
        validate_plan({'resource_changes': [resource('google_storage_bucket_iam_member.dataset_usage_raw_reader', {'member': 'allUsers'})]}, IMAGE, verified=False)




@pytest.mark.parametrize('producer', sorted(dataset_usage_deploy.RETIRED_APP_WRITERS))
def test_retired_receipt_grants_can_only_be_removed_at_their_exact_existing_scope(producer):
    address = f'google_storage_bucket_iam_member.dataset_usage_app_writer["{producer}"]'
    grant = {'bucket': 'skytruth-shared-datasets-1-usage-raw', 'role': 'roles/storage.objectCreator',
             'member': 'serviceAccount:' + dataset_usage_deploy.RETIRED_APP_WRITERS[producer],
             'condition': [{'title': 'application_usage_' + producer.replace('-', '_'),
                            'expression': f"resource.name.startsWith('projects/_/buckets/skytruth-shared-datasets-1-usage-raw/objects/app-usage/{producer}/')"}]}
    item = resource(address, None, grant, ['delete'])
    validate_plan({'resource_changes': [item]}, IMAGE, verified=False)
    for field, value in [('bucket', 'skytruth-shared-datasets-1'), ('role', 'roles/storage.objectViewer'),
                         ('member', 'serviceAccount:other@x30-399415.iam.gserviceaccount.com'),
                         ('condition', []),
                         ('condition', [{'title': 'unexpected', 'expression': grant['condition'][0]['expression']}]),
                         ('condition', [{'title': grant['condition'][0]['title'], 'expression': 'true'}])]:
        changed = copy.deepcopy(item)
        changed['change']['before'][field] = value
        with pytest.raises(ValueError):
            validate_plan({'resource_changes': [changed]}, IMAGE, verified=False)
    for actions in (['create'], ['delete', 'create']):
        with pytest.raises(ValueError):
            validate_plan({'resource_changes': [resource(address, grant, grant, actions)]}, IMAGE, verified=False)
    unrelated = resource('google_storage_bucket_iam_member.dataset_usage_app_writer["unknown-production"]', None, grant, ['delete'])
    with pytest.raises(ValueError):
        validate_plan({'resource_changes': [unrelated]}, IMAGE, verified=False, retirement_verified=True)


def test_retirement_requires_native_health_without_any_application_receipts(tmp_path):
    directory = tmp_path / 'catalog'
    directory.mkdir()
    for name in ('dataset-usage.json', 'dataset-usage-activation.json', 'shared-datasets-catalog.csv'):
        (directory / name).write_bytes((ROOT / 'catalog' / name).read_bytes())
    now = dt.datetime.now(dt.UTC)
    activation = json.loads((directory / 'dataset-usage-activation.json').read_text())
    activation['configuration_sha256'] = dataset_usage_deploy.Classifier(
        dataset_usage_deploy.read_catalog_rows_text((directory / 'shared-datasets-catalog.csv').read_text()),
        json.loads((directory / 'dataset-usage.json').read_text()), 'skytruth-shared-datasets-1').version
    for source, entry in activation['sources'].items():
        entry.update(verified_at=(now - dt.timedelta(days=4)).isoformat(), checks=dict.fromkeys(TRAFFIC_CHECKS[source], True))
    (directory / 'dataset-usage-activation.json').write_text(json.dumps(activation))
    payload = {'schema_version': 3, 'collection_errors': [], 'generated_at': now.isoformat(),
               'source_health': [{'source': source, 'healthy_days': 2} for source in TRAFFIC_CHECKS],
               'coverage': [{'source': source, 'day': (now - dt.timedelta(days=days)).date().isoformat(), 'state': 'healthy'}
                            for source in TRAFFIC_CHECKS for days in (3, 2)],
               }
    assert dataset_usage_deploy.retirement_ready(tmp_path, payload)
    payload['coverage'].pop()
    assert not dataset_usage_deploy.retirement_ready(tmp_path, payload)


def test_native_probe_scope_is_additive_and_legacy_grant_deletes_only_after_proof():
    condition = "resource.name == 'projects/_/buckets/skytruth-shared-datasets-1/objects/100-geographic-reference/110-boundaries/lsib/latest/lsib.fgb'"
    grant = {'bucket': 'skytruth-shared-datasets-1', 'role': 'roles/storage.objectViewer',
             'member': 'serviceAccount:dataset-usage@shared-datasets-1.iam.gserviceaccount.com',
             'condition': [{'title': 'usage_native_probe_sentinel_only', 'expression': condition}]}
    address = 'google_storage_bucket_iam_member.dataset_usage_native_probe_reader'
    validate_plan({'resource_changes': [resource(address, grant, actions=['create'])]}, IMAGE, verified=False)
    for field, value in [('role', 'roles/storage.objectAdmin'), ('member', 'serviceAccount:other@example.iam.gserviceaccount.com'), ('condition', [{'expression': 'true'}])]:
        changed = {**grant, field: value}
        with pytest.raises(ValueError, match='exact registered scope'):
            validate_plan({'resource_changes': [resource(address, changed)]}, IMAGE, verified=False)
    legacy = copy.deepcopy(grant)
    legacy['condition'][0]['title'] = 'usage_probe_sentinels_only'
    legacy['bucket'] = 'b/skytruth-shared-datasets-1'
    legacy['condition'][0]['expression'] += " || resource.name == 'projects/_/buckets/skytruth-shared-datasets-1/objects/300-infrastructure-industrial/320-mining/wri-forest-atlas-drc-mining-permits/latest/wri-forest-atlas-drc-mining-permits.fgb' || resource.name == 'projects/_/buckets/skytruth-shared-datasets-1/objects/_catalog/shared-datasets-catalog.csv'"
    item = resource('google_storage_bucket_iam_member.dataset_usage_probe_reader', None, legacy, ['delete'])
    with pytest.raises(ValueError, match='replacement evidence'):
        validate_plan({'resource_changes': [item]}, IMAGE, verified=True)
    validate_plan({'resource_changes': [item]}, IMAGE, verified=True, retirement_verified=True)
    item['change']['before']['condition'][0]['expression'] = 'true'
    with pytest.raises(ValueError, match='exact registered scope'):
        validate_plan({'resource_changes': [item]}, IMAGE, verified=True, retirement_verified=True)


def test_health_role_cannot_gain_object_or_project_authority():
    role = {'project': 'shared-datasets-1', 'role_id': 'sharedDatasetsUsageBucketHealth',
            'permissions': ['storage.buckets.get', 'storage.buckets.getIamPolicy']}
    address = 'google_project_iam_custom_role.dataset_usage_bucket_health'
    validate_plan({'resource_changes': [resource(address, role)]}, IMAGE, verified=False)
    for permission in ['storage.objects.get', 'storage.objects.delete', 'resourcemanager.projects.getIamPolicy']:
        with pytest.raises(ValueError, match='bucket-read-only'):
            validate_plan({'resource_changes': [resource(address, {**role, 'permissions': [*role['permissions'], permission]})]}, IMAGE, verified=False)


@pytest.mark.parametrize('address,after', [
    ('google_project_iam_custom_role.dataset_usage_health', {'project': 'shared-datasets-1', 'role_id': 'sharedDatasetsUsageHealth', 'permissions': ['resourcemanager.projects.setIamPolicy']}),
    ('google_project_iam_member.dataset_usage_health', {'project': 'shared-datasets-1', 'member': 'allUsers', 'role': 'roles/owner'}),
    ('google_service_account_iam_member.dataset_usage_deployer_act_as', {'member': 'allUsers', 'role': 'roles/iam.serviceAccountUser'}),
    ('google_cloud_run_v2_job_iam_member.dataset_usage_scheduler_invoker', {'project': 'shared-datasets-1', 'location': 'us-central1', 'name': 'dataset-usage', 'member': 'allUsers', 'role': 'roles/run.invoker'}),
    ('module.dataset_usage_job.google_cloud_run_v2_job.this', {'template': [{'template': [{'service_account': 'other@shared-datasets-1.iam.gserviceaccount.com'}]}]}),
    ('google_storage_bucket_iam_member.dataset_usage_sink_writer', {'bucket': 'skytruth-shared-datasets-1-usage-raw', 'member': 'allUsers', 'role': 'roles/storage.objectAdmin'}),
])
def test_existing_usage_authority_cannot_be_broadened(address, after):
    with pytest.raises(ValueError):
        validate_plan({'resource_changes': [resource(address, after)]}, IMAGE, verified=False)


def test_exact_legacy_deletion_requires_replacement_proof_and_rejects_replacements():
    before = {'project': 'shared-datasets-1', 'name': 'dataset-usage', 'disabled': True}
    item = resource('google_logging_project_sink.dataset_usage', None, before, actions=['delete'])
    with pytest.raises(ValueError, match='replacement evidence'):
        validate_plan({'resource_changes': [item]}, IMAGE, verified=False)
    validate_plan({'resource_changes': [item]}, IMAGE, verified=True, retirement_verified=True)
    for changes in ({'name': 'other'}, {'disabled': False}, {'project': 'other'}):
        changed = copy.deepcopy(item)
        changed['change']['before'].update(changes)
        with pytest.raises(ValueError):
            validate_plan({'resource_changes': [changed]}, IMAGE, verified=True, retirement_verified=True)
    for address, actions in [('google_storage_bucket.dataset_usage_raw', ['delete']),
                             ('google_logging_project_sink.dataset_usage', ['delete', 'create'])]:
        with pytest.raises(ValueError):
            validate_plan({'resource_changes': [resource(address, {}, before, actions)]}, IMAGE, verified=True, retirement_verified=True)


def test_collection_cost_gate_requires_measured_incremental_evidence(tmp_path):
    directory = tmp_path / 'catalog'
    directory.mkdir()
    for name in ('dataset-usage.json', 'dataset-usage-activation.json', 'shared-datasets-catalog.csv'):
        (directory/name).write_bytes((ROOT/'catalog'/name).read_bytes())
    assert validate_rollout(tmp_path) is False
    config = json.loads((directory/'dataset-usage.json').read_text())
    config['collection_enabled'] = True
    (directory/'dataset-usage.json').write_text(json.dumps(config))
    for amount in (None, -1, 26):
        activation = {'estimated_monthly_cost_usd': amount, 'evidence': 'reviewed measurements'}
        (directory/'dataset-usage-activation.json').write_text(json.dumps(activation))
        with pytest.raises(ValueError, match='cost evidence'):
            validate_rollout(tmp_path)
    (directory/'dataset-usage-activation.json').write_text(json.dumps({'estimated_monthly_cost_usd': 20, 'evidence': {'cost': {'measurement_days': 7, 'scope': 'tracker_incremental', 'monthly_usd': {key: (20 if key == 'worker' else 0) for key in COST_COMPONENTS}}}}))
    assert validate_rollout(tmp_path) is False  # Collection canary only; schedule remains paused.


def test_saved_plan_permissions_cover_logging_and_audit_configuration():
    plan = {'format_version': '1.2', 'resource_changes': [resource('google_logging_project_sink.dataset_usage', {'project': 'shared-datasets-1', 'name': 'dataset-usage'}, actions=['create']), resource('google_logging_project_exclusion.dataset_usage_duplicate', {'project': 'shared-datasets-1', 'name': 'dataset-usage-exported-copy'}, actions=['create']), resource('google_project_iam_audit_config.storage_data_write', {'project': 'shared-datasets-1', 'service': 'storage.googleapis.com'})]}
    required = {p for _, permissions in plan_checks(plan, project_number='123', target='dataset-usage') for p in permissions}
    assert {'logging.sinks.create', 'logging.sinks.get', 'logging.exclusions.create', 'logging.exclusions.get', 'resourcemanager.projects.getIamPolicy', 'resourcemanager.projects.setIamPolicy'} <= required


def test_duplicate_storage_exclusion_is_scoped_and_disabled_with_collection():
    exclusion = {'project': 'shared-datasets-1', 'name': 'dataset-usage-exported-copy', 'filter': json.loads(re.search(r'dataset_usage_legacy_filter\s*=\s*("[^\n]+")', (ROOT/'terraform/envs/prod/dataset_usage_legacy.tf').read_text())[1]), 'disabled': True}
    item = resource('google_logging_project_exclusion.dataset_usage_duplicate', exclusion, actions=['create'])
    validate_plan({'resource_changes': [item]}, IMAGE, verified=False)
    for field, value in [('filter', 'resource.type="gcs_bucket"'), ('disabled', False), ('project', 'other'), ('name', 'all-project-logs')]:
        changed = copy.deepcopy(item)
        changed['change']['after'][field] = value
        with pytest.raises(ValueError, match='Logging|logging exclusion'):
            validate_plan({'resource_changes': [changed]}, IMAGE, verified=False)
    changed = copy.deepcopy(item)
    changed['change']['after_unknown']['filter'] = True
    with pytest.raises(ValueError, match='Logging'):
        validate_plan({'resource_changes': [changed]}, IMAGE, verified=False)


def test_raw_export_cannot_be_sampled_or_have_a_different_destination():
    sink = {'project': 'shared-datasets-1', 'name': 'dataset-usage', 'filter': json.loads(re.search(r'dataset_usage_legacy_filter\s*=\s*("[^\n]+")', (ROOT/'terraform/envs/prod/dataset_usage_legacy.tf').read_text())[1]), 'disabled': True, 'destination': 'storage.googleapis.com/skytruth-shared-datasets-1-usage-raw'}
    item = resource('google_logging_project_sink.dataset_usage', sink, actions=['create'])
    # A new sink's computed writer is legitimately unknown before creation.
    item['change']['after_unknown']['writer_identity'] = True
    validate_plan({'resource_changes': [item]}, IMAGE, verified=False)
    for field, value in [('exclusions', [{'filter': 'sample(insertId, 0.5)'}]), ('destination', 'storage.googleapis.com/other')]:
        changed = copy.deepcopy(item)
        changed['change']['after'][field] = value
        with pytest.raises(ValueError, match='complete and unsampled'):
            validate_plan({'resource_changes': [changed]}, IMAGE, verified=False)


@pytest.mark.parametrize('rules', [[], [{'action': [{'type': 'Delete'}], 'condition': [{'age': 1}]}], [{'action': [{'type': 'Delete'}], 'condition': [{'age': 7, 'matches_prefix': ['only-some/']}]}], [{'action': [{'type': 'Delete'}], 'condition': [{'age': 7, 'with_state': 'ARCHIVED'}]}], [{'action': [{'type': 'Delete'}], 'condition': [{'age': 7, 'send_num_newer_versions_if_zero': True}]}]])
def test_raw_retention_cannot_drift_or_delete_inputs_too_early(rules):
    raw = {'name': 'skytruth-shared-datasets-1-usage-raw', 'public_access_prevention': 'enforced', 'uniform_bucket_level_access': True, 'lifecycle_rule': rules}
    with pytest.raises(ValueError, match='retention'):
        validate_plan({'resource_changes': [resource('google_storage_bucket.dataset_usage_raw', raw)]}, IMAGE, verified=False)


def test_real_provider_plan_preserves_defaults_and_computed_creation_fields():
    full_plan = json.loads((ROOT / 'tests/fixtures/dataset_usage/retention-plan.json').read_text())
    with pytest.raises(ValueError, match='sink writer identity is unknown'):
        validate_plan(full_plan, IMAGE, verified=False)
    # Retain the provider's real raw-bucket defaults; obsolete fresh sink setup
    # with a computed writer is outside the replacement's grant authority.
    plan = {**full_plan, 'resource_changes': [row for row in full_plan['resource_changes'] if row['address'] == 'google_storage_bucket.dataset_usage_raw']}
    validate_plan(plan, IMAGE, verified=False)
    required = {p for _, permissions in plan_checks(full_plan, project_number='123', target='dataset-usage') for p in permissions}
    assert {'logging.exclusions.create', 'logging.exclusions.get', 'logging.sinks.create', 'storage.buckets.create'} <= required
    raw = next(row for row in plan['resource_changes'] if row['address'] == 'google_storage_bucket.dataset_usage_raw')
    assert raw['change']['after']['lifecycle_rule'][0]['condition'][0]['with_state'] == 'ANY'
    changed = copy.deepcopy(plan)
    raw = next(row for row in changed['resource_changes'] if row['address'] == 'google_storage_bucket.dataset_usage_raw')
    raw['change']['after_unknown']['lifecycle_rule'] = [{'condition': [{'with_state': True}]}]
    with pytest.raises(ValueError, match='Unknown.*retention'):
        validate_plan(changed, IMAGE, verified=False)


def test_usage_deployment_is_protected_and_uses_tested_images_without_schedule_resumption():
    workflow = load_workflow(ROOT/'.github/workflows/dataset-usage-deploy.yml')
    job = workflow['jobs']['deploy']
    assert job['environment'] == 'shared-datasets-production'
    assert job['concurrency'] == {'group': 'prod-terraform-state', 'queue': 'max', 'cancel-in-progress': False}
    shell = '\n'.join(step.get('run', '') for step in job['steps'])
    assert 'tested_image_authorization.py' in shell
    assert 'docker build ' not in shell
    assert 'scheduler jobs resume' not in shell
    assert 'dataset_usage_deploy.py --plan-json' in shell
    assert '--plan-json "$RUNNER_TEMP/dataset-usage.tfplan.json"' in shell
    assert 'deployment_revision.py start' in shell
    assert 'shared_bucket' not in workflow['jobs']['bootstrap']['with']['allowed_exact']
