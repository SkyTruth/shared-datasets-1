"""Round-eight producer boundaries, historical receipts and clean hosted imports."""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from scripts import ci_contract as ci, deployment_emission as emission, deployment_revision as revision
from scripts import terraform_plan_allowlist as allowlist

ROOT = Path(__file__).resolve().parents[1]
BASE_REGISTRY_SHA256 = 'd47a2e54ce917338d3509e41264a916e41798924effa709d43bf116a152c8791'


@pytest.fixture
def source(tmp_path):
    root = tmp_path / 'source'
    for name in ('.github', 'scripts', 'terraform', 'ingestion', 'services', 'catalog', 'api'):
        shutil.copytree(ROOT / name, root / name,
                        ignore=shutil.ignore_patterns('.terraform', '__pycache__', 'node_modules'))
    return root


def test_normalized_registry_preserves_every_preexisting_value():
    value = json.loads((ROOT / '.github/terraform-targets.json').read_text())
    identity = value['targets']['GitHub workflow identity sync']['resources']
    additions = {'google_storage_bucket_iam_member.github_readonly_object_viewer',
                 *(f'{kind}.github_job["cleanup"]' for kind in (
                     'google_service_account', 'google_project_iam_custom_role', 'google_project_iam_member')),
                 'google_service_account_iam_member.github_job_wif["cleanup"]'}
    assert additions <= set(identity)
    identity[:] = [row for row in identity if row not in additions]
    exceptions = value.pop('selection_exceptions')
    for path, row in exceptions.items():
        for target, reason in row.get('deployments', {}).items():
            value['deployments'][target].setdefault('input_exceptions', {})[path] = reason
    digest = hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    assert digest == BASE_REGISTRY_SHA256
    # Mapping and list structure is readable; only indivisible URL strings may
    # exceed 200 columns. No resource/probe/exception row is a giant JSON line.
    for line in (ROOT / '.github/terraform-targets.json').read_text().splitlines():
        if len(line) > 200:
            assert line.strip().startswith('"https://') and line.count('"') == 2


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


@pytest.mark.parametrize('file,job', [('ci.yml', 'dataset-usage-rollout'),
                                    ('dataset-usage-deploy.yml', 'deploy'), ('ingestion-deploy.yml', 'apply')])
def test_usage_apply_signal_follows_renamed_yaml_jobs(source, monkeypatch, file, job):
    path = source / '.github/workflows' / file
    document = yaml.safe_load(path.read_text())
    document['jobs'][job]['name'] = 'Renamed usage phase'
    path.write_text(yaml.safe_dump(document))
    monkeypatch.setattr(revision, 'ROOT', source)
    jobs = {file: yaml.safe_load((source / '.github/workflows' / file).read_text())['jobs']
            for file in ('ci.yml', 'dataset-usage-deploy.yml', 'ingestion-deploy.yml')}
    name = ' / '.join(jobs[file][job].get('name', job) for file, job in (
        ('ci.yml', 'dataset-usage-rollout'), ('dataset-usage-deploy.yml', 'deploy'), ('ingestion-deploy.yml', 'apply')))
    assert 'Renamed usage phase' in name
    api = Mock()
    api.pages.return_value = [{'name': name, 'status': 'completed', 'conclusion': 'success'}]
    monkeypatch.setattr(revision, 'verify_context', lambda *_: 'SkyTruth/shared-datasets-1')
    monkeypatch.setenv('GITHUB_RUN_ID', '13')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    revision.wait_applied(SimpleNamespace(target='dataset-usage'), api, attempts=1)
    assert revision.apply_job_path('dataset-usage') == name
    api.post.assert_not_called()


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
    sync = next(i for i, item in enumerate(steps) if item.get('run') == 'uv sync --locked --only-group ci-plan')
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


def test_hosted_usage_gate_imports_only_ci_plan_dependencies_and_resolves_real_yaml(tmp_path):
    ci_jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    step = next(s for s in ci_jobs['dataset-usage']['steps'] if 'wait-applied' in s.get('run', ''))
    assert 'uv run --frozen --only-group ci-plan python' in step['run']
    project = tomllib.loads((ROOT / 'pyproject.toml').read_text())
    assert any(item.startswith('PyYAML') for item in project['dependency-groups']['ci-plan'])
    sha, repository = 'a' * 40, 'SkyTruth/shared-datasets-1'
    run = {'id': 13, 'run_attempt': 2, 'head_sha': sha, 'head_branch': 'main', 'event': 'push',
           'path': '.github/workflows/ci.yml', 'workflow_id': 17,
           'repository': {'id': 1, 'full_name': repository}, 'head_repository': {'id': 1, 'full_name': repository}}
    routes = {f'repos/{repository}': run['repository'],
              f'repos/{repository}/actions/workflows/ci.yml': {'id': 17, 'path': '.github/workflows/ci.yml'},
              f'repos/{repository}/actions/runs/13': run, f'repos/{repository}/actions/runs/13/attempts/2': run,
              f'repos/{repository}/actions/runs/13/attempts/2/jobs?per_page=100': [{
                  'total_count': 2, 'jobs': [{'name': 'ci-ready', 'status': 'completed', 'conclusion': 'success'},
                      {'name': revision.apply_job_path('dataset-usage'), 'status': 'completed', 'conclusion': 'success'}]}]}
    binary = tmp_path / 'bin'
    binary.mkdir()
    gh = binary / 'gh'
    gh.write_text(f'#!{sys.executable}\nimport json,sys\nroutes={routes!r}\nassert sys.argv[1:4] == ["api", "--method", "GET"]\nprint(json.dumps(routes[sys.argv[-1]]))\n')
    git = binary / 'git'
    git.write_text(f'#!{sys.executable}\nimport sys\nargs=sys.argv[1:]\nif args == ["rev-parse", "HEAD"]: print({sha!r})\nelif args[:2] not in (["cat-file", "-e"], ["merge-base", "--is-ancestor"]): raise SystemExit(1)\n')
    gh.chmod(0o755)
    git.chmod(0o755)
    # Run the entire workflow CLI through serialization, provenance and job
    # selection; only external GitHub and Git reads are replaced.
    result = isolated_python(tmp_path, ['PyYAML'],
        'import runpy; sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name="__main__")',
        'scripts/deployment_revision.py', 'wait-applied', '--target', 'dataset-usage', '--workflow', 'dataset-usage-deploy.yml',
        '--executor-sha', sha, '--source-run-id', '13', '--source-run-attempt', '2',
        env={**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'], 'GITHUB_REPOSITORY': repository,
             'GITHUB_REF': 'refs/heads/main', 'GITHUB_RUN_ID': '13', 'GITHUB_RUN_ATTEMPT': '2'})
    assert result.returncode == 0, result.stdout + result.stderr
