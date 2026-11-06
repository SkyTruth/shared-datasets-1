from __future__ import annotations

import copy
import json
import unittest
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import deployment_revision as revision

import pytest
from scripts.deployment_revision import TARGET_WORKFLOWS
from scripts.ingestion_targets import DEPLOY_TARGETS as TARGETS, environment as target_environment
from scripts.deployment_receipt_contracts import expanded_steps

from workflow_helpers import (
    load_workflow,
    workflow_steps_by_name,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_WORKFLOW = REPO_ROOT / ".github/workflows/ingestion-deploy.yml"
DOCKERFILE = REPO_ROOT / "ingestion/wdpa_monthly/Dockerfile"


class WdpaMonthlyDeployWorkflowTests(unittest.TestCase):
    def test_schedule_control_authentication_requires_its_exact_main_workflow(self):
        workflow = load_workflow(REPO_ROOT / ".github/workflows/ingestion-schedule-control.yml")
        steps = workflow_steps_by_name(workflow, "control")
        guard = steps["Require trusted main workflow"]["run"]
        own_path = ".github/workflows/ingestion-schedule-control.yml"
        for ref, path, accepted in (
            ("refs/heads/main", own_path, True),
            ("refs/heads/feature", own_path, False),
            ("refs/heads/main", ".github/workflows/unrelated.yml", False),
        ):
            with self.subTest(ref=ref, path=path):
                result = subprocess.run(["bash", "-c", guard], capture_output=True, text=True,
                    env={**os.environ, "GITHUB_REF": ref, "GITHUB_REPOSITORY": "SkyTruth/shared-datasets-1",
                         "GITHUB_WORKFLOW_REF": f"SkyTruth/shared-datasets-1/{path}@refs/heads/main"})
                self.assertEqual(result.returncode == 0, accepted, result.stderr)
        sequence = workflow["jobs"]["control"]["steps"]
        self.assertLess(next(i for i, step in enumerate(sequence) if step.get("name") == "Validate fixed schedule scope"),
                        next(i for i, step in enumerate(sequence) if step.get("uses", "").startswith("google-github-actions/auth")))

    def test_resume_refuses_worker_without_verified_automatic_deployment(self):
        workflow = load_workflow(REPO_ROOT / ".github/workflows/ingestion-schedule-control.yml")
        script = workflow_steps_by_name(workflow, "control")["Verify publication before resuming"]["run"]
        fake_uv = '''uv() {
          if [[ "$*" == *wdpa_schedule_readiness.py* ]]; then
            echo "WDPA automatic deployment is not ready" >&2
            return 1
          fi
        }
        '''
        result = subprocess.run(["bash", "-e", "-c", fake_uv + script],
                                env={**os.environ, "JOB_NAME": "wdpa-monthly"},
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_image_runs_the_complete_automatic_pipeline(self):
        self.assertIn('CMD ["python", "-m", "ingestion.wdpa_monthly.automatic"]', DOCKERFILE.read_text())

    def test_allowlist_cannot_increase_worker_size_or_use_ram_scratch(self):
        image = "registry/wdpa@sha256:" + "a" * 64
        config = "sha256:" + "b" * 64
        after = {"template": [{"template": [{
            "timeout": "46800s", "volumes": [{"name": "work", "empty_dir": [{"medium": "DISK", "size_limit": "100Gi"}]}],
            "containers": [{"image": image, "resources": [{"limits": {"cpu": "4", "memory": "8Gi"}}],
                "volume_mounts": [{"name": "work", "mount_path": "/work"}],
                "env": [{"name": "TMPDIR", "value": "/work/tmp"}, {"name": "SHARED_DATASETS_WORKDIR", "value": "/work/shared-datasets-1"},
                        {"name": "WDPA_BUILD_IMAGE", "value": image},
                        {"name": "WDPA_BUILD_IMAGE_CONFIG_DIGEST", "value": config}]}],
        }]}]}
        for mismatch in (None, "memory", "disk", "scratch", "missing_config", "wrong_config", "pinned_bundle", "persistent_date"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as root:
                proposed = copy.deepcopy(after)
                task = proposed["template"][0]["template"][0]
                if mismatch == "memory":
                    task["containers"][0]["resources"][0]["limits"]["memory"] = "16Gi"
                elif mismatch == "disk":
                    task["volumes"][0]["empty_dir"][0]["medium"] = "MEMORY"
                elif mismatch == "scratch":
                    task["containers"][0]["env"][0]["value"] = "/tmp"
                elif mismatch == "missing_config":
                    task["containers"][0]["env"].pop()
                elif mismatch == "wrong_config":
                    task["containers"][0]["env"][-1]["value"] = "sha256:" + "c" * 64
                elif mismatch == "pinned_bundle":
                    task["containers"][0]["env"].append({"name": "WDPA_PROMOTION_BUNDLE", "value": "old"})
                elif mismatch == "persistent_date":
                    task["containers"][0]["env"].append({"name": "RUN_DATE", "value": "2026-10-01"})
                plan = Path(root) / "plan.json"
                plan.write_text(json.dumps({"resource_changes": [{
                    "address": "module.wdpa_monthly_job.google_cloud_run_v2_job.this",
                    "change": {"actions": ["update"], "after": proposed}}]}))
                result = subprocess.run([sys.executable, "scripts/terraform_plan_allowlist.py", str(plan), "--job-image-target=wdpa-monthly", "--expected-image=" + image, "--expected-config=" + config, "--refusal-prefix=Refusing WDPA"], capture_output=True, text=True, env={**os.environ, "TESTED_IMAGE_CONFIG": config, "TARGET_CONFIG": json.dumps(target_table()["wdpa-monthly"])})
                self.assertEqual(result.returncode, 0 if mismatch is None else 1, result.stdout + result.stderr)

    def test_schedule_resume_requires_publication_but_no_monthly_approval(self):
        workflow = (REPO_ROOT / ".github/workflows/ingestion-schedule-control.yml").read_text()
        self.assertIn("publication_rollout_gate.py", workflow)
        self.assertIn("--require-published", workflow)
        self.assertNotIn("wdpa_build_authorization", workflow)
        self.assertNotIn("wdpa_monthly_build.py", workflow)
        self.assertFalse((REPO_ROOT / ".github/workflows/wdpa-monthly-build.yml").exists())

    def test_scheduled_job_has_no_monthly_pin(self):
        terraform = (REPO_ROOT / "terraform/envs/prod/wdpa_monthly.tf").read_text()
        self.assertNotIn("WDPA_PROMOTION_BUNDLE", terraform)
        self.assertNotIn("wdpa-processing-acceptance", terraform)
        self.assertNotRegex(terraform, r"(?m)^\s*RUN_DATE\s*=")
        self.assertIn("var.wdpa_monthly_image_config_digest", terraform)




    def test_ci_filter_selects_helper_and_its_behavior_tests(self):
        from scripts.ci_contract import NATIVE_TESTS, resolve_selection
        for path in ('scripts/translation_local_io.py', 'tests/test_translation_local_io.py', 'scripts/feature_metadata_translation_reuse.py', 'tests/test_feature_metadata_translation_reuse.py', 'tests/test_wdpa_translation_inputs.py'):
            with self.subTest(path=path):
                self.assertIn('geospatial-integration', resolve_selection([path])["suites"])
        self.assertNotIn('geospatial-integration', resolve_selection(['docs/unrelated.md'])["suites"])
        self.assertIn('tests/test_translation_local_io.py', NATIVE_TESTS)

    def test_wdpa_monthly_dockerfile_copies_conditional_runtime_dependencies(self):
        dockerfile = DOCKERFILE.read_text(encoding="utf-8")
        self.assertIn(
            "COPY scripts/slack_notify.py ./scripts/slack_notify.py",
            dockerfile,
            "wdpa-monthly image must copy scripts/slack_notify.py; "
            "the job imports it when sending metadata notifications",
        )
        self.assertIn(
            "COPY catalog/feature-identity-resolutions ./catalog/feature-identity-resolutions",
            dockerfile,
            "wdpa-monthly image must ship reviewed feature-identity resolutions; "
            "the job loads them from catalog/feature-identity-resolutions/ at runtime",
        )


# Shared workflow boundary checks execute the shipped shell with harmless external
# command recorders. Per-target data remains in the deploy workflow's one table.


def shared_workflow():
    return load_workflow(DEPLOY_WORKFLOW)


def target_table():
    return TARGETS


def shared_step(job, name):
    return {s.get("name"): s for s in expanded_steps(REPO_ROOT, shared_workflow()["jobs"][job])}[name]


def run_shell(tmp_path, script, *, target='eamlis-monthly', extra=None, prefix=''):
    binary = tmp_path / 'bin'
    binary.mkdir(exist_ok=True)
    if not (binary / 'python').exists():
        (binary / 'python').symlink_to(sys.executable)
    env = {
        **os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
        'TARGET': target, 'WORKFLOW_FILE': target + '-deploy.yml',
        'RUNNER_TEMP': str(tmp_path), 'GITHUB_ENV': str(tmp_path / 'environment'),
        'GITHUB_OUTPUT': str(tmp_path / 'outputs'), 'GITHUB_WORKSPACE': str(REPO_ROOT),
        'GOOGLE_CLOUD_PROJECT': 'shared-datasets-1', 'REGION': 'us-central1',
        'CANARY_RUN_DATE': '', 'CANCEL_RUNNING_CANARY': 'false',
        **{key: str(value).lower() if isinstance(value, bool) else str(value)
           for key, value in target_environment(target_table()[target]).items()},
        **(extra or {}),
    }
    return subprocess.run(['bash', '-e', '-c', prefix + script], cwd=REPO_ROOT, env=env, capture_output=True, text=True)


@pytest.mark.parametrize('target', TARGETS)
def test_target_resolution_and_exact_plan_arguments(tmp_path, target):
    config = target_table()[target]
    assert config['workflow'] == TARGET_WORKFLOWS[target]
    resolved = run_shell(tmp_path, shared_step('prepare', 'Resolve registered ingestion target')['run'], target=target)
    assert resolved.returncode == 0, resolved.stderr
    assert 'PLAN_GUARD<<INGESTION_POLICY\n' + config['plan_guard'] + '\n' in (tmp_path / 'environment').read_text()
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/terraform_retry.sh').write_text('python -c \'import json,sys; print(json.dumps(sys.argv[1:]))\' "$@"\n')
    image = 'registry/job@sha256:' + 'a' * 64
    result = run_shell(tmp_path, shared_step('apply', 'Terraform plan')['run'], target=target,
                       extra={'GITHUB_WORKSPACE': str(tmp_path), 'IMAGE': image, 'TESTED_IMAGE_CONFIG': 'sha256:' + 'b' * 64})
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert {a.removeprefix('-target=') for a in args if a.startswith('-target=')} == set(subprocess.check_output([sys.executable, 'scripts/dataset_usage_deploy.py', '--targets'], text=True).splitlines() if config['plan_guard'] == 'usage' else config['targets'])
    variables = dict(a.removeprefix('-var=').split('=', 1) for a in args if a.startswith('-var='))
    assert variables[config['image_variable']] == image
    assert set(t['image_variable'] for t in target_table().values()) <= variables.keys()
    assert ('wdpa_monthly_image_config_digest' in variables) == (target == 'wdpa-monthly')


def test_unregistered_target_fails_before_image_or_authentication(tmp_path):
    result = run_shell(tmp_path, shared_step('prepare', 'Resolve registered ingestion target')['run'],
                       extra={'TARGET': 'arbitrary-job', 'WORKFLOW_FILE': 'arbitrary-job-deploy.yml'})
    assert result.returncode != 0
    assert not (tmp_path / 'outputs').exists()


def test_target_discovery_failure_stops_before_terraform(tmp_path):
    binary = tmp_path / 'bin'
    binary.mkdir()
    uv = binary / 'uv'
    uv.write_text('#!/bin/sh\nif [ "$1" = sync ]; then exit 0; fi\nexit 2\n')
    uv.chmod(0o755)
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/terraform_retry.sh').write_text('touch "$RUNNER_TEMP/terraform-called"\n')
    result = run_shell(tmp_path, shared_step('apply', 'Terraform plan')['run'], target='dataset-usage',
                       extra={'GITHUB_WORKSPACE': str(tmp_path), 'IMAGE': 'registry/job@sha256:' + 'a' * 64})
    assert result.returncode != 0
    assert not (tmp_path / 'terraform-called').exists()


@pytest.mark.parametrize('filename,job', [
    ('ingestion-deploy.yml', 'prepare'), ('prod-terraform-target-apply.yml', 'prepare'),
    ('prod-terraform-target-apply.yml', 'sync'), ('catalog-viewer-deploy.yml', 'prepare'),
    ('catalog-viewer-deploy.yml', 'deploy'), ('pmtiles-cdn-sync.yml', 'sync'),
    ('pmtiles-cdn-sync.yml', 'verify'),
])
def test_immutable_verifier_restore_replaces_candidate_bytes_and_rejects_unknown_revision(tmp_path, filename, job):
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO_ROOT, text=True).strip()
    candidate = tmp_path / 'candidate'
    (candidate / 'scripts').mkdir(parents=True)
    (candidate / 'scripts/deployment_revision.py').write_text('raise RuntimeError("older candidate verifier")\n')
    (candidate / 'scripts/terraform_plan_allowlist.py').write_text('raise RuntimeError("older candidate plan guard")\n')
    (candidate / '.github').mkdir()
    (candidate / '.github/terraform-targets.json').write_text('untrusted candidate registry')
    workflow = load_workflow(REPO_ROOT / '.github/workflows' / filename)
    script = workflow_steps_by_name(workflow, job)['Restore immutable receipt verifier after candidate checkout']['run']
    snapshot = workflow_steps_by_name(workflow, job)['Snapshot trusted verifier import closure']['run']
    captured = run_shell(tmp_path, snapshot, extra={'TRUSTED_WORKFLOW_SHA': revision})
    assert captured.returncode == 0, captured.stderr
    result = run_shell(tmp_path, script, extra={'GITHUB_WORKSPACE': str(candidate), 'TRUSTED_WORKFLOW_SHA': revision})
    assert result.returncode == 0, result.stderr
    for filename in ('deployment_revision.py', 'deployment_emission.py', 'ingestion_targets.py', 'terraform_plan_allowlist.py', 'terraform_target_contracts.py', 'ci_toolchain.py', 'workflow_identity.py', 'github_api_read.py', 'install_deployment_verifier.py'):
        expected = subprocess.check_output(['git', 'show', f'{revision}:scripts/{filename}'], cwd=REPO_ROOT)
        assert (candidate / 'scripts' / filename).read_bytes() == expected
    expected = subprocess.check_output(['git', 'show', f'{revision}:.github/actions/deployment-receipt/action.yml'], cwd=REPO_ROOT)
    assert (candidate / '.github/actions/deployment-receipt/action.yml').read_bytes() == expected
    expected = subprocess.check_output(['git', 'show', f'{revision}:.github/terraform-targets.json'], cwd=REPO_ROOT)
    assert (candidate / '.github/terraform-targets.json').read_bytes() == expected
    subprocess.run([sys.executable, str(candidate / 'scripts/deployment_revision.py'), '--help'],
                   check=True, capture_output=True, text=True)
    help_text = subprocess.check_output([sys.executable, '-S', str(candidate / 'scripts/terraform_plan_allowlist.py'), '--help'], text=True)
    assert '--registered-scope' in help_text
    result = run_shell(tmp_path, snapshot, extra={'TRUSTED_WORKFLOW_SHA': '0' * 40})
    assert result.returncode != 0


@pytest.mark.parametrize('target', ['wdpa-monthly', 'mpatlas-monthly', 'eamlis-monthly', 'sea-ice-daily'])
@pytest.mark.parametrize('caller,ref,accepted', [('ci.yml', 'main', True), ('self', 'main', True), ('foreign.yml', 'main', False), ('self', 'feature', False)])
def test_each_runner_rejects_foreign_caller_before_bootstrap(tmp_path, target, caller, ref, accepted):
    name = target + '-deploy.yml' if caller == 'self' else caller
    for job in ('prepare', 'apply', 'verify'):
        result = run_shell(tmp_path, shared_step(job, 'Validate main ref')['run'], target=target, extra={
            'GITHUB_REF': 'refs/heads/' + ref, 'GITHUB_REPOSITORY': 'SkyTruth/shared-datasets-1',
            'GITHUB_WORKFLOW_REF': f'SkyTruth/shared-datasets-1/.github/workflows/{name}@refs/heads/{ref}',
        })
        assert (result.returncode == 0) == accepted, result.stderr


def test_only_apply_holds_state_lock_and_each_target_serializes_its_entire_rollout():
    from scripts.repo_guardrails import check_workflow_boundaries
    assert check_workflow_boundaries(REPO_ROOT) == []
    workflow = shared_workflow()
    jobs = workflow['jobs']
    assert {target for target, config in target_table().items() if config['rollout_gate']} == {'wdpa-monthly', 'sea-ice-daily'}
    assert {target for target, config in target_table().items() if config['paused_gate']} == {'wdpa-monthly', 'eamlis-monthly'}
    assert 'concurrency' not in workflow
    locked = [name for name, job in jobs.items() if job.get('concurrency', {}).get('group') == 'prod-terraform-state']
    assert locked == ['apply']
    assert jobs['apply']['concurrency'] == {'group': 'prod-terraform-state', 'queue': 'max', 'cancel-in-progress': False}
    assert jobs['apply']['needs'] == 'prepare'
    assert jobs['verify']['needs'] == ['prepare', 'apply']
    assert 'always()' in jobs['verify']['if'] and "deployment_id != ''" in jobs['verify']['if']
    for job in jobs.values():
        assert job['environment'] == 'shared-datasets-production'
        sequence = list(expanded_steps(REPO_ROOT, job))
        names = [s.get('name') for s in sequence]
        assert names.index('Verify executor before candidate checkout') < names.index('Check out repository')
        assert names.index('Verify tested main revision') < names.index('Authenticate to Google Cloud')
        auth = next(s for s in sequence if s.get('uses', '').startswith('google-github-actions/auth@'))
        assert auth['with']['workload_identity_provider'] == '${{ env.TERRAFORM_WORKLOAD_IDENTITY_PROVIDER }}'
        assert 'inputs.service-account || env.TERRAFORM_SERVICE_ACCOUNT' in auth['with']['service_account']
    for target in target_table():
        wrapper = load_workflow(REPO_ROOT / f'.github/workflows/{target}-deploy.yml')
        job = wrapper['jobs']['deploy']
        assert job['uses'] == './.github/workflows/ingestion-deploy.yml'
        assert 'secrets' not in job
        assert job['with']['target'] == target
        for key, requested in workflow['permissions'].items():
            assert wrapper['permissions'].get(key) == requested, 'a reusable workflow cannot elevate its caller token'
        triggers = wrapper['on']
        for key in ('executor_sha', 'source_run_id', 'source_run_attempt'):
            assert job['with'][key] == '${{ inputs.' + key + ' }}'
            assert all(triggers[event]['inputs'][key]['required'] for event in ('workflow_call', 'workflow_dispatch'))
        if target != 'dataset-usage':
            assert job['concurrency'] == {'group': 'ingestion-' + target, 'queue': 'max', 'cancel-in-progress': False}
    locked_runs = '\n'.join(s.get('run', '') for s in jobs['apply']['steps'])
    assert 'jobs execute' not in locked_runs and 'jobs executions describe' not in locked_runs
    names = [s.get('name') for s in jobs['apply']['steps']]
    order = ['Terraform plan', 'Export Terraform plan JSON', 'Enforce ingestion resource-change allowlist', 'Verify complete saved-plan mutation permissions',
             'Claim tested deployment revision', 'Attest serialized deployment claim before mutation', 'Terraform apply']
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    assert "steps.claim-receipt.outcome == 'success'" in shared_step('apply', 'Terraform apply')['if']


@pytest.mark.parametrize('apply_result,claim', [('failure', 'success'), ('cancelled', 'success'), ('success', 'failure'), ('success', 'skipped'), ('success', 'success')])
def test_canary_requires_successful_apply_and_signed_claim(tmp_path, apply_result, claim):
    result = run_shell(tmp_path, shared_step('verify', 'Require successful signed apply')['run'],
                       extra={'APPLY_RESULT': apply_result, 'CLAIM_SIGNED': claim})
    assert (result.returncode == 0) == (apply_result == claim == 'success')


@pytest.mark.parametrize('target', [name for name, config in TARGETS.items() if config['plan_guard'] != 'usage'])
@pytest.mark.parametrize('defect', ['valid', 'image', 'foreign', 'delete', 'replace', 'create', 'config'])
def test_shared_allowlist_executes_real_guard_and_rejects_unauthorized_changes(tmp_path, target, defect):
    address = target_table()[target]['targets'][0]
    image = 'registry/job@sha256:' + 'a' * 64
    actions = {'delete': ['delete'], 'replace': ['delete', 'create'], 'create': ['create']}.get(defect, ['update'])
    plan = {'resource_changes': [{'address': address if defect != 'foreign' else 'module.foreign.google_cloud_run_v2_job.this', 'change': {'actions': actions, 'after': {'template': [{'template': [{'containers': [{'image': 'wrong' if defect == 'image' else image}]}]}]}}}]}
    config_digest = 'sha256:' + 'b' * 64
    if target == 'wdpa-monthly':
        task = plan['resource_changes'][0]['change']['after']['template'][0]['template'][0]
        task.update(timeout='46800s', volumes=[{'name': 'work', 'empty_dir': [{'medium': 'DISK', 'size_limit': '100Gi'}]}])
        task['containers'][0].update(resources=[{'limits': {'cpu': '4', 'memory': '8Gi'}}],
            volume_mounts=[{'name': 'work', 'mount_path': '/work'}], env=[
                {'name': 'TMPDIR', 'value': '/work/tmp'}, {'name': 'SHARED_DATASETS_WORKDIR', 'value': '/work/shared-datasets-1'},
                {'name': 'WDPA_BUILD_IMAGE', 'value': image},
                {'name': 'WDPA_BUILD_IMAGE_CONFIG_DIGEST', 'value': 'sha256:' + 'c' * 64 if defect == 'config' else config_digest}])
    (tmp_path / 'ingestion.tfplan.json').write_text(json.dumps(plan))
    result = run_shell(tmp_path, shared_step('apply', 'Enforce ingestion resource-change allowlist')['run'], target=target,
                       extra={'IMAGE': image, 'TESTED_IMAGE_CONFIG': config_digest})
    assert (result.returncode == 0) == (defect == 'valid' or defect == 'config' and target != 'wdpa-monthly'
                                      or defect == 'create' and target == 'mpatlas-monthly'), result.stderr


@pytest.mark.parametrize('job_status,phase,execution,expected', [
    ('success', 'verified', 'projects/p/jobs/job/executions/canary-1', 'verified'),
    ('failure', 'verified', 'canary-2', 'failed'), ('cancelled', '', '', 'failed'),
    ('success', 'verification_pending', 'wdpa-1', 'verification_pending'), ('success', '', '', 'unknown'),
])
def test_record_outcome_retains_actual_execution_and_never_promotes_failure(tmp_path, job_status, phase, execution, expected):
    result = run_shell(tmp_path, shared_step('verify', 'Record deployment outcome')['run'], extra={
        'JOB_STATUS': job_status, 'DEPLOYMENT_PHASE': phase, 'CANARY_EXECUTION': execution,
        'DEPLOYMENT_ID': '42', 'ARGS': str(tmp_path / 'args'),
    }, prefix='python() { printf "%s\\n" "$@" > "$ARGS"; }\n')
    assert result.returncode == 0, result.stderr
    args = (tmp_path / 'args').read_text().splitlines()
    assert args[:4] == ['scripts/deployment_revision.py', 'finish', '--deployment-id', '42']
    assert args[4:6] == ['--phase', expected]
    assert args[6:] == (['--execution', execution.rsplit('/', 1)[-1]] if execution else [])
    assert 'always()' in shared_step('verify', 'Record deployment outcome')['if']
    assert 'always()' in shared_step('verify', 'Attest terminal deployment outcome')['if']


@pytest.mark.parametrize('apply_exit', [0, 1])
def test_apply_failure_propagates_without_claiming_runtime_success(tmp_path, apply_exit):
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/terraform_retry.sh').write_text('exit "$TEST_EXIT"\n')
    result = run_shell(tmp_path, shared_step('apply', 'Terraform apply')['run'], extra={
        'GITHUB_WORKSPACE': str(tmp_path), 'TEST_EXIT': str(apply_exit),
    })
    assert result.returncode == apply_exit
    assert not (tmp_path / 'environment').exists()


@pytest.mark.parametrize('target', ['wdpa-monthly', 'mpatlas-monthly', 'eamlis-monthly', 'sea-ice-daily'])
@pytest.mark.parametrize('date', ['', '2026-10-01', 'invalid; exit 0'])
def test_canary_date_is_one_argument_and_execution_is_recorded_before_watch(tmp_path, target, date):
    result = run_shell(tmp_path, shared_step('verify', 'Execute ingestion canary')['run'], target=target, extra={
        'CANARY_RUN_DATE': date, 'ARGS': str(tmp_path / 'args'), 'GITHUB_SERVER_URL': 'https://github.com',
        'GITHUB_REPOSITORY': 'SkyTruth/shared-datasets-1', 'GITHUB_RUN_ID': '123',
    }, prefix='gcloud() { printf "%s\\n" "$@" > "$ARGS"; echo canary-123; }\n')
    if date.startswith('invalid'):
        assert result.returncode != 0
        assert not (tmp_path / 'args').exists()
        return
    assert result.returncode == 0, result.stderr
    args = (tmp_path / 'args').read_text().splitlines()
    assert args[:4] == ['run', 'jobs', 'execute', target]
    assert '--async' in args
    overrides = [a for a in args if a.startswith('--update-env-vars=')]
    assert ('RUN_DATE=2026-10-01' in ''.join(overrides)) == bool(date)
    assert ('SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL=' in ''.join(overrides)) == (target != 'wdpa-monthly')
    assert (tmp_path / 'environment').read_text() == 'CANARY_EXECUTION=canary-123\n'


@pytest.mark.parametrize('target', list(TARGETS))
@pytest.mark.parametrize('status,wrong_image,phase,success', [
    ({'succeededCount': 1, 'completionTime': 'now'}, False, 'verified', True),
    ({'succeededCount': 1}, False, None, False), ({'cancelledCount': 1}, False, 'failed', False),
    ({'failedCount': 1}, False, 'failed', False), ({'completionTime': 'now'}, False, 'unknown', False),
    ({'succeededCount': 1, 'completionTime': 'now'}, True, None, False),
])
def test_short_canary_requires_terminal_success_of_exact_digest(tmp_path, target, status, wrong_image, phase, success):
    assert target_environment(target_table()[target])['CANARY_ATTEMPTS'] == (20 if target == 'wdpa-monthly' else 125)
    image = 'registry/job@sha256:' + 'a' * 64
    payload = {'spec': {'template': {'spec': {'containers': [{'image': 'wrong' if wrong_image else image}]}}}, 'status': status}
    if target == 'wdpa-monthly' and status == {'succeededCount': 1}:
        phase, success = 'verification_pending', True
    result = run_shell(tmp_path, shared_step('verify', 'Watch ingestion canary')['run'], target=target,
                       extra={'IMAGE': image, 'CANARY_EXECUTION': 'canary-123', 'EXECUTION_JSON': json.dumps(payload), 'CALLS': str(tmp_path / 'calls'), 'CANARY_ATTEMPTS': '3'},
                       prefix='gcloud() { echo call >> "$CALLS"; echo "$EXECUTION_JSON"; }\nsleep() { :; }\n')
    assert (result.returncode == 0) == success, result.stderr
    assert len((tmp_path / 'calls').read_text().splitlines()) == (3 if status == {'succeededCount': 1} else 1)
    output = tmp_path / 'environment'
    assert (output.read_text() if output.exists() else '') == (f'DEPLOYMENT_PHASE={phase}\n' if phase else '')



def test_canary_validation_and_hygiene_precede_scheduler_resume(tmp_path):
    names = [s.get('name') for s in shared_workflow()['jobs']['verify']['steps']]
    order = ['Require successful signed apply', 'Confirm deployed digest', 'Execute ingestion canary', 'Watch ingestion canary',
             'Validate latest release contract', 'Run asset bucket hygiene audit', 'Resume verified ingestion scheduler', 'Record deployment outcome']
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    resume = shared_step('verify', 'Resume verified ingestion scheduler')
    assert resume['if'] == "env.AFTER_VERIFY != '' && inputs.resume_scheduler"
    from scripts import ingestion_targets
    assert target_table()['sea-ice-daily']['after_verify'] == 'resume_published_scheduler'
    assert resume['run'] == 'uv run --no-sync python scripts/ingestion_targets.py after-verify'
    for published in (True, False):
        def run(args, **kwargs):
            if not published:
                raise subprocess.CalledProcessError(1, args)
        with patch.dict(os.environ, REGION='us-central1', GOOGLE_CLOUD_PROJECT='shared-datasets-1'), patch.object(ingestion_targets.subprocess, 'run', side_effect=run) as external:
            if published:
                ingestion_targets.after_verify('sea-ice-daily')
                assert [call.args[0][3] for call in external.call_args_list[1:]] == ['resume', 'describe']
            else:
                with pytest.raises(subprocess.CalledProcessError):
                    ingestion_targets.after_verify('sea-ice-daily')
                assert external.call_count == 1
    assert 'COPY catalog/feature-identity-resolutions ./catalog/feature-identity-resolutions' in (REPO_ROOT / 'ingestion/sea_ice_daily/Dockerfile').read_text()


def test_installed_smokes_run_only_in_ci_on_the_retained_image():
    from scripts.production_image_contracts import commands
    assert not any('Smoke-test' in step.get('name', '') for step in shared_workflow()['jobs']['prepare']['steps'])
    checks = commands('sea-ice-daily', 'a' * 40, image_id='sha256:' + 'b' * 64)[1:]
    assert any('--sea-ice-only' in args for args in checks)
    assert all('--memory' in args and 'sha256:' + 'b' * 64 in args for args in checks)


@pytest.mark.parametrize('apply_result,claim', [('failure', 'success'), ('success', 'failure'), ('success', 'success')])
def test_cdn_verification_admits_only_successful_signed_apply(tmp_path, apply_result, claim):
    workflow = load_workflow(REPO_ROOT / '.github/workflows/pmtiles-cdn-sync.yml')
    sync, verify = (workflow['jobs'][name] for name in ('sync', 'verify'))
    assert 'concurrency' not in verify
    assert verify['if'] == "${{ always() && needs.sync.outputs.deployment_id != '' }}"
    assert not any('sleep ' in step.get('run', '') for step in sync['steps'])
    steps = workflow_steps_by_name(workflow, 'verify')
    assert list(steps).index('Require successful signed apply') < list(steps).index('Invalidate catalog CDN cache')
    result = run_shell(tmp_path, steps['Require successful signed apply']['run'],
                       extra={'APPLY_RESULT': apply_result, 'CLAIM_SIGNED': claim})
    assert (result.returncode == 0) == (apply_result == claim == 'success')
    assert steps['Record terminal deployment outcome']['if'] == '${{ always() }}'
    assert 'always()' in steps['Attest terminal deployment outcome']['if']


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("state,date,accepted", [
    ("PAUSED", "", False), ("PAUSED", "2026-11-01", True), ("ENABLED", "", True),
    ("UPDATE_FAILED", "", None), ("", "", None),
])
def test_paused_gate_is_shared_by_every_runtime_window(tmp_path, monkeypatch, target, state, date, accepted):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    args = SimpleNamespace(target=target, canary_run_date=date, allow_cancel="false")
    def read(command, **kwargs):
        return state if command[1] == "scheduler" else b"[]"
    with patch.object(revision.subprocess, "check_output", side_effect=read):
        if TARGETS[target]["paused_gate"] and accepted is None:
            with pytest.raises(revision.DeploymentError, match="scheduler state"):
                revision.runtime_window(args)
            assert not (tmp_path / "outputs").exists()
        else:
            revision.runtime_window(args)
            ready = accepted if TARGETS[target]["paused_gate"] else True
            assert (tmp_path / "outputs").read_text() == f"ready={str(ready).lower()}\n"


@pytest.mark.parametrize("cancel", ["false", "true"])
@pytest.mark.parametrize("outcome", ["drained", "timeout", "denied"])
def test_runtime_wait_drains_execution_and_cancellation_before_canary(tmp_path, monkeypatch, cancel, outcome):
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "outputs"))
    args = SimpleNamespace(target="sea-ice-daily", canary_run_date="", allow_cancel=cancel, wait_seconds=60)
    clock = [0]
    def sleep(seconds):
        clock[0] += seconds
    def read(command, **kwargs):
        if outcome == "denied":
            raise subprocess.CalledProcessError(1, command)
        return json.dumps([{"metadata": {"name": "scheduled-1"}, "status": (
            {"completionTime": "now"} if outcome == "drained" and clock[0] >= 30 else {"runningCount": 0}
        )}]).encode()
    with patch.object(revision.subprocess, "check_output", side_effect=read), patch.object(revision.subprocess, "check_call") as cancel_execution, \
         patch.object(revision.time, "monotonic", side_effect=lambda: clock[0]), patch.object(revision.time, "sleep", side_effect=sleep):
        if outcome == "drained":
            revision.runtime_window(args)
            assert (tmp_path / "outputs").read_text() == "ready=true\n"
            assert clock[0] == 30
        else:
            with pytest.raises((revision.DeploymentError, subprocess.CalledProcessError)):
                revision.runtime_window(args)
            assert not (tmp_path / "outputs").exists()
        assert cancel_execution.call_count == (1 if cancel == "true" and outcome != "denied" else 0)


