from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from unittest import mock

import pytest
import yaml

from scripts import ci_preflight
from scripts import terraform_target_contracts as contracts


ROOT = Path(__file__).resolve().parents[1]
OWNER = 'google_project_iam_member.owner'
ROLE = 'google_project_iam_custom_role.owner'
SHARED = 'google_storage_bucket.shared'
RAW = 'google_storage_bucket.telemetry'


@pytest.mark.parametrize('filename,scope,item', [
    (filename, scope, item) for filename, scopes in contracts.registry(section='plan_scopes').items()
    for scope, item in scopes.items()])
def test_registered_scopes_pass_real_saved_plan_guard_and_foreign_resources_fail(tmp_path, filename, scope, item):
    resources = set(item.get('exact', []) + item.get('instances', []))
    resources.update(prefix + '.' + child for prefix, child in item.get('modules', {}).items())
    for resource in sorted(resources | {'google_storage_bucket.foreign'}):
        address = resource + '["fixture"]' if resource.endswith('catalog_viewer_accessors') else resource
        plan = {'resource_changes': [{'address': address, 'change': {'actions': ['update']}}]}
        path = tmp_path / 'saved-plan.json'
        path.write_text(json.dumps(plan))
        result = subprocess.run([sys.executable, 'scripts/terraform_plan_allowlist.py', str(path),
            '--registered-scope', filename, scope, '--refusal-prefix', 'Refusing fixture'], cwd=ROOT, capture_output=True)
        assert result.returncode == (0 if resource in resources else 1), (resource, result.stderr)


def write_workflow(root, workflow, filename='owned.yml'):
    document = copy.deepcopy(workflow)
    path = root / '.github/terraform-targets.json'
    registry = json.loads(path.read_text()) if path.exists() else {'schema_version': 1, 'targets': {}}
    for name, job in document['jobs'].items():
        inputs = job['with']
        sync = filename + '/' + name
        resources = inputs['targets'].splitlines()
        item = {'workflow': filename, 'readiness_target': 'iam-bootstrap', 'resources': resources}
        if not resources and not inputs['allowed_exact']:
            item['state_only'] = True
        if inputs['targets'] != inputs['allowed_exact']:
            item['invalid_allowed_exact'] = inputs['allowed_exact']
        for key in ('allowed_patterns', 'prepare_monitoring_slack'):
            if inputs.get(key):
                item[key] = inputs[key]
        registry['targets'][sync] = item
        job['with'] = {'sync_name': sync, 'caller_workflow': filename}
    if path.exists():
        for sync, row in registry['targets'].items():
            row.update({key: value for key, value in json.loads(path.read_text())['targets'].get(sync, {}).items() if key in {'prerequisites', 'retired_resources'}})
    path.write_text(json.dumps(registry))
    (root / '.github/workflows' / filename).write_text(yaml.safe_dump(document))


@pytest.fixture
def policy(tmp_path):
    directory = tmp_path / '.github/workflows'
    directory.mkdir(parents=True)
    workflow = {'jobs': {
        'bootstrap': {'uses': contracts.CALLER, 'with': {'targets': ROLE, 'allowed_exact': ROLE}},
        'sync': {'uses': contracts.CALLER, 'needs': 'bootstrap', 'with': {'targets': OWNER, 'allowed_exact': OWNER}},
    }}
    write_workflow(tmp_path, workflow)
    (tmp_path / 'terraform').mkdir()
    value = {'schema_version': 1, 'jobs': {'owned.yml/bootstrap': [], 'owned.yml/sync': [SHARED]}, 'retired_targets': {}}
    write_policy(tmp_path, value)
    return tmp_path, value, workflow


