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
        'probe_reader', 'configuration_reader')),
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
    if not re.fullmatch(r'[a-z0-9./_-]+@sha256:[0-9a-f]{64}', image):
        raise ValueError('An immutable tested image is required')
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
