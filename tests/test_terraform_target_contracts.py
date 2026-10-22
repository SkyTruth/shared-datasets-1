from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from unittest import mock

import pytest
import yaml

from scripts import ci_preflight
from scripts import terraform_target_contracts as contracts
from test_operational_log_filter import serialized_views


ROOT = Path(__file__).resolve().parents[1]
OWNER = 'google_project_iam_member.owner'
ROLE = 'google_project_iam_custom_role.owner'
SHARED = 'google_storage_bucket.shared'
RAW = 'google_storage_bucket.telemetry'


@pytest.fixture
def policy(tmp_path):
    directory = tmp_path / '.github/workflows'
    directory.mkdir(parents=True)
    workflow = {'jobs': {
        'bootstrap': {'uses': contracts.CALLER, 'with': {'targets': ROLE, 'allowed_exact': ROLE}},
        'sync': {'uses': contracts.CALLER, 'needs': 'bootstrap', 'with': {'targets': OWNER, 'allowed_exact': OWNER}},
    }}
    (directory / 'owned.yml').write_text(yaml.safe_dump(workflow))
    (tmp_path / 'terraform').mkdir()
    value = {'schema_version': 1, 'jobs': {'owned.yml/bootstrap': [], 'owned.yml/sync': [SHARED]}, 'retired_targets': {}}
    (tmp_path / 'terraform/iam-plan-prerequisites.json').write_text(json.dumps(value))
    return tmp_path, value, workflow


def write_policy(root, value):
    (root / 'terraform/iam-plan-prerequisites.json').write_text(json.dumps(value))


def test_transitive_managed_ancestor_must_have_an_explicit_owner(policy):
    root, _, _ = policy
    graph = {OWNER: {ROLE, SHARED}, ROLE: set(), SHARED: set()}
    evidence = contracts.check_graph(root, graph)
    assert evidence['owned.yml/sync']['managed_prerequisites'] == sorted([ROLE, SHARED])
    graph[SHARED] = {RAW}
    graph[RAW] = set()
    with pytest.raises(ValueError, match=OWNER + ' -> ' + SHARED + ' -> ' + RAW):
        contracts.check_graph(root, graph)


def test_shared_bad_dependency_reports_every_target_path(policy):
    root, _, workflow = policy
    other = 'google_storage_bucket_iam_member.mpatlas'
    for key in ('targets', 'allowed_exact'):
        workflow['jobs']['sync']['with'][key] += '\n' + other
    (root / '.github/workflows/owned.yml').write_text(yaml.safe_dump(workflow))
    graph = {OWNER: {ROLE, SHARED}, other: {SHARED}, ROLE: set(), SHARED: {RAW}, RAW: set()}
    with pytest.raises(ValueError) as failure:
        contracts.check_graph(root, graph)
    for target in (OWNER, other):
        assert target + ' -> ' + SHARED + ' -> ' + RAW in str(failure.value)


def test_readonly_data_node_cannot_hide_a_new_managed_dependency(policy):
    root, _, _ = policy
    data = 'data.google_storage_bucket.input'
    graph = {OWNER: {data}, data: {RAW}, RAW: set(), ROLE: set()}
    with pytest.raises(ValueError, match='undeclared managed prerequisite'):
        contracts.check_graph(root, graph)


def test_managed_resource_in_module_named_data_is_not_a_data_source(policy):
    root, _, _ = policy
    managed = 'module.data.google_storage_bucket.unowned'
    graph = {OWNER: {managed}, managed: set(), ROLE: set()}
    with pytest.raises(ValueError, match='undeclared managed prerequisite: ' + OWNER + ' -> ' + managed):
        contracts.check_graph(root, graph)
    assert not contracts.DATA.match(managed)
    assert contracts.DATA.match('module.data.data.google_storage_bucket.input')


