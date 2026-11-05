"""Native rollout barriers, shared workflow declarations and clean CDN execution."""
from pathlib import Path
import shlex
import subprocess
import sys

import pytest
import yaml

from scripts import production_image_contracts as images
from scripts.terraform_target_contracts import resource_index, registry

ROOT = Path(__file__).resolve().parents[1]


def test_usage_has_one_native_queue_owning_caller_and_no_polling_cli():
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    assert 'dataset-usage-rollout' not in jobs
    assert jobs['dataset-usage']['uses'] == './.github/workflows/dataset-usage-deploy.yml'
    assert all('dataset-usage' in jobs[name]['needs'] for name in ('ingestion-iam', 'scratch-cleanup-iam'))
    result = subprocess.run([sys.executable, '-I', '-S', 'scripts/deployment_revision.py', '--help'],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'wait-applied' not in result.stdout


@pytest.mark.parametrize('filename', ['catalog-viewer-deploy.yml', 'pmtiles-cdn-sync.yml'])
def test_every_specialized_phase_reuses_the_same_trusted_bootstrap(filename):
    jobs = yaml.safe_load((ROOT / '.github/workflows' / filename).read_text())['jobs']
    for step_name in ('Check out trusted deployment verifier', 'Authorize tested executor and retain trusted receipt policy'):
        steps = [next(step for step in job['steps'] if step.get('name') == step_name) for job in jobs.values()]
        assert len(steps) >= 2
        assert all(step is steps[0] for step in steps)


def test_deploy_callers_share_exact_source_mapping_and_keep_main_guards():
    jobs = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text())['jobs']
    mappings = []
    for target, row in registry(section='deployments').items():
        job, = [job for job in jobs.values() if job.get('uses') == './.github/workflows/' + row['workflow']]
        assert "github.event_name == 'push'" in job['if']
        assert "github.ref == 'refs/heads/main'" in job['if']
        if 'sync_iam' not in job['with']:
            mappings.append(job['with'])
    assert all(mapping is mappings[0] for mapping in mappings)


@pytest.mark.parametrize('target,kind,body', [
    ('sea-ice-daily', 'module "sea_ice_daily_job"', 'cpu = "2"\n memory = "512Mi"'),
    ('catalog-viewer', 'resource "google_cloud_run_v2_service" "catalog_viewer"',
     'template { containers { resources { limits = { cpu = "2", memory = "512Mi" } } } }'),
])
def test_image_limits_read_real_hcl_and_ignore_comment_decoys(tmp_path, monkeypatch, target, kind, body):
    path = tmp_path / f'terraform/envs/prod/{target.replace("-", "_")}.tf'
    path.parent.mkdir(parents=True)
    path.write_text(f'# {kind} {{ cpu = "999" }}\n  {kind} {{\n {body}\n  }}\n')
    monkeypatch.setattr(images, 'ROOT', tmp_path)
    try:
        assert images.resource_limits(target) == ['--cpu-period', '100000', '--cpu-quota', '200000', '--memory', '512m']
    finally:
        resource_index.cache_clear()


@pytest.mark.parametrize('body', ['cpu = var.cpu\n memory = "512Mi"', 'cpu = "2"\n memory = var.memory'])
def test_image_limits_refuse_nonliteral_values(tmp_path, monkeypatch, body):
    path = tmp_path / 'terraform/envs/prod/sea_ice_daily.tf'
    path.parent.mkdir(parents=True)
    path.write_text('module "sea_ice_daily_job" {\n' + body + '\n}\n')
    monkeypatch.setattr(images, 'ROOT', tmp_path)
    try:
        with pytest.raises(ValueError, match='literal'):
            images.resource_limits('sea-ice-daily')
    finally:
        resource_index.cache_clear()


