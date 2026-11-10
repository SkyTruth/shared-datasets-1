"""A single registry and uniform status conditions preserve deployment admission."""
from graphlib import TopologicalSorter
from itertools import combinations
import json
from pathlib import Path

import pytest
import yaml

from scripts import ci_contract as ci, deployment_receipt_contracts as receipts
from scripts import deployment_revision as revisions, workflow_failure_alert as alerts
from scripts.terraform_target_contracts import registry
from workflow_helpers import deployment_prerequisites, identity_sync_needs, terraform_environment

ROOT = Path(__file__).resolve().parents[1]


def test_delivery_callers_wait_for_created_identities_in_nested_reusable_workflows():
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    missing = {name: sorted(required - set([needs] if isinstance(needs := jobs[name].get('needs', []), str) else needs))
               for name, required in identity_sync_needs(ROOT).items()}
    assert not {name: needs for name, needs in missing.items() if needs}


def test_identity_sync_exports_a_bounded_propagation_barrier(tmp_path):
    # Exercise the registry CLI/environment transport consumed by the apply job.
    environment = terraform_environment(tmp_path, {
        'sync_name': 'Artifact Registry writer binding sync', 'caller_workflow': 'artifact-registry-iam-sync.yml',
    })
    seconds = int(environment['POST_APPLY_WAIT_SECONDS'])
    assert seconds == 120
    workflow = yaml.safe_load((ROOT / '.github/workflows/prod-terraform-target-apply.yml').read_text())
    sync, propagation = workflow['jobs']['sync'], workflow['jobs']['propagation']
    assert sync['outputs']['wait_seconds'] == '${{ env.POST_APPLY_WAIT_SECONDS }}'
    assert propagation['needs'] == 'sync'
    assert propagation['if'] == "${{ needs.sync.outputs.applied == 'true' && fromJSON(needs.sync.outputs.wait_seconds) > 0 }}"
    assert propagation['steps'][0]['env']['WAIT_SECONDS'] == '${{ needs.sync.outputs.wait_seconds }}'
    assert propagation['steps'][0]['run'] == 'sleep "$WAIT_SECONDS"'
    assert 0 < seconds < propagation['timeout-minutes'] * 60
    assert propagation['permissions'] == {}
    assert not {'environment', 'concurrency'} & propagation.keys()


def test_merge_rollout_holds_the_receipt_signer_incompatible_with_old_main():
    # 4aa19c71 deployment_revision.verify_receipt trusts these original leaves.
    old_signers = {'catalog-viewer': 'catalog-viewer-deploy.yml', 'pmtiles-cdn': 'pmtiles-cdn-sync.yml',
                   'dataset-usage': 'dataset-usage-deploy.yml'}
    from unittest.mock import Mock
    api = Mock()
    api.get.return_value = {'id': 13, 'run_attempt': 1, 'head_sha': 'a' * 40, 'referenced_workflows': [{
        'path': 'SkyTruth/shared-datasets-1/.github/workflows/ingestion-deploy.yml@refs/heads/main',
        'sha': 'a' * 40, 'ref': 'refs/heads/main',
    }]}
    holds = json.loads((ROOT / ci.INGESTION_ROLLOUT_HOLDS).read_text())
    for target, old_signer in old_signers.items():
        signer = revisions.receipt_signer(api, 'SkyTruth/shared-datasets-1', target, 13, 1)
        if target == 'dataset-usage':
            assert signer == revisions.INGESTION_SIGNER
            assert signer != '.github/workflows/' + old_signer
            assert 'dataset_usage' in ci.rollout_holds(holds)
            assert holds['dataset_usage']['owner'] == 'jonaraphael'
            assert holds['dataset_usage']['expires_at'] == '2026-11-19T00:00:00Z'
        else:
            assert signer == '.github/workflows/' + old_signer