def test_unknown_caller_removed_policy_and_scope_expansion_fail(policy):
    root, value, workflow = policy
    graph = {OWNER: {ROLE}, ROLE: set()}
    changed = copy.deepcopy(value)
    del changed['jobs']['owned.yml/bootstrap']
    write_policy(root, changed)
    with pytest.raises(ValueError, match='exactly every'):
        contracts.check_graph(root, graph)
    write_policy(root, value)
    workflow['jobs']['sync']['with']['allowed_patterns'] = 'google_storage_bucket.*'
    (root / '.github/workflows/owned.yml').write_text(yaml.safe_dump(workflow))
    with pytest.raises(ValueError, match='exact mutation allowlist'):
        contracts.check_graph(root, graph)


def test_only_explicit_retirement_permits_a_missing_target(policy):
    root, value, _ = policy
    graph = {ROLE: set()}
    with pytest.raises(ValueError, match='absent'):
        contracts.check_graph(root, graph)
    value['retired_targets']['owned.yml/sync'] = [OWNER]
    write_policy(root, value)
    assert contracts.check_graph(root, graph)
    graph[OWNER] = set()
    with pytest.raises(ValueError, match='retired target must remain absent'):
        contracts.check_graph(root, graph)


@pytest.mark.parametrize('retired', [[ROLE], [OWNER, OWNER], [OWNER + '[0]'], [None], 'not-a-list'])
def test_retirement_cannot_hide_wrong_scope_duplicates_or_invalid_identity(policy, retired):
    root, value, _ = policy
    value['retired_targets']['owned.yml/sync'] = retired
    write_policy(root, value)
    with pytest.raises(ValueError, match='retired target'):
        contracts.check_graph(root, {ROLE: set()})


def test_retirement_cannot_name_an_unknown_job(policy):
    root, value, _ = policy
    value['retired_targets']['missing.yml/sync'] = [OWNER]
    write_policy(root, value)
    with pytest.raises(ValueError, match='registered narrow caller'):
        contracts.check_graph(root, {ROLE: set()})


def test_yaml_narrow_callers_cannot_bypass_policy_enumeration(policy):
    root, _, workflow = policy
    (root / '.github/workflows/unregistered.yaml').write_text(yaml.safe_dump(workflow))
    assert 'unregistered.yaml/sync' in contracts.callers(root)
    with pytest.raises(ValueError, match='exactly every'):
        contracts.check_graph(root, {OWNER: {ROLE}, ROLE: set()})


def test_real_simplified_graph_parser_preserves_module_and_data_edges():
    dot = '''digraph G {
  rankdir = "RL";
  node [shape = rect, fontname = "sans-serif"];
  "data.google_project.current" [label="data.google_project.current"];
  subgraph "cluster_module.worker" {
    label = "module.worker"
    fontname = "sans-serif"
    "module.worker.google_service_account.this" [label="google_service_account.this"];
  }
  "module.worker.google_service_account.this" -> "data.google_project.current";
}
'''
    result = contracts.parse_graph(dot)
    assert result == {'data.google_project.current': set(), 'module.worker.google_service_account.this': {'data.google_project.current'}}
    assert contracts.address('module.worker[0].google_service_account.this["exact-key"]') == 'module.worker.google_service_account.this'
    for broken in ('', dot[:-2], dot.replace('data.google_project.current";', 'google_storage_bucket.missing";'), dot.replace('fontname = "sans-serif"\n', 'unknown = "unmodeled"\n')):
        with pytest.raises(ValueError):
            contracts.parse_graph(broken)


