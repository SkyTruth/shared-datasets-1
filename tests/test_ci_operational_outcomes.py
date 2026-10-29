"""Supersession may stop PR validation, never active production delivery."""
from pathlib import Path
import os
import subprocess
import re

import pytest

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = {
    'geospatial-changes', 'lint', 'tests', 'geospatial-integration',
    'production-images', 'sdk-validation', 'browser', 'ci-ready',
}


def group(block, *, pr=None, run=101, event='pull_request'):
    result = block['group'].replace('${{ github.event.pull_request.number || github.run_id }}', str(pr or run))
    return result.replace('${{ github.event_name }}', event)


def test_supersession_cancels_the_same_pr_graph_without_cancelling_delivery_sources():
    workflow = load_workflow(ROOT / '.github/workflows/ci.yml')
    block = workflow['concurrency']
    assert block['cancel-in-progress'] == "${{ github.event_name == 'pull_request' }}"
    assert group(block, pr=91, run=101) == group(block, pr=91, run=102)
    assert group(block, pr=91) != group(block, pr=92)
    assert group(block, pr=91) != group(block, event='push', run=91)
    assert group(block, event='push', run=91) != group(block, event='workflow_dispatch', run=91)
    for event in ('push', 'workflow_dispatch'):
        assert group(block, event=event, run=101) != group(block, event=event, run=102)
    # Graph ownership prevents old downstream jobs cancelling newer selection.
    for name in VALIDATION:
        assert 'concurrency' not in workflow['jobs'][name]
    assert workflow['jobs']['ci-ready']['if'] == 'always()'
    assert workflow['jobs']['sdk-validation']['strategy']['matrix']['node'] == ['22', '24']
    for name, job in workflow['jobs'].items():
        if name not in VALIDATION:
            assert not job.get('concurrency', {}).get('cancel-in-progress', False)


@pytest.mark.parametrize('filename', [
    'publish-dataset.yml', 'catalog-web-deploy.yml', 'catalog-viewer-deploy.yml',
    'pmtiles-cdn-sync.yml', 'prod-terraform-target-apply.yml',
    'publish-typescript-sdk.yml', 'deployment-verification.yml',
    'deployment-recovery.yml',
])
def test_production_workers_keep_their_noncancelling_queues(filename):
    workflow = load_workflow(ROOT / '.github/workflows' / filename)
    assert not workflow.get('concurrency', {}).get('cancel-in-progress', False)
    locked = [job['concurrency'] for job in workflow['jobs'].values() if 'concurrency' in job]
    assert locked
    for block in locked:
        assert block['cancel-in-progress'] is False
        assert block['queue'] == 'max'


def test_main_announcement_allocation_requires_complete_range_detection_and_validation():
    workflow = load_workflow(ROOT / '.github/workflows/ci.yml')
    selection = workflow['jobs']['dataset-mutation-selection']
    assert selection['needs'] == 'ci-ready'
    assert "github.event_name == 'push'" in selection['if']
    assert "github.ref == 'refs/heads/main'" in selection['if']
    assert selection['outputs']['has_repo_alert'] == '${{ steps.repo_alert_selection.outputs.has_repo_alert }}'
    steps = workflow_steps_by_name(workflow, 'dataset-mutation-selection')
    checkout = next(step for step in selection['steps'] if step.get('uses') == 'actions/checkout@v4')
    assert checkout['with']['fetch-depth'] == 0
    scanner = steps['Inspect every commit for repository announcements']
    assert scanner['env']['BASE_SHA'] == '${{ github.event.before }}'
    assert 'select-from-git-range --base "$BASE_SHA" --head "$GITHUB_SHA"' in scanner['run']
    caller = workflow['jobs']['announce-repo-functionality']
    for prerequisite in ("github.event_name == 'push'", "github.ref == 'refs/heads/main'", "needs.ci-ready.result == 'success'", "needs.dataset-mutation-selection.outputs.has_repo_alert == 'true'"):
        assert prerequisite in caller['if']
    assert set(caller['needs']) == {'ci-ready', 'dataset-mutation-selection'}
    assert caller['uses'] == './.github/workflows/repo-functionality-alert.yml'
    assert caller['with']['executor_sha'] == '${{ needs.ci-ready.outputs.tested_sha }}'
    assert caller['with']['base_sha'] == '${{ github.event.before }}'
    announcement = load_workflow(ROOT / '.github/workflows/repo-functionality-alert.yml')
    assert set(workflow_triggers(announcement)) == {'workflow_call'}


def test_successful_or_skipped_upstream_events_cannot_allocate_failure_alert_worker():
    workflow = load_workflow(ROOT / '.github/workflows/unattended-workflow-alert.yml')
    condition = workflow['jobs']['notify']['if']
    match = re.search(r"contains\(fromJSON\('([^']+)'\), github.event.workflow_run.conclusion\)", condition)
    assert match is not None
    import json
    conclusions = json.loads(match[1])
    assert {'failure', 'timed_out', 'cancelled'} <= set(conclusions)
    assert not {'success', 'skipped', 'neutral'} & set(conclusions)
    assert "github.event.workflow_run.status == 'completed'" in condition
    assert 'github.event.workflow_run.head_repository.full_name == github.repository' in condition


