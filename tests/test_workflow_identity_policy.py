"""Trigger, CEL and execution-filter regressions at the declared IAM boundary."""
from functools import lru_cache
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from identity_policy_helpers import cel
from terraform_helpers import evaluate, load_terraform, resources
from workflow_helpers import load_workflow, workflow_triggers

ROOT = Path(os.environ.get('WS24_ROOT', Path(__file__).resolve().parents[1]))
PROD = ROOT / 'terraform/envs/prod'
REPO = 'SkyTruth/shared-datasets-1'
MAIN = 'refs/heads/main'
FEATURE = 'refs/heads/review-fixture'


def workflow(name):
    return load_workflow(ROOT / '.github/workflows' / name)


def resource(filename, kind, name):
    return next(body for type_, resource_, body in resources(load_terraform(PROD / filename))
                if (type_, resource_) == (kind, name))


def claims(filename, ref=MAIN, event='workflow_dispatch', **extra):
    return dict(repository=REPO, ref=ref, event_name=event,
                environment='shared-datasets-production', sub='fixture',
                workflow_ref=f'{REPO}/.github/workflows/{filename}@{ref}', **extra)


@lru_cache
def policies():
    values = load_terraform(PROD / 'github_job_identities.tf')['locals'][0]
    values['github_workflow_identities'] = json.loads((PROD / 'github-workflow-identities.json').read_text())
    for key, filename, name in [('production', 'canonical_mutation_iam.tf', 'github'),
                               ('readonly', 'github_readonly_iam.tf', 'github_readonly'),
                               ('pr', 'pr_plan_iam.tf', 'pr_plan')]:
        values[key] = resource(filename, 'google_iam_workload_identity_pool_provider', name)['attribute_condition']
    return evaluate(values, '{production = local.production, readonly = local.readonly, pr = local.pr, attributes = local.github_identity_attributes}',
                    variables={'github_repository': REPO, 'github_publish_environment': 'shared-datasets-production'})


def allowed(identity, assertion):
    policy = policies()
    try:
        if identity.startswith('pr_plan'):
            return cel(policy['pr'], assertion) is True
        if identity == 'bucket_reader':
            return cel(policy['readonly'], assertion) is True
        return (cel(policy['production'], assertion) is True
                and cel(policy['attributes']['attribute.' + identity], assertion) == 'allowed')
    except KeyError:
        return False  # Missing required claims fail authentication.


def roots(filename, seen=frozenset()):
    assert filename not in seen, 'recursive workflow calls'
    value = workflow(filename)
    triggers = workflow_triggers(value)
    for event in set(triggers) - {'workflow_call'}:
        if event == 'push' and isinstance(triggers[event], dict):
            assert 'main' in triggers[event].get('branches', ['main'])
        ref = 'refs/pull/42/merge' if event == 'pull_request' else MAIN
        yield filename, ref, event
    if 'workflow_call' in triggers:
        for path in (ROOT / '.github/workflows').glob('*.yml'):
            if any(job.get('uses') == './.github/workflows/' + filename
                   for job in workflow(path.name)['jobs'].values()):
                yield from roots(path.name, seen | {filename})


REGISTERED = json.loads((PROD / 'github-workflow-identities.json').read_text())


@pytest.mark.parametrize('identity,filename', [(key, name) for key, names in REGISTERED.items() for name in names])
def test_every_registered_workflow_has_a_trigger_compatible_with_its_identity(identity, filename):
    candidates = []
    for caller, ref, event in roots(filename):
        assertion = claims(caller, ref, event)
        if filename != caller:
            assertion['job_workflow_ref'] = f'{REPO}/.github/workflows/{filename}@{ref}'
        if identity == 'pr_plan_caller':
            assertion['job_workflow_ref'] = f'{REPO}/.github/workflows/pr-terraform-plan.yml@{ref}'
        candidates.append(assertion)
    assert any(allowed(identity, assertion) for assertion in candidates), (identity, filename, candidates)
    # A dispatch that accepts its own selected branch as a separate source must
    # either authenticate that branch or reject it before reaching credentials.
    value = workflow(filename)
    if 'workflow_dispatch' in workflow_triggers(value):
        for job in value['jobs'].values():
            if any(step.get('with', {}).get('ref') == '${{ github.ref }}'
                   for step in job.get('steps', [])):
                guards = [step['run'] for step in job['steps'] if step.get('name') == 'Validate selected ref']
                for guard in guards:
                    result = run_guard(guard, ref=FEATURE)
                    assert result.returncode != 0 or allowed(identity, claims(filename, FEATURE))