@pytest.mark.parametrize('create_account', [False, True])
def test_identity_ordering_follows_created_accounts_through_multiple_reusable_hops(tmp_path, create_account):
    directory = tmp_path / '.github/workflows'
    directory.mkdir(parents=True)
    inventory = tmp_path / 'terraform/envs/prod/github-workflow-identities.json'
    inventory.parent.mkdir(parents=True)
    inventory.write_text(json.dumps({'fresh': ['authenticate.yml'], 'existing': ['existing.yml']}))
    resources = ['google_project_iam_custom_role.github_job["existing"]']
    if create_account:
        resources.append('google_service_account.github_job["fresh"]')
    (directory.parent / 'terraform-targets.json').write_text(json.dumps({'schema_version': 1, 'targets': {
        'Artifact Registry writer binding sync': {'workflow': 'create.yml', 'resources': resources}}}))
    def call(filename):
        return {'uses': './.github/workflows/' + filename}
    documents = {
        'ci.yml': {'creator': call('create.yml'), 'direct': call('authenticate.yml'),
                   'nested': call('wrapper.yml'), 'existing': call('existing.yml')},
        'wrapper.yml': {'nested': call('inner.yml')},
        'inner.yml': {'nested': call('authenticate.yml'), 'external': {'uses': 'org/repo/.github/workflows/other.yml@main'}},
        **dict.fromkeys(('create.yml', 'authenticate.yml', 'existing.yml'), {}),
    }
    for filename, jobs in documents.items():
        (directory / filename).write_text(yaml.safe_dump({'jobs': jobs}))
    assert identity_sync_needs(tmp_path) == ({'direct': {'creator'}, 'nested': {'creator'}} if create_account else {})


def test_every_post_gate_job_directly_needs_all_post_gate_ancestors():
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    graph = {name: set([needs] if isinstance(needs := job.get('needs', []), str) else needs)
             for name, job in jobs.items()}
    ancestors = {}
    for name in TopologicalSorter(graph).static_order():
        ancestors[name] = graph[name] | set().union(*(ancestors[parent] for parent in graph[name]))
    post_gate = {name for name in graph if 'ci-ready' in ancestors[name]}
    missing = {name: sorted((ancestors[name] & post_gate) - graph[name]) for name in post_gate}
    assert not {name: needs for name, needs in missing.items() if needs}


def test_registry_migration_preserves_all_signed_and_historical_routing_facts():
    expected = json.loads((ROOT / 'tests/fixtures/deployment-registry-contract.json').read_text())
    assert revisions.TARGET_WORKFLOWS == expected['records']
    assert {key: sorted(value) for key, value in alerts.WORKFLOW_EVENTS.items()} == expected['events']
    assert sorted(alerts.PUBLICATION_JOBS) == expected['publication_jobs']
    assert {name: row['prerequisites'] for name, row in registry().items()} == expected['prerequisites']
    assert {name: row['retired_resources'] for name, row in registry().items() if 'retired_resources' in row} == expected['retired_resources']
    assert not (ROOT / 'terraform/iam-plan-prerequisites.json').exists()
    assert 'record_workflows' not in json.loads((ROOT / '.github/terraform-targets.json').read_text())
    exceptions = registry(section='selection_exceptions')
    assert not any(name.endswith(('/approval.py', '/restore.py')) for name in exceptions)


