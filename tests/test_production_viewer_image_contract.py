"""The actual default viewer entrypoint must start and serve its credential-free health route."""
import subprocess
from unittest.mock import Mock

import pytest

from scripts.production_image_contracts import INTERPRETER_PROBE, VIEWER_HEALTH_PROBE, commands, verify_viewer_image


def test_viewer_recipe_and_default_entrypoint_health_are_selected():
    selected = commands('catalog-viewer', 'a' * 40)
    assert selected[0][selected[0].index('-f') + 1] == 'services/catalog_viewer/Dockerfile'
    assert '--viewer-image' in selected[-1]
    assert '--network' in selected[1] and 'none' in selected[1]


def test_missing_default_entrypoint_fails_and_cleans_up_only_created_container(monkeypatch):
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ['docker', 'exec']:
            raise subprocess.CalledProcessError(1, args)
    monkeypatch.setattr('scripts.production_image_contracts.subprocess.run', run)
    with pytest.raises(subprocess.CalledProcessError):
        verify_viewer_image('test-viewer-image')
    assert calls[0][:2] == ['docker', 'run']
    assert '--network' in calls[0] and 'none' in calls[0]
    assert not any(value in calls[0] for value in ['--entrypoint', '-e', '-v', '--publish'])
    name = calls[0][calls[0].index('--name') + 1]
    assert calls[-1] == ['docker', 'rm', '--force', name]
    assert ['docker', 'logs', name] in calls


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
