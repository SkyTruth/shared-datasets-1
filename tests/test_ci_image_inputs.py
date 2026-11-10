"""Build inputs are installed bytes and probes, with the deployment image floor."""
from pathlib import Path
import shlex

import pytest

from scripts.ci_contract import resolve_selection, SUITES
from scripts import ci_contract
from scripts.tested_image_bundle import RECIPES

ROOT = Path(__file__).resolve().parents[1]


def test_historical_recipe_before_probe_registry_keeps_its_copy_inputs(tmp_path, monkeypatch):
    recipe = 'ingestion/mpatlas_monthly/Dockerfile'
    monkeypatch.setattr(ci_contract, 'RECIPES', {'mpatlas-monthly': recipe})
    monkeypatch.setattr(ci_contract, 'repository_files', lambda *_: {recipe: b'FROM python:3.12\nCOPY ingestion/mpatlas_monthly /app/job\n',
                                                                 'ingestion/mpatlas_monthly/run.py': b''})
    inputs = ci_contract.dependency_inputs('before-registry', tmp_path)[0]['mpatlas-monthly']
    assert ci_contract.matches('ingestion/mpatlas_monthly/run.py', inputs)


@pytest.mark.parametrize('path', ['uv.lock', 'pyproject.toml'])
def test_dependency_metadata_keeps_all_suites_and_only_recipes_that_copy_it(path):
    expected = {target for target, recipe in RECIPES.items()
                if any(path in shlex.split(line)[1:-1] for line in (ROOT / recipe).read_text().splitlines() if line.startswith('COPY '))}
    plan = resolve_selection([path])
    assert set(plan['production_image_targets']) == expected
    assert plan['suites'] == list(SUITES)


def test_installed_fixture_change_checks_only_its_registered_producers():
    assert resolve_selection(['scripts/installed_ingestion_fixture.py'])['production_image_targets'] == ['eamlis-monthly', 'mpatlas-monthly']


def test_optional_import_absent_from_image_does_not_become_a_build_input():
    # Sea ice/MPAtlas can import this host helper through optional code, but
    # neither Dockerfile installs it. Its real image consumers remain covered.
    assert set(resolve_selection(['scripts/catalog_csv.py'])['production_image_targets']) == {'dataset-usage', 'eamlis-monthly', 'wdpa-monthly'}


@pytest.mark.parametrize('target,recipe', RECIPES.items())
def test_every_recipe_and_copied_file_including_new_directory_members_selects_its_image(target, recipe):
    inputs = {recipe, '.dockerignore'}
    for line in (ROOT / recipe).read_text().splitlines():
        if line.startswith('COPY ') and '--from=' not in line:
            for name in shlex.split(line)[1:-1]:
                inputs.add(name + '/new-runtime.py' if (ROOT / name).is_dir() else name)
    for path in inputs:
        assert target in resolve_selection([path])['production_image_targets'], (target, path)
