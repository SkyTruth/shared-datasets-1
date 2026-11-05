"""Exact cloud impersonation and capability boundaries, including executor handoffs."""
import json
import os
from pathlib import Path
import re

import pytest

from scripts.ingestion_targets import DEPLOY_TARGETS
from scripts.terraform_plan_permissions import plan_checks
from terraform_helpers import evaluate, load_terraform, resources
from workflow_helpers import load_workflow

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / 'terraform/envs/prod'
REPO = 'SkyTruth/shared-datasets-1'
OBSERVER = 'shared-datasets-observer@shared-datasets-1.iam.gserviceaccount.com'
RUNTIME = 'shared-datasets-runtime@shared-datasets-1.iam.gserviceaccount.com'


def workflow(name):
    return load_workflow(ROOT / '.github/workflows' / name)


def policy():
    return json.loads((PROD / 'github-workflow-identities.json').read_text())


def attributes(filename, kind, name):
    return next(body for type_, resource, body in resources(load_terraform(PROD / filename))
                if (type_, resource) == (kind, name))


def auth_accounts(value, job):
    environment = {**value.get('env', {}), **job.get('env', {})}
    for step in job.get('steps', []):
        uses, inputs = step.get('uses', ''), step.get('with', {})
        if uses in {'./.github/actions/deployment-auth', './.github/actions/deployment-bootstrap/terraform'}:
            account = inputs.get('service-account', environment.get('TERRAFORM_SERVICE_ACCOUNT'))
        elif uses == 'google-github-actions/auth@v2':
            account = inputs.get('service_account')
        else:
            continue
        if account and account.startswith('${{ env.'):
            account = environment[account.removeprefix('${{ env.').removesuffix(' }}')]
        yield step, account


def declared_workflows():
    found = {name: set() for name in ('terraform', 'publisher', 'preview', 'observer', 'runtime', 'heartbeat', 'reconciler', 'cleanup', 'bucket_reader', 'pr_plan')}
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        value = load_workflow(path)
        for job in value['jobs'].values():
            for _step, account in auth_accounts(value, job):
                if account == '${{ vars.GCP_TERRAFORM_SERVICE_ACCOUNT }}':
                    identity = 'terraform'
                elif account == '${{ vars.GCP_PR_PLAN_SERVICE_ACCOUNT }}':
                    identity = 'pr_plan'
                elif account == '${{ vars.GCP_READONLY_SERVICE_ACCOUNT }}':
                    identity = 'bucket_reader'
                elif account == 'feature-preview-loader@shared-datasets-1.iam.gserviceaccount.com':
                    identity = 'preview'
                else:
                    identity = account.split('@')[0].removeprefix('shared-datasets-').replace('-', '_')
                assert identity in found, (path.name, account)
                found[identity].add(path.name)
    found['pr_plan_caller'] = {'ci.yml'}
    return found


def test_generated_workflow_allowlists_equal_actual_auth_and_registries():
    expected = declared_workflows()
    assert {key: set(value) for key, value in policy().items()} == expected
    registry = json.loads((ROOT / '.github/terraform-targets.json').read_text())
    for item in DEPLOY_TARGETS.values():
        assert workflow(item['workflow'])['jobs']['deploy']['uses'] == './.github/workflows/ingestion-deploy.yml'
    assert 'ingestion-deploy.yml' in expected['terraform']
    for item in registry['targets'].values():
        assert any(job.get('uses') == './.github/workflows/prod-terraform-target-apply.yml'
                   for job in workflow(item['workflow'])['jobs'].values())
    assert 'prod-terraform-target-apply.yml' in expected['terraform']
    assert set(registry['plan_scopes']) - {item['workflow'] for item in DEPLOY_TARGETS.values()} <= expected['terraform']
    # A new auth consumer cannot silently obtain an existing capability.
    expected['terraform'].add('unregistered.yml')
    assert expected['terraform'] != set(policy()['terraform'])


def test_identity_provisioning_precedes_the_existing_ci_prerequisite():
    jobs = workflow('artifact-registry-iam-sync.yml')['jobs']
    assert jobs['identities']['needs'] == 'bootstrap'
    assert jobs['identities']['with']['sync_name'] == 'GitHub workflow identity sync'
    assert jobs['writer']['needs'] == ['bootstrap', 'identities']
    registry = json.loads((ROOT / '.github/terraform-targets.json').read_text())
    scope = registry['targets']['GitHub workflow identity sync']
    assert scope['block_deletes'] is False  # Existing IAM members replace when their principal changes.
    assert 'google_service_account_iam_binding.github_actions_terraform_wif' in scope['resources']
    assert 'artifact-registry-iam' in workflow('ci.yml')['jobs']['preview-terraform-iam']['needs']
    from scripts.terraform_plan_allowlist import blocked_changes
    def row(address):
        return {'address': address, 'change': {'actions': ['delete', 'create']}}
    plan = {'resource_changes': [row('google_service_account_iam_member.shared_datasets_publisher_github_wif')]}
    assert blocked_changes(plan, allowed_exact=set(scope['resources']), allowed_patterns=[], block_deletes=False) == []
    plan['resource_changes'].append(row('google_cloud_run_v2_job.unrelated'))
    assert blocked_changes(plan, allowed_exact=set(scope['resources']), allowed_patterns=[], block_deletes=False)


