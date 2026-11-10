"""Current checkpoint writers must survive a plain revert to pinned old main."""
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from scripts import deployment_emission as emissions, deployment_revision as deployments
from scripts import slack_incidents as incidents
from scripts.terraform_target_contracts import registry

ROOT = Path(__file__).resolve().parents[1]
OLD_MAIN = '4aa19c71'
REPO = 'SkyTruth/shared-datasets-1'
SHA = 'a' * 40
IDENTITY = {'team': 'T123456789', 'user': 'U123456789', 'channel': 'C123456789'}


@pytest.fixture(scope='module')
def old_snapshot(tmp_path_factory):
    root = tmp_path_factory.mktemp('revert-readers')
    archive = subprocess.check_output(['git', 'archive', OLD_MAIN, 'scripts', '.github/workflows'], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        source.extractall(root, filter='data')
    return root


@contextmanager
def old_readers(snapshot):
    # Import the actual old scripts and their dependencies as an isolated package.
    # Restore all module identities afterwards; current writers keep their modules.
    saved = {key: value for key, value in sys.modules.items() if key == 'scripts' or key.startswith('scripts.')}
    original_path = sys.path[:]
    try:
        for key in saved:
            del sys.modules[key]
        sys.path.insert(0, str(snapshot))
        yield SimpleNamespace(**{name: importlib.import_module('scripts.' + name) for name in (
            'deployment_revision', 'deployment_emission', 'slack_incidents', 'operational_safety_check')})
    finally:
        for key in list(sys.modules):
            if key == 'scripts' or key.startswith('scripts.'):
                del sys.modules[key]
        sys.modules.update(saved)
        sys.path[:] = original_path


class CheckpointAPI:
    """Only API and attestation transport are fake; stored bytes stay immutable."""
    def __init__(self):
        self.records, self.statuses, self.signed = {}, {}, {}
        repository = {'id': 17, 'full_name': REPO}
        base = {'run_attempt': 1, 'workflow_id': 12, 'head_sha': SHA, 'head_branch': 'main',
                'repository': repository, 'head_repository': repository}
        self.runs = {13: {**base, 'id': 13, 'path': '.github/workflows/ci.yml', 'event': 'push'},
                     14: {**base, 'id': 14, 'path': incidents.WORKFLOW, 'event': 'workflow_dispatch'}}

    def get(self, path):
        if path == 'repos/' + REPO:
            return self.runs[13]['repository']
        if '/actions/workflows/' in path:
            return {'id': 12, 'path': '.github/workflows/' + path.rsplit('/', 1)[-1]}
        if '/actions/runs/' in path:
            return self.runs[int(path.split('/runs/')[1].split('/')[0])]
        return copy.deepcopy(self.records[int(path.rsplit('/', 1)[-1])])

    def pages(self, path, field=None):
        if path.endswith('/jobs?per_page=100'):
            return [{'name': 'ci-ready', 'status': 'completed', 'conclusion': 'success'}]
        if '/statuses?' in path:
            return copy.deepcopy(self.statuses[int(path.split('/deployments/')[1].split('/')[0])])
        environment = path.split('environment=')[1].split('&')[0]
        return [copy.deepcopy(row) for row in self.records.values() if row['environment'] == environment]

    def post(self, path, payload):
        if path.endswith('/deployments'):
            identifier = len(self.records) + 71
            row = {'id': identifier, 'sha': payload['ref'],
                   'creator': {'login': 'github-actions[bot]', 'type': 'Bot'}, **payload}
            self.records[identifier], self.statuses[identifier] = row, []
        else:
            identifier = int(path.split('/deployments/')[1].split('/')[0])
            row = {'id': 81 + sum(map(len, self.statuses.values())),
                   'created_at': '2026-11-10T12:00:00Z', **payload}
            self.statuses[identifier].insert(0, row)
        return copy.deepcopy(row)

    def capture(self, directory):
        for path in directory.rglob('record-*-status-*.json'):
            value = json.loads(path.read_bytes())
            run = self.runs[value['payload']['execution_run_id']]
            signer = incidents.WORKFLOW if value['environment'] == incidents.ENVIRONMENT else '.github/workflows/prod-terraform-target-apply.yml'
            self.signed[path.name] = [{'verificationResult': {
                'signature': {'certificate': emissions.policy(REPO, 17, value, run, signer)},
                'verifiedTimestamps': [{'type': 'tlog', 'timestamp': '2026-11-10T12:00:00Z'}],
                'statement': {'_type': 'https://in-toto.io/Statement/v1', 'predicateType': emissions.PREDICATE,
                              'subject': [{'name': path.name, 'digest': {'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}}]},
            }}]

    def attestation(self, command, repository, digest):
        assert repository == REPO
        return json.dumps(self.signed[Path(command[3]).name])


def write_checkpoint(tmp_path, monkeypatch, sync_name, item, resolved):
    api = CheckpointAPI()
    monkeypatch.setenv('GITHUB_REPOSITORY', REPO)
    monkeypatch.setenv('GITHUB_RUN_ID', '13')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '1')
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'output'))
    monkeypatch.setattr(deployments, 'verify_context', lambda *_: REPO)
    monkeypatch.setattr(emissions, 'verify_rehearsal', lambda *_: None)
    target = 'terraform-' + hashlib.sha256(sync_name.encode()).hexdigest()[:16]
    args = SimpleNamespace(target=target, workflow=item['workflow'], artifact='terraform@sha256:' + 'b' * 64,
                           executor_sha=SHA, source_run_id=13, source_run_attempt=1,
                           plan_json=None, plan_scope='\n'.join(item['resources']))
    started = deployments.start(args, api)
    record = api.records[int(started['deployment_id'])]
    deployments.post_status(api, REPO, record, 'failed')
    failure = {'scope': 'deployment:' + target, 'kind': 'deployment', 'label': sync_name,
               'run_id': 13, 'attempt': 1, 'sha': SHA, 'time': '2026-11-10T11:00:00Z',
               'job_id': 91, 'job_name': 'Apply ' + sync_name, 'conclusion': 'failure', 'proof': None}
    state = incidents.fold(None, failure, IDENTITY)
    if resolved:
        deployments.post_status(api, REPO, record, 'applied')
        healthy = incidents.terminal_observation(state['scope'], record, record['payload'], api.statuses[record['id']][0])
        state = incidents.fold(state, healthy, IDENTITY, ancestor=lambda *_: True)
        transport = Mock(wraps=subprocess)
        transport.check_output.return_value = 'gh version ' + incidents.verifier.VERSION + ' (fixture)\n'
        monkeypatch.setattr(incidents, 'subprocess', transport)
        # Use the actual current contract digest, not the old reader's cache.
        state['terminal_validation'] = {'evidence_sha256': incidents.terminal_evidence_sha256(record, api.statuses[record['id']]),
                                        'contract_sha256': incidents.terminal_contract_sha256()}
    for key in {'parent', *incidents.desired_posts(state)}:
        state['incident']['posts'][key] = {'state': 'delivered', 'ts': '1794312000.000001'}
    state['incident']['permalink'] = 'https://example.slack.com/archives/C123456789/p1794312000000001'
    state['incident']['parent_view_sha256'] = incidents.parent_view_sha256(state)
    checkpoint = incidents.Ledger(api, api.runs[14], tmp_path).save(state, 'outcomes')
    api.capture(tmp_path)
    # Cross the same JSON boundary as GitHub, including string-encoded payloads.
    for row in api.records.values():
        row['payload'] = json.dumps(row['payload'])
    return api, record, state, checkpoint


# Exercise every enabled Terraform scope, including changed existing scopes and
# manual ones. New-only held scopes are separately proven unable to emit below.
@pytest.mark.parametrize('sync_name,item', [(name, item) for name, item in registry().items()
                                          if not item.get('deployment_hold')])
@pytest.mark.parametrize('resolved', [False, True], ids=['retained-open', 'retained-resolved'])
def test_old_main_reconciles_current_checkpoints(old_snapshot, tmp_path, monkeypatch, sync_name, item, resolved):
    api, record, state, checkpoint = write_checkpoint(tmp_path, monkeypatch, sync_name, item, resolved)
    with old_readers(old_snapshot) as old:
        transport = Mock(wraps=subprocess)
        transport.check_output.side_effect = lambda command, **kw: (
            'gh version ' + old.slack_incidents.verifier.VERSION + ' (fixture)\n'
            if command == ['gh', '--version'] else subprocess.check_output(command, **kw))
        old.slack_incidents.subprocess = transport
        old.deployment_emission.verified_output = api.attestation
        ledger = old.slack_incidents.Ledger(api, api.runs[14], tmp_path)
        loaded = ledger.load()[state['scope']]
        assert bool(loaded['incident']['resolution']) == resolved
        old.slack_incidents.prepare(ledger, IDENTITY, [], ancestor=lambda *_: True)
        api.capture(tmp_path)
        payload = old.deployment_revision.verify_record(api, REPO, record)
        assert payload['targets'] == item['resources']
        if item['resources']:
            monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
            monkeypatch.setenv('GITHUB_WORKFLOW_REF', REPO + '/.github/workflows/deployment-recovery.yml@refs/heads/main')
            _, recovery = old.deployment_revision.recovery_record(SimpleNamespace(deployment_id=record['id']), api)
            assert recovery['targets'] == item['resources']
        if resolved:
            old.slack_incidents.verify_saved_success(api, loaded['incident']['resolution'])
        # The real old periodic safety dry-run reads the same retained ledger.
        settled = old.operational_safety_check.dry_reconciliation(
            api, api.runs[14], IDENTITY, [], datetime(2026, 11, 10, tzinfo=timezone.utc),
            datetime(2026, 11, 11, tzinfo=timezone.utc), tmp_path, None)
        assert settled is True
        assert old.slack_incidents.Ledger(api, api.runs[14], tmp_path).verify(checkpoint)


@pytest.mark.parametrize('defect', ['environment', 'resources', 'signer', 'receipt-digest'])
def test_old_readers_still_reject_incompatible_signed_evidence(old_snapshot, tmp_path, monkeypatch, defect):
    name = 'Artifact Registry writer binding sync'
    api, record, state, _ = write_checkpoint(tmp_path, monkeypatch, name, registry()[name], True)
    if defect == 'environment':
        record['environment'] = 'production-terraform-unregistered'
    elif defect == 'resources':
        payload = json.loads(record['payload'])
        payload['targets'].append('google_storage_bucket.unreviewed')
        record['payload'] = json.dumps(payload)
    else:
        for results in api.signed.values():
            verification = results[0]['verificationResult']
            if defect == 'signer':
                verification['signature']['certificate']['buildSignerURI'] = 'https://github.com/' + REPO + '/.github/workflows/untrusted.yml@refs/heads/main'
            else:
                verification['statement']['subject'][0]['digest']['sha256'] = '0' * 64
    with old_readers(old_snapshot) as old:
        old.deployment_emission.verified_output = api.attestation
        with pytest.raises(old.deployment_revision.DeploymentError):
            old.deployment_revision.verify_record(api, REPO, record)
        with pytest.raises(old.deployment_revision.DeploymentError):
            old.slack_incidents.verify_saved_success(api, state['incident']['resolution'])


@pytest.mark.parametrize('name', ['PR plan identity IAM bootstrap', 'PR plan identity provisioning'])
def test_contract_phase_targets_cannot_export_or_write_checkpoints(tmp_path, name):
    item = registry()[name]
    assert 'contract-phase PR' in item['deployment_hold']
    environment = tmp_path / 'github-env'
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/terraform_target_contracts.py'),
                             '--sync-name', name, '--workflow', item['workflow']],
                            env={'GITHUB_ENV': str(environment)}, capture_output=True, text=True)
    assert result.returncode == 1
    assert 'Deployment held' in result.stderr
    assert not environment.exists()
    api = Mock()
    args = SimpleNamespace(target='terraform-' + hashlib.sha256(name.encode()).hexdigest()[:16])
    for entrypoint in (deployments.check, deployments.start):
        with pytest.raises(deployments.DeploymentError, match='deployment held'):
            entrypoint(args, api)
    assert api.mock_calls == []


def test_every_new_only_target_is_held_and_legacy_identity_name_is_removed(old_snapshot):
    with old_readers(old_snapshot) as old:
        old_names = set(old.deployment_revision.TERRAFORM_SYNCS)
    targets = registry()
    assert 'GitHub workflow identity sync' not in targets
    assert set(targets) - old_names == {'PR plan identity IAM bootstrap', 'PR plan identity provisioning'}
    assert all(targets[name]['deployment_hold'] for name in set(targets) - old_names)


def test_identity_fold_preserves_original_scopes_and_readiness_guards():
    before = json.loads(subprocess.check_output(['git', 'show', '18f53a71:.github/terraform-targets.json'], cwd=ROOT))['targets']
    original = [before[name] for name in ('GitHub workflow identity sync', 'Artifact Registry writer binding sync')]
    combined = registry()['Artifact Registry writer binding sync']
    for key in ('resources', 'prerequisites'):
        assert set(combined[key]) == set().union(*(row[key] for row in original))
    from scripts.deployment_permissions import checks
    expected = set(check for row in original for check in checks(row['readiness_target']))
    assert set(checks(combined['readiness_target'])) == expected
    assert combined['block_deletes'] is True
    assert combined['post_apply_wait_seconds'] == 120
