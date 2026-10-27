"""The temporary timing diagnostic cannot grant production authority."""
from pathlib import Path

from workflow_helpers import load_workflow


ROOT = Path(__file__).resolve().parents[1]


def test_cache_diagnostic_is_narrow_pr_only_without_production_permissions():
    workflow = load_workflow(ROOT / '.github/workflows/ci.yml')
    selector = workflow['jobs']['geospatial-changes']['outputs']['small_image_cache_benchmark']
    assert "github.event_name == 'pull_request'" in selector
    assert 'github.event.pull_request.head.repo.full_name == github.repository' in selector
    assert 'scripts/production_image_contracts.py' in selector
    assert 'scripts/benchmark_small_image_cache.py' in selector
    job = workflow['jobs']['small-image-cache-benchmark']
    assert job['permissions'] == {'contents': 'read'}
    assert 'environment' not in job and 'secrets' not in job
    assert job['needs'] == 'geospatial-changes'
    assert job['if'] == "needs.geospatial-changes.outputs.small_image_cache_benchmark == 'true'"
    assert not job.get('continue-on-error')
    assert 'small-image-cache-benchmark' not in workflow['jobs']['ci-ready']['needs']
    assert not any('google-github-actions/' in step.get('uses', '') for step in job['steps'])
    checkout = next(step['with'] for step in job['steps'] if step.get('uses') == 'actions/checkout@v4')
    assert checkout == {'fetch-depth': 0, 'persist-credentials': False}
    buildx = next(step['with'] for step in job['steps'] if step.get('uses') == 'docker/setup-buildx-action@v3')
    assert buildx == {'version': 'v0.38.1', 'driver-opts': 'image=moby/buildkit:v0.33.1'}
    run = next(step for step in job['steps'] if 'benchmark_small_image_cache.py' in step.get('run', ''))
    assert run['env'] == {'BASE_SHA': '${{ github.event.pull_request.base.sha }}'}
    assert '--base "$BASE_SHA"' in run['run']
    assert not any('docker push' in step.get('run', '') or 'terraform' in step.get('run', '') for step in job['steps'])


def test_cache_diagnostic_artifacts_cannot_replace_required_image_or_gate_evidence():
    workflow = load_workflow(ROOT / '.github/workflows/ci.yml')
    job = workflow['jobs']['small-image-cache-benchmark']
    uploads = [step for step in job['steps'] if step.get('uses') == 'actions/upload-artifact@v4']
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload['if'] == 'always()'
    assert upload['with']['name'] == 'small-image-cache-benchmark-attempt${{ github.run_attempt }}'
    assert upload['with']['if-no-files-found'] == 'error'
    assert upload['with']['retention-days'] == 14
    assert '!${{ runner.temp }}/small-image-cache-benchmark/**/*.docker.tar' in upload['with']['path']
    assert not upload.get('continue-on-error')