def test_runtime_window_wait_is_required_before_canary_launch():
    workflow = shared_workflow()
    names = [s.get("name") for s in workflow["jobs"]["verify"]["steps"]]
    assert names.index("Check runtime verification window") < names.index("Execute ingestion canary")
    for job in ("prepare", "apply", "verify"):
        command = shared_step(job, "Check runtime verification window")["run"]
        assert ("--wait-seconds 3750" in command) == (job == "verify")
    for target, config in TARGETS.items():
        wrapper = load_workflow(REPO_ROOT / f".github/workflows/{target}-deploy.yml")
        if config["plan_guard"] != "usage":
            assert wrapper["on"]["workflow_dispatch"]["inputs"]["cancel_running_canary"]["default"] is False


@pytest.mark.parametrize("defect", ["valid", "manifest", "schema", "sidecar", "fgb", "tiles", "hygiene"])
def test_extracted_release_commands_validate_real_serialized_artifacts(tmp_path, monkeypatch, defect):
    import gzip
    import struct
    from ingestion.common import feature_metadata
    from scripts import ingestion_verification as verify, release_feature_model as model
    target = "eamlis-monthly"
    slug, release = TARGETS[target]["asset_slug"], "2026-11-01"
    monkeypatch.setenv("TARGET", target)
    monkeypatch.setenv("BUCKET", "skytruth-shared-datasets-1")
    record = {"schema_version": model.METADATA_SIDECAR_SCHEMA_VERSION, "provenance": {}, "asset_slug": slug, "release": release, "feature_id": "1", "identity_key": ["1"],
              "geometry_hash": "sha256:" + "a" * 64, "properties_hash": "sha256:" + "b" * 64, "properties": {}}
    schema = feature_metadata.schema_from_records(asset_slug=slug, release=release, records=[record])
    manifest = model.build_release_manifest(asset_slug=slug, release=release, source_inputs=[], schema=schema,
        identity=model.build_identity_metadata(strategy="source_field", source_fields=["id"]), validation={},
        artifacts=[{"role": role, "path": "gs://bucket/" + role, **({"generation": 1} if role != "manifest" else {}), "sha256": "a" * 64}
                   for role in ("fgb", "pmtiles", "metadata", "schema", "manifest")])
    if defect == "manifest":
        manifest["identity"] = {}
    if defect == "schema":
        schema["asset_slug"] = "foreign"
    if defect == "sidecar":
        record["feature_id"] = "invalid-id"
    metadata = gzip.compress(json.dumps({"vector_layers": [{"id": "features", "fields": {"feature_id": "String", **({"extra": "String"} if defect == "tiles" else {})}}]}).encode())
    blobs = {".manifest.json": json.dumps(manifest).encode(), ".schema.json": json.dumps(schema).encode(),
             ".metadata.ndjson.gz": gzip.compress(json.dumps(record).encode()),
             ".fgb": b"feature_id geometry_hash" + (b" properties_hash" if defect != "fgb" else b"")}
    def read(command):
        uri = command[-1]
        if uri.endswith(".pmtiles"):
            return b"PMTiles\x03" + struct.pack("<QQQQ", 127, 0, 127, len(metadata)) + bytes(87) if "--range=0-126" in command else metadata
        return next((value for suffix, value in blobs.items() if uri.endswith(suffix)), json.dumps({"latest_release": {"date": release}}).encode())
    with patch.object(verify.subprocess, "check_output", side_effect=read):
        if defect in {"valid", "hygiene"}:
            verify.release_contract()
        else:
            with pytest.raises((RuntimeError, model.ReleaseFeatureModelError)):
                verify.release_contract()
    report = tmp_path / "hygiene.json"
    report.write_text(json.dumps({"findings": [{"asset_slug": slug, "rule": "feature-metadata-invalid" if defect == "hygiene" else "unrelated"}]}))
    if defect == "hygiene":
        with pytest.raises(SystemExit):
            verify.hygiene(report)
    else:
        verify.hygiene(report)