def test_isolated_graph_cannot_read_production_state_or_caller_credentials(tmp_path):
    with mock.patch.dict(contracts.os.environ, {'GOOGLE_APPLICATION_CREDENTIALS': 'must-not-pass', 'GH_TOKEN': 'must-not-pass', 'TF_CLI_ARGS': '-backend-config=production', 'TF_VAR_dataset_usage_image': 'must-not-pass'}, clear=False):
        environment = contracts.isolated_environment(tmp_path)
    assert not set(environment) & {'GOOGLE_APPLICATION_CREDENTIALS', 'GH_TOKEN', 'TF_CLI_ARGS', 'TF_VAR_dataset_usage_image'}
    assert environment['HOME'] == str(tmp_path / 'home')
    provider = tmp_path / 'initialized-providers'
    provider.mkdir()
    copy = tmp_path / 'owned-copy'
    copy.mkdir()
    prod = contracts.prepare(ROOT, copy, provider)
    assert json.loads((prod / 'ci_local_backend_override.tf.json').read_text()) == {'terraform': {'backend': {'local': {}}}}
    assert not list(copy.rglob('*.tfstate'))
    assert not (copy / '.github').exists()


def test_both_boundaries_run_the_actual_graph_gate_after_provider_initialization(tmp_path):
    commands = [argv for argv, _ in ci_preflight.suite_commands('lint', ROOT, {'base': 'a' * 40, 'tested_sha': 'b' * 40}, tmp_path)]
    check = next(command for command in commands if 'scripts/terraform_target_contracts.py' in command)
    initialized = next(command for command in commands if command[:3] == ['terraform', '-chdir=terraform/envs/prod', 'init'])
    assert commands.index(initialized) < commands.index(check)
    assert check == ['uv', 'run', '--no-sync', 'python', 'scripts/terraform_target_contracts.py', '--output', str(tmp_path / 'terraform-targets')]


def test_historical_negative_control_is_exact_failed_source_not_a_cached_graph():
    directory = ROOT / 'tests/fixtures/terraform_targets'
    metadata = json.loads((directory / 'failed-main.json').read_text())
    raw = (directory / metadata['fixture']).read_bytes()
    assert metadata['revision'] == 'ffe2dd53b9dd9c57e46c8a593234a39f966836e4'
    assert metadata['run_id'] == 37647645882
    assert hashlib.sha256(raw).hexdigest() == metadata['sha256']
    assert b'log_bucket        = google_storage_bucket.dataset_usage_raw.name' in raw
    # Graphs are produced from the active configuration and both failed-source
    # variants by the shared lint command, rather than replayed as a DOT fixture.
    assert not list(directory.glob('*.dot'))


def test_operational_view_filters_are_read_from_real_console_serialization(tmp_path):
    value = serialized_views()
    output = json.dumps(json.dumps(value)) + '\n'
    completed = mock.Mock(returncode=0, stdout=output, stderr='')
    environment = {'HOME': str(tmp_path / 'home'), 'TF_INPUT': '0'}
    with mock.patch.object(contracts.subprocess, 'run', return_value=completed) as run:
        proof = contracts.operational_views(tmp_path / 'prod', 'pinned-terraform', environment, tmp_path, ROOT)
    run.assert_called_once_with(['pinned-terraform', '-chdir=' + str(tmp_path / 'prod'), 'console', '-no-color'],
                                input='jsonencode(local.operational_observer_log_views)\n', env=environment,
                                text=True, stdout=contracts.subprocess.PIPE, stderr=contracts.subprocess.PIPE, timeout=120)
    assert len(proof['views']) == 8
    assert json.loads((tmp_path / 'operational-views.json').read_text()) == value
    source = (ROOT / 'scripts/terraform_target_contracts.py').read_text()
    assert "'operational_log_views': operational_views(prod, executable, environment, positive, root)" in source


def test_failed_console_cannot_supply_cached_or_partial_filter_evidence(tmp_path):
    completed = mock.Mock(returncode=1, stdout=json.dumps(json.dumps(serialized_views())), stderr='invalid Terraform expression')
    with mock.patch.object(contracts.subprocess, 'run', return_value=completed), pytest.raises(ValueError, match='serialization failed'):
        contracts.operational_views(tmp_path / 'prod', 'pinned-terraform', {}, tmp_path, ROOT)
    assert not (tmp_path / 'operational-views.json').exists()