@pytest.mark.parametrize('path,expected', [
    ('terraform/envs/prod/github-workflow-identities.json', {'artifact_registry_iam'}),
    ('.github/actions/deployment-auth/action.yml', {'sea_ice', 'pmtiles_cdn', 'catalog_viewer', 'artifact_registry_iam'}),
])
def test_identity_contract_edits_select_their_automatic_delivery_path(path, expected):
    from scripts.ci_contract import select_deployments
    selected = set(select_deployments([path]))
    assert selected == expected
    assert select_deployments(['docs/ci-deployment.md']) == []


def test_production_provider_requires_repo_environment_main_and_per_identity_leaf():
    provider = attributes('canonical_mutation_iam.tf', 'google_iam_workload_identity_pool_provider', 'github')
    condition = provider['attribute_condition']
    for required in ("assertion.repository == '${var.github_repository}'", "assertion.environment == '${var.github_publish_environment}'", "assertion.ref == 'refs/heads/main'"):
        assert required in condition
    assert 'merge(local.github_identity_attributes' in provider['attribute_mapping']
    source = (PROD / 'github_job_identities.tf').read_text()
    assert "('job_workflow_ref' in assertion ? assertion.job_workflow_ref : assertion.workflow_ref)" in source
    assert '@refs/heads/main' in source
    assert "? 'allowed' : 'denied'" in source
    assert 'assertion.workflow ==' not in source


@pytest.mark.parametrize('name,identity', [('shared_datasets_publisher_github_wif', 'publisher'), ('feature_preview_loader_github_wif', 'preview'), ('github_readonly_wif', 'bucket_reader')])
def test_bindings_are_capability_specific_not_environment_or_repository_subjects(name, identity):
    filename = {'publisher': 'canonical_mutation_iam.tf', 'preview': 'preview_terraform_iam.tf', 'bucket_reader': 'github_readonly_iam.tf'}[identity]
    binding = attributes(filename, 'google_service_account_iam_member', name)
    assert binding['member'].endswith('/attribute.' + identity + '/allowed')
    assert '/subject/' not in binding['member']
    assert '/attribute.repository/' not in binding['member']


def test_terraform_wif_replaces_legacy_environment_wide_grants():
    filename = PROD / 'github_job_identities.tf'
    assert filename.exists(), 'Terraform WIF must be owned and narrowed in this repository'
    binding = attributes(filename.name, 'google_service_account_iam_binding', 'github_actions_terraform_wif')
    assert binding['role'] == 'roles/iam.workloadIdentityUser'
    assert len(binding['members']) == 1
    assert '/attribute.terraform/allowed' in binding['members'][0]
    assert '/subject/' not in json.dumps(binding)


def test_readonly_provider_uses_main_file_identity_not_mutable_display_name():
    provider = attributes('github_readonly_iam.tf', 'google_iam_workload_identity_pool_provider', 'github_readonly')
    condition = provider['attribute_condition']
    assert "assertion.ref == 'refs/heads/main'" in condition
    assert 'local.github_job_workflow_ref' in condition
    assert 'github_workflow_identities.bucket_reader' in condition
    assert 'assertion.workflow ==' not in condition
    assert provider['attribute_mapping']['attribute.bucket_reader'] == "'allowed'"


@pytest.mark.parametrize('filename,job', [('deployment-verification.yml', 'verify'), ('catalog-viewer-deploy.yml', 'ready'), ('catalog-viewer-deploy.yml', 'verify'), ('ingestion-deploy.yml', 'verify'), ('pmtiles-cdn-sync.yml', 'verify')])
def test_read_phases_use_observer_and_mutation_phases_use_runtime(filename, job):
    value = workflow(filename)
    account = None
    reads = mutations = 0
    for step in value['jobs'][job]['steps']:
        auth = list(auth_accounts(value, {'steps': [step]}))
        if auth:
            account = auth[0][1]
        run = step.get('run', '')
        if any(command in run for command in ('gcloud run jobs describe', 'gcloud run services describe', 'gcloud run jobs executions describe', '--target catalog-viewer-observe', 'ingestion_verification.py', 'deployment_revision.py observe', 'cdn_plan_readiness.py verify-routes')):
            assert account == OBSERVER, (filename, step['name'], account)
            reads += 1
        if any(command in run for command in ('gcloud run jobs execute', 'invalidate-cdn-cache', 'scripts/ingestion_targets.py after-verify')):
            assert account == RUNTIME, (filename, step['name'], account)
            mutations += 1
        if re.search(r'(?:terraform|terraform_retry.sh).*\sapply\s', run):
            pytest.fail('Observer job contains a Terraform apply')
    assert reads
    assert mutations == (1 if filename == 'pmtiles-cdn-sync.yml' else 2 if filename == 'ingestion-deploy.yml' else 0)