def test_usage_wrapper_keeps_bootstrap_and_shared_canary_boundary():
    from scripts.deployment_receipt_contracts import ingestion_boundary
    workflow = load_workflow(REPO_ROOT / ".github/workflows/dataset-usage-deploy.yml")
    assert set(workflow["jobs"]) == {"bootstrap", "deploy"}
    assert workflow["jobs"]["deploy"]["uses"] == "./.github/workflows/ingestion-deploy.yml"
    assert workflow["jobs"]["deploy"]["with"]["target"] == "dataset-usage"
    assert workflow["jobs"]["deploy"]["needs"] == "bootstrap"
    assert workflow["jobs"]["bootstrap"]["with"]["sync_name"] == "Dataset usage deploy IAM bootstrap"
    assert workflow["concurrency"] == {"group": "deployment-dataset-usage", "queue": "max", "cancel-in-progress": False}
    assert ingestion_boundary(shared_workflow()) == []


def test_usage_window_does_not_require_broader_regional_inventory_permissions(tmp_path, monkeypatch):
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'output'))
    with patch.object(revision.subprocess, 'check_output') as cloud:
        revision.runtime_window(SimpleNamespace(target='dataset-usage', canary_run_date='', allow_cancel='false'))
    cloud.assert_not_called()
    assert (tmp_path / 'output').read_text() == 'ready=true\n'
    assert TARGETS['dataset-usage']['plan_guard'] == 'usage'
    guard = shared_step('apply', 'Enforce usage telemetry, activation and cost boundaries')
    assert "steps.window.outputs.ready == 'true'" in guard['if']
    assert 'scripts/dataset_usage_deploy.py --plan-json' in guard['run']