def write_policy(root, value):
    path = root / '.github/terraform-targets.json'
    document = json.loads(path.read_text())
    for sync, row in document['targets'].items():
        row.pop('prerequisites', None)
        row.pop('retired_resources', None)
    for sync, prerequisites in value['jobs'].items():
        # Tests intentionally include orphan policy entries to prove exact coverage.
        document['targets'].setdefault(sync, {'workflow': sync.split('/')[0], 'resources': []})['prerequisites'] = prerequisites
    for sync, retired in value['retired_targets'].items():
        document['targets'].setdefault(sync, {'workflow': sync.split('/')[0], 'resources': []})['retired_resources'] = retired
    path.write_text(json.dumps(document))


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
    write_workflow(root, workflow)
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
    write_workflow(root, workflow)
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
    write_workflow(root, workflow, 'unregistered.yaml')
    assert 'unregistered.yaml/sync' in contracts.callers(root)
    with pytest.raises(ValueError, match='exactly every'):
        contracts.check_graph(root, {OWNER: {ROLE}, ROLE: set()})


def state_only_job(policy):
    root, value, workflow = policy
    workflow['jobs']['state'] = {'uses': contracts.CALLER, 'with': {'targets': '', 'allowed_exact': ''}}
    value['jobs']['owned.yml/state'] = []
    write_policy(root, value)
    return root, value, workflow


def test_paired_empty_scope_has_no_managed_mutation_dependencies(policy):
    root, _, workflow = state_only_job(policy)
    write_workflow(root, workflow)
    graph = {OWNER: {ROLE, SHARED}, ROLE: set(), SHARED: set()}
    assert contracts.check_graph(root, graph)['owned.yml/state'] == {
        'targets': [], 'managed_prerequisites': [], 'state_only': True,
    }


@pytest.mark.parametrize('change', [
    {'allowed_patterns': '.*'}, {'prepare_monitoring_slack': True},
    {'targets': OWNER}, {'allowed_exact': OWNER},
])
def test_state_only_cannot_acquire_resource_scope_or_prepare_external_mutations(policy, change):
    root, _, workflow = state_only_job(policy)
    workflow['jobs']['state']['with'].update(change)
    write_workflow(root, workflow)
    with pytest.raises(ValueError, match='state-only|nonempty'):
        contracts.check_graph(root, {OWNER: {ROLE, SHARED}, ROLE: set(), SHARED: set()})


@pytest.mark.parametrize('kind', ['prerequisite', 'retirement', 'bootstrap'])
def test_state_only_has_no_hidden_ownership_or_bootstrap_edges(policy, kind):
    root, value, workflow = state_only_job(policy)
    if kind == 'prerequisite':
        value['jobs']['owned.yml/state'] = [OWNER]
    elif kind == 'retirement':
        value['retired_targets']['owned.yml/state'] = [OWNER]
    else:
        workflow['jobs']['state']['needs'] = 'bootstrap'
    write_policy(root, value)
    write_workflow(root, workflow)
    with pytest.raises(ValueError, match='state-only'):
        contracts.check_graph(root, {OWNER: {ROLE, SHARED}, ROLE: set(), SHARED: set()})


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


def test_narrow_sync_has_exactly_one_caller_and_cannot_share_implicit_policy(policy):
    root, _, _ = policy
    path = root / '.github/terraform-targets.json'
    document = json.loads(path.read_text())
    del document['targets']['owned.yml/sync']
    path.write_text(json.dumps(document))
    path = root / '.github/workflows/owned.yml'
    workflow = yaml.safe_load(path.read_text())
    workflow['jobs']['sync']['with']['sync_name'] = 'owned.yml/bootstrap'
    path.write_text(yaml.safe_dump(workflow))
    with pytest.raises(ValueError, match='exactly every'):
        contracts.check_graph(root, {ROLE: set()})


def test_registry_schema_version_rejects_boolean(tmp_path):
    (tmp_path / '.github').mkdir()
    (tmp_path / '.github/terraform-targets.json').write_text('{"schema_version":true,"targets":{}}')
    with pytest.raises(ValueError, match='Unsupported'):
        contracts.registry(tmp_path)
