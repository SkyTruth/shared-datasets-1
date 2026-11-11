"""Bootstrap, exclusion and IAM boundaries exercised through real consumers."""
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

import pytest
import yaml

from scripts import pr_terraform_plan as pr
from scripts import terraform_target_contracts as contracts
from scripts.ci_preflight import suite_commands
from terraform_helpers import terraform_executable

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('active', [False, True])
def test_planner_cli_consumes_reviewed_activation_before_emitting_selection(tmp_path, monkeypatch, capsys, active):
    workflow_path = Path('.github/workflows/pr-terraform-plan.yml')
    workflow = yaml.safe_load((ROOT / workflow_path).read_text())
    workflow[True]['workflow_call']['inputs']['active']['default'] = active
    target = tmp_path / workflow_path
    target.parent.mkdir(parents=True)
    target.write_text(yaml.safe_dump(workflow))
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'changed_paths': ['terraform/envs/prod/pr_plan_iam.tf'], 'deployments': []}))
    output = tmp_path / 'github-output'
    monkeypatch.setattr(pr, 'ROOT', tmp_path)
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setattr(pr.sys, 'argv', ['planner', 'select', '--plan', str(plan)])
    pr.main()
    assert output.read_text() == f'pr_terraform={str(active).lower()}\n'
    assert ('not yet activated' in capsys.readouterr().out) is (not active)


@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('missing', [None, 'PROVIDER', 'PLANNER', 'PRODUCTION_TERRAFORM_SERVICE_ACCOUNT'])
def test_reviewed_activation_never_silently_disappears(active, missing):
    from workflow_helpers import condition_result
    workflow = yaml.safe_load((ROOT / '.github/workflows/pr-terraform-plan.yml').read_text())
    declaration = workflow[True]['workflow_call']['inputs']['active']
    assert declaration['type'] == 'boolean' and declaration['default'] is False
    leaf = workflow['jobs']
    values = {'github.event_name': 'pull_request', 'github.event.pull_request.head.repo.full_name': pr.REPOSITORY,
              'github.repository': pr.REPOSITORY, 'inputs.active': active}
    # Inject only the status predicate required by the shared expression evaluator.
    enabled = {name: condition_result({'if': job['if'].replace('${{', '${{ !cancelled() && ')}, values)
               for name, job in leaf.items()}
    assert enabled == {'plan': active}
    if not active:
        # The planner emits the inactive notice without a dedicated runner.
        output = ROOT / '.github/workflows/ci.yml'
        assert 'scripts/pr_terraform_plan.py select' in output.read_text()
        result = 'skipped'
    else:
        guard = next(s for s in leaf['plan']['steps'] if s.get('name') == 'Require activated planner configuration')
        environment = dict.fromkeys(('PROVIDER', 'PLANNER', 'PRODUCTION_TERRAFORM_SERVICE_ACCOUNT'), 'configured')
        if missing:
            environment[missing] = ''
        done = subprocess.run(['bash', '-euo', 'pipefail', '-c', guard['run']], env=environment)
        assert (done.returncode == 0) is (missing is None)
        result = 'failure' if done.returncode else 'success'
        assert leaf['plan']['steps'].index(guard) < next(i for i, s in enumerate(leaf['plan']['steps'])
            if s.get('uses', '').startswith('google-github-actions/auth'))
    ci = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    assert 'active' not in ci['pr-terraform-plan'].get('with', {})
    gate = next(step for step in ci['ci-ready']['steps'] if step.get('name') == 'Require selected same-repo Terraform plan')
    done = subprocess.run(['bash', '-c', gate['run']], env={'REQUIRED': str(active).lower(), 'RESULT': result})
    assert (done.returncode == 0) is (not active or missing is None)
    for result in ['failure', 'cancelled', 'skipped', 'success']:
        done = subprocess.run(['bash', '-c', gate['run']], env={'REQUIRED': 'true', 'RESULT': result})
        assert (done.returncode == 0) is (result == 'success')


