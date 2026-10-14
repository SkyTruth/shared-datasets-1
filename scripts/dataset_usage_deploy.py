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
from scripts import deployment_revision

READER_FILES = (
    'services/catalog_viewer/usage.py',
    'services/catalog_viewer/usage_assets/app.js',
    'services/catalog_viewer/usage_assets/index.html',
)


def verify_reader_ready(root, api, repository):
    """Require a signed deployment of the current reader before any writer switch.

    A later failed or pending reader attempt cannot reuse an older success.
    Unrelated executor changes may reuse the reader only when its bytes match.
    This also guards standalone protected usage-workflow dispatches.
    """
    records = api.pages(f'repos/{repository}/deployments?environment=production-catalog-viewer&per_page=100')
    if not records:
        raise ValueError('Deploy the compatible catalog reader before the usage writer')
    record = max(records, key=lambda row: row['id'])
    payload = deployment_revision.verify_record(api, repository, record)
    statuses = deployment_revision.verified_statuses(api, repository, record)
    if payload['target'] != 'catalog-viewer' or not statuses or statuses[0].get('state') != 'success' or statuses[0].get('description') != 'verified':
        raise ValueError('Latest catalog reader deployment is not verified')
    for relative in READER_FILES:
        deployed = subprocess.check_output(['git', 'show', f"{record['sha']}:{relative}"])
        if deployed != (root / relative).read_bytes():
            raise ValueError('Deploy the current compatible catalog reader before the usage writer')

RETIREMENT_RESOURCES = {
    'google_storage_bucket_iam_member.dataset_usage_probe_reader',
    'google_logging_project_sink.dataset_usage',
    'google_logging_project_exclusion.dataset_usage_duplicate',
    'google_storage_bucket_iam_member.dataset_usage_sink_writer',
    'google_project_iam_member.dataset_usage_health',
    'google_project_iam_custom_role.dataset_usage_health',
}


def retirement_ready(root, payload):
    """A reviewed cleanup must also have live replacement publication evidence."""
    import datetime as dt
    from ingestion.dataset_usage.model import SOURCES, timestamp
    config = json.loads((root / 'catalog/dataset-usage.json').read_text())
    activation = json.loads((root / 'catalog/dataset-usage-activation.json').read_text())
    classifier = Classifier(read_catalog_rows_text((root / 'catalog/shared-datasets-catalog.csv').read_text()), config, 'skytruth-shared-datasets-1')
    now = dt.datetime.now(dt.UTC)
    if payload.get('schema_version') != 2 or payload.get('collection_errors') or not 0 <= (now - timestamp(payload['generated_at'])).total_seconds() <= 48 * 3600:
        return False
    if not all(activation_valid(activation, classifier, source) for source in SOURCES):
        return False
    health = {entry['source']: entry for entry in payload['source_health']}
    if not all(health.get(source, {}).get('healthy_days', 0) >= 2 and len({entry['day'] for entry in payload['coverage'] if entry['source'] == source and entry['state'] == 'healthy' and entry['day'] >= activation['sources'][source]['verified_at'][:10] and entry['day'] >= (now - dt.timedelta(days=7)).date().isoformat()}) >= 2 for source in SOURCES):
        return False
    reporting = {entry['producer'] for entry in payload['producers'] if entry['connection'] == 'reporting'}
    expected = {name for name, entry in config['producers'].items() if entry['status'] == 'configured' and entry['environment'] == 'production'}
    return bool(expected) and expected <= reporting

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
    'google_storage_bucket.shared_bucket', 'google_storage_bucket_iam_member.dataset_usage_app_writer',
    *('google_storage_bucket_iam_member.dataset_usage_' + name for name in (
        'sink_writer', 'storage_logger', 'raw_reader', 'state_writer', 'viewer',
        'probe_reader', 'native_probe_reader', 'configuration_reader', 'raw_configuration_reader')),
}


def validate_rollout(root):
    config = json.loads((root / 'catalog/dataset-usage.json').read_text())
    activation = json.loads((root / 'catalog/dataset-usage-activation.json').read_text())
    c = Classifier(read_catalog_rows_text((root / 'catalog/shared-datasets-catalog.csv').read_text()), config, 'skytruth-shared-datasets-1')
    if config['collection_enabled'] and not cost_evidence_valid(activation):
        raise ValueError('Collection requires reviewed incremental tracking cost evidence at or below $25/month')
    if any(entry.get('verified_at') and not activation_valid(activation, c, source) for source, entry in activation.get('sources', {}).items()):
        raise ValueError('Activation does not match the current source configuration and cost gate')
    return activation_valid(activation, c) and config['collection_enabled']