@pytest.mark.parametrize('bootstrap,sync,event,accepted', [
    ('success', True, 'push', True), ('skipped', False, 'push', True),
    ('failure', True, 'push', False), ('skipped', True, 'push', False),
    ('success', False, 'workflow_dispatch', True), ('failure', False, 'workflow_dispatch', False),
])
def test_usage_wrapper_preserves_iam_admission(bootstrap, sync, event, accepted):
    from test_ci_deployments import condition_result
    job = load_workflow(REPO_ROOT / '.github/workflows/dataset-usage-deploy.yml')['jobs']['deploy']
    assert condition_result(job, {
        'needs.bootstrap.result': bootstrap, 'inputs.sync_iam': sync,
        'github.event_name': event, 'github.ref': 'refs/heads/main',
    }) is accepted


@pytest.mark.parametrize('change', [
    {'plan_guard': 'unknown'}, {'canary_mode': 'unknown'}, {'canary_mode': 'deferred'},
    {'plan_guard': 'wdpa'}, {'plan_guard': 'usage'}, {'rollout_gate': 'true'},
    {'targets': ['module.foreign_job.google_cloud_run_v2_job.this']},
    {'targets': []}, {'asset_root': '../foreign/asset'}, {'synthetic_smoke': True},
])
def test_registry_rejects_invalid_combinations_at_construction(change):
    from scripts.ingestion_targets import target
    config = dict(selection='example', asset_root='100-category/110-family/example',
                  targets=['module.example_job.google_cloud_run_v2_job.this'])
    with pytest.raises((ValueError, TypeError)):
        target('example', **(config | change))


