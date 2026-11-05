"""Credential-free changed-HCL checks consume the executor's actual allowlists."""
import subprocess
import sys
import json

import pytest

from scripts.terraform_plan_permissions import static_checks
from scripts.ci_preflight import suite_commands
from test_ci_preflight import ROOT, fixture_repo


@pytest.mark.parametrize('defect', [None, 'unlisted', 'unsupported', 'permission', 'module', 'deleted'])
@pytest.mark.parametrize('contract', ['inline', 'terraform', 'ingestion'])
def test_changed_hcl_checks_real_snapshots_before_cloud_auth(tmp_path, defect, contract):
    root, git = fixture_repo(tmp_path)
    workflows = root / '.github/workflows'
    (workflows / 'ci.yml').write_text('jobs:\n  artifact-registry-iam:\n    uses: ./.github/workflows/sync.yml\n')
    address = 'google_project_iam_custom_role.publisher'
    path = root / 'terraform/envs/prod/artifact_registry_iam.tf'
    path.parent.mkdir(parents=True, exist_ok=True)
    body = 'resource "google_project_iam_custom_role" "publisher" { permissions = ["iam.roles.get"] }\n'
    path.write_text(body)
    if defect == 'module':
        path.write_text('module "worker" { source = "../../modules/worker" }\n')
        child = root / 'terraform/modules/worker/main.tf'
        child.parent.mkdir(parents=True)
        child.write_text(body)
        address = 'module.worker.' + address
    (workflows / 'sync.yml').write_text('jobs:\n  sync:\n    with:\n      allowed_exact: ' + address + '\n')
    if contract == 'terraform':
        (root / '.github/terraform-targets.json').write_text(json.dumps({'schema_version': 1, 'targets': {
            'Fixture sync': {'workflow': 'sync.yml', 'readiness_target': 'iam-bootstrap', 'resources': [address]},
        }}))
        (workflows / 'sync.yml').write_text('jobs:\n  sync:\n    uses: ./.github/workflows/prod-terraform-target-apply.yml\n'
            '    with:\n      sync_name: Fixture sync\n      caller_workflow: sync.yml\n')
        (workflows / 'prod-terraform-target-apply.yml').write_text('jobs: {}\n')
    elif contract == 'ingestion':
        (root / 'scripts/ingestion_targets.py').write_text('TARGETS = ' + repr({'eamlis-monthly': {'targets': [address]}}))
        (workflows / 'sync.yml').write_text('jobs:\n  sync:\n    uses: ./.github/workflows/ingestion-deploy.yml\n'
            '    with:\n      target: eamlis-monthly\n')
        (workflows / 'ingestion-deploy.yml').write_text('jobs: {}\n')
    git('add', '.')
    git('commit', '-m', 'static scope baseline')
    base = git('rev-parse', 'HEAD')
    if defect == 'module':
        path.write_text(path.read_text().replace(' }', ' depends_on = [] }'))
        child.write_text(body.replace(' }', ' title = "updated" }'))
    elif defect == 'deleted':
        path.unlink()
    else:
        path.write_text(body.replace('publisher', 'unknown') if defect == 'unlisted' else
                        body.replace('google_project_iam_custom_role', 'google_unhandled_resource') if defect == 'unsupported' else
                        body.replace('"iam.roles.get"', '') if defect == 'permission' else body.replace(' }', ' title = "updated" }'))
    git('add', '.')
    git('commit', '-m', 'changed HCL')
    if defect in {'unlisted', 'unsupported', 'permission', 'deleted'}:
        with pytest.raises(ValueError, match='STATIC_IAC_CONTRACT'):
            static_checks(root, base, 'HEAD')
    else:
        changed = static_checks(root, base, 'HEAD')
        assert address in changed
        # The real CLI returns before asking for a token or invoking gcloud.
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/deployment_permissions.py'),
                                 '--static-base', base], cwd=root, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    commands = suite_commands('lint', ROOT,
        {'base': base, 'tested_sha': 'HEAD', 'changed_paths': ['terraform/envs/prod/artifact_registry_iam.tf']}, tmp_path)
    assert any('--static-base' in argv for argv, _ in commands)
    assert not any('gcloud' in argv or 'apply' in argv for argv, _ in commands)