def test_catalog_worker_allocation_requires_mutation_completion_index_rebuild_or_selected_main_ci():
    publisher = load_workflow(ROOT / '.github/workflows/publish-dataset.yml')
    refresh = publisher['jobs']['refresh-catalog']
    assert "needs.apply-approved-pr-plans.outputs.mutation_completed == 'true'" in refresh['if']
    assert refresh['with']['mutation_completed'] is True
    catalog = load_workflow(ROOT / '.github/workflows/catalog-web-deploy.yml')
    assert workflow_triggers(catalog)['workflow_run']['workflows'] == ['Catalog / Release index rebuild', 'Release index rebuild']
    assert "contains(fromJSON('[\"Catalog / Release index rebuild\",\"Release index rebuild\"]'), github.event.workflow_run.name)" in catalog['jobs']['deploy']['if']
    ci = load_workflow(ROOT / '.github/workflows/ci.yml')
    selected = ci['jobs']['dataset-mutation-selection']
    assert selected['outputs']['has_catalog_changes'] == '${{ steps.catalog.outputs.should_deploy }}'
    steps = workflow_steps_by_name(ci, 'dataset-mutation-selection')
    catalog_selection = steps['Select reviewed catalog changes without duplicating publication refresh']
    assert catalog_selection['run'] == 'python scripts/catalog_web_changes.py'
    assert catalog_selection['if'] == "${{ steps.discover.outputs.has_mutations == 'false' }}"
    caller = ci['jobs']['refresh-reviewed-catalog']
    assert set(caller['needs']) == {'ci-ready', 'dataset-mutation-selection', 'geospatial-changes', 'pmtiles-cdn'}
    assert "(needs.geospatial-changes.outputs.pmtiles_cdn != 'true' || needs.pmtiles-cdn.result == 'success')" in caller['if']
    assert caller['uses'] == './.github/workflows/catalog-web-deploy.yml'
    assert caller['with'] == {'executor_sha': '${{ needs.ci-ready.outputs.tested_sha }}',
                              'authorization_artifact_id': '', 'authorization_sha256': '',
                              'pr_number': 0, 'mutation_completed': False}
    assert "needs.dataset-mutation-selection.outputs.has_catalog_changes == 'true'" in caller['if']
    assert 'select-repo-changes' not in catalog['jobs']
    assert "github.event_name == 'push'" in catalog['jobs']['deploy']['if']
    viewer = load_workflow(ROOT / '.github/workflows/catalog-viewer-deploy.yml')
    triggers = workflow_triggers(viewer)
    assert set(triggers) == {'workflow_call', 'workflow_dispatch'}
    inputs = triggers['workflow_dispatch']['inputs']
    assert set(inputs) == {'executor_sha', 'source_run_id', 'source_run_attempt'}
    assert all(field['required'] is True and field['type'] == 'string' for field in inputs.values())


@pytest.mark.parametrize("changes,allowed", [
    ({}, True),
    ({"GITHUB_EVENT_NAME": "schedule", "MUTATION_COMPLETED": "", "EXECUTOR_SHA": ""}, True),
    ({"GITHUB_EVENT_NAME": "workflow_run", "MUTATION_COMPLETED": "", "EXECUTOR_SHA": ""}, True),
    ({"EXECUTOR_SHA": "b" * 40}, False),
    ({"GITHUB_REF": "refs/heads/topic"}, False),
    ({"GITHUB_WORKFLOW_REF": "SkyTruth/shared-datasets-1/.github/workflows/other.yml@refs/heads/main"}, False),
    ({"AUTHORIZATION_ARTIFACT_ID": "123"}, False),
    ({"AUTHORIZATION_SHA256": "c" * 64}, False),
    ({"PR_NUMBER": "3"}, False),
    ({"MUTATION_COMPLETED": ""}, False),
    ({"MUTATION_COMPLETED": "true"}, False),
    ({"MUTATION_COMPLETED": "true", "AUTHORIZATION_ARTIFACT_ID": "123",
      "AUTHORIZATION_SHA256": "c" * 64, "PR_NUMBER": "3"}, True),
    ({"MUTATION_COMPLETED": "true", "AUTHORIZATION_ARTIFACT_ID": "123",
      "AUTHORIZATION_SHA256": "c" * 64, "PR_NUMBER": "3", "EXECUTOR_SHA": ""}, False),
    ({"MUTATION_COMPLETED": "true", "AUTHORIZATION_ARTIFACT_ID": "123", "PR_NUMBER": "3"}, False),
    ({"MUTATION_COMPLETED": "true", "AUTHORIZATION_SHA256": "c" * 64, "PR_NUMBER": "3"}, False),
])
def test_catalog_receiver_executes_strict_precredential_call_shape(changes, allowed):
    workflow = load_workflow(ROOT / '.github/workflows/catalog-web-deploy.yml')
    steps = workflow_steps_by_name(workflow, 'deploy')
    guard = steps['Validate reusable catalog caller shape']
    env = {**os.environ, "GITHUB_EVENT_NAME": "push", "GITHUB_REF": "refs/heads/main",
           "GITHUB_REPOSITORY": "SkyTruth/shared-datasets-1",
           "GITHUB_WORKFLOW_REF": "SkyTruth/shared-datasets-1/.github/workflows/ci.yml@refs/heads/main",
           "GITHUB_SHA": "a" * 40, "EXECUTOR_SHA": "a" * 40,
           "MUTATION_COMPLETED": "false", "AUTHORIZATION_ARTIFACT_ID": "",
           "AUTHORIZATION_SHA256": "", "PR_NUMBER": "0", **changes}
    result = subprocess.run(['bash', '-c', guard['run']], env=env, capture_output=True, text=True)
    assert (result.returncode == 0) is allowed, result.stderr
    ordered = list(steps)
    assert ordered.index('Validate reusable catalog caller shape') < ordered.index('Authenticate to Google Cloud')
    assert ordered.index('Verify trusted catalog source authority') < ordered.index('Authenticate to Google Cloud')
    assert ordered.index('Verify successful mutation provenance') < ordered.index('Authenticate to Google Cloud')
