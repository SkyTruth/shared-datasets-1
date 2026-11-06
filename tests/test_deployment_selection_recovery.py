"""Prospective selection and persisted rollout intent survive no-op and deferral."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from scripts import ci_contract as ci, ci_preflight as preflight, deployment_revision as deployment
from scripts import operational_safety_check as safety
from test_ci_dependency_selection import source  # noqa: F401 - shared real-source fixture
from test_deployment_revision import CURRENT, IMAGE, REPO
import test_deployment_revision as fixtures

ROOT = Path(__file__).resolve().parents[1]


def test_diverged_main_dependency_selects_its_consumer_in_prospective_plan(source, tmp_path, monkeypatch):  # noqa: F811
    # The caller never has main's new dependency. Selection must inspect the
    # merge tree, through the real planner CLI and serialized outputs.
    for filename in ci.CONTRACT_FILES:
        path = source / filename
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / filename).read_bytes())
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), '-c', 'core.hooksPath=/dev/null',
            '-c', 'commit.gpgsign=false', *args], text=True,
            env={**os.environ, 'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.test',
                 'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.test'}).strip()
    leaf = source / 'scripts/prospective_only.py'
    leaf.write_text('VALUE = 1\n')
    git('init', '-b', 'main')
    git('add', '.')
    git('commit', '-m', 'initial')
    original = git('rev-parse', 'HEAD')
    entry = source / 'ingestion/eamlis_monthly/run.py'
    entry.write_text(entry.read_text() + '\nfrom scripts import prospective_only\n')
    recipe = source / ci.RECIPES['eamlis-monthly']
    recipe.write_text(recipe.read_text() + '\nCOPY scripts/prospective_only.py /app/scripts/\n')
    workflow = source / '.github/workflows/catalog-viewer-deploy.yml'
    workflow.write_text(workflow.read_text() + '\n  probe:\n    runs-on: ubuntu-latest\n    steps:\n    - run: python scripts/prospective_only.py\n')
    git('add', '.')
    git('commit', '-m', 'main adds consumers')
    base = git('rev-parse', 'HEAD')
    git('checkout', '-b', 'feature', original)
    leaf.write_text('VALUE = 2\n')
    git('add', '.')
    git('commit', '-m', 'feature changes dependency')
    head = git('rev-parse', 'HEAD')
    merge, _, _ = preflight.isolated_merge(source, base, head, tmp_path / 'merge')
    output, github_output = tmp_path / 'plan.json', tmp_path / 'output'
    done = subprocess.run([sys.executable, str(ROOT / 'scripts/ci_preflight.py'), 'plan', '--base', base,
        '--head', 'HEAD', '--output', str(output), '--github-output', str(github_output)],
        cwd=merge, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    plan = json.loads(output.read_text())
    assert 'catalog_viewer' in plan['deployments']
    assert {'eamlis-monthly', 'catalog-viewer'} == set(plan['production_image_targets'])
    assert 'production-images' in plan['suites']
    assert 'catalog_viewer=true' in github_output.read_text()
    assert git('rev-parse', 'HEAD') == head
    assert not git('status', '--porcelain')


@pytest.mark.parametrize('path', ['main.tf', 'production.auto.tfvars'])
def test_shared_production_inputs_select_every_scoped_consumer_and_image(path):
    selected = ci.resolve_selection(['terraform/envs/prod/' + path])
    scoped = {name for name in ci.DEPLOYMENTS if name != 'catalog_web'}
    assert scoped <= set(selected['deployments'])
    assert set(ci.DEPLOYMENT_IMAGES.values()) <= set(selected['production_image_targets'])


@pytest.mark.parametrize('actions,phase', [([], 'failed'), (['no-op'], 'unknown'), (['read'], 'failed')])
def test_no_change_apply_retains_scope_through_failed_canary_and_reconciliation(tmp_path, monkeypatch, actions, phase):
    fixture = fixtures.DeploymentProvenanceTests()
    fixture.setUp()
    api = fixture.api
    address = 'module.wdpa_monthly_job.google_cloud_run_v2_job.this'
    plan = tmp_path / 'plan.json'
    plan.write_text(json.dumps({'variables': {'wdpa_monthly_image_config_digest': {'value': 'sha256:' + 'e' * 64}},
        'resource_changes': [{'address': address, 'change': {'actions': actions}}]}))
    args = SimpleNamespace(target='wdpa-monthly', workflow='wdpa-monthly-deploy.yml', executor_sha=CURRENT,
        source_run_id=13, source_run_attempt=2, artifact=IMAGE, plan_json=str(plan), plan_scope=None,
        deferred=False, command='reconcile', deployment_id=42, executor_dir=None)
    record = fixtures.ReplayTests.record(CURRENT, 42)
    monkeypatch.setenv('GITHUB_REPOSITORY', REPO)
    monkeypatch.setenv('GITHUB_RUN_ID', '13')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    monkeypatch.setenv('GITHUB_REF', 'refs/heads/main')
    monkeypatch.setenv('GITHUB_WORKFLOW_REF', f'{REPO}/.github/workflows/deployment-recovery.yml@refs/heads/main')
    monkeypatch.setattr(deployment, 'check', lambda *_: {'proceed': 'true'})
    monkeypatch.setattr(deployment.emissions, 'verify_rehearsal', Mock())
    saved = []
    def post(path, payload):
        assert path.endswith('/deployments')
        record.update(json.loads(json.dumps(payload)))
        return record
    api.post.side_effect = post
    monkeypatch.setattr(deployment, 'post_status', lambda _a, _r, _d, phase, *urls: saved.append(phase))
    assert deployment.start(args, api)['deployment_id'] == '42'
    assert record['payload']['targets'] == [address]
    # A failed/interrupted canary cannot erase the saved scope.
    args.phase, args.execution = phase, ''
    monkeypatch.setattr(deployment, 'verify_record_identity', lambda *_: record['payload'])
    api.get.side_effect = None
    api.get.return_value = json.loads(json.dumps(record))
    api.pages.return_value = [record]
    monkeypatch.setattr(deployment, 'verify_record', lambda *_: record['payload'])
    if phase == 'unknown':
        with pytest.raises(deployment.DeploymentError, match='terminal runtime evidence'):
            deployment.finish(args, api)
    else:
        deployment.finish(args, api)
    assert saved[-1] == phase
    monkeypatch.setattr(deployment, 'git_ancestor', lambda *_: True)
    responses = [CURRENT, json.dumps({'spec': {'template': {'spec': {'template': {'spec': {'containers': [{'image': IMAGE}]}}}}}}),
        json.dumps([{'metadata': {'name': 'wdpa-monthly-abcde'}, 'spec': {'template': {'spec': {'containers': [{'image': IMAGE}]}}},
                     'status': {'completionTime': 'now', 'succeededCount': 1}}])]
    monkeypatch.setattr(deployment.subprocess, 'check_output', Mock(side_effect=responses))
    deployment.recover(args, api)
    assert saved[-1] == 'verified'


@pytest.mark.parametrize('later_deploy', [False, True])
def test_signed_deferral_is_visible_after_72h_and_retry_can_deploy(tmp_path, monkeypatch, later_deploy):
    fixture = fixtures.DeploymentProvenanceTests()
    fixture.setUp()
    api = fixture.api
    record = fixtures.ReplayTests.record(CURRENT, 42)
    now = datetime(2026, 11, 5, tzinfo=timezone.utc)
    record['created_at'] = (now - timedelta(hours=73)).isoformat()
    args = SimpleNamespace(target='wdpa-monthly', workflow='wdpa-monthly-deploy.yml', executor_sha=CURRENT,
        source_run_id=13, source_run_attempt=2, artifact=IMAGE, plan_json=None, plan_scope=None, deferred=True)
    monkeypatch.setenv('GITHUB_REPOSITORY', REPO)
    monkeypatch.setenv('GITHUB_RUN_ID', '13')
    monkeypatch.setenv('GITHUB_RUN_ATTEMPT', '2')
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'outputs'))
    monkeypatch.setenv('RUNNER_TEMP', str(tmp_path))
    monkeypatch.setattr(deployment, 'check', lambda *_: {'proceed': 'true'})
    monkeypatch.setattr(deployment.emissions, 'verify_rehearsal', Mock())
    statuses = []
    def post(path, payload):
        if path.endswith('/deployments'):
            record.update(json.loads(json.dumps(payload)))
            return record
        status = {**payload, 'id': len(statuses) + 1}
        statuses.insert(0, status)
        return status
    api.post.side_effect = post
    assert deployment.start(args, api) == {'proceed': 'false'}
    receipt = json.loads(Path((tmp_path / 'outputs').read_text().strip().split('=', 1)[1]).read_text())
    assert receipt['status']['description'] == 'deferred'
    assert receipt['status']['state'] == 'pending'
    assert deployment.replay_decision([record], CURRENT, lambda *_: True, lambda _: statuses, artifact=IMAGE) == 'proceed'
    with pytest.raises(deployment.DeploymentError, match='same revision now produces different artifact'):
        deployment.replay_decision([record], CURRENT, lambda *_: True, lambda _: statuses, artifact=IMAGE.replace('d'*64, 'e'*64))
    api.post.reset_mock()
    monkeypatch.setattr(safety, 'ROOT', tmp_path)
    holds = tmp_path / safety.INGESTION_ROLLOUT_HOLDS
    holds.parent.mkdir()
    holds.write_text('{}')
    monkeypatch.setattr(safety, 'DEPLOY_TARGETS', {'wdpa-monthly': safety.TARGETS['wdpa-monthly']})
    api.pages.side_effect = lambda path, **_: [record] if '/deployments?' in path else statuses if '/statuses?' in path else [{'name': 'ci-ready', 'status': 'completed', 'conclusion': 'success'}]
    monkeypatch.setattr(deployment, 'verify_receipt', Mock())
    monkeypatch.setattr(safety.subprocess, 'check_output', Mock(return_value=IMAGE if later_deploy else IMAGE.replace('d'*64, 'e'*64)))
    findings = safety.deployed_image_lag(api, now=now)
    assert bool(findings) is not later_deploy
    api.post.assert_not_called()
    deployment.verify_receipt.assert_called_once()


def test_prepare_and_apply_record_and_sign_closed_runtime_windows():
    jobs = yaml.safe_load((ROOT / '.github/workflows/ingestion-deploy.yml').read_text())['jobs']
    for name in ('prepare', 'apply'):
        steps = jobs[name]['steps']
        window = next(i for i, step in enumerate(steps) if step.get('id') == 'window')
        deferred = next(i for i, step in enumerate(steps) if step.get('id') == 'deferred')
        assert window < deferred
        assert steps[deferred]['if'] == "steps.window.outputs.ready == 'false'"
        assert 'start --deferred' in steps[deferred]['run']
        assert any(step.get('with', {}).get('receipt-path') == '${{ steps.deferred.outputs.receipt_path }}' for step in steps[deferred + 1:])
    prepare = jobs['prepare']['steps']
    assert next(i for i, step in enumerate(prepare) if step.get('id') == 'image') < next(i for i, step in enumerate(prepare) if step.get('id') == 'window')


def test_policy_tests_reject_unpinned_terraform_before_console(tmp_path, monkeypatch):
    import terraform_helpers as helpers
    if hasattr(helpers, "terraform_executable"):
        helpers.terraform_executable.cache_clear()
    executable = tmp_path / "terraform"
    executable.write_text(f"#!{sys.executable}\nimport json; print(json.dumps({{'terraform_version':'1.2.6'}}))\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    try:
        with pytest.raises(RuntimeError, match="require Terraform 1.8.5.*reports 1.2.6"):
            helpers.evaluate({}, "{}")
    finally:
        if hasattr(helpers, "terraform_executable"):
            helpers.terraform_executable.cache_clear()


@pytest.mark.parametrize("deferred", [True, False])
def test_repeated_deferral_preserves_original_lag_clock_but_open_window_retries(monkeypatch, deferred):
    fixture = fixtures.DeploymentProvenanceTests()
    fixture.setUp()
    record = fixtures.ReplayTests.record(CURRENT)
    args = SimpleNamespace(target="wdpa-monthly", workflow="wdpa-monthly-deploy.yml", executor_sha=CURRENT,
        source_run_id=13, source_run_attempt=2, artifact=IMAGE, deferred=deferred)
    for name, value in {"GITHUB_REF":"refs/heads/main", "GITHUB_REPOSITORY":REPO,
                        "GITHUB_RUN_ID":"13", "GITHUB_RUN_ATTEMPT":"2"}.items():
        monkeypatch.setenv(name, value)
    fixture.api.pages.side_effect = lambda path, **_: [record] if "/deployments?" in path else [{"name":"ci-ready", "status":"completed", "conclusion":"success"}]
    monkeypatch.setattr(deployment.subprocess, "check_output", lambda *_a, **_k: CURRENT)
    monkeypatch.setattr(deployment, "git_ancestor", lambda *_: True)
    verified = Mock(return_value=record["payload"])
    monkeypatch.setattr(deployment, "verify_record", verified)
    monkeypatch.setattr(deployment, "verified_statuses", lambda *_: [{"state":"pending", "description":"deferred"}])
    assert deployment.check(args, fixture.api) == {"proceed": str(not deferred).lower()}
    verified.assert_called_once()
    fixture.api.post.assert_not_called()


def test_deferred_claim_cli_emits_receipt_without_site_packages(tmp_path):
    # Replace remote admission/signature services; execute real CLI parsing,
    # record construction, status creation and receipt/output serialization.
    program = '''
import json, os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts import deployment_revision as d
d.check = lambda *_: {"proceed":"true"}
d.emissions.verify_rehearsal = lambda *_: None
class API:
    def get(self, path):
        return {"id":13,"run_attempt":2}
    def post(self, path, payload):
        if path.endswith("/deployments"):
            Path(os.environ["RUNNER_TEMP"], "record.json").write_text(json.dumps(payload))
            return {**payload,"id":42,"sha":payload["ref"]}
        return {**payload,"id":43}
d.GitHub = API
sys.argv = ["deployment_revision.py", "start", "--deferred", "--target", "wdpa-monthly",
    "--workflow", "wdpa-monthly-deploy.yml", "--executor-sha", "b"*40,
    "--source-run-id", "13", "--source-run-attempt", "2", "--artifact", os.environ["IMAGE"]]
d.main()
'''
    output = tmp_path / 'outputs'
    result = subprocess.run([sys.executable, '-I', '-S', '-c', program, str(ROOT)],
        capture_output=True, text=True, env={**os.environ, 'GITHUB_REPOSITORY': REPO,
            'GITHUB_RUN_ID': '13', 'GITHUB_RUN_ATTEMPT': '2', 'RUNNER_TEMP': str(tmp_path),
            'GITHUB_OUTPUT': str(output), 'IMAGE': IMAGE})
    assert result.returncode == 0, result.stderr
    values = dict(line.split('=', 1) for line in output.read_text().splitlines())
    assert values['proceed'] == 'false' and 'deployment_id' not in values
    receipt = json.loads(Path(values['receipt_path']).read_text())
    assert receipt['payload'] == json.loads((tmp_path / 'record.json').read_text())['payload']
    assert receipt['status']['description'] == 'deferred' and receipt['status']['state'] == 'pending'


def test_pushed_images_cannot_bypass_the_new_signed_apply_claim():
    jobs = yaml.safe_load((ROOT / '.github/workflows/ingestion-deploy.yml').read_text())['jobs']
    apply = jobs['apply']
    assert apply['needs'] == 'prepare'
    assert apply['if'] == "needs.prepare.outputs.ready == 'true'"
    assert apply['env']['IMAGE'] == '${{ needs.prepare.outputs.image }}'
    steps = apply['steps']
    claim = next(i for i, step in enumerate(steps) if step.get('id') == 'revision')
    signed = next(i for i, step in enumerate(steps) if step.get('id') == 'claim-receipt')
    mutate = next(i for i, step in enumerate(steps) if step.get('name') == 'Terraform apply')
    assert claim < signed < mutate
    assert '--artifact "$IMAGE"' in steps[claim]['run']
    assert steps[signed]['with']['receipt-path'] == '${{ steps.revision.outputs.receipt_path }}'
    assert steps[mutate]['if'] == "steps.revision.outputs.proceed == 'true' && steps.claim-receipt.outcome == 'success'"
    assert '--deferred' not in steps[claim]['run']
