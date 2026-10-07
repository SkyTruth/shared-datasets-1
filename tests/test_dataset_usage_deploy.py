from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from ingestion.dataset_usage.run import COST_COMPONENTS
from scripts.dataset_usage_deploy import validate_plan, validate_rollout
from scripts.terraform_plan_permissions import plan_checks
from workflow_helpers import load_workflow

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/dataset-usage@sha256:' + 'a' * 64


def resource(address, after, before=None, actions=None):
    return {'address': address, 'mode': 'managed', 'type': address.split('.')[-2], 'change': {'actions': actions or ['update'], 'before': before, 'after': after, 'after_unknown': {}}}


def test_shared_bucket_logging_update_cannot_change_iam_retention_cors_or_security():
    before = {'name': 'skytruth-shared-datasets-1', 'project': 'shared-datasets-1', 'uniform_bucket_level_access': True, 'public_access_prevention': 'inherited', 'retention_policy': [], 'cors': [{'origin': ['example']}], 'lifecycle_rule': [], 'logging': []}
    after = {**before, 'logging': [{'log_bucket': 'skytruth-shared-datasets-1-usage-raw', 'log_object_prefix': 'storage-usage'}]}
    plan = {'resource_changes': [resource('google_storage_bucket.shared_bucket', after, before)]}
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


@pytest.mark.parametrize('actions', [['delete'], ['delete', 'create'], ['create', 'delete']])
def test_no_destructive_usage_operations_or_unrelated_resources(actions):
    with pytest.raises(ValueError, match='destructive'):
        validate_plan({'resource_changes': [resource('google_storage_bucket.dataset_usage_raw', {}, actions=actions)]}, IMAGE, verified=False)
    with pytest.raises(ValueError, match='Unrelated'):
        validate_plan({'resource_changes': [resource('module.sea_ice_daily_job.google_cloud_run_v2_job.this', {})]}, IMAGE, verified=False)


def test_data_read_addition_preserves_existing_data_write():
    before = {'project': 'shared-datasets-1', 'service': 'storage.googleapis.com', 'audit_log_config': [{'log_type': 'DATA_WRITE', 'exempted_members': []}]}
    after = {**before, 'audit_log_config': [*before['audit_log_config'], {'log_type': 'DATA_READ', 'exempted_members': []}]}
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


def test_collection_cost_gate_requires_measured_project_wide_evidence(tmp_path):
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
    (directory/'dataset-usage-activation.json').write_text(json.dumps({'estimated_monthly_cost_usd': 20, 'evidence': {'cost': {'measurement_days': 7, 'project_wide_audit_volume_reviewed': True, 'logging_retention_and_exclusions_reviewed': True, 'monthly_usd': {key: (20 if key == 'project_logging' else 0) for key in COST_COMPONENTS}}}}))
    assert validate_rollout(tmp_path) is False  # Collection canary only; schedule remains paused.


def test_saved_plan_permissions_cover_logging_and_audit_configuration():
    plan = {'format_version': '1.2', 'resource_changes': [resource('google_logging_project_sink.dataset_usage', {'project': 'shared-datasets-1', 'name': 'dataset-usage'}, actions=['create']), resource('google_project_iam_audit_config.storage_data_write', {'project': 'shared-datasets-1', 'service': 'storage.googleapis.com'})]}
    required = {p for _, permissions in plan_checks(plan, project_number='123', target='dataset-usage') for p in permissions}
    assert {'logging.sinks.create', 'logging.sinks.get', 'resourcemanager.projects.getIamPolicy', 'resourcemanager.projects.setIamPolicy'} <= required


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
