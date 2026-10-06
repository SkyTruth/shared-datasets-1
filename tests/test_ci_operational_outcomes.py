"""Supersession may stop PR validation, never active production delivery."""
from pathlib import Path
import re

import pytest

from workflow_helpers import load_workflow, workflow_steps_by_name, workflow_triggers

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = {
    'geospatial-changes', 'lint', 'tests', 'geospatial-integration',
    'production-images', 'sdk-validation', 'browser', 'ci-ready',
}


def group(block, *, pr=None, run=101, node='22'):
    result = block['group'].replace('${{ github.event.pull_request.number || github.run_id }}', str(pr or run))
    return result.replace('${{ matrix.node }}', node)


def test_supersession_cancels_only_the_same_pr_and_same_validation_suite():
    workflow = load_workflow(ROOT / '.github/workflows/ci.yml')
    assert 'concurrency' not in workflow
    groups = set()
    for name in VALIDATION:
        block = workflow['jobs'][name]['concurrency']
        assert block['cancel-in-progress'] == "${{ github.event_name == 'pull_request' }}"
        assert group(block, pr=91, run=101) == group(block, pr=91, run=102)
        assert group(block, pr=91) != group(block, pr=92)
        assert group(block, run=101) != group(block, run=102)
        assert group(block, pr=91) not in groups
        groups.add(group(block, pr=91))
    sdk = workflow['jobs']['sdk-validation']['concurrency']
    assert group(sdk, pr=91, node='22') != group(sdk, pr=91, node='24')
    for name, job in workflow['jobs'].items():
        if name not in VALIDATION:
            assert not job.get('concurrency', {}).get('cancel-in-progress', False)


@pytest.mark.parametrize('filename', [
    'publish-dataset.yml', 'catalog-web-deploy.yml', 'catalog-viewer-deploy.yml',
    'pmtiles-cdn-sync.yml', 'prod-terraform-target-apply.yml',
    'publish-typescript-sdk.yml', 'deployment-verification.yml',
    'deployment-recovery.yml', 'cron-alert-delivery-test.yml',
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


def test_catalog_worker_allocation_requires_actual_mutation_completion_or_index_rebuild():
    publisher = load_workflow(ROOT / '.github/workflows/publish-dataset.yml')
    refresh = publisher['jobs']['refresh-catalog']
    assert "needs.apply-approved-pr-plans.outputs.mutation_completed == 'true'" in refresh['if']
    assert refresh['with']['mutation_completed'] is True
    catalog = load_workflow(ROOT / '.github/workflows/catalog-web-deploy.yml')
    assert workflow_triggers(catalog)['workflow_run']['workflows'] == ['Release index rebuild']
    viewer = load_workflow(ROOT / '.github/workflows/catalog-viewer-deploy.yml')
    triggers = workflow_triggers(viewer)
    assert set(triggers) == {'workflow_call', 'workflow_dispatch'}
    inputs = triggers['workflow_dispatch']['inputs']
    assert set(inputs) == {'executor_sha', 'source_run_id', 'source_run_attempt'}
    assert all(field['required'] is True and field['type'] == 'string' for field in inputs.values())