@pytest.mark.parametrize('identity,names', REGISTERED.items())
def test_rendered_cel_claim_boundaries(identity, names):
    filename = names[0]
    if identity.startswith('pr_plan'):
        assertion = claims('ci.yml', 'refs/pull/42/merge', 'pull_request',
                           job_workflow_ref=f'{REPO}/.github/workflows/pr-terraform-plan.yml@refs/pull/42/merge')
    else:
        assertion = claims(filename)
    assert allowed(identity, assertion)
    assert not allowed(identity, {**assertion, 'repository': 'foreign/repository'})
    assert not allowed(identity, {**assertion, 'ref': FEATURE})
    assert not allowed(identity, {**assertion, 'workflow_ref': f'{REPO}/.github/workflows/foreign.yml@{MAIN}',
                                  'job_workflow_ref': f'{REPO}/.github/workflows/foreign.yml@{MAIN}'})
    if not identity.startswith('pr_plan'):
        assert not allowed(identity, {**assertion, 'ref': 'refs/pull/42/merge', 'event_name': 'pull_request'})
    if identity != 'bucket_reader' and not identity.startswith('pr_plan'):
        assert not allowed(identity, {**assertion, 'environment': 'unprotected'})
        assert not allowed(identity, {key: value for key, value in assertion.items() if key != 'environment'})
        reusable = claims('foreign.yml', job_workflow_ref=f'{REPO}/.github/workflows/{filename}@{MAIN}')
        assert allowed(identity, reusable), 'registered immutable reusable leaf overrides caller'
        assert not allowed(identity, {**reusable, 'job_workflow_ref': reusable['job_workflow_ref'].replace(MAIN, FEATURE)})


@pytest.mark.parametrize('filename,event,ref,expected', [
    ('dataset-breaking-change-alert.yml', 'pull_request', 'refs/pull/42/merge', True),
    ('dataset-breaking-change-alert.yml', 'pull_request', 'refs/pull/42/head', False),
    ('dataset-breaking-change-alert.yml', 'pull_request_target', MAIN, False),
    ('dataset-breaking-change-alert.yml', 'workflow_dispatch', 'refs/pull/42/merge', False),
    ('catalog-drift-guard.yml', 'pull_request', 'refs/pull/42/merge', False),
    ('bucket-hygiene-audit.yml', 'pull_request', 'refs/pull/42/merge', False),
    ('eamlis-held-candidate-preview.yml', 'pull_request', 'refs/pull/42/merge', False),
    ('foreign.yml', 'pull_request', 'refs/pull/42/merge', False),
])
def test_pr_bucket_read_exception_is_confined_to_exact_advisory_leaf(filename, event, ref, expected):
    assertion = claims(filename, ref, event)
    assert allowed('bucket_reader', assertion) is expected
    if expected:
        assert not allowed('bucket_reader', {**assertion, 'repository': 'foreign/repository'})
        assert not allowed('terraform', assertion)
        assert not allowed('publisher', assertion)
    if filename == 'dataset-breaking-change-alert.yml' and event == 'pull_request':
        assert not allowed('bucket_reader', {**assertion, 'workflow_ref': assertion['workflow_ref'].replace(filename, 'foreign.yml'),
                                             'job_workflow_ref': assertion['workflow_ref']})
        assert not allowed('bucket_reader', {**assertion, 'job_workflow_ref': assertion['workflow_ref'].replace(filename, 'foreign.yml')})
    value = workflow('dataset-breaking-change-alert.yml')
    route = value['jobs']['route']['if']
    assert 'github.event.pull_request.head.repo.full_name == github.repository' in route
    assert 'github.event.pull_request.base.repo.full_name == github.repository' in route
    assert value['jobs']['alert']['needs'] == 'route'
    checkout = next(step for step in value['jobs']['alert']['steps'] if step.get('uses') == 'actions/checkout@v4')
    assert checkout['with']['ref'] == '${{ github.event.pull_request.base.sha }}'


def run_guard(command, *, ref=MAIN, source=FEATURE, mode='preserve'):
    return subprocess.run(['bash', '-euo', 'pipefail', '-c', command], text=True, capture_output=True,
                          env={**os.environ, 'GITHUB_REF': ref, 'PREVIEW_REF': source,
                               'PREVIEW_SOURCE_REF': ref, 'PREVIEW_DATA_MODE': mode, 'GITHUB_REPOSITORY': REPO,
                               'GITHUB_WORKFLOW_REF': f'{REPO}/.github/workflows/feature-preview-deploy.yml@{ref}'})


@pytest.mark.parametrize('ref,source,mode,expected', [
    (MAIN, FEATURE, 'preserve', True), (MAIN, 'refs/tags/v1', 'reset', True),
    (FEATURE, FEATURE, 'preserve', False), (MAIN, 'refs/pull/42/merge', 'preserve', False),
    (MAIN, '', 'preserve', False), (MAIN, 'refs/heads/../../main', 'preserve', False),
    (MAIN, FEATURE, 'invalid', False),
])
def test_preview_dispatch_validates_control_ref_and_independent_source(ref, source, mode, expected):
    value = workflow('feature-preview-deploy.yml')
    assert workflow_triggers(value)['workflow_dispatch']['inputs']['source_ref']['required'] is True
    job = value['jobs']['preview']
    assert job['env']['PREVIEW_REF'] == '${{ inputs.source_ref }}'
    steps = {step['name']: step for step in job['steps'] if 'name' in step}
    assert steps['Check out preview control plane']['with']['ref'] == '${{ github.workflow_sha }}'
    assert steps['Check out selected feature branch']['with']['ref'] == '${{ inputs.source_ref }}'
    command = steps['Require trusted main workflow']['run'] + '\n' + steps['Validate selected ref']['run']
    assert (run_guard(command, ref=ref, source=source, mode=mode).returncode == 0) is expected


