"""Phase 1 preserves installed access until main proves every new identity."""
import json
from pathlib import Path

import pytest

from scripts.terraform_plan_allowlist import blocked_changes
from scripts.terraform_plan_permissions import plan_checks
from terraform_helpers import evaluate, load_terraform, resources

ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / 'terraform/envs/prod'
BASELINE = json.loads((ROOT / 'tests/fixtures/github-identity-legacy.json').read_text())


def rows():
    return {f'{kind}.{name}': body for path in PROD.glob('*.tf')
            for kind, name, body in resources(load_terraform(path))}


def test_each_legacy_grant_keeps_its_main_address_and_policy():
    # Name these explicitly: removing any one requires a deliberate phase-2 edit.
    legacy = {
        'google_service_account_iam_member.shared_datasets_publisher_github_wif',
        'google_service_account_iam_member.github_readonly_wif',
        'google_storage_bucket_iam_member.github_readonly_object_viewer',
        'google_service_account_iam_member.feature_preview_loader_github_wif',
        'google_storage_bucket_iam_member.monitoring_slack_recovery',
    }
    declared = rows()
    assert legacy <= BASELINE['grants'].keys()
    for address, baseline in BASELINE['grants'].items():
        policy = {key: declared[address][key] for key in baseline['policy']}
        # The IAM-only bucket data source resolves to the same var.bucket_name;
        # changing graph ownership does not change the policy resource identity.
        if policy.get('bucket') == '${data.google_storage_bucket.shared_bucket_iam.name}':
            policy['bucket'] = '${var.bucket_name}'
        assert policy == baseline['policy'], address
        assert set(declared[address]) & {'condition', 'for_each', 'count'} == set(baseline['policy']) & {'condition', 'for_each', 'count'}, address
    assert legacy <= declared.keys()
    for address, permissions in BASELINE['custom_roles'].items():
        assert set(permissions) <= set(declared[address]['permissions']), address


def test_legacy_provider_keys_and_token_conditions_are_preserved():
    declared = rows()
    values = load_terraform(PROD / 'github_job_identities.tf')['locals'][0]
    provider = declared['google_iam_workload_identity_pool_provider.github']
    baseline = BASELINE['providers']['github']
    assert provider['attribute_condition'] == baseline['attribute_condition']
    values['github_workflow_identities'] = json.loads((PROD / 'github-workflow-identities.json').read_text())
    values['mapping'] = provider['attribute_mapping']
    mapping = evaluate(values, 'local.mapping', variables={'github_repository': 'SkyTruth/shared-datasets-1'})
    assert baseline['attribute_mapping'].items() <= mapping.items()
    readonly = declared['google_iam_workload_identity_pool_provider.github_readonly']
    baseline = BASELINE['providers']['github_readonly']
    assert baseline['attribute_mapping'].items() <= readonly['attribute_mapping'].items()
    legacy = load_terraform(PROD / 'github_readonly_iam.tf')['locals'][0]
    assert legacy['github_readonly_legacy_condition'] == baseline['attribute_condition']


def test_production_provider_keeps_legacy_environment_tokens_without_new_capabilities():
    from identity_policy_helpers import cel
    from test_workflow_identity_policy import allowed, claims, policies

    assertion = claims('legacy.yml', 'refs/heads/legacy')
    assert cel(policies()['production'], assertion) is True
    assert not allowed('terraform', assertion)
    assert not allowed('publisher', assertion)


@pytest.mark.parametrize('workflow', ['Catalog drift guard', 'Bucket hygiene audit', 'Dataset breaking change alert'])
def test_readonly_provider_still_accepts_all_legacy_display_names(workflow):
    from identity_policy_helpers import cel
    from test_workflow_identity_policy import claims, policies

    assertion = {**claims('legacy.yml', 'refs/heads/legacy'), 'workflow': workflow}
    assert cel(policies()['readonly'], assertion) is True
    assert cel(policies()['readonly'], {**assertion, 'repository': 'foreign/repository'}) is False


def test_every_non_retirement_iam_target_rejects_deletes_and_both_replacement_orders():
    registry = json.loads((ROOT / '.github/terraform-targets.json').read_text())
    for name, scope in registry['targets'].items():
        # Existing exact obsolete-resource retirements and temporary-authority
        # revocation are separate contracts, never permission to retire live IAM.
        if scope.get('retired_resources') or scope.get('delete_only'):
            assert not set(scope['resources']) & BASELINE['grants'].keys(), name
            continue
        if scope.get('state_only'):
            continue
        assert scope['block_deletes'] is True, name
        for address in scope['resources']:
            if '_iam_' not in address and 'workload_identity' not in address:
                continue
            for actions in (['delete'], ['delete', 'create'], ['create', 'delete']):
                plan = {'resource_changes': [{'address': address, 'change': {'actions': actions}}]}
                assert blocked_changes(plan, allowed_exact=set(scope['resources']), allowed_patterns=[],
                                       block_deletes=scope['block_deletes']), (name, address, actions)


def test_phase_one_permission_boundary_rejects_authoritative_sa_bindings():
    plan = {'format_version': '1.2', 'resource_changes': [{
        'address': 'google_service_account_iam_binding.github_actions_terraform_wif',
        'type': 'google_service_account_iam_binding', 'change': {'actions': ['create'], 'after': {
            'service_account_id': 'projects/shared-datasets-1/serviceAccounts/shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com',
            'role': 'roles/iam.workloadIdentityUser', 'members': ['principalSet://fixture/attribute.terraform/allowed'],
        }},
    }]}
    with pytest.raises(ValueError, match='unsupported'):
        plan_checks(plan, project_number='123456789')
