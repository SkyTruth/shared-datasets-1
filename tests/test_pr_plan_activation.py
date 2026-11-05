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

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('provider', ['', 'configured-provider'])
def test_activation_notice_and_required_failure_gate(provider):
    leaf = yaml.safe_load((ROOT / '.github/workflows/pr-terraform-plan.yml').read_text())['jobs']
    assert "vars.GCP_PR_PLAN_WORKLOAD_IDENTITY_PROVIDER == ''" in leaf['activation']['if']
    assert "vars.GCP_PR_PLAN_WORKLOAD_IDENTITY_PROVIDER != ''" in leaf['plan']['if']
    notice = subprocess.run(['bash', '-e', '-c', leaf['activation']['steps'][0]['run']], capture_output=True, text=True, check=True)
    assert '::notice::' in notice.stdout and 'not yet activated' in notice.stdout
    assert not any('uses' in step for step in leaf['activation']['steps'])
    ci = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    gate = next(step for step in ci['ci-ready']['steps'] if step.get('name') == 'Require selected same-repo Terraform plan')
    # The reusable call succeeds via the notice job before activation. Once
    # configured, a failed or skipped planner cannot pass the caller's gate.
    results = ['success'] if not provider else ['failure', 'cancelled', 'skipped', 'success']
    for result in results:
        done = subprocess.run(['bash', '-c', gate['run']], env={'REQUIRED': 'true', 'RESULT': result})
        assert done.returncode == (0 if result == 'success' else 1)
    auth = next(s for s in leaf['plan']['steps'] if s.get('uses', '').startswith('google-github-actions/auth'))
    assert auth['with'] == {'workload_identity_provider': '${{ vars.GCP_PR_PLAN_WORKLOAD_IDENTITY_PROVIDER }}',
                            'service_account': '${{ vars.GCP_PR_PLAN_SERVICE_ACCOUNT }}'}


def test_exclusions_equal_exact_secret_refresh_dependency_closures(tmp_path):
    providers = Path(os.environ.get('TF_DATA_DIR', ROOT / 'terraform/envs/prod/.terraform')) / 'providers'
    prod = contracts.prepare(ROOT, tmp_path, providers)
    graph = contracts.parse_graph(contracts.graph(prod, 'terraform', contracts.isolated_environment(tmp_path), tmp_path))
    all_scopes = pr.scopes({'deployments': list(contracts.registry(section='deployments')),
                           'changed_paths': [p.relative_to(ROOT).as_posix() for p in (ROOT / 'terraform/envs/prod').glob('*.tf')]})
    retired = {r for rows in json.loads((ROOT / 'terraform/iam-plan-prerequisites.json').read_text())['retired_targets'].values() for r in rows}
    secret_scopes = set()
    for scope in all_scopes:
        roots = {contracts.address(r) for r in scope['resources']}
        assert roots - graph.keys() <= retired
        if any('google_secret_manager_' in r for r in contracts.closure(graph, roots & graph.keys())):
            secret_scopes.add(scope['name'])
    exclusions = contracts.registry(section='pr_plan_exclusions')
    assert set(exclusions) == secret_scopes
    assert len(exclusions) == 6 and all(reason.strip() for reason in exclusions.values())
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
