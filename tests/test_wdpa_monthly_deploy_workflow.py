from __future__ import annotations

import copy
import json
import unittest
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from scripts.deployment_revision import TARGET_WORKFLOWS
from scripts.terraform_plan_allowlist import job_image_scope

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
        run = workflow_steps_by_name(load_workflow(DEPLOY_WORKFLOW), "apply")["Enforce ingestion resource-change allowlist"]["run"]
        code = run.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
        image = "registry/wdpa@sha256:" + "a" * 64
        config = "sha256:" + "b" * 64
        after = {"template": [{"template": [{
            "timeout": "86400s", "volumes": [{"name": "work", "empty_dir": [{"medium": "DISK", "size_limit": "100Gi"}]}],
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
                result = subprocess.run([sys.executable, "-c", code, str(plan), image], capture_output=True, text=True, env={**os.environ, "TESTED_IMAGE_CONFIG": config, "TARGET_CONFIG": json.dumps(target_table()["wdpa-monthly"])})
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


    def test_canary_watch_requires_completed_exact_image_and_reports_cancellation(self):
        script = workflow_steps_by_name(load_workflow(DEPLOY_WORKFLOW), "verify")["Watch ingestion canary"]["run"]
        image = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-monthly@sha256:" + "a" * 64
        tools = """gcloud() { printf '%s\\n' "$EXECUTION_JSON"; echo call >> "$CALLS"; }
sleep() { :; }
"""
        for status, actual_image, phase, exit_code, calls in (
            ({"succeededCount": 1}, image, "verification_pending", 0, 20),
            ({"succeededCount": 1, "completionTime": "now"}, image, "verified", 0, 1),
            ({"cancelledCount": 1}, image, "failed", 1, 1),
            ({"completionTime": "now"}, image, "unknown", 1, 1),
            ({"succeededCount": 1, "completionTime": "now"}, image.replace("a" * 64, "b" * 64), None, 1, 1),
        ):
            with self.subTest(status=status, phase=phase), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                binary = directory / "bin"
                binary.mkdir()
                (binary / "python").symlink_to(sys.executable)
                env_file, call_file = directory / "env", directory / "calls"
                payload = {"spec": {"template": {"spec": {"containers": [{"image": actual_image}]}}}, "status": status}
                result = subprocess.run(["bash", "-c", tools + script], cwd=REPO_ROOT, capture_output=True, text=True,
                                        env={**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                                             "EXECUTION_JSON": json.dumps(payload), "CALLS": str(call_file),
                                             "IMAGE": image, "CANARY_EXECUTION": "wdpa-execution", "TARGET": "wdpa-monthly",
                                             "REGION": "us-central1", "GOOGLE_CLOUD_PROJECT": "shared-datasets-1",
                                             "GITHUB_ENV": str(env_file)})
                self.assertEqual(result.returncode, exit_code, result.stderr)
                self.assertEqual(len(call_file.read_text().splitlines()), calls)
                self.assertEqual(env_file.read_text().strip() if env_file.exists() else None,
                                 f"DEPLOYMENT_PHASE={phase}" if phase else None)


    def test_ci_filter_selects_helper_and_its_behavior_tests(self):
        from scripts.ci_contract import NATIVE_TESTS, select_suites
        for path in ('scripts/translation_local_io.py', 'tests/test_translation_local_io.py', 'scripts/feature_metadata_translation_reuse.py', 'tests/test_feature_metadata_translation_reuse.py', 'tests/test_wdpa_translation_inputs.py'):
            with self.subTest(path=path):
                self.assertIn('geospatial-integration', select_suites([path])[0])
        self.assertNotIn('geospatial-integration', select_suites(['docs/unrelated.md'])[0])
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
    return json.loads(shared_workflow()['env']['INGESTION_TARGETS'])


def shared_step(job, name):
    return workflow_steps_by_name(shared_workflow(), job)[name]


def run_shell(tmp_path, script, *, target='eamlis-monthly', extra=None, prefix=''):
    binary = tmp_path / 'bin'
    binary.mkdir(exist_ok=True)
    if not (binary / 'python').exists():
        (binary / 'python').symlink_to(sys.executable)
    env = {
        **os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
        'TARGET': target, 'WORKFLOW_FILE': target + '-deploy.yml',
        'TARGET_CONFIG': json.dumps(target_table()[target]),
        'INGESTION_TARGETS': shared_workflow()['env']['INGESTION_TARGETS'],
        'RUNNER_TEMP': str(tmp_path), 'GITHUB_ENV': str(tmp_path / 'environment'),
        'GITHUB_OUTPUT': str(tmp_path / 'outputs'), 'GITHUB_WORKSPACE': str(REPO_ROOT),
        'GOOGLE_CLOUD_PROJECT': 'shared-datasets-1', 'REGION': 'us-central1',
        'CANARY_RUN_DATE': '', 'CANCEL_RUNNING_CANARY': 'false',
        **(extra or {}),
    }
    return subprocess.run(['bash', '-e', '-c', prefix + script], cwd=REPO_ROOT, env=env, capture_output=True, text=True)


@pytest.mark.parametrize('target', ['wdpa-monthly', 'mpatlas-monthly', 'eamlis-monthly', 'sea-ice-daily'])
def test_target_resolution_and_exact_plan_arguments(tmp_path, target):
    config = target_table()[target]
    assert config['workflow'] == TARGET_WORKFLOWS[target]
    if target != 'wdpa-monthly':
        assert set(config['targets']) == job_image_scope(target)
    resolved = run_shell(tmp_path, shared_step('prepare', 'Resolve registered ingestion target')['run'], target=target)
    assert resolved.returncode == 0, resolved.stderr
    assert json.loads((tmp_path / 'outputs').read_text().removeprefix('config=')) == config
    (tmp_path / 'scripts').mkdir()
    (tmp_path / 'scripts/terraform_retry.sh').write_text('python -c \'import json,sys; print(json.dumps(sys.argv[1:]))\' "$@"\n')
    image = 'registry/job@sha256:' + 'a' * 64
    result = run_shell(tmp_path, shared_step('apply', 'Terraform plan')['run'], target=target,
                       extra={'GITHUB_WORKSPACE': str(tmp_path), 'IMAGE': image, 'TESTED_IMAGE_CONFIG': 'sha256:' + 'b' * 64})
    assert result.returncode == 0, result.stderr
    args = json.loads(result.stdout)
    assert {a.removeprefix('-target=') for a in args if a.startswith('-target=')} == set(config['targets'])
    variables = dict(a.removeprefix('-var=').split('=', 1) for a in args if a.startswith('-var='))
    assert variables[config['image_variable']] == image
    assert set(t['image_variable'] for t in target_table().values()) <= variables.keys()
    assert ('wdpa_monthly_image_config_digest' in variables) == (target == 'wdpa-monthly')


def test_unregistered_target_fails_before_image_or_authentication(tmp_path):
    result = run_shell(tmp_path, shared_step('prepare', 'Resolve registered ingestion target')['run'],
                       extra={'TARGET': 'arbitrary-job', 'WORKFLOW_FILE': 'arbitrary-job-deploy.yml'})
    assert result.returncode != 0
    assert not (tmp_path / 'outputs').exists()


def test_immutable_verifier_restore_replaces_candidate_bytes_and_rejects_unknown_revision(tmp_path):
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=REPO_ROOT, text=True).strip()
    candidate = tmp_path / 'candidate'
    (candidate / 'scripts').mkdir(parents=True)
    (candidate / 'scripts/deployment_revision.py').write_text('raise RuntimeError("older candidate verifier")\n')
    script = shared_step('prepare', 'Restore immutable receipt verifier after candidate checkout')['run']
    result = run_shell(tmp_path, script, extra={'GITHUB_WORKSPACE': str(candidate), 'TRUSTED_WORKFLOW_SHA': revision})
    assert result.returncode == 0, result.stderr
    for filename in ('deployment_revision.py', 'deployment_emission.py', 'workflow_identity.py', 'github_api_read.py', 'install_deployment_verifier.py'):
        expected = subprocess.check_output(['git', 'show', f'{revision}:scripts/{filename}'], cwd=REPO_ROOT)
        assert (candidate / 'scripts' / filename).read_bytes() == expected
    expected = subprocess.check_output(['git', 'show', f'{revision}:.github/actions/deployment-receipt/action.yml'], cwd=REPO_ROOT)
    assert (candidate / '.github/actions/deployment-receipt/action.yml').read_bytes() == expected
    result = run_shell(tmp_path, script, extra={'GITHUB_WORKSPACE': str(candidate), 'TRUSTED_WORKFLOW_SHA': '0' * 40})
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
        sequence = job['steps']
        names = [s.get('name') for s in sequence]
        assert names.index('Verify executor before candidate checkout') < names.index('Check out repository')
        assert names.index('Verify tested main revision') < names.index('Authenticate to Google Cloud')
        auth = next(s for s in sequence if s.get('uses', '').startswith('google-github-actions/auth@'))
        assert auth['with'] == {'workload_identity_provider': '${{ env.TERRAFORM_WORKLOAD_IDENTITY_PROVIDER }}', 'service_account': '${{ env.TERRAFORM_SERVICE_ACCOUNT }}'}
    for target in target_table():
        wrapper = load_workflow(REPO_ROOT / f'.github/workflows/{target}-deploy.yml')
        job = wrapper['jobs']['deploy']
        assert job['uses'] == './.github/workflows/ingestion-deploy.yml'
        assert job['with']['target'] == target
        for key, requested in workflow['permissions'].items():
            assert wrapper['permissions'].get(key) == requested, 'a reusable workflow cannot elevate its caller token'
        triggers = wrapper['on']
        for key in ('executor_sha', 'source_run_id', 'source_run_attempt'):
            assert job['with'][key] == '${{ inputs.' + key + ' }}'
            assert all(triggers[event]['inputs'][key]['required'] for event in ('workflow_call', 'workflow_dispatch'))
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


@pytest.mark.parametrize('target', ['mpatlas-monthly', 'eamlis-monthly', 'sea-ice-daily'])
@pytest.mark.parametrize('defect', ['valid', 'image', 'foreign', 'delete', 'replace', 'create'])
def test_shared_allowlist_executes_real_guard_and_rejects_unauthorized_changes(tmp_path, target, defect):
    address = target_table()[target]['targets'][0]
    image = 'registry/job@sha256:' + 'a' * 64
    actions = {'delete': ['delete'], 'replace': ['delete', 'create'], 'create': ['create']}.get(defect, ['update'])
    plan = {'resource_changes': [{'address': address if defect != 'foreign' else 'module.foreign.google_cloud_run_v2_job.this', 'change': {'actions': actions, 'after': {'template': [{'template': [{'containers': [{'image': 'wrong' if defect == 'image' else image}]}]}]}}}]}
    (tmp_path / 'ingestion.tfplan.json').write_text(json.dumps(plan))
    result = run_shell(tmp_path, shared_step('apply', 'Enforce ingestion resource-change allowlist')['run'], target=target, extra={'IMAGE': image})
    assert (result.returncode == 0) == (defect == 'valid' or defect == 'create' and target == 'mpatlas-monthly'), result.stderr