@pytest.mark.parametrize('event,fork', [('pull_request', True), ('push', False), ('workflow_dispatch', False)])
@pytest.mark.parametrize('active', [False, True])
def test_activation_does_not_admit_forks_or_non_pr_events(event, fork, active):
    from workflow_helpers import condition_result
    leaf = yaml.safe_load((ROOT / '.github/workflows/pr-terraform-plan.yml').read_text())['jobs']
    values = {'github.event_name': event, 'github.event.pull_request.head.repo.full_name': 'other/fork' if fork else pr.REPOSITORY,
              'github.repository': pr.REPOSITORY, 'inputs.active': active}
    assert not any(condition_result({'if': job['if'].replace('${{', '${{ !cancelled() && ').replace(
        'vars.GCP_PR_PLAN_WORKLOAD_IDENTITY_PROVIDER', "''")}, values) for job in leaf.values())


def test_exclusions_equal_exact_secret_refresh_dependency_closures(tmp_path):
    providers = Path(os.environ.get('TF_DATA_DIR', ROOT / 'terraform/envs/prod/.terraform')) / 'providers'
    prod = contracts.prepare(ROOT, tmp_path, providers)
    graph = contracts.parse_graph(contracts.graph(prod, terraform_executable(), contracts.isolated_environment(tmp_path), tmp_path))
    # Real provider graph: an IAM-only plan checks bucket existence but cannot
    # pull its logging configuration into scope or wait for the usage canary.
    for name in ('Scheduled ingestion deploy IAM sync', 'Scratch cleanup IAM sync'):
        roots = {contracts.address(r) for r in contracts.registry()[name]['resources']}
        ancestors = contracts.closure(graph, roots)
        assert 'data.google_storage_bucket.shared_bucket_iam' in ancestors
        assert 'google_storage_bucket.shared_bucket' not in ancestors
    all_scopes = pr.scopes({'deployments': list(contracts.registry(section='deployments')),
                           'changed_paths': [p.relative_to(ROOT).as_posix() for p in (ROOT / 'terraform/envs/prod').glob('*.tf')]})
    retired = {r for rows in (row.get('retired_resources', []) for row in json.loads((ROOT / '.github/terraform-targets.json').read_text())['targets'].values()) for r in rows}
    secret_scopes = set()
    for scope in all_scopes:
        roots = {contracts.address(r) for r in scope['resources']}
        assert roots - graph.keys() <= retired
        if any('google_secret_manager_' in r for r in contracts.closure(graph, roots & graph.keys())):
            secret_scopes.add(scope['name'])
    exclusions = contracts.registry(section='pr_plan_exclusions')
    assert set(exclusions) == secret_scopes
    assert len(exclusions) == 7 and all(reason.strip() for reason in exclusions.values())
    # Exemptions cannot skip static IaC declaration/permission checks.
    commands = [argv for argv, _ in suite_commands('lint', ROOT, {'base': 'a'*40, 'tested_sha': 'b'*40}, tmp_path)]
    assert any('scripts/deployment_permissions.py' in argv and '--static-base' in argv for argv in commands)


def test_excluded_only_batch_notices_without_terraform_or_iam(tmp_path, capsys):
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': {side: {'repo': {'full_name': pr.REPOSITORY}} for side in ('head', 'base')}}))
    scope = {'name': 'eamlis-monthly'}
    with (patch.dict(os.environ, GITHUB_EVENT_NAME='pull_request', GITHUB_EVENT_PATH=str(event)),
          patch.object(pr, 'scopes', return_value=[scope]), patch.object(pr, 'terraform') as terraform,
          patch.object(pr, 'PolicyReader') as iam):
        pr.run({'changed_paths': ['terraform/envs/prod/eamlis_monthly.tf']})
    assert '::notice::Live PR plan excluded: eamlis-monthly:' in capsys.readouterr().out
    terraform.assert_not_called()
    iam.assert_not_called()


def test_pr_identity_never_maps_production_pool_capabilities():
    from terraform_helpers import load_terraform, evaluate
    values = load_terraform(ROOT / 'terraform/envs/prod/github_job_identities.tf')['locals'][0]
    values['github_workflow_identities'] = json.loads((ROOT / 'terraform/envs/prod/github-workflow-identities.json').read_text())
    mappings = evaluate(values, 'local.github_identity_attributes', variables={'github_repository': pr.REPOSITORY})
    assert not any(name.startswith('attribute.pr_plan') for name in mappings)
    source = (ROOT / 'terraform/envs/prod/pr_plan_iam.tf').read_text()
    assert 'local.github_identity_attributes' not in source
    assert 'local.github_workflow_identities.pr_plan_caller' in source
    assert 'local.github_workflow_identities.pr_plan ' in source