def test_every_selected_prerequisite_runs_or_has_failed_or_cancelled_ancestry():
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    graph = deployment_prerequisites(ROOT)
    order = list(TopologicalSorter(graph).static_order())
    # Bind the scheduler truth table to the exact checked-in expression, rather
    # than parsing a second expression language in Python. failure() is transitive:
    # https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#failure
    for name in graph:
        caller = name.replace('_', '-')
        assert jobs[caller]['if'] == "${{ !cancelled() && !failure() && !contains(needs.*.result, 'cancelled') && fromJSON(needs.ci-ready.outputs.delivery || '{}')." + caller + " }}"
        assert set(graph[name]) == {parent.replace('-', '_') for parent in jobs[caller]['needs'] if parent.replace('-', '_') in graph}
    for width in range(len(order) + 1):
        for subset in combinations(order, width):
            selected = set(subset)
            for stopped, outcome in ((None, None), *((name, result) for name in selected for result in ('failure', 'cancelled'))):
                delivery = json.loads(json.dumps(ci.delivery_outputs({'deployments': list(selected), 'suites': []}, 'push', 'refs/heads/main')))
                outcomes, failed_ancestry, cancelled_ancestry = {}, {}, {}
                for name in order:
                    # failure() is transitive; contains(needs.*.result, 'cancelled') sees direct needs only,
                    # which suffices because every caller lists its transitive prerequisites.
                    failed_ancestry[name] = any(outcomes[parent] == 'failure' or failed_ancestry[parent] for parent in graph[name])
                    cancelled_ancestry[name] = any(outcomes[parent] == 'cancelled' or cancelled_ancestry[parent] for parent in graph[name])
                    direct_cancelled = any(outcomes[parent] == 'cancelled' for parent in graph[name])
                    assert direct_cancelled == cancelled_ancestry[name]
                    admitted = delivery[name.replace('_', '-')] and not failed_ancestry[name] and not direct_cancelled
                    outcomes[name] = outcome if admitted and name == stopped else 'success' if admitted else 'skipped'
                    if name in selected and outcomes[name] == 'skipped':
                        assert failed_ancestry[name] or cancelled_ancestry[name]


@pytest.mark.parametrize('defect', ['producer-event', 'producer-ref', 'producer-success', 'failure', 'cancelled', 'cancelled-needs', 'lookup', 'needs'])
def test_delivery_write_tokens_require_exact_main_output_and_caller_guards(tmp_path, defect):
    document = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())
    directory = tmp_path / '.github/workflows'
    directory.mkdir(parents=True)
    path = directory / 'ci.yml'
    path.write_text(yaml.safe_dump(document))
    assert receipts.permissions(tmp_path) == []
    jobs = document['jobs']
    if defect.startswith('producer-'):
        clause = {'producer-event': "github.event_name == 'push' && ", 'producer-ref': "github.ref == 'refs/heads/main' && ",
                  'producer-success': "job.status == 'success' && "}[defect]
        jobs['ci-ready']['outputs']['delivery'] = jobs['ci-ready']['outputs']['delivery'].replace(clause, '')
    elif defect == 'needs':
        jobs['mpatlas']['needs'] = ['geospatial-changes']
    else:
        clause = {'failure': '!failure() && ', 'cancelled': '!cancelled() && ', 'cancelled-needs': "!contains(needs.*.result, 'cancelled') && ",
                  'lookup': '.mpatlas'}[defect]
        jobs['mpatlas']['if'] = jobs['mpatlas']['if'].replace(clause, '.wdpa' if defect == 'lookup' else '')
    path.write_text(yaml.safe_dump(document))
    assert any('PR-capable validation' in error for error in receipts.permissions(tmp_path))


def test_health_artifact_names_describe_the_retained_contract():
    assert not (ROOT / 'scripts/incident_verification_cache.py').exists()
    from scripts import incident_health_artifacts
    assert incident_health_artifacts.MEMBER == 'cache.json'
    assert (ROOT / 'tests/test_incident_health_artifacts.py').is_file()


def test_deployment_procedure_is_the_single_target_change_entrypoint():
    procedure = (ROOT / 'docs/ci-deployment.md').read_text()
    assert '## Caller scheduling' in procedure
    assert 'retired_resources' in procedure and 'permission_probes' in procedure
    terraform = (ROOT / 'docs/terraform-reconciliation.md').read_text()
    section = terraform.split('## Change a Terraform or IAM target')[1].split('## Manual state-only reconciliation')[0]
    assert '(ci-deployment.md)' in section and '1.' not in section
