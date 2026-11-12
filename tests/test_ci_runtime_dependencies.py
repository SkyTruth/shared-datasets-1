"""Real locked exports own image inputs; development groups grant no delivery."""
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

from scripts import ci_contract, ci_preflight, runtime_dependencies as runtime

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    for name in ('pyproject.toml', 'uv.lock'):
        shutil.copyfile(ROOT / name, tmp_path / name)
    return tmp_path


def test_committed_exports_are_current_and_preserve_native_build_policy():
    generated = runtime.generate()
    assert all((ROOT / path).read_bytes() == raw for path, raw in generated.items())
    for profile in ('runtime', 'wdpa'):
        lock = tomllib.loads(generated[runtime.OUTPUT / f'pylock.{profile}.toml'].decode())
        assert lock['requires-python'] == tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['requires-python']
        assert all(package.get('sdist', {}).get('hashes') or package.get('wheels') for package in lock['packages'])
        names = {package['name'] for package in lock['packages']}
        assert not names & {'ruff', 'pytest', 'geopandas', 'rasterio', 'python-hcl2'}
        assert ('gdal' in names) is (profile == 'wdpa')
    native = tomllib.loads(generated[runtime.OUTPUT / 'pylock.wdpa.toml'].decode())
    packages = {row['name']: row['version'] for row in native['packages']}
    assert packages['gdal'] == '3.6.2' and packages['numpy'] == '1.26.4'
    config = tomllib.loads(generated[runtime.OUTPUT / 'uv.toml'].decode())
    assert config['pip']['extra-build-dependencies'] == {'gdal': ['numpy==1.26.4']}
    parser = tomllib.loads(generated[runtime.OUTPUT / 'pylock.ci-plan.toml'].decode())
    parser_names = {package['name'] for package in parser['packages']}
    assert {'python-hcl2', 'pyyaml'} <= parser_names
    assert not parser_names & {'google-cloud-storage', 'flatgeobuf', 'gdal', 'pytest'}


def test_native_build_configuration_is_projected_without_relocking(project):
    original = runtime.generate(project)
    source = project / 'pyproject.toml'
    source.write_text(source.read_text().replace('gdal = ["numpy==1.26.4"]', 'gdal = ["numpy==1.26.3"]'))
    updated = runtime.generate(project)
    assert all(original[runtime.OUTPUT / f'pylock.{name}.toml'] == updated[runtime.OUTPUT / f'pylock.{name}.toml']
               for name in runtime.PROFILES)
    assert original[runtime.OUTPUT / 'uv.toml'] != updated[runtime.OUTPUT / 'uv.toml']


def test_real_installer_refuses_incompatible_python(project):
    raw = runtime.generate(project)[runtime.OUTPUT / 'pylock.runtime.toml'].decode()
    source = project / 'pylock.test.toml'
    source.write_text(raw.replace('requires-python = ">=3.12"', 'requires-python = ">=3.99"', 1))
    result = subprocess.run(['uv', 'pip', 'sync', '--preview-features', 'pylock', '--dry-run', '--offline',
                             '--require-hashes', '--python', sys.executable, str(source)], capture_output=True, text=True)
    assert result.returncode != 0 and 'incompatible' in result.stderr


def test_stale_and_malformed_exports_cannot_pass_the_lint_boundary(project, monkeypatch, capsys):
    generated = runtime.generate(project)
    for path, raw in generated.items():
        destination = project / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
    monkeypatch.setattr(runtime, 'ROOT', project)
    assert runtime.main(['--check']) == 0
    (project / runtime.OUTPUT / 'pylock.runtime.toml').write_text('stale')
    assert runtime.main(['--check']) == 1
    assert 'Regenerate runtime inputs' in capsys.readouterr().err
    (project / 'uv.lock').write_text('not valid TOML [')
    assert runtime.main(['--check']) == 1
    plan = ci_contract.resolve_selection(['uv.lock'])
    commands = ci_preflight.suite_commands('lint', ROOT, {'base': 'HEAD', 'tested_sha': 'HEAD', 'changed_paths': ['uv.lock'], **plan}, project)
    assert any(command[-2:] == ['scripts/runtime_dependencies.py', '--check'] for command, _ in commands)


@pytest.mark.parametrize('addition', ['\n[build-system]\nrequires = ["setuptools"]\n', '\n[tool.uv]\npackage = true\n'])
def test_new_packaging_settings_require_an_explicit_runtime_projection(project, addition):
    source = project / 'pyproject.toml'
    source.write_text(source.read_text() + addition)
    with pytest.raises(ValueError, match='explicit support'):
        runtime.generate(project)


@pytest.mark.parametrize('profile,targets', [
    ('runtime', {'mpatlas', 'eamlis', 'sea_ice', 'cron_alert_policy'}),
    ('wdpa', {'wdpa'}),
    ('ci-plan', {'sea_ice'}),
])
def test_generated_file_changes_select_actual_image_or_executor_consumers(profile, targets):
    plan = ci_contract.resolve_selection([str(runtime.OUTPUT / f'pylock.{profile}.toml')])
    assert set(plan['readiness_targets'] if profile == 'ci-plan' else plan['deployments']) == targets
    if profile == 'ci-plan':
        assert plan['deployments'] == []
    assert ci_contract.bootstrap_deployments([str(runtime.OUTPUT / 'pylock.ci-plan.toml')]) == set()


def test_native_build_configuration_has_only_its_actual_consumer():
    plan = ci_contract.resolve_selection([str(runtime.OUTPUT / 'uv.toml')])
    assert plan['deployments'] == ['wdpa']
    assert plan['production_image_targets'] == ['wdpa-monthly']


def test_dev_only_change_reaches_the_actual_plan_without_delivery(tmp_path):
    from test_ci_preflight import fixture_repo, emit_plan
    root, git = fixture_repo(tmp_path)
    base = git('rev-parse', 'HEAD')
    original = runtime.generate(root)
    source = root / 'pyproject.toml'
    source.write_text(source.read_text().replace('[dependency-groups]', '[dependency-groups]\n# Development tooling documentation changed.'))
    assert runtime.generate(root) == original
    git('add', '.')
    git('commit', '-m', 'development-only dependency metadata')
    plan, _ = emit_plan(root, base, tmp_path / 'selection')
    assert plan['deployments'] == [] and ci_contract.deploy_image_targets(plan) == []
    assert plan['suites'] == [suite for suite in ci_contract.SUITES if suite != 'production-images']
    assert plan['production_image_targets'] == []
