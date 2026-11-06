"""Round-eight producer boundaries, historical receipts and clean hosted imports."""
from __future__ import annotations

import copy
import importlib.metadata
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tomllib
from unittest.mock import Mock

import pytest
import yaml

from scripts import ci_contract as ci, deployment_emission as emission, deployment_revision as revision
from scripts import terraform_plan_allowlist as allowlist

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    for name in ('.github', 'scripts', 'terraform', 'ingestion', 'services', 'catalog', 'api'):
        shutil.copytree(ROOT / name, root / name,
                        ignore=shutil.ignore_patterns('.terraform', '__pycache__', 'node_modules'))
    return root


@pytest.mark.parametrize('kind,expected', [('boundary', ['catalog_viewer']), ('exclude', []), ('include', ['catalog_viewer'])])
def test_one_exception_table_owns_traversal_and_explicit_inputs(source, kind, expected):
    path = source / '.github/terraform-targets.json'
    value = json.loads(path.read_text())
    policy = 'scripts/ws27_policy.py'
    entrypoint = 'scripts/ws27_admission.py'
    (source / entrypoint).write_text('from scripts import ws27_policy\n')
    (source / policy).write_text('POLICY = True\n')
    workflow = source / '.github/workflows/catalog-viewer-deploy.yml'
    with workflow.open('a') as stream:
        stream.write('\n  ws27-check:\n    runs-on: ubuntu-latest\n    steps:\n    - run: python ' + entrypoint + '\n')
    row = {'kind': kind, 'reason': 'Exercise the reviewed selection boundary'}
    if kind == 'include':
        row = {'kind': kind, 'deployments': {'catalog_viewer': row['reason']}}
    value['selection_exceptions'][entrypoint] = row
    path.write_text(json.dumps(value))
    assert ci.select_deployments([entrypoint], root=source) == expected
    selected = ci.select_deployments([policy], root=source)
    assert selected == (['catalog_viewer'] if kind == 'include' else [])


def test_selection_exception_requires_reason(source):
    path = source / '.github/terraform-targets.json'
    value = json.loads(path.read_text())
    value['selection_exceptions']['scripts/deployment_revision.py']['reason'] = ' '
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='selection exceptions'):
        ci.deployment_paths(source)


def test_wdpa_hcl_index_accepts_valid_indentation_and_comments(tmp_path, monkeypatch):
    path = tmp_path / 'terraform/envs/prod/wdpa_monthly.tf'
    path.parent.mkdir(parents=True)
    # Indented blocks are legal HCL. A regex cannot
    # distinguish these inputs from a commented-out decoy module.
    path.write_text('''# module "wdpa_monthly_job" { cpu = "999" }
  module "wdpa_monthly_job" {
    source = "../../modules/cloud_run_job"
    cpu = "4"
    memory = "8Gi"
    timeout = "43200s"
  }
''')
    monkeypatch.setattr(allowlist, 'ROOT', tmp_path)
    monkeypatch.setattr(allowlist, 'WDPA_MODULE', path, raising=False)
    task = {'timeout': '43200s', 'containers': [{'resources': [{'limits': {'cpu': '4', 'memory': '8Gi'}}]}]}
    assert 'unexpected WDPA processing limits' not in allowlist.wdpa_errors({'template': [{'template': [task]}]}, 'image', 'config')


@pytest.mark.parametrize('corrupt', [False, True])
def test_real_historical_wrapper_receipt_remains_bound_to_its_original_signer(tmp_path, monkeypatch, corrupt):
    fixture = json.loads((ROOT / 'tests/fixtures/deployment-attestation/wdpa-wrapper-7521868392.json').read_text())
    record = copy.deepcopy(fixture['record'])
    api = Mock()
    api.get.side_effect = fixture['api'].__getitem__
    api.pages.side_effect = lambda path, *args, **kwargs: fixture['api'][path]
    calls = []
    def verified(command, repository, digest):
        calls.append(command)
        return json.dumps(fixture['verification'])
    monkeypatch.setattr(emission, 'verified_output', verified)
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    if corrupt:
        record['payload']['artifact'] = 'image@sha256:' + 'f' * 64
        with pytest.raises(revision.DeploymentError, match='exact record'):
            revision.verify_record(api, 'SkyTruth/shared-datasets-1', record)
    else:
        assert revision.verify_record(api, 'SkyTruth/shared-datasets-1', record)['target'] == 'wdpa-monthly'
    assert calls[0][calls[0].index('--signer-workflow') + 1].endswith('/wdpa-monthly-deploy.yml')
    api.post.assert_not_called()


def isolated_python(tmp_path, packages, code, *args, env=None):
    """Ignore the dev environment; expose only workflow-declared distributions."""
    site = tmp_path / 'declared-site'
    site.mkdir()
    for package in packages:
        distribution = importlib.metadata.distribution(package)
        for member in distribution.files:
            if '..' in member.parts or '__pycache__' in member.parts:
                continue
            source = Path(distribution.locate_file(member))
            if source.is_file():
                destination = site / member
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
    boot = f'import sys; sys.path[:0] = [{str(site)!r}, {str(ROOT)!r}]; ' + code
    return subprocess.run([sys.executable, '-I', '-S', '-c', boot, *args], cwd=ROOT,
                          env=env, text=True, capture_output=True)


