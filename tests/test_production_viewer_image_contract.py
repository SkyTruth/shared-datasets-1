"""The actual default viewer entrypoint must start and serve its credential-free health route."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from scripts.production_image_contracts import INTERPRETER_PROBE, NATIVE_VERSION_PROBE, VIEWER_HEALTH_PROBE, cached_build, commands, main, resolve_image, verify_viewer_image

IMAGE_ID = 'sha256:' + 'a' * 64


@pytest.fixture
def native_tool_outputs(tmp_path, monkeypatch):
    outputs = {
        'ogr2ogr': 'GDAL 3.6.2, released 2023/01/02\n',
        'tippecanoe': 'tippecanoe v2.52.0\n',
        'pmtiles': 'pmtiles 1.30.1, commit none, built at unknown\n',
    }
    paths = {}
    for name in [*outputs, 'tippecanoe-decode']:
        path = tmp_path / name
        path.write_text('fixture native tool\n')
        paths[name] = path
    monkeypatch.setattr('shutil.which', lambda name: str(paths[name]) if name in paths else None)
    def run(args, **kwargs):
        assert kwargs['check'] is True
        assert kwargs['stderr'] == subprocess.STDOUT
        return subprocess.CompletedProcess(args, 0, stdout=outputs[args[0].split('/')[-1]])
    monkeypatch.setattr('subprocess.run', run)
    return outputs, paths


def test_native_probe_records_actual_resolved_paths_and_pinned_versions(native_tool_outputs, capsys):
    outputs, paths = native_tool_outputs
    exec(NATIVE_VERSION_PROBE)
    evidence = json.loads(capsys.readouterr().out)
    assert evidence['schema_version'] == 1
    assert {name: item['version'] for name, item in evidence['native_tools'].items() if 'version' in item} == {
        'ogr2ogr': '3.6.2', 'tippecanoe': '2.52.0', 'pmtiles': '1.30.1',
    }
    for name, item in evidence['native_tools'].items():
        assert item['path'] == str(paths[name].resolve())
        if name in outputs:
            assert item['output'] == outputs[name].strip()


@pytest.mark.parametrize('tool,output', [
    ('ogr2ogr', 'GDAL 3.5.3, released 2022/10/21'),
    ('tippecanoe', 'tippecanoe v2.51.0'),
    ('pmtiles', 'pmtiles dev, commit none, built at unknown'),
    ('pmtiles', 'pmtiles 1.29.0, commit none, built at unknown'),
    ('pmtiles', 'unrecognized version output'),
])
def test_wrong_native_versions_fail_before_fixture_execution(native_tool_outputs, tool, output):
    outputs, _ = native_tool_outputs
    outputs[tool] = output
    with pytest.raises(RuntimeError, match=f'{tool} must report pinned version'):
        exec(NATIVE_VERSION_PROBE)


def test_native_probe_requires_installed_decoder(native_tool_outputs):
    _, paths = native_tool_outputs
    del paths['tippecanoe-decode']
    with pytest.raises(RuntimeError, match='Missing native image tool: tippecanoe-decode'):
        exec(NATIVE_VERSION_PROBE)


def test_failed_native_version_command_remains_a_failure(native_tool_outputs, monkeypatch):
    def failed(args, **kwargs):
        raise subprocess.CalledProcessError(7, args)
    monkeypatch.setattr('subprocess.run', failed)
    with pytest.raises(subprocess.CalledProcessError) as raised:
        exec(NATIVE_VERSION_PROBE)
    assert raised.value.returncode == 7


@pytest.mark.parametrize('target', ['sea-ice-daily', 'eamlis-monthly', 'wdpa-monthly'])
def test_every_native_image_runs_the_strict_installed_version_probe(target):
    checks = commands(target, 'b' * 40, image_id=IMAGE_ID)
    assert sum(NATIVE_VERSION_PROBE in command for command in checks) == 1


def test_viewer_recipe_and_default_entrypoint_health_are_selected():
    selected = commands('catalog-viewer', 'a' * 40)
    assert selected[0][selected[0].index('-f') + 1] == 'services/catalog_viewer/Dockerfile'
    assert '--viewer-image' in selected[-1]
    assert '--network' in selected[1] and 'none' in selected[1]


@pytest.mark.parametrize('failed_command', ['run', 'exec'])
def test_missing_default_entrypoint_fails_and_cleans_up_only_created_container(monkeypatch, failed_command):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['docker', failed_command]:
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.run', run)
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.check_output', lambda *args, **kwargs: IMAGE_ID + '\n')
    with pytest.raises(subprocess.CalledProcessError):
        verify_viewer_image('test-viewer-image')
    assert calls[0][:2] == ['docker', 'run']
    assert '--network' in calls[0] and 'none' in calls[0]
    assert not any(value in calls[0] for value in ['--entrypoint', '-e', '-v', '--publish'])
    assert calls[0][-1] == IMAGE_ID
    name = calls[0][calls[0].index('--name') + 1]
    assert calls[-1] == ['docker', 'rm', '--force', name]
    assert ['docker', 'logs', name] in calls
    if failed_command == 'run':
        assert not any(command[:2] == ['docker', 'exec'] for command in calls)


@pytest.mark.parametrize('target', ['sea-ice-daily', 'eamlis-monthly', 'wdpa-monthly', 'catalog-viewer'])
def test_checks_use_one_immutable_image_without_fixture_bind_mounts(target):
    selected = commands(target, 'b' * 40, image_id=IMAGE_ID)
    tag = f'shared-datasets-preflight/{target}:' + 'b' * 40
    assert tag in selected[0]
    for command in selected[1:]:
        assert IMAGE_ID in command
        assert tag not in command
        assert not any(value in command for value in ['-v', '--volume', '--mount'])


@pytest.mark.parametrize('invalid', ['', 'mutable:tag', 'sha256:' + 'a' * 63, 'sha256:' + 'A' * 64])
def test_unresolved_image_stops_before_launch(monkeypatch, invalid):
    run = Mock()
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.run', run)
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.check_output', lambda *args, **kwargs: invalid)
    with pytest.raises(ValueError, match='immutable image ID'):
        verify_viewer_image('missing-image')
    run.assert_not_called()


def test_failed_image_lookup_is_not_hidden(monkeypatch):
    lookup = Mock(side_effect=subprocess.CalledProcessError(1, ['docker', 'image', 'inspect']))
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.check_output', lookup)
    with pytest.raises(subprocess.CalledProcessError):
        resolve_image('missing-image')
    assert lookup.call_args.args[0] == ['docker', 'image', 'inspect', '--format', '{{.Id}}', 'missing-image']


def test_main_resolves_built_bytes_before_any_installed_code_check(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
    def output(args, **kwargs):
        calls.append(args)
        return ('b' * 40) if args[0] == 'git' else IMAGE_ID + '\n'
    monkeypatch.setattr('sys.argv', ['production_image_contracts.py', '--target', 'sea-ice-daily'])
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.run', run)
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.check_output', output)
    main()
    build = next(i for i, args in enumerate(calls) if args[:2] == ['docker', 'build'])
    resolve = next(i for i, args in enumerate(calls) if args[:3] == ['docker', 'image', 'inspect'])
    checks = [i for i, args in enumerate(calls) if args[:2] == ['docker', 'run']]
    assert build < resolve < min(checks)
    assert all(IMAGE_ID in calls[i] for i in checks)


@pytest.mark.parametrize('cache', [None, 'true'])
@pytest.mark.parametrize('target', ['wdpa-monthly', 'catalog-viewer', 'monitoring-slack'])
def test_failed_build_cannot_run_or_retain_old_tagged_image(tmp_path, monkeypatch, cache, target):
    monkeypatch.delenv('SHARED_DATASETS_GITHUB_IMAGE_CACHE', raising=False)
    if cache is not None:
        monkeypatch.setenv('SHARED_DATASETS_GITHUB_IMAGE_CACHE', cache)
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['docker', 'build'] or args[:3] == ['docker', 'buildx', 'build']:
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr('sys.argv', ['production_image_contracts.py', '--target', target, '--output', str(tmp_path)])
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.run', run)
    lookup = Mock(return_value='b' * 40)
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.check_output', lookup)
    packed, loaded = Mock(), Mock()
    monkeypatch.setattr('scripts.production_image_contracts.pack_image', packed)
    monkeypatch.setattr('scripts.production_image_contracts.load_image', loaded)
    with pytest.raises(subprocess.CalledProcessError):
        main()
    lookup.assert_called_once_with(['git', 'rev-parse', 'HEAD'], text=True)
    assert not any(args[:2] == ['docker', 'run'] for args in calls)
    assert not any('--viewer-image' in args for args in calls)
    packed.assert_not_called()
    loaded.assert_not_called()
    assert not (tmp_path / 'images/manifest.json').exists()


@pytest.mark.parametrize('body,valid', [(b'ok', True), (b'wrong', False)])
def test_health_probe_requires_actual_expected_response(monkeypatch, body, valid):
    response = Mock(status=200)
    response.read.return_value = body
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr('urllib.request.urlopen', lambda *args, **kwargs: context)
    monkeypatch.setattr('time.sleep', lambda duration: None)
    if valid:
        exec(VIEWER_HEALTH_PROBE)
    else:
        with pytest.raises(AssertionError):
            exec(VIEWER_HEALTH_PROBE)


def test_gdal_actual_shebang_interpreter_dependency_failure_is_not_hidden(tmp_path, monkeypatch):
    script = tmp_path / 'gdal_calc.py'
    script.write_text('#!/actual/gdal/python\n')
    monkeypatch.setattr('shutil.which', lambda name: str(script))
    calls = []
    def missing_numpy(args, **kwargs):
        calls.append(args)
        raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr('subprocess.run', missing_numpy)
    with pytest.raises(subprocess.CalledProcessError):
        exec(INTERPRETER_PROBE)
    assert calls[0][0] == '/actual/gdal/python'
    assert 'import numpy; from osgeo import gdal_array' in calls[0][-1]


@pytest.mark.parametrize('target', ['wdpa-monthly', 'mpatlas-monthly', 'eamlis-monthly', 'sea-ice-daily', 'catalog-viewer', 'dataset-usage', 'monitoring-slack'])
@pytest.mark.parametrize('enabled', [False, True])
def test_build_cache_policy_preserves_actual_recipe_and_target_scopes(monkeypatch, target, enabled):
    monkeypatch.delenv('SHARED_DATASETS_GITHUB_IMAGE_CACHE', raising=False)
    original = commands(target, 'b' * 40)[0]
    if enabled:
        monkeypatch.setenv('SHARED_DATASETS_GITHUB_IMAGE_CACHE', 'true')
    actual = cached_build(original, target)
    if not enabled:
        assert actual == original
        return
    if target in {'catalog-viewer', 'monitoring-slack'}:
        assert actual == ['docker', 'buildx', 'build', '--load', *original[2:]]
        return
    cache = f'type=gha,version=2,scope=production-{target}'
    assert actual == ['docker', 'buildx', 'build', '--load', '--cache-from', cache,
                      '--cache-to', cache + ',mode=max,ignore-error=true', *original[2:]]


@pytest.mark.parametrize('targets', [
    ['wdpa-monthly'], ['catalog-viewer'], ['sea-ice-daily', 'catalog-viewer'],
])
def test_selected_builds_emit_exact_retained_manifest(tmp_path, monkeypatch, targets):
    calls = []
    monkeypatch.setattr('sys.argv', ['production_image_contracts.py', '--output', str(tmp_path),
                                   *(value for target in targets for value in ('--target', target))])
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.run',
                        lambda args, **kwargs: calls.append(args))
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.check_output',
                        lambda args, **kwargs: 'b' * 40 if args[0] == 'git' else '[]' if '{{json .Config.Env}}' in args else IMAGE_ID + '\n')
    packed = Mock(side_effect=lambda target, *args: {'archive': target + '.docker.tar'})
    loaded = Mock()
    monkeypatch.setattr('scripts.production_image_contracts.pack_image', packed)
    monkeypatch.setattr('scripts.production_image_contracts.load_image', loaded)
    main()
    retained = targets
    manifest = json.loads((tmp_path / 'images/manifest.json').read_text())
    assert manifest == {'schema_version': 1, 'tested_sha': 'b' * 40,
                        'images': {target: {'archive': target + '.docker.tar'} for target in retained}}
    builds = [args for args in calls if args[:2] == ['docker', 'build']]
    assert len(builds) == len(targets)
    assert [args[-2] for args in builds] == [f'shared-datasets-preflight/{target}:' + 'b' * 40 for target in targets]
    assert [call.args[0] for call in packed.call_args_list] == retained
    assert loaded.call_count == len(retained)
    for args in calls:
        if args[:2] == ['docker', 'run'] or '--viewer-image' in args:
            assert IMAGE_ID in args


def test_failed_selected_installed_probe_cannot_emit_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr('sys.argv', ['production_image_contracts.py', '--target', 'wdpa-monthly',
                                   '--output', str(tmp_path)])
    def run(args, **kwargs):
        if args[:2] == ['docker', 'run']:
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.run', run)
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.check_output',
                        lambda args, **kwargs: 'b' * 40 if args[0] == 'git' else IMAGE_ID + '\n')
    with pytest.raises(subprocess.CalledProcessError):
        main()
    assert not (tmp_path / 'images/manifest.json').exists()


@pytest.mark.parametrize('environment', [['PATH=/bin'], ['GITHUB_SHA=abc'], ['SHARED_DATASETS_EXECUTOR_SHA=abc'], ['OTHER=' + 'b' * 40], {}, [1]])
def test_tree_bound_image_checks_actual_immutable_environment(monkeypatch, environment):
    from scripts.production_image_contracts import verify_tree_bound_image
    output = Mock(return_value=json.dumps(environment))
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.check_output', output)
    if environment == ['PATH=/bin']:
        verify_tree_bound_image(IMAGE_ID, 'b' * 40)
    else:
        with pytest.raises(ValueError, match='revision-dependent'):
            verify_tree_bound_image(IMAGE_ID, 'b' * 40)
    output.assert_called_once_with(['docker', 'image', 'inspect', '--format', '{{json .Config.Env}}', IMAGE_ID], text=True)


def test_tree_bound_recipes_have_no_revision_or_git_context_inputs():
    from pathlib import Path
    from scripts.production_image_contracts import REUSABLE_IMAGE_TARGETS
    assert REUSABLE_IMAGE_TARGETS == {'mpatlas-monthly', 'eamlis-monthly', 'catalog-viewer', 'dataset-usage'}
    root = Path(__file__).resolve().parents[1]
    for target in REUSABLE_IMAGE_TARGETS:
        folder = 'services' if target in {'catalog-viewer', 'monitoring-slack'} else 'ingestion'
        recipe = (root / folder / target.replace('-', '_') / 'Dockerfile').read_text()
        assert 'SHARED_DATASETS_EXECUTOR_SHA' not in recipe and 'GITHUB_SHA' not in recipe and '.git' not in recipe
        for line in recipe.splitlines():
            if line.startswith(('COPY ', 'ADD ')) and '--from=' not in line:
                assert not any(source in {'.', './', '/', '*'} for source in line.split()[1:-1])