def test_observer_permissions_and_grants_are_read_only_and_runtime_has_no_iam_admin():
    assert (PROD / 'github_job_identities.tf').exists(), 'Dedicated observer identity is missing'
    values = load_terraform(PROD / 'github_job_identities.tf')['locals'][0]
    values['github_workflow_identities'] = policy()
    permissions = evaluate(values, 'local.github_job_permissions', variables={'github_repository': REPO})
    for identity in ('observer', 'heartbeat', 'reconciler'):
        assert all(permission.rsplit('.', 1)[1] in {'get', 'list'} for permission in permissions[identity])
    assert not any('setIamPolicy' in permission or 'secretmanager.versions.' in permission for row in permissions.values() for permission in row)
    assert {'run.jobs.run', 'run.executions.cancel', 'cloudscheduler.jobs.enable', 'compute.urlMaps.invalidateCache'} <= set(permissions['runtime'])
    assert not set(permissions['observer']) & {'run.jobs.run', 'logging.logEntries.create', 'run.services.update'}
    # The sole observer storage grant has the predefined read-only object role.
    grant = attributes('github_job_identities.tf', 'google_storage_bucket_iam_member', 'github_job_reader')
    assert grant['role'] == 'roles/storage.objectViewer'
    assert 'local.canonical_mutation_publisher_condition' in grant['condition'][0]['expression']
    assert '000-system' not in grant['condition'][0]['expression']


def test_heartbeat_writer_is_dedicated_and_metric_deletion_is_not_granted():
    value = workflow('operational-safety-check.yml')
    accounts = [account for job in value['jobs'].values() for _, account in auth_accounts(value, job)]
    assert accounts == ['shared-datasets-heartbeat@shared-datasets-1.iam.gserviceaccount.com']
    grant = attributes('monitoring.tf', 'google_project_iam_member', 'operational_safety_reader')
    assert 'github_job["heartbeat"]' in grant['member']
    role = attributes('monitoring.tf', 'google_project_iam_custom_role', 'operational_safety_reader')
    assert role['permissions'] == ['logging.logEntries.create']
    manager = attributes('monitoring_alert_policy_iam.tf', 'google_project_iam_custom_role', 'monitoring_alert_policy_manager')
    assert 'logging.logMetrics.delete' not in manager['permissions']
    for path in PROD.glob('*.tf'):
        for kind, _name, body in resources(load_terraform(path)):
            if 'shared_datasets_publisher_service_account.member' in str(body.get('member', '')):
                assert kind != 'google_project_iam_member', 'Publisher must not hold a project logging grant'


def test_reconcilers_keep_state_writes_separate_from_observer():
    expected = 'shared-datasets-reconciler@shared-datasets-1.iam.gserviceaccount.com'
    for filename in ('identity-decision-reconcile.yml', 'resolve-workflow-alert.yml'):
        value = workflow(filename)
        assert [account for job in value['jobs'].values() for _, account in auth_accounts(value, job)] == [expected]
    grant = attributes('monitoring_slack.tf', 'google_storage_bucket_iam_member', 'monitoring_slack_recovery')
    assert 'github_job["reconciler"]' in grant['member']
    assert grant['role'] == 'roles/storage.objectUser'
    assert expected in (ROOT / 'scripts/reconcile_identity_decisions.py').read_text()


@pytest.mark.parametrize('account,provider,success', [('observer', 'provider', True), ('', 'provider', False), ('observer', '', False), ('', '', False)])
def test_auth_configuration_cannot_fall_back_to_direct_federation(account, provider, success):
    import subprocess
    action = load_workflow(ROOT / '.github/actions/deployment-auth/action.yml')
    first = action['runs']['steps'][0]
    assert first.get('name') == 'Require a configured protected identity'
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', first['run']],
                            env={**os.environ, 'ACCOUNT': account, 'PROVIDER': provider}, capture_output=True)
    assert (result.returncode == 0) is success


@pytest.mark.parametrize('kind,values,permission', [
    ('google_service_account_iam_binding', {'service_account_id': 'projects/shared-datasets-1/serviceAccounts/shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com', 'role': 'roles/iam.workloadIdentityUser', 'members': ['principalSet://fixture/attribute.terraform/allowed']}, 'iam.serviceAccounts.setIamPolicy'),
    ('google_iam_workload_identity_pool_provider', {'project': 'shared-datasets-1', 'workload_identity_pool_id': 'github', 'workload_identity_pool_provider_id': 'github'}, 'iam.workloadIdentityPoolProviders.update'),
])
def test_new_iam_plan_boundaries_check_actual_mutation_permission_and_reject_other_project(kind, values, permission):
    change = {'address': kind + '.fixture', 'type': kind, 'change': {'actions': ['update'], 'before': values, 'after': values}}
    plan = {'format_version': '1.2', 'resource_changes': [change]}
    checks = plan_checks(plan, project_number='123456789')
    assert permission in {item for _, required in checks for item in required}
    changed = dict(values)
    if 'project' in changed:
        changed['project'] = 'foreign-project'
    else:
        changed['service_account_id'] = changed['service_account_id'].replace('shared-datasets-1', 'foreign-project')
    change['change']['after'] = changed
    with pytest.raises(ValueError):
        plan_checks(plan, project_number='123456789')
