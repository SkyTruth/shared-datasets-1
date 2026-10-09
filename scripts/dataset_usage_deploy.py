"""Constrain usage deployment to telemetry resources and logging-only adoption."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ingestion.dataset_usage.model import Classifier
from ingestion.dataset_usage.run import activation_valid, cost_evidence_valid
from scripts.catalog_csv import read_catalog_rows_text

RESOURCES = {
    'google_storage_bucket.dataset_usage_raw', 'google_storage_bucket.dataset_usage_state',
    'google_logging_project_sink.dataset_usage',
    'google_logging_project_exclusion.dataset_usage_duplicate',
    'module.dataset_usage_service_account.google_service_account.this',
    'module.dataset_usage_scheduler_service_account.google_service_account.this',
    'module.dataset_usage_job.google_cloud_run_v2_job.this',
    'module.dataset_usage_scheduler.google_cloud_scheduler_job.this',
    'google_cloud_run_v2_job_iam_member.dataset_usage_scheduler_invoker',
    'google_project_iam_custom_role.dataset_usage_health',
    'google_project_iam_custom_role.dataset_usage_bucket_health',
    'google_project_iam_member.dataset_usage_health',
    'google_service_account_iam_member.dataset_usage_deployer_act_as',
    'google_service_account_iam_member.dataset_usage_scheduler_deployer_act_as',
    'google_project_iam_audit_config.storage_data_write',
    'google_storage_bucket.shared_bucket',
    *('google_storage_bucket_iam_member.dataset_usage_' + name for name in (
        'sink_writer', 'storage_logger', 'raw_reader', 'state_writer', 'viewer',
        'probe_reader', 'configuration_reader', 'raw_configuration_reader')),
}


def validate_rollout(root):
    config = json.loads((root / 'catalog/dataset-usage.json').read_text())
    activation = json.loads((root / 'catalog/dataset-usage-activation.json').read_text())
    c = Classifier(read_catalog_rows_text((root / 'catalog/shared-datasets-catalog.csv').read_text()), config, 'skytruth-shared-datasets-1')
    if config['collection_enabled'] and not cost_evidence_valid(activation):
        raise ValueError('Collection requires reviewed project-wide cost evidence at or below $25/month')
    if activation.get('verified_at') and not activation_valid(activation, c):
        raise ValueError('Activation does not match the complete current configuration and cost gate')
    return activation_valid(activation, c) and config['collection_enabled']


def validate_plan(plan, image, *, verified):
    config = json.loads((Path(__file__).resolve().parents[1] / 'catalog/dataset-usage.json').read_text())
    if not re.fullmatch(r'[a-z0-9./_-]+@sha256:[0-9a-f]{64}', image):
        raise ValueError('An immutable tested image is required')
    rows = {row['address']: row for row in plan.get('resource_changes', [])}
    shared = rows.get('google_storage_bucket.shared_bucket', {}).get('change', {})
    def unknown(value):
        if isinstance(value, dict):
            return any(unknown(item) for item in value.values())
        if isinstance(value, list):
            return any(unknown(item) for item in value)
        return bool(value)
    if unknown(shared.get('after_unknown', {}).get('logging', False)):
        raise ValueError('Unknown shared bucket logging configuration')
    logging = (shared.get('after') or {}).get('logging', [])
    if logging:
        # Logging consumes an existing bucket by its reviewed name. It must not
        # introduce a provisioning edge into every shared-bucket IAM target, nor
        # race a first bucket creation in this owner's saved plan.
        raw = rows.get('google_storage_bucket.dataset_usage_raw', {}).get('change', {})
        raw_name = (raw.get('after') or {}).get('name')
        expected = (shared.get('after') or {}).get('name', '') + '-usage-raw'
        if unknown(raw.get('after_unknown', {}).get('name', False)) or unknown(shared.get('after_unknown', {}).get('name', False)):
            raise ValueError('Unknown usage bucket logging identity')
        if len(logging) != 1 or raw_name != expected or logging[0].get('log_bucket') != raw_name or logging[0].get('log_object_prefix') != 'storage-usage':
            raise ValueError('Logging must use the exact reviewed usage bucket name')
        if 'create' in raw.get('actions', []):
            raise ValueError('Provision the usage bucket with collection disabled before enabling logging')
    for row in plan.get('resource_changes', []):
        change = row['change']
        actions = change['actions']
        if actions in ([], ['no-op'], ['read']):
            continue
        address = row['address']
        if address not in RESOURCES or actions not in (['create'], ['update']):
            raise ValueError('Unrelated, destructive, or replacement change: ' + address)
        after, before = change.get('after') or {}, change.get('before') or {}
        if address == 'google_storage_bucket.shared_bucket':
            if actions != ['update']:
                raise ValueError('Shared bucket must already exist')
            computed = {'id', 'self_link', 'url', 'effective_labels', 'terraform_labels'}
            # Provider-generated fields may become unknown after a logging update.
            for key in set(before) | set(after):
                if key not in computed | {'logging'} and before.get(key) != after.get(key):
                    raise ValueError('Shared bucket mutation outside logging: ' + key)
            for key, value in change.get('after_unknown', {}).items():
                if value and key not in computed | {'logging'}:
                    raise ValueError('Unknown shared bucket mutation: ' + key)
        elif address == 'google_project_iam_audit_config.storage_data_write':
            configs = after['audit_log_config']
            if not any(item['log_type'] == 'DATA_WRITE' for item in configs):
                raise ValueError('Write auditing must be preserved')
            if any(item not in configs for item in before.get('audit_log_config', [])):
                raise ValueError('Existing audit settings changed')
            if after['service'] != 'storage.googleapis.com' or after['project'] != 'shared-datasets-1':
                raise ValueError('Unexpected audit scope')
        elif address.startswith('google_storage_bucket.dataset_usage_'):
            if after.get('public_access_prevention') != 'enforced' or not after.get('uniform_bucket_level_access') or after.get('force_destroy'):
                raise ValueError('Usage storage must be private and protected')
            if address == 'google_storage_bucket.dataset_usage_raw':
                # Providers include optional condition/action defaults. Only one
                # unconditional age-based deletion is permitted for raw evidence.
                actual = after.get('lifecycle_rule', [])
                if len(actual) != 1 or unknown(change.get('after_unknown', {}).get('lifecycle_rule', False)):
                    raise ValueError('Unknown or missing raw evidence retention')
                rule = actual[0]
                if len(rule.get('action', [])) != 1 or len(rule.get('condition', [])) != 1:
                    raise ValueError('Unexpected raw evidence retention')
                action, condition = rule['action'][0], rule['condition'][0]
                extra_conditions = {key: value for key, value in condition.items() if key not in {'age', 'with_state'}}
                if action.get('type') != 'Delete' or condition.get('age') != config['raw_retention_days'] or condition.get('with_state') not in (None, '', 'ANY') or any(value not in (None, [], '', 0) for value in extra_conditions.values()) or action.get('storage_class'):
                    raise ValueError('Raw evidence retention must match the reviewed configuration')
        elif address in {'google_logging_project_sink.dataset_usage', 'google_logging_project_exclusion.dataset_usage_duplicate'}:
            exclusion = address == 'google_logging_project_exclusion.dataset_usage_duplicate'
            fields = {'project', 'filter', 'disabled', 'name'} | (set() if exclusion else {'destination', 'exclusions'})
            if after.get('project') != 'shared-datasets-1' or after.get('filter') != config['sink_filter'] or after.get('disabled', False) != (not config['collection_enabled']) or any(unknown(change.get('after_unknown', {}).get(field, False)) for field in fields):
                raise ValueError('Logging export and duplicate exclusion must match the reviewed configuration')
            if exclusion:
                if after.get('name') != 'dataset-usage-exported-copy':
                    raise ValueError('Unexpected logging exclusion identity')
            elif after.get('name') != 'dataset-usage' or after.get('destination') != 'storage.googleapis.com/skytruth-shared-datasets-1-usage-raw' or any(not item.get('disabled', False) for item in after.get('exclusions', [])):
                raise ValueError('The raw usage export must remain complete and unsampled')
        elif address == 'module.dataset_usage_job.google_cloud_run_v2_job.this':
            task = after['template'][0]['template'][0]
            if task['containers'][0]['image'] != image or task['max_retries'] != 0 or task['timeout'] != '1800s':
                raise ValueError('Unexpected usage execution contract')
            if task['containers'][0]['resources'][0]['limits'] != {'cpu': '1', 'memory': '1Gi'}:
                raise ValueError('Unexpected usage execution budget')
        elif address == 'module.dataset_usage_scheduler.google_cloud_scheduler_job.this':
            if after['paused'] != (not verified) or after['schedule'] != '0 9 * * *' or after['time_zone'] != 'UTC' or after['retry_config'][0]['retry_count'] != 0:
                raise ValueError('Unverified monitoring cannot enable a schedule')
        elif address.startswith('google_storage_bucket_iam_member.'):
            if after.get('member') in {'allUsers', 'allAuthenticatedUsers'}:
                raise ValueError('Telemetry must never be public')
            if address in {'google_storage_bucket_iam_member.dataset_usage_probe_reader', 'google_storage_bucket_iam_member.dataset_usage_viewer'} and not after.get('condition'):
                raise ValueError('Usage readers require object-scoped grants')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-json', type=Path)
    parser.add_argument('--image')
    parser.add_argument('--targets', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    verified = validate_rollout(root)
    if args.targets:
        print('\n'.join(sorted(RESOURCES)))
    elif args.plan_json and args.image:
        plan = json.loads(args.plan_json.read_text())
        validate_plan(plan, args.image, verified=verified)
        # Refuse concurrent canaries before claiming a deployment. A new job has
        # no executions; all other errors remain failures rather than an empty list.
        job = next(row for row in plan['resource_changes'] if row['address'] == 'module.dataset_usage_job.google_cloud_run_v2_job.this')
        if job['change']['actions'] != ['create']:
            executions = json.loads(subprocess.check_output(['gcloud', 'run', 'jobs', 'executions', 'list', '--job=dataset-usage', '--region=us-central1', '--project=shared-datasets-1', '--format=json']))
            if any(not entry.get('status', {}).get('completionTime') for entry in executions):
                raise ValueError('Usage execution is in flight; retry this deployment after completion')
        print('Usage resource boundaries, activation and cost gates verified')
    else:
        parser.error('--targets or --plan-json with --image is required')


if __name__ == '__main__':
    main()