def validate_plan(plan, image, *, verified, retirement_verified=False):
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / 'catalog/dataset-usage.json').read_text())
    legacy_path = root / 'terraform/envs/prod/dataset_usage_legacy.tf'
    legacy_filter = json.loads(re.search(r'dataset_usage_legacy_filter\s*=\s*("[^\n]+")', legacy_path.read_text())[1]) if legacy_path.exists() else None
    legacy_probe = " || ".join(f"resource.name == 'projects/_/buckets/skytruth-shared-datasets-1/objects/{path}'" for path in (
        '100-geographic-reference/110-boundaries/lsib/latest/lsib.fgb',
        '300-infrastructure-industrial/320-mining/wri-forest-atlas-drc-mining-permits/latest/wri-forest-atlas-drc-mining-permits.fgb',
        '_catalog/shared-datasets-catalog.csv'))
    public = next(row for row in read_catalog_rows_text((root / 'catalog/shared-datasets-catalog.csv').read_text()) if row['access_tier'] == 'public')
    native_probe = "resource.name == 'projects/_/buckets/skytruth-shared-datasets-1/objects/" + public['canonical_path'].split('skytruth-shared-datasets-1/', 1)[1] + "'"
    collector = 'serviceAccount:dataset-usage@shared-datasets-1.iam.gserviceaccount.com'
    raw_bucket, state_bucket = 'skytruth-shared-datasets-1-usage-raw', 'skytruth-shared-datasets-1-usage-state'
    health_role = 'projects/shared-datasets-1/roles/sharedDatasetsUsageBucketHealth'
    grants = {
        'raw_reader': (raw_bucket, 'roles/storage.objectViewer', collector, None),
        'state_writer': (state_bucket, 'roles/storage.objectUser', collector, None),
        'viewer': (state_bucket, 'roles/storage.objectViewer', 'serviceAccount:catalog-viewer@shared-datasets-1.iam.gserviceaccount.com', f"resource.name.startsWith('projects/_/buckets/{state_bucket}/objects/published/')"),
        'probe_reader': ('skytruth-shared-datasets-1', 'roles/storage.objectViewer', collector, legacy_probe),
        'native_probe_reader': ('skytruth-shared-datasets-1', 'roles/storage.objectViewer', collector, native_probe),
        'configuration_reader': ('skytruth-shared-datasets-1', health_role, collector, None),
        'raw_configuration_reader': (raw_bucket, health_role, collector, None),
        'storage_logger': (raw_bucket, 'roles/storage.objectCreator', 'group:cloud-storage-analytics@google.com', None),
    }
    def check_grant(after, expected):
        bucket, role, member, expression = expected
        conditions = after.get('condition') or []
        actual = conditions[0].get('expression') if len(conditions) == 1 else None
        if after.get('bucket', '').removeprefix('b/') != bucket or after.get('role') != role or after.get('member') != member or actual != expression or (expression is None and conditions):
            raise ValueError('Usage IAM grant exceeds its exact registered scope')
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
        if actions == ['delete'] and address in RETIREMENT_RESOURCES:
            if not retirement_verified:
                raise ValueError('Legacy retirement requires verified replacement evidence')
            before = change.get('before') or {}
            if address == 'google_storage_bucket_iam_member.dataset_usage_probe_reader':
                check_grant(before, grants['probe_reader'])
                if before['condition'][0].get('title') != 'usage_probe_sentinels_only':
                    raise ValueError('Unexpected legacy probe binding')
            if address == 'google_logging_project_sink.dataset_usage' and before.get('name') != 'dataset-usage':
                raise ValueError('Unexpected retired sink identity')
            if address == 'google_logging_project_exclusion.dataset_usage_duplicate' and before.get('name') != 'dataset-usage-exported-copy':
                raise ValueError('Unexpected retired exclusion identity')
            if address.startswith('google_logging_project_') and (before.get('project') != 'shared-datasets-1' or not before.get('disabled')):
                raise ValueError('Only disabled project usage exports may be retired')
            if address == 'google_storage_bucket_iam_member.dataset_usage_sink_writer':
                sink = rows.get('google_logging_project_sink.dataset_usage', {}).get('change', {}).get('before') or {}
                if before.get('bucket', '').removeprefix('b/') != 'skytruth-shared-datasets-1-usage-raw' or before.get('role') != 'roles/storage.objectCreator' or before.get('member') != sink.get('writer_identity'):
                    raise ValueError('Retirement writer must match the exact retired sink')
            if address == 'google_project_iam_member.dataset_usage_health' and (before.get('member') != 'serviceAccount:dataset-usage@shared-datasets-1.iam.gserviceaccount.com' or before.get('role') != 'projects/shared-datasets-1/roles/sharedDatasetsUsageHealth' or before.get('project') != 'shared-datasets-1'):
                raise ValueError('Unexpected legacy health binding')
            if address == 'google_project_iam_custom_role.dataset_usage_health' and (before.get('role_id') != 'sharedDatasetsUsageHealth' or before.get('project') != 'shared-datasets-1'):
                raise ValueError('Unexpected legacy health role')
            continue
        producer_match = re.fullmatch(r'google_storage_bucket_iam_member\.dataset_usage_app_writer\["([a-z0-9-]+)"\]', address)
        if (address not in RESOURCES and producer_match is None) or actions not in (['create'], ['update']):
            raise ValueError('Unrelated, destructive, or replacement change: ' + address)
        after, before = change.get('after') or {}, change.get('before') or {}
        if address == 'google_project_iam_custom_role.dataset_usage_bucket_health':
            if after.get('project') != 'shared-datasets-1' or after.get('role_id') != 'sharedDatasetsUsageBucketHealth' or set(after.get('permissions', [])) != {'storage.buckets.get', 'storage.buckets.getIamPolicy'}:
                raise ValueError('Usage health role must remain bucket-read-only')
        elif address == 'google_project_iam_custom_role.dataset_usage_health':
            if after.get('project') != 'shared-datasets-1' or after.get('role_id') != 'sharedDatasetsUsageHealth' or set(after.get('permissions', [])) != {'logging.sinks.get', 'logging.exclusions.get', 'monitoring.timeSeries.list', 'resourcemanager.projects.getIamPolicy'}:
                raise ValueError('Legacy health authority must not expand')
        elif address == 'google_project_iam_member.dataset_usage_health':
            if after.get('project') != 'shared-datasets-1' or after.get('member') != collector or after.get('role') != 'projects/shared-datasets-1/roles/sharedDatasetsUsageHealth' or after.get('condition'):
                raise ValueError('Legacy health binding must not expand')
        elif address in {'google_service_account_iam_member.dataset_usage_deployer_act_as', 'google_service_account_iam_member.dataset_usage_scheduler_deployer_act_as'}:
            account = 'dataset-usage-scheduler' if 'scheduler_deployer' in address else 'dataset-usage'
            if after.get('role') != 'roles/iam.serviceAccountUser' or after.get('member') != 'serviceAccount:shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com' or after.get('service_account_id') != f'projects/shared-datasets-1/serviceAccounts/{account}@shared-datasets-1.iam.gserviceaccount.com':
                raise ValueError('Usage deployment authority must remain scoped to its runtime')
        elif address == 'google_storage_bucket.shared_bucket':
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
            if sorted(json.dumps(item, sort_keys=True) for item in configs) != sorted(json.dumps(item, sort_keys=True) for item in before.get('audit_log_config', [])):
                raise ValueError('Shared storage auditing must remain unchanged')
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
            if after.get('project') != 'shared-datasets-1' or after.get('filter') != legacy_filter or after.get('disabled', False) is not True or any(unknown(change.get('after_unknown', {}).get(field, False)) for field in fields):
                raise ValueError('Logging export and duplicate exclusion must match the reviewed configuration')
            if exclusion:
                if after.get('name') != 'dataset-usage-exported-copy':
                    raise ValueError('Unexpected logging exclusion identity')
            elif after.get('name') != 'dataset-usage' or after.get('destination') != 'storage.googleapis.com/skytruth-shared-datasets-1-usage-raw' or any(not item.get('disabled', False) for item in after.get('exclusions', [])):
                raise ValueError('The raw usage export must remain complete and unsampled')
        elif address == 'module.dataset_usage_job.google_cloud_run_v2_job.this':
            task = after['template'][0]['template'][0]
            if task.get('service_account') != 'dataset-usage@shared-datasets-1.iam.gserviceaccount.com':
                raise ValueError('Usage job must retain its registered runtime identity')
            if task['containers'][0]['image'] != image or task['max_retries'] != 0 or task['timeout'] != '1800s':
                raise ValueError('Unexpected usage execution contract')
            if task['containers'][0]['resources'][0]['limits'] != {'cpu': '1', 'memory': '1Gi'}:
                raise ValueError('Unexpected usage execution budget')
        elif address == 'module.dataset_usage_scheduler.google_cloud_scheduler_job.this':
            if after['paused'] != (not verified) or after['schedule'] != '0 9 * * *' or after['time_zone'] != 'UTC' or after['retry_config'][0]['retry_count'] != 0:
                raise ValueError('Unverified monitoring cannot enable a schedule')
        elif address == 'google_cloud_run_v2_job_iam_member.dataset_usage_scheduler_invoker':
            if after.get('project') != 'shared-datasets-1' or after.get('location') != 'us-central1' or after.get('name') not in {'dataset-usage', 'projects/shared-datasets-1/locations/us-central1/jobs/dataset-usage'} or after.get('role') != 'roles/run.invoker' or after.get('member') != 'serviceAccount:dataset-usage-scheduler@shared-datasets-1.iam.gserviceaccount.com':
                raise ValueError('Usage invocation must remain private to its scheduler')
        elif address.startswith('google_storage_bucket_iam_member.'):
            if after.get('member') in {'allUsers', 'allAuthenticatedUsers'}:
                raise ValueError('Telemetry must never be public')
            name = address.removeprefix('google_storage_bucket_iam_member.dataset_usage_')
            if name == 'sink_writer':
                sink = rows.get('google_logging_project_sink.dataset_usage', {}).get('change', {}).get('after') or {}
                writer = sink.get('writer_identity')
                if not isinstance(writer, str) or not writer.startswith('serviceAccount:'):
                    raise ValueError('Legacy sink writer identity is unknown')
                check_grant(after, (raw_bucket, 'roles/storage.objectCreator', writer, None))
            if name in grants:
                if unknown(change.get('after_unknown', {}).get('condition', False)):
                    raise ValueError('Unknown usage IAM condition')
                check_grant(after, grants[name])
            if producer_match:
                producer = producer_match[1]
                registration = config["producers"].get(producer)
                expected = f"resource.name.startsWith('projects/_/buckets/skytruth-shared-datasets-1-usage-raw/objects/app-usage/{producer}/')"
                conditions = after.get("condition", [])
                if not registration or registration["status"] != "configured" or after.get("member") != "serviceAccount:" + registration["service_account"] or after.get("role") != "roles/storage.objectCreator" or after.get("bucket") != "skytruth-shared-datasets-1-usage-raw" or len(conditions) != 1 or conditions[0].get("expression") != expected or unknown(change.get("after_unknown", {}).get("condition", False)):
                    raise ValueError("Application writers require their exact registered create-only prefix")
            if address in {'google_storage_bucket_iam_member.dataset_usage_probe_reader' , 'google_storage_bucket_iam_member.dataset_usage_viewer'} and not after.get('condition'):
                raise ValueError('Usage readers require object-scoped grants')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan-json', type=Path)
    parser.add_argument('--image')
    parser.add_argument('--targets', action='store_true')
    parser.add_argument('--retire-legacy', action='store_true', help='Verify live replacement evidence before exact legacy deletion')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    verified = validate_rollout(root)
    if args.targets:
        print('\n'.join(sorted(RESOURCES)))
    elif args.plan_json and args.image:
        import os
        verify_reader_ready(root, deployment_revision.GitHub(), os.environ['GITHUB_REPOSITORY'])
        plan = json.loads(args.plan_json.read_text())
        retirement_verified = False
        if args.retire_legacy:
            from services.catalog_viewer.usage import ReportReader
            retirement_verified = retirement_ready(root, ReportReader('skytruth-shared-datasets-1-usage-state').read())
            if not retirement_verified:
                raise ValueError('Replacement is not yet verified for legacy retirement')
        validate_plan(plan, args.image, verified=verified, retirement_verified=retirement_verified)
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