def test_hosted_wdpa_allowlist_executes_with_only_its_locked_declared_dependencies(tmp_path):
    workflow = yaml.safe_load((ROOT / '.github/workflows/ingestion-deploy.yml').read_text())
    step = next(s for s in workflow['jobs']['apply']['steps'] if s.get('name') == 'Enforce ingestion resource-change allowlist')
    assert 'uv run --frozen --only-group ci-plan python scripts/terraform_plan_allowlist.py' in step['run']
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert any(item.startswith('python-hcl2') for item in project['dependency-groups']['ci-plan'])
    steps = workflow['jobs']['apply']['steps']
    sync = next(i for i, item in enumerate(steps) if 'uv sync --locked --only-group ci-plan' in item.get('run', '').splitlines()[-1:])
    assert sync < next(i for i, item in enumerate(steps) if item.get('uses') == './.github/actions/deployment-auth')
    image, config = 'image@sha256:' + 'a' * 64, 'sha256:' + 'b' * 64
    task = {'timeout': '43200s', 'volumes': [{'name': 'work', 'empty_dir': [{'medium': 'DISK', 'size_limit': '100Gi'}]}],
            'containers': [{'image': image, 'resources': [{'limits': {'cpu': '4', 'memory': '8Gi'}}],
                            'env': [{'name': k, 'value': v} for k, v in {
                                'WDPA_BUILD_IMAGE': image, 'WDPA_BUILD_IMAGE_CONFIG_DIGEST': config,
                                'TMPDIR': '/work/tmp', 'SHARED_DATASETS_WORKDIR': '/work/shared-datasets-1'}.items()],
                            'volume_mounts': [{'name': 'work', 'mount_path': '/work'}]}]}
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'resource_changes': [{'address': allowlist.JOB_IMAGE_TARGETS['wdpa-monthly'],
                           'change': {'actions': ['update'], 'after': {'template': [{'template': [task]}]}}}]}))
    result = isolated_python(tmp_path, ['python-hcl2', 'lark', 'regex'],
        'import runpy; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name="__main__")',
        'scripts/terraform_plan_allowlist.py', str(plan), '--job-image-target', 'wdpa-monthly',
        '--expected-image', image, '--expected-config', config, '--refusal-prefix', 'Refusing WDPA')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('declared', [False, True])
def test_hosted_cdn_folder_guard_parses_the_immutable_hcl_revision(tmp_path, declared):
    role = ('# resource "google_project_iam_custom_role" "pmtiles_managed_folder_sync" { permissions = ["storage.managedFolders.delete"] }\n'
            '  resource "google_project_iam_custom_role" "pmtiles_managed_folder_sync" {\n'
            '    permissions = ["storage.managedFolders.' + ('delete' if declared else 'create') + '"]\n  }\n').encode()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w') as archive:
        member = tarfile.TarInfo('terraform/envs/prod/shared_bucket_public.tf')
        member.size = len(role)
        archive.addfile(member, io.BytesIO(role))
    binary = tmp_path / 'bin'
    binary.mkdir()
    git = binary / 'git'
    git.write_text(f'''#!{sys.executable}
import sys
args = sys.argv[1:]
if args == ['archive', {'b' * 40!r}]:
    sys.stdout.buffer.write({raw.getvalue()!r})
elif args[0] == 'show' and args[1].endswith(':catalog/shared-datasets-catalog.csv'):
    print('canonical_path')
    if args[1].startswith({'a' * 40!r}):
        print('gs://skytruth-shared-datasets-1/example/asset/latest/a.fgb')
else:
    raise SystemExit(91)
''')
    git.chmod(0o755)
    result = isolated_python(tmp_path, ['PyYAML', 'python-hcl2', 'lark', 'regex'],
        'import runpy; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name="__main__")',
        'scripts/cdn_plan_readiness.py', 'check-diff', '--base', 'a' * 40, '--head', 'b' * 40,
        env={**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH']})
    assert (result.returncode == 0) is declared, result.stdout + result.stderr
    if not declared:
        assert 'does not declare storage.managedFolders.delete' in result.stderr


def test_registry_reason_edit_preserves_semantic_resource_and_selection_contracts(source):
    from scripts.terraform_target_contracts import plan_scope
    path = source / '.github/terraform-targets.json'
    value = json.loads(path.read_text())
    before = plan_scope('catalog-viewer-deploy.yml', 'runtime', source)
    value['selection_exceptions']['scripts/deployment_revision.py']['reason'] += ' Reviewed wording correction.'
    path.write_text(json.dumps(value, indent=2))
    after = plan_scope('catalog-viewer-deploy.yml', 'runtime', source)
    assert before[0] == after[0]
    assert [item.pattern for item in before[1]] == [item.pattern for item in after[1]]
    assert ci.select_deployments(['scripts/deployment_revision.py'], root=source)
