"""Local reuse and cancellation never become hosted release evidence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from threading import Event, Thread
from time import monotonic
from unittest import mock

import pytest

from scripts import ci_preflight as p, production_image_contracts as producer
from scripts.ci_contract import ALWAYS, SUITES, resolve_selection, verify_results
from scripts.tested_image_bundle import ImageError, validate_local_manifest, validate_manifest
from test_ci_preflight import ROOT, fixture_repo, results_for, plan
from test_ci_coordination import child


def local_results(validation):
    results = results_for(validation)
    for result in results:
        if result['suite'] == 'production-images':
            manifest = result['production_images']
            manifest['transport'] = 'local-daemon'
            manifest['images'] = {target: dict(image_id=image['config_digest'], source_tag=image['source_tag'], platform=image['platform'])
                                  for target, image in manifest['images'].items()}
    return results


def save_evidence(validation, *, execution=None, since=None, reused=None):
    evidence = {**validation, 'status': 'success', 'scope': 'local', 'execution_plan': execution or validation,
                'results': local_results(execution or validation), 'since': since, 'reused_suites': reused or {}}
    path = p.local_evidence_path(validation['base'], validation['head'])
    p.write_json(path, evidence)
    return path, evidence


def test_delta_requires_prior_coverage_and_preserves_receipt_identities(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'LOCAL_COORDINATION_DIRECTORY', tmp_path / 'coordination')
    source, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    (source / 'unclassified.bin').write_bytes(b'new component')
    test = source / 'tests/test_fixup.py'
    test.parent.mkdir(exist_ok=True)
    test.write_text('def test_fixup(): assert True\n')
    git('add', '.')
    git('commit', '-m', 'full selection')
    prior = p.make_plan(source, base, 'HEAD')
    assert prior['suites'] == list(SUITES)
    path, evidence = save_evidence(prior)
    original = path.read_bytes()
    for index in range(2):
        test.write_text(test.read_text() + f'# fixup {index}\n')
        git('add', '.')
        git('commit', '-m', 'fixup')
        current = p.make_plan(source, base, 'HEAD')
        execution, reused = p.local_execution_plan(source, current, prior['head'])
        assert set(execution['suites']) == ALWAYS
        assert execution['delta_paths'] == [str(test.relative_to(source))]
        assert set(reused) == set(SUITES) - ALWAYS
        assert execution['production_image_targets'] == []
        assert all(record['head'] == prior['head'] for record in reused.values())
        assert path.read_bytes() == original
        assert evidence['results'][0]['head'] != current['head']
        _, evidence = save_evidence(current, execution=execution, since=prior['head'], reused=reused)
        _, coverage, images = p.local_coverage(base, current['head'], current['contract_digest'])
        assert coverage == set(SUITES) and images == set(prior['production_image_targets'])
        prior = current


@pytest.mark.parametrize('defect', ['missing', 'failure', 'base', 'contract', 'failed-command', 'missing-suite', 'missing-image', 'cycle'])
def test_incomplete_or_incompatible_local_evidence_refuses_reuse(tmp_path, monkeypatch, defect):
    monkeypatch.setattr(p, 'LOCAL_COORDINATION_DIRECTORY', tmp_path)
    validation = plan()
    path, evidence = save_evidence(validation)
    if defect == 'missing':
        path.unlink()
    else:
        if defect == 'failure':
            evidence['status'] = 'failure'
        if defect in {'base', 'contract'}:
            evidence[{'base': 'base', 'contract': 'contract_digest'}[defect]] = 'f' * 40
        if defect == 'failed-command':
            evidence['results'][0]['commands'][0]['exit_code'] = 1
        if defect == 'missing-suite':
            evidence['results'].pop()
        if defect == 'missing-image':
            next(result for result in evidence['results'] if result['suite'] == 'production-images')['production_images']['images'].clear()
        if defect == 'cycle':
            evidence['since'] = validation['head']
        p.write_json(path, evidence)
    with pytest.raises((ValueError, OSError)):
        p.local_coverage(validation['base'], validation['head'], validation['contract_digest'])


def test_since_cannot_overwrite_its_own_prior_evidence_or_use_a_nonancestor(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'LOCAL_COORDINATION_DIRECTORY', tmp_path / 'coordination')
    source, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    (source / 'one.txt').write_text('one')
    git('add', '.')
    git('commit', '-m', 'first')
    prior = p.make_plan(source, base, 'HEAD')
    path, _ = save_evidence(prior)
    original = path.read_bytes()
    with pytest.raises(ValueError, match='earlier validated head'):
        p.local_execution_plan(source, prior, prior['head'])
    git('checkout', '--detach', base)
    (source / 'two.txt').write_text('two')
    git('add', '.')
    git('commit', '-m', 'divergent')
    current = p.make_plan(source, base, 'HEAD')
    with pytest.raises(subprocess.CalledProcessError):
        p.local_execution_plan(source, current, prior['head'])
    assert path.read_bytes() == original


@pytest.mark.parametrize('corrupt', [False, True])
def test_changed_contract_runs_full_selection_without_reusing_prior_receipts(tmp_path, monkeypatch, corrupt):
    monkeypatch.setattr(p, 'LOCAL_COORDINATION_DIRECTORY', tmp_path / 'coordination')
    source, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    (source / 'component.bin').write_bytes(b'new')
    git('add', '.')
    git('commit', '-m', 'initial full selection')
    prior = p.make_plan(source, base, 'HEAD')
    path, evidence = save_evidence(prior)
    if corrupt:
        evidence['results'][0]['commands'][0]['exit_code'] = 1
        p.write_json(path, evidence)
    script = source / 'scripts/ci_preflight.py'
    script.write_text(script.read_text() + '\n# changed validator\n')
    git('add', '.')
    git('commit', '-m', 'change validation contract')
    current = p.make_plan(source, base, 'HEAD')
    assert current['contract_digest'] != prior['contract_digest']
    if corrupt:
        with pytest.raises(ValueError, match='failed command'):
            p.local_execution_plan(source, current, prior['head'])
    else:
        execution, reused = p.local_execution_plan(source, current, prior['head'])
        assert execution == current
        assert reused == {}
        assert execution['suites'] == list(SUITES)


def test_local_manifest_never_passes_hosted_verification():
    validation = plan()
    results = local_results(validation)
    verify_results(validation, results, local=True)
    with pytest.raises(ValueError, match='invalid production-image evidence'):
        verify_results(validation, results)
    with pytest.raises(ValueError, match='cannot authorize hosted'):
        verify_results(validation, results, local=True, jobs={})
    manifest = next(row['production_images'] for row in results if row['suite'] == 'production-images')
    with pytest.raises(ImageError):
        validate_manifest(manifest, validation['tested_sha'])


@pytest.mark.parametrize('defect', ['id', 'tag', 'platform', 'tar-fields'])
def test_local_manifest_rejects_corrupted_or_export_receipts(defect):
    validation = plan()
    manifest = next(row['production_images'] for row in local_results(validation) if row['suite'] == 'production-images')
    image = next(iter(manifest['images'].values()))
    image[{'id': 'image_id', 'tag': 'source_tag', 'platform': 'platform', 'tar-fields': 'archive'}[defect]] = 'invalid'
    with pytest.raises(ImageError):
        validate_local_manifest(manifest, validation['tested_sha'])


@pytest.mark.parametrize('target', ['catalog-viewer', 'sea-ice-daily'])
def test_local_producer_runs_amd64_probes_but_never_exports_tar(tmp_path, monkeypatch, target):
    image_id, sha = 'sha256:' + 'a' * 64, 'b' * 40
    tag = f'shared-datasets-preflight/{target}:{sha}'
    monkeypatch.delenv('GITHUB_RUN_ID', raising=False)
    monkeypatch.setattr(sys, 'argv', ['images', '--local-images', '--target', target, '--output', str(tmp_path)])
    monkeypatch.setattr(producer.subprocess, 'check_output', lambda args, **kw: sha if args[0] == 'git' else image_id)
    monkeypatch.setattr(producer, 'inspect_image', lambda _: {'Id': image_id, 'RepoTags': [tag]})
    with mock.patch.object(producer.subprocess, 'run') as run, mock.patch.object(producer, 'pack_image') as pack, mock.patch.object(producer, 'load_image') as load:
        producer.main()
    pack.assert_not_called()
    load.assert_not_called()
    assert not list(tmp_path.rglob('*.tar'))
    manifest = json.loads((tmp_path / 'images/manifest.json').read_text())
    assert validate_local_manifest(manifest, sha) == {target: dict(image_id=image_id, source_tag=tag, platform='linux/amd64')}
    for call in run.call_args_list:
        command = call.args[0]
        if command[:2] == ['docker', 'run']:
            assert command[command.index('--platform') + 1] == 'linux/amd64'
            assert '--init' in command and '--label' in command and image_id in command


def test_actions_cannot_request_local_image_receipts(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_RUN_ID', '1')
    monkeypatch.setattr(sys, 'argv', ['images', '--local-images', '--output', str(tmp_path)])
    with pytest.raises(SystemExit):
        producer.main()


@pytest.mark.parametrize('path,extra', [
    ('tests/fixtures/README.md', set()), ('tests/fixtures/sample.geojson', set()),
    ('tests/fixture.config', set()), ('tests/browser/README.md', {'browser', 'sdk-node22', 'sdk-node24'}),
    ('tests/fixtures/production_images/eamlis-monthly.json', {'production-images'}),
])
def test_test_data_follows_consumers_with_a_native_floor(path, extra):
    selection = resolve_selection([path])
    assert set(selection['suites']) == ALWAYS | {'geospatial-integration'} | extra
    assert selection['selection_reason'] == 'tiers: component'
    if 'production-images' not in extra:
        assert selection['production_image_targets'] == []


def test_chromium_is_baked_with_the_same_browser_lock():
    dockerfile = (ROOT / '.github/docker/preflight.Dockerfile').read_text()
    assert 'ENV PLAYWRIGHT_BROWSERS_PATH="/opt/ms-playwright"' in dockerfile
    assert 'COPY tests/browser/package.json tests/browser/package-lock.json /opt/ci-browser/' in dockerfile
    assert 'npx --no-install playwright install chromium' in dockerfile


def test_origin_only_base_is_fetched_and_merge_identity_is_reproducible(tmp_path):
    source, git = fixture_repo(tmp_path)
    initial = git('rev-parse', 'HEAD')
    (source / 'base-only.txt').write_text('base\n')
    git('add', '.')
    git('commit', '-m', 'base')
    base = git('rev-parse', 'HEAD')
    git('update-ref', 'refs/remotes/origin/main', base)
    git('checkout', '-b', 'feature', initial)
    git('branch', '-D', 'main')
    (source / 'head-only.txt').write_text('head\n')
    git('add', '.')
    git('commit', '-m', 'head')
    head = git('rev-parse', 'HEAD')
    before = (source / '.git/index').read_bytes()
    first, *_ = p.isolated_merge(source, base, head, tmp_path / 'first')
    second, *_ = p.isolated_merge(source, base, head, tmp_path / 'second')
    assert p.git(first, 'rev-parse', 'HEAD') == p.git(second, 'rev-parse', 'HEAD')
    assert (first / 'base-only.txt').read_text() == 'base\n'
    assert (first / 'head-only.txt').read_text() == 'head\n'
    assert (source / '.git/index').read_bytes() == before
    assert git('rev-parse', 'refs/remotes/origin/main') == base


def test_wait_uses_the_kernel_slot_and_reports_elapsed_seconds(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'LOCAL_COORDINATION_DIRECTORY', tmp_path)
    program = "with p.local_preflight_slot(Path('.'), 'base', 'waiting-head', wait=True) as seconds:\n    print('acquired ' + str(seconds), flush=True)\n"
    with p.local_preflight_slot(ROOT, 'base', 'owning-head'):
        process = child(tmp_path, program, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdout.readline().startswith('Waiting for preflight slot')
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    assert 'Preflight slot wait:' in stdout and 'acquired ' in stdout
    assert float(stdout.split('acquired ')[1]) >= 0


@pytest.mark.parametrize('termination', [signal.SIGINT, signal.SIGTERM])
def test_termination_exits_through_driver_finally_even_when_background_shell_ignored_sigint(tmp_path, termination):
    program = """import signal, sys
