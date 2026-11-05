"""Integrated acceptance permissions and the shared HCL parsing boundary."""
from pathlib import Path

import yaml

from scripts.release_contracts import role_permissions

ROOT = Path(__file__).resolve().parents[1]


def test_all_bootstrap_callers_preserve_pull_request_read_permission():
    workflows = {path.name: yaml.safe_load(path.read_text())
                 for path in (ROOT / '.github/workflows').glob('*.yml')}
    owners = {name for name, workflow in workflows.items()
              if any(step.get('uses', '').startswith('./.github/actions/deployment-bootstrap')
                     for job in workflow['jobs'].values() for step in job.get('steps', []))}
    def calls(job):
        return job.get('uses', '').removeprefix('./.github/workflows/') in owners
    while True:
        parents = {name for name, workflow in workflows.items() if any(calls(job) for job in workflow['jobs'].values())}
        if parents <= owners:
            break
        owners |= parents
    for name in owners:
        workflow = workflows[name]
        for identifier, job in workflow['jobs'].items():
            if calls(job) or any(step.get('uses', '').startswith('./.github/actions/deployment-bootstrap')
                                 for step in job.get('steps', [])):
                assert job.get('permissions', workflow.get('permissions', {})).get('pull-requests') in {'read', 'write'}, (name, identifier)


def test_role_permission_contract_uses_hcl_declarations_not_comment_decoys(tmp_path):
    path = tmp_path / 'terraform/envs/prod/roles.tf'
    path.parent.mkdir(parents=True)
    path.write_text('''# resource "google_project_iam_custom_role" "reader" { permissions = ["unsafe.write"] }
  resource "google_project_iam_custom_role" "reader" {
    permissions = ["storage.objects.get", "storage.objects.list"]
  }
''')
    assert role_permissions(tmp_path, 'roles.tf', 'reader') == {'storage.objects.get', 'storage.objects.list'}
    assert role_permissions(tmp_path, 'roles.tf', 'missing') == set()
