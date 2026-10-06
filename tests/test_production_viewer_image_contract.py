"""The actual default viewer entrypoint must start and serve its credential-free health route."""
import subprocess
from unittest.mock import Mock

import pytest

from scripts.production_image_contracts import INTERPRETER_PROBE, VIEWER_HEALTH_PROBE, commands, main, resolve_image, verify_viewer_image

IMAGE_ID = 'sha256:' + 'a' * 64


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


def test_failed_build_cannot_run_old_tagged_image(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['docker', 'build']:
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr('sys.argv', ['production_image_contracts.py', '--target', 'wdpa-monthly'])
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.run', run)
    lookup = Mock(return_value='b' * 40)
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.check_output', lookup)
    with pytest.raises(subprocess.CalledProcessError):
        main()
    assert lookup.call_count == 1
    assert not any(args[:2] == ['docker', 'run'] for args in calls)


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