signal.signal(signal.SIGINT, signal.SIG_IGN)
def run(args, **kwargs):
    print('running', flush=True)
    signal.pause()
p.preflight = run
sys.argv = ['ci_preflight.py', '--base', 'base', '--head', 'head', '--wait']
raise SystemExit(p.main())
"""
    with child(tmp_path, program, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        assert process.stdout.readline().startswith('Preflight slot wait:')
        assert process.stdout.readline().strip() == 'running'
        process.send_signal(termination)
        _, stderr = process.communicate(timeout=10)
    assert process.returncode == 130 and 'cancelled' in stderr
    record, = [json.loads(line) for line in (tmp_path / 'preflight-runs.jsonl').read_text().splitlines()]
    assert record['outcome'] == 'cancelled' and record['end'] >= record['start']
    assert {'head', 'base', 'slot_wait_s', 'per_suite_s'} <= set(record)


def test_host_driver_receives_sigint_and_finishes_before_return(tmp_path):
    ready, stopped = tmp_path / 'ready', tmp_path / 'stopped'
    command = [sys.executable, '-c', f"""import signal, time
from pathlib import Path
def stop(*_):
    Path({str(stopped)!r}).write_text('SIGINT')
    raise SystemExit(130)
signal.signal(signal.SIGINT, stop)
Path({str(ready)!r}).touch()
time.sleep(30)
"""]
    cancelled = Event()
    def cancel():
        for _ in range(500):
            if ready.exists():
                cancelled.set()
                return
            Event().wait(0.01)
    thread = Thread(target=cancel)
    thread.start()
    try:
        result = p.run_host_suite(command, cwd=tmp_path, env=dict(os.environ), cancelled=cancelled)
    finally:
        thread.join(timeout=10)
    assert result.returncode == 130 and stopped.read_text() == 'SIGINT'


def test_cleanup_selects_only_owned_containers_and_expired_preflight_tags(monkeypatch):
    now = datetime.now(timezone.utc)
    old, fresh, foreign = 'shared-datasets-preflight/component:old', 'shared-datasets-native:new', 'foreign:old'
    images = [{'Created': (now-timedelta(hours=25)).isoformat(), 'RepoTags': [old, foreign]},
              {'Created': now.isoformat(), 'RepoTags': [fresh]}]
    with mock.patch.object(p.subprocess, 'check_output', side_effect=['container-a\ncontainer-b', f'{old}\n{fresh}\n{foreign}\nshared-datasets-preflight:<none>\n', json.dumps(images)]) as query, mock.patch.object(p.subprocess, 'run') as run:
        p.remove_containers('this-run')
        p.cleanup_old_images()
    assert query.call_args_list[0].args[0] == ['docker', 'ps', '-aq', '--filter', 'label=shared-datasets-preflight-driver=this-run']
    assert query.call_args_list[2].args[0] == ['docker', 'image', 'inspect', fresh, old]
    assert [call.args[0] for call in run.call_args_list] == [['docker', 'rm', '-f', 'container-a', 'container-b'], ['docker', 'image', 'rm', old]]


@pytest.mark.parametrize('run_id', [None, 'this-run'])
def test_cleanup_preserves_live_standalone_probe_and_removes_only_driver_containers(monkeypatch, run_id):
    monkeypatch.delenv('SHARED_DATASETS_PREFLIGHT_RUN', raising=False)
    standalone = producer.container_options()[-1]
    monkeypatch.setenv('SHARED_DATASETS_PREFLIGHT_RUN', 'this-run')
    owned = producer.container_options()[-1]
    assert standalone.startswith('shared-datasets-preflight=standalone-')
    assert owned == 'shared-datasets-preflight-driver=this-run'
    labels = {'live-standalone': standalone, 'abandoned-driver': owned}

    def query(command, **kwargs):
        selected = command[-1].removeprefix('label=')
        return '\n'.join(name for name, label in labels.items() if label == selected or label.split('=')[0] == selected)

    with mock.patch.object(p.subprocess, 'check_output', side_effect=query), mock.patch.object(p.subprocess, 'run') as remove:
        p.remove_containers(run_id)
    assert remove.call_args.args[0] == ['docker', 'rm', '-f', 'abandoned-driver']


@pytest.mark.parametrize('workers', [2, 3])
def test_failed_suite_stops_host_and_queued_work_immediately(tmp_path, monkeypatch, workers):
    from contextlib import nullcontext
    validation = plan()
    root = tmp_path / 'repo'
    root.mkdir()
    monkeypatch.setenv('SHARED_DATASETS_WORKDIR', str(tmp_path))
    monkeypatch.setattr(p, 'LOCAL_COORDINATION_DIRECTORY', tmp_path / 'coordination')
    monkeypatch.setattr(p, 'isolated_merge', lambda *a: (root, validation['base'], validation['head']))
    monkeypatch.setattr(p, 'make_plan', lambda *a, **k: validation)
    monkeypatch.setattr(p, 'local_runtime', lambda: dict(server_architecture='amd64', platform='linux/amd64', suite_workers=workers))
    monkeypatch.setattr(p, 'isolated_host_environment', lambda *a: nullcontext({}))
    monkeypatch.setattr(p.shutil, 'which', lambda _: '/tools/docker')
    monkeypatch.setattr(p.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0))
    monkeypatch.setattr(p, 'cleanup_old_images', lambda: None)
    removals, started = [], []
    monkeypatch.setattr(p, 'remove_containers', lambda run=None: removals.append(run))
    host = Event()
    def run_host(command, *, cancelled, **kwargs):
        host.set()
        output = Path(command[command.index('--output')+1])
        (output / 'failed.docker.tar').write_bytes(b'failed run disposable TAR')
        assert cancelled.wait(5), 'failed suite did not interrupt the active host suite'
        return subprocess.CompletedProcess(command, 130)
    monkeypatch.setattr(p, 'run_host_suite', run_host)
    def container(root, work, suite, *a, **k):
        started.append(suite)
        if suite == 'lint':
            assert host.wait(5)
            raise ValueError('lint failed first')
        assert suite == 'browser', 'queued work started after the first failure'
        assert k['cancelled'].wait(5)
        raise ValueError('browser interrupted after lint failure')
    monkeypatch.setattr(p, 'run_container', container)
    monkeypatch.setattr(sys, 'argv', ['ci_preflight.py', '--base', validation['base'], '--head', validation['head']])
    before = monotonic()
    assert p.main() == 1
    assert monotonic()-before < 5
    assert set(started) == ({'browser', 'lint'} if workers == 3 else {'lint'})
    assert any(removals) and not list(tmp_path.rglob('*.docker.tar'))
    record = json.loads((tmp_path / 'coordination/preflight-runs.jsonl').read_text())
    assert record['outcome'] == 'failure' and 'production-images' in record['per_suite_s']
