"""Validator evidence follows actual dependencies without absorbing every CLI."""
import ast
from pathlib import Path
import sys

from scripts import ci_contract as ci
from scripts.production_image_contracts import HOST_PROBE_FILES


def project(tmp_path):
    sources = {name: b'' for name in ci.CONTRACT_REQUIRED}
    sources.update({
        'scripts/ci_preflight.py': b'from scripts import validator\n',
        'scripts/ci_contract.py': b'', 'scripts/ci_main_reuse.py': b'',
        'scripts/validator.py': b'from ingestion.common import helper\n',
        'ingestion/common/helper.py': b'VALUE = 1\n',
        'scripts/unrelated.py': b'VALUE = 1\n',
        '.github/workflows/ci.yml': b'jobs: {}\n',
    })
    for name, raw in sources.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    return tmp_path


def test_unrelated_cli_does_not_invalidate_validator_evidence(tmp_path):
    root = project(tmp_path)
    before = ci.contract_digest(root)
    (root / 'scripts/unrelated.py').write_text('VALUE = 2\n')
    assert ci.contract_digest(root) == before
    (root / 'ingestion/common/helper.py').write_text('VALUE = 2\n')
    assert ci.contract_digest(root) != before


def test_new_transitive_import_and_nested_action_invalidate_the_contract(tmp_path):
    root = project(tmp_path)
    before = ci.contract_digest(root)
    (root / 'scripts/validator.py').write_text('from scripts import extra\n')
    (root / 'scripts/extra.py').write_text('VALUE = 1\n')
    changed = ci.contract_digest(root)
    assert changed != before
    (root / 'scripts/extra.py').write_text('VALUE = 2\n')
    assert ci.contract_digest(root) != changed
    action = root / '.github/actions/nested/action.yml'
    action.parent.mkdir(parents=True)
    action.write_text('runs: {}\n')
    assert action.relative_to(root).as_posix() in ci.contract_files(root)


def test_installed_probe_sources_remain_in_the_validation_contract():
    assert set(HOST_PROBE_FILES.values()) <= set(ci.contract_files(ci.ROOT))


def test_release_model_has_one_way_standard_library_dependencies():
    path = Path(__file__).resolve().parents[1] / 'ingestion/common/release_feature_model.py'
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not node.level and node.module.split('.')[0] in sys.stdlib_module_names
        elif isinstance(node, ast.Import):
            assert all(alias.name.split('.')[0] in sys.stdlib_module_names for alias in node.names)
    assert not (path.parents[2] / 'scripts/release_feature_model.py').exists()