@pytest.mark.parametrize('binding', ['preview', 'legacy', 'foreign'])
def test_preview_bootstrap_checks_the_actual_capability_binding(tmp_path, binding):
    command = next(step['run'] for step in workflow('feature-preview-deploy.yml')['jobs']['preview']['steps']
                   if step.get('name') == 'Validate preview IAM bootstrap')
    member = resource('preview_terraform_iam.tf', 'google_service_account_iam_member', 'feature_preview_loader_github_wif')['member']
    expected = member.replace('${google_iam_workload_identity_pool.github.name}', 'projects/123/locations/global/workloadIdentityPools/github')
    assert '/attribute.preview/allowed' in expected
    project = 'shared-datasets-1'
    account = f'shared-datasets-terraform@{project}.iam.gserviceaccount.com'
    actual = {'preview': expected, 'legacy': 'principal://fixture/subject/repo:SkyTruth/shared-datasets-1:environment:shared-datasets-production',
              'foreign': expected.replace('attribute.preview', 'attribute.publisher')}[binding]
    policies = [
        {'bindings': [{'role': f'projects/{project}/roles/sharedDatasetsPreviewTerraform', 'members': ['serviceAccount:' + account]}]},
        {'bindings': [{'role': f'projects/{project}/roles/sharedDatasetsCatalogViewerSignBlob', 'members': [f'serviceAccount:feature-preview-service@{project}.iam.gserviceaccount.com']}]},
        {'bindings': [{'role': 'roles/iam.workloadIdentityUser', 'members': [actual]}]},
    ]
    # Execute the workflow's whole bootstrap script through fake read-only gcloud.
    cli = tmp_path / 'gcloud'
    cli.write_text('#!' + sys.executable + '\nimport json,sys\n'
                   + 'policies=' + repr(policies) + '\n'
                   + "print('123' if sys.argv[1:3] == ['projects', 'describe'] else json.dumps(policies[0 if sys.argv[1] == 'projects' else 2 if 'loader' in sys.argv[4] else 1]))\n")
    cli.chmod(0o755)
    result = subprocess.run(['bash', '-euo', 'pipefail', '-c', command], text=True, capture_output=True,
                            env={**os.environ, 'PATH': str(tmp_path) + ':' + os.environ['PATH'],
                                 'RUNNER_TEMP': str(tmp_path), 'GOOGLE_CLOUD_PROJECT': project,
                                 'TERRAFORM_SERVICE_ACCOUNT': account})
    assert (result.returncode == 0) is (binding == 'preview'), result.stdout + result.stderr


@pytest.mark.parametrize('creator,marked,suppressed', [('runtime', True, True), ('runtime', False, False),
    ('terraform', True, False), ('scheduler', False, False), ('scheduler', True, False), ('', True, False)])
def test_native_failure_filter_suppresses_only_marked_runtime_canaries(creator, marked, suppressed):
    policy = resource('monitoring.tf', 'google_monitoring_alert_policy', 'scheduled_ingestion_cloud_run_failure')
    filter_ = policy['conditions'][0]['condition_matched_log'][0]['filter']
    suppressor = re.search(r'run.googleapis.com/creator"="([^\n]+)"', filter_).group(1)
    account = resource('github_job_identities.tf', 'google_service_account', 'github_job')
    runtime = account['account_id'].replace('${each.key}', 'runtime') + '@shared-datasets-1.iam.gserviceaccount.com'
    # Resolve the resource reference, using the actual account's declared identity.
    suppressor = suppressor.replace('${google_service_account.github_job["runtime"].email}', runtime)
    suppressor = suppressor.replace('${var.github_actions_terraform_service_account_email}', 'shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com')
    assert re.search(r'NOT\s*\(\s*protoPayload.*creator.*\n\s*AND .*env.name="SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL"\s*\)', filter_)
    actual = f'shared-datasets-{creator}@shared-datasets-1.iam.gserviceaccount.com' if creator else ''
    assert (actual == suppressor and marked) is suppressed
    if suppressed:
        steps = workflow('ingestion-deploy.yml')['jobs']['verify']['steps']
        auth = next(step for step in steps if step.get('name') == 'Authenticate canary runner')
        assert auth['with']['service-account'] == suppressor
    assert 'resource.labels.job_name' not in filter_


def test_held_candidate_uses_readonly_identity_and_no_unmanaged_observer_variables():
    value = workflow('eamlis-held-candidate-preview.yml')
    auth = [step for step in value['jobs']['inspect']['steps'] if step.get('uses') == 'google-github-actions/auth@v2']
    assert auth[0]['with'] == {'workload_identity_provider': '${{ vars.GCP_READONLY_WORKLOAD_IDENTITY_PROVIDER }}',
                               'service_account': '${{ vars.GCP_READONLY_SERVICE_ACCOUNT }}'}
    assert auth[1]['with']['service_account'].startswith('feature-preview-loader@')
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        assert 'GCP_OPERATIONAL_OBSERVER_' not in path.read_text()