def test_new_trusted_import_and_package_initializer_cannot_load_candidate_bytes(tmp_path):
    import runpy
    snapshot = runpy.run_path(str(REPO_ROOT / 'scripts/tested_image_bundle.py'))['snapshot_trusted']
    root = tmp_path / 'repository'
    (root / 'scripts').mkdir(parents=True)
    (root / '.github').mkdir()
    (root / '.github/terraform-targets.json').write_text('{}')
    (root / 'scripts/deployment_revision.py').write_text('from scripts import new_dependency\nprint(new_dependency.VALUE)\n')
    (root / 'scripts/new_dependency.py').write_text('from . import second_dependency\nVALUE = second_dependency.VALUE\n')
    (root / 'scripts/second_dependency.py').write_text('VALUE = "trusted dependency"\n')
    for name in ('terraform_plan_allowlist', 'install_deployment_verifier'):
        (root / f'scripts/{name}.py').write_text('')
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.PIPE, text=True).strip()
    git('init')
    git('add', '.')
    git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.org', 'commit', '-m', 'Trusted import closure')
    snapshot(git('rev-parse', 'HEAD'), tmp_path / 'trusted-deployment.tar', root=root)
    for name in ('new_dependency', 'second_dependency', '__init__'):
        (root / f'scripts/{name}.py').write_text('raise RuntimeError("candidate bytes executed")\n')
    subprocess.run(['tar', '-xf', str(tmp_path / 'trusted-deployment.tar'), '-C', str(root)], check=True)
    result = subprocess.run([sys.executable, str(root / 'scripts/deployment_revision.py')], cwd=root,
                            env={**os.environ, 'PYTHONPATH': str(root)}, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'trusted dependency'