@pytest.mark.parametrize('case', ['latest', 'historical', 'role', 'retry', 'content-type', 'redirect', 'http-error', 'no-sidecar', 'foreign'])
def test_hosted_cdn_verifier_runs_with_only_stdlib_and_checks_all_routes(case):
    workflow = yaml.safe_load((ROOT / '.github/workflows/pmtiles-cdn-sync.yml').read_text())
    step = next(s for s in workflow['jobs']['verify']['steps'] if s.get('name') == 'Verify catalog CDN routes')
    command = shlex.split(step['run'])
    assert command == ['python', '-I', '-S', '.github/actions/deployment-bootstrap/verify_cdn.py']
    # Only HTTP transport and time are replaced. The exact entrypoint parses the
    # serialized release index, chooses the sidecar and validates HTTP responses.
    boot = r'''
import io, json, runpy, sys, time, urllib.error, urllib.request
from unittest.mock import Mock
case, script = sys.argv[1:]
base = 'https://tiles.skytruth.org'
path = '100-test/asset/releases/2026-11-05/test.metadata.ndjson.gz'
entry = {'path': ('gs://foreign/' if case == 'foreign' else 'gs://skytruth-shared-datasets-1/') + path,
         ('role' if case == 'role' else 'format'): 'METADATA'}
index = {'latest_release': {}, 'releases': [{'files': [entry]}]} if case == 'historical' else {'latest_release': {'files': [entry]}}
if case == 'no-sidecar': index = {'releases': []}
urllib.request.urlopen = Mock(return_value=io.BytesIO(json.dumps(index).encode()))
urls, sleeps = [], []
class Response(io.BytesIO):
    status = 200
    def __init__(self, headers): super().__init__(b''); self.headers = headers
def open_url(url, timeout):
    assert timeout == 30
    urls.append(url)
    if case == 'http-error' or case == 'retry' and len(urls) == 1:
        raise urllib.error.HTTPError(url, 503, 'unavailable', {}, None)
    content = 'text/csv' if url.endswith('.csv') else 'application/json' if url.endswith('.json') else 'text/html'
    return Response({'Content-Type': 'wrong/type' if case == 'content-type' else content,
                     **({'Location': 'https://foreign/'} if case == 'redirect' else {})})
def opener(*handlers):
    assert handlers[0].redirect_request(None, None, 302, '', {}, 'https://foreign/') is None
    return Mock(open=open_url)
urllib.request.build_opener = opener
time.sleep = sleeps.append
accepted = case in {'latest', 'historical', 'role', 'retry'}
try:
    runpy.run_path(script, run_name='__main__')
except (OSError, ValueError):
    assert not accepted
else:
    assert accepted
    assert urls[-1] == base + '/artifacts/' + path
    assert set(urls[:-1]) == {base + '/_catalog/' + p for p in ('shared-datasets-catalog.csv', 'web/catalog.json', 'web/index.html')}
if case in {'content-type', 'redirect', 'http-error'}: assert len(urls) == 12 and sleeps == [10] * 11
elif case == 'retry': assert sleeps == [10] and len(urls) == 5
else: assert not sleeps
'''
    result = subprocess.run([sys.executable, *command[1:3], '-c', boot, case, command[3]],
                            cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_composite_actions_contain_no_inline_python_programs():
    for path in (ROOT / '.github/actions').rglob('action.yml'):
        document = yaml.safe_load(path.read_text())
        for step in document['runs']['steps']:
            run = step.get('run', '')
            assert not ('python' in run and ('<<' in run or ' -c ' in run)), path


def test_every_receipt_caller_installs_the_trusted_verifier_once_before_emission():
    from scripts.deployment_receipt_contracts import expanded_steps, ACTION
    action = yaml.safe_load((ROOT / '.github/actions/deployment-receipt/action.yml').read_text())
    assert not any('scripts/install_deployment_verifier.py' in step.get('run', '')
                   for step in action['runs']['steps'])
    for path in (ROOT / '.github/workflows').glob('*.yml'):
        for job in yaml.safe_load(path.read_text())['jobs'].values():
            steps = list(expanded_steps(ROOT, job))
            emissions = [i for i, step in enumerate(steps) if step.get('uses') == ACTION
                         or 'scripts/deployment_emission.py' in step.get('run', '')]
            if emissions:
                installs = [i for i, step in enumerate(steps)
                            if step.get('run') == 'python scripts/install_deployment_verifier.py']
                assert len(installs) == 1 and installs[0] < min(emissions), path


@pytest.mark.parametrize('deleted', [False, True])
def test_hosted_cdn_scope_guard_runs_with_only_stdlib(tmp_path, deleted):
    workflow = yaml.safe_load((ROOT / '.github/workflows/pmtiles-cdn-sync.yml').read_text())
    step = next(s for s in workflow['jobs']['sync']['steps'] if s.get('name') == 'Enforce PMTiles resource-change allowlist')
    code = step['run'].split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
    plan = tmp_path / 'plan.json'
    import json
    plan.write_text(json.dumps({'resource_changes': [{'address': 'google_storage_bucket.shared_bucket',
                              'change': {'actions': ['delete' if deleted else 'update']}}]}))
    boot = 'import sys; sys.path.insert(0, ' + repr(str(ROOT)) + '); sys.argv = ["-", sys.argv[1]]; exec(' + repr(code) + ')'
    result = subprocess.run([sys.executable, '-I', '-S', '-c', boot, str(plan)],
                            cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == int(deleted), result.stdout + result.stderr
