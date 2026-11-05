from __future__ import annotations

import unittest
import json
import os
import subprocess
import sys

import pytest
import yaml
from scripts.terraform_target_contracts import registry
from pathlib import Path

from test_ingestion_deploy_workflow import shared_workflow
from scripts.deployment_receipt_contracts import expanded_steps
from workflow_helpers import (
    assert_target_apply_caller,
    load_workflow,
    workflow_steps_by_name,
    workflow_triggers,
    terraform_environment,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REUSABLE = REPO_ROOT / ".github/workflows/prod-terraform-target-apply.yml"
ARTIFACT_REGISTRY = REPO_ROOT / ".github/workflows/artifact-registry-iam-sync.yml"
PREVIEW_TERRAFORM = REPO_ROOT / ".github/workflows/preview-terraform-iam-sync.yml"
SCHEDULED_INGESTION = REPO_ROOT / ".github/workflows/scheduled-ingestion-deploy-iam-sync.yml"
SCRATCH_CLEANUP = REPO_ROOT / ".github/workflows/scratch-cleanup-iam-sync.yml"
MAIN_DEPLOYMENT_EVENT = "${{ github.ref == 'refs/heads/main' && (github.event_name == 'push' || github.event_name == 'workflow_dispatch') }}"



def shared_steps():
    return workflow_steps_by_name(load_workflow(REUSABLE), 'sync')


class ReusableTargetApplyWorkflowTests(unittest.TestCase):
    def test_only_callable_and_protected(self):
        workflow = load_workflow(REUSABLE)
        self.assertEqual(list(workflow_triggers(workflow)), ['workflow_call'])
        job = workflow['jobs']['sync']
        self.assertEqual(job['environment'], 'shared-datasets-production')
        self.assertEqual(job['concurrency'], {'group': 'prod-terraform-state', 'queue': 'max', 'cancel-in-progress': False})
        self.assertEqual(workflow['env']['TERRAFORM_DIR'], 'terraform/envs/prod')
        self.assertEqual(set(workflow_triggers(workflow)['workflow_call']['inputs']), {
            'caller_workflow', 'executor_sha', 'source_run_id', 'source_run_attempt', 'sync_name', 'tf_vars',
        })
        usage = load_workflow(REPO_ROOT / '.github/workflows/dataset-usage-deploy.yml')
        raw = yaml.safe_load((REPO_ROOT / '.github/workflows/dataset-usage-deploy.yml').read_text())
        self.assertEqual(set(raw['jobs']['bootstrap']['with']), {
            'caller_workflow', 'executor_sha', 'source_run_id', 'source_run_attempt', 'sync_name',
        })
        self.assertEqual(set(usage['jobs']['bootstrap']['with']['allowed_exact'].splitlines()),
                         set(registry()['Dataset usage deploy IAM bootstrap']['resources']))
        propagation = workflow['jobs']['propagation']
        self.assertNotIn('concurrency', propagation)
        self.assertNotIn('environment', propagation)
        self.assertEqual(propagation['permissions'], {})
        self.assertEqual([name for name, job in workflow['jobs'].items() if 'concurrency' in job], ['sync'])
        self.assertEqual(job['needs'], 'prepare')
        self.assertEqual(job['if'], "needs.prepare.outputs.proceed == 'true'")
        self.assertNotIn('concurrency', workflow['jobs']['prepare'])
        self.assertIn('Prepare tested Monitoring image and bot credential',
                      workflow_steps_by_name(workflow, 'prepare'))
        self.assertNotIn('Prepare tested Monitoring image and bot credential', shared_steps())


@pytest.mark.parametrize('sync_name,item', list(registry().items()))
def test_target_contract_reaches_actual_plan_and_allowlist_without_scope_drift(tmp_path, sync_name, item):
    inputs = {'sync_name': sync_name, 'caller_workflow': item['workflow']}
    environment = terraform_environment(tmp_path, inputs)
    fake = tmp_path / 'terraform'
    arguments = tmp_path / 'arguments.json'
    fake.write_text(f'#!{sys.executable}\nimport json,sys\nfrom pathlib import Path\n'
                    f'Path({str(arguments)!r}).write_text(json.dumps(sys.argv[1:]))\n')
    fake.chmod(0o755)
    environment.update(TERRAFORM_BIN=str(fake), GITHUB_WORKSPACE=str(REPO_ROOT),
                       TERRAFORM_DIR='terraform/envs/prod', RUNNER_TEMP=str(tmp_path),
                       PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'])
    result = subprocess.run(['bash', '-c', shared_steps()['Terraform plan']['run']],
                            env=environment, cwd=REPO_ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    values = json.loads(arguments.read_text())
    assert [v.removeprefix('-target=') for v in values if v.startswith('-target=')] == item['resources']
    assert '-refresh=true' in values
    assert [v.removeprefix('-var=') for v in values if v.startswith('-var=')] == environment['TF_VARS'].splitlines()
    plan_path = tmp_path / 'target-apply.tfplan.json'
    own_actions = ['delete'] if item.get('delete_only') else ['create']
    for address, accepted in [(r, True) for r in item['resources']] + [('google_storage_bucket.unowned', False)]:
        plan_path.write_text(json.dumps({'resource_changes': [{'address': address, 'change': {'actions': own_actions}}]}))
        guarded = subprocess.run(['bash', '-c', shared_steps()['Enforce resource-change allowlist']['run']],
                                 env=environment, cwd=REPO_ROOT, text=True, capture_output=True)
        assert (guarded.returncode == 0) is accepted, guarded.stdout + guarded.stderr


@pytest.mark.parametrize('sync,workflow', [('unknown', 'artifact-registry-iam-sync.yml'),
                                         ('Preview Terraform IAM sync', 'artifact-registry-iam-sync.yml')])
def test_unregistered_or_foreign_target_fails_before_environment_transport(tmp_path, sync, workflow):
    destination = tmp_path / 'env'
    result = subprocess.run([sys.executable, str(REPO_ROOT / 'scripts/terraform_target_contracts.py'),
                             '--sync-name', sync, '--workflow', workflow],
                            env={**os.environ, 'GITHUB_ENV': str(destination)}, text=True, capture_output=True)
    assert result.returncode != 0
    assert not destination.exists()


@pytest.mark.parametrize('mutation', ['none', 'empty', 'missing', 'extra', 'newline'])
def test_monitoring_job_output_transport_preserves_complete_immutable_identities(tmp_path, mutation):
    values = {'monitoring_slack_image': 'example.invalid/image@sha256:' + 'a' * 64,
              'monitoring_slack_channel_id': 'C12345678', 'monitoring_slack_token_version': '12'}
    if mutation == 'empty':
        values = {}
    elif mutation == 'missing':
        del values['monitoring_slack_image']
    elif mutation == 'extra':
        values['unexpected'] = 'forbidden'
    elif mutation == 'newline':
        values['monitoring_slack_token_version'] = '12\nTF_VAR_injected=true'
    destination = tmp_path / 'env'
    script = shared_steps()['Load prepared Monitoring variables']['run']
    result = subprocess.run(['bash', '-c', script], cwd=REPO_ROOT, text=True, capture_output=True,
                            env={**os.environ, 'MONITORING_VARS': json.dumps(values), 'GITHUB_ENV': str(destination),
                                 'PATH': str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']})
    assert (result.returncode == 0) is (mutation == 'none'), result.stderr
    if mutation == 'none':
        assert dict(line.removeprefix('TF_VAR_').split('=', 1) for line in destination.read_text().splitlines()) == values


def test_caller_scope_environment_cannot_override_the_registered_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv('ALLOWED_EXACT', 'google_storage_bucket.unowned')
    inputs = {'sync_name': 'Preview Terraform IAM sync', 'caller_workflow': 'preview-terraform-iam-sync.yml'}
    environment = terraform_environment(tmp_path, inputs)
    assert 'google_storage_bucket.unowned' not in environment['ALLOWED_EXACT']


class TargetApplyCallerTests(unittest.TestCase):
    def test_scheduled_ingestion_deploy_iam_sync_caller(self):
        assert_target_apply_caller(
            self,
            SCHEDULED_INGESTION,
            expected_name="Infrastructure / Ingestion deploy IAM sync",
            push_paths=None,
            sync_name="Scheduled ingestion deploy IAM sync",
            expected_needs="bootstrap",
            expected_job_if=MAIN_DEPLOYMENT_EVENT,
            refusal_prefix="Refusing automatic scheduled ingestion deploy IAM sync",
            expected_targets={
                "google_project_iam_custom_role.scheduled_ingestion_deployer",
                "google_project_iam_member.github_actions_scheduled_ingestion_deployer",
                "module.wdpa_builder_service_account.google_service_account.this",
                "google_project_iam_custom_role.wdpa_monthly_build_stager",
                "google_storage_bucket_iam_member.wdpa_monthly_build_stager",
                "google_storage_bucket_iam_member.wdpa_automatic_build_stager",
                "google_storage_bucket_iam_member.wdpa_monthly_build_inputs",
                "google_storage_bucket_iam_member.wdpa_monthly_build_evidence_reader",
                "google_service_account_iam_member.wdpa_builder_deployer",
                "google_storage_bucket_iam_member.wdpa_reset_translation_reader",
                'google_secret_manager_secret_iam_member.translation_notice["wdpa"]',
                'google_secret_manager_secret_iam_member.translation_notice["eamlis"]',
                'google_storage_bucket_iam_member.translation_debt_writer["wdpa"]',
                'google_storage_bucket_iam_member.translation_debt_writer["eamlis"]',
                "google_storage_bucket_iam_member.publisher_translation_debt_writer",
                "google_storage_bucket_iam_member.eamlis_job_object_user",
                "google_cloud_run_v2_job_iam_member.eamlis_quality_execution_reader",
                "module.mpatlas_job_service_account.google_service_account.this",
                "module.mpatlas_scheduler_service_account.google_service_account.this",
                "google_storage_bucket_iam_member.mpatlas_job_object_user",
                'google_service_account_iam_member.mpatlas_deployer_act_as["job"]',
                'google_service_account_iam_member.mpatlas_deployer_act_as["scheduler"]',
            },
            expected_tf_vars={
                "wdpa_monthly_image=unused-by-scheduled-ingestion-deploy-iam-sync",
                "sea_ice_daily_image=unused-by-scheduled-ingestion-deploy-iam-sync",
                "eamlis_monthly_image=unused-by-scheduled-ingestion-deploy-iam-sync",
            },
        )

    def test_translation_notice_bootstrap_precedes_secret_reads_and_job_deploys(self):
        workflow = load_workflow(SCHEDULED_INGESTION)
        self.assertIn("workflow_call", workflow_triggers(workflow))
        bootstrap = workflow["jobs"]["bootstrap"]
        self.assertNotIn("needs", bootstrap)
        self.assertNotIn("concurrency", bootstrap)
        self.assertEqual(bootstrap["uses"], "./.github/workflows/prod-terraform-target-apply.yml")
        inputs = bootstrap["with"]
        expected = {
            "google_project_iam_custom_role.translation_notice_iam_manager",
            "google_project_iam_member.github_actions_translation_notice_iam_manager",
            "google_project_iam_custom_role.eamlis_quality_iam_manager",
            "google_project_iam_member.github_actions_eamlis_quality_iam_manager",
        }
        self.assertEqual(set(inputs["targets"].split()), expected)
        self.assertEqual(set(inputs["allowed_exact"].split()), expected)
        self.assertEqual(inputs["post_apply_wait_seconds"], 30)
        for name in ("eamlis-monthly-deploy.yml", "wdpa-monthly-deploy.yml"):
            with self.subTest(workflow=name):
                jobs = load_workflow(REPO_ROOT / ".github/workflows" / name)["jobs"]
                self.assertNotIn("iam", jobs)
                self.assertEqual(jobs["deploy"]["uses"], "./.github/workflows/ingestion-deploy.yml")
                steps = {step.get("name"): step for step in expanded_steps(REPO_ROOT, shared_workflow()["jobs"]["prepare"])}
                self.assertIn("Verify live deployment permissions", steps)
                self.assertIn("Verify tested main revision", steps)


    def test_preview_terraform_iam_sync_caller_blocks_deletes(self):
        assert_target_apply_caller(
            self,
            PREVIEW_TERRAFORM,
            expected_name="Infrastructure / Preview Terraform IAM sync",
            push_paths=None,
            sync_name="Preview Terraform IAM sync",
            refusal_prefix="Refusing automatic preview Terraform IAM sync",
            expected_block_deletes=True,
            expected_needs="bootstrap",
            expected_job_if=MAIN_DEPLOYMENT_EVENT,
            expected_targets={
                "module.feature_preview_service_account.google_service_account.this",
                "module.feature_preview_loader_service_account.google_service_account.this",
                "google_service_account_iam_member.feature_preview_loader_github_wif",
                "google_service_account_iam_member.feature_preview_service_self_sign_blob",
            },
            expected_tf_vars={
                "wdpa_monthly_image=unused-by-preview-terraform-iam-sync",
                "sea_ice_daily_image=unused-by-preview-terraform-iam-sync",
                "eamlis_monthly_image=unused-by-preview-terraform-iam-sync",
            },
        )

    def test_preview_role_bootstrap_retains_existing_narrow_authority(self):
        workflow = load_workflow(PREVIEW_TERRAFORM)
        bootstrap = workflow["jobs"]["bootstrap"]["with"]
        self.assertEqual(set(bootstrap["targets"].split()), {
            "google_project_iam_custom_role.preview_terraform", "google_project_iam_member.github_actions_preview_terraform",
        })
        self.assertEqual(bootstrap["targets"], bootstrap["allowed_exact"])
        self.assertTrue(bootstrap["block_deletes"])
        self.assertEqual(bootstrap["readiness_target"], "iam-bootstrap")
        self.assertEqual(workflow["jobs"]["sync"]["with"]["readiness_target"], "preview-service-account-iam")

    def test_scratch_cleanup_iam_sync_caller_uses_full_prod_root(self):
        assert_target_apply_caller(
            self,
            SCRATCH_CLEANUP,
            expected_name="Infrastructure / Scratch cleanup IAM sync",
            push_paths=None,
            sync_name="Scratch cleanup IAM sync",
            expected_job_if=MAIN_DEPLOYMENT_EVENT,
            refusal_prefix="Refusing automatic scratch cleanup IAM sync",
            expected_targets={
                "google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_viewer",
                "google_storage_bucket_iam_member.shared_datasets_publisher_pending_publish_cleanup_user",
            },
            blocked_resources={
                "google_compute_url_map.pmtiles_cdn",
                "google_storage_bucket.shared_bucket",
                "google_storage_managed_folder.shared_bucket_public_prefixes",
            },
            expected_tf_vars={
                "wdpa_monthly_image=unused-by-scratch-cleanup-iam-sync",
                "sea_ice_daily_image=unused-by-scratch-cleanup-iam-sync",
                "eamlis_monthly_image=unused-by-scratch-cleanup-iam-sync",
            },
        )

    def test_artifact_registry_iam_sync_runs_bootstrap_then_writer(self):
        tf_vars = {
            "wdpa_monthly_image=unused-by-artifact-registry-iam-sync",
            "sea_ice_daily_image=unused-by-artifact-registry-iam-sync",
            "eamlis_monthly_image=unused-by-artifact-registry-iam-sync",
        }
        assert_target_apply_caller(
            self,
            ARTIFACT_REGISTRY,
            expected_name="Infrastructure / Artifact Registry IAM sync",
            push_paths=None,
            job_name="bootstrap",
            expected_job_if=MAIN_DEPLOYMENT_EVENT,
            sync_name="Artifact Registry IAM sync",
            refusal_prefix="Refusing automatic Artifact Registry IAM bootstrap",
            expected_post_apply_wait_seconds=30,
            expected_targets={
                "google_project_iam_custom_role.artifact_registry_iam_policy_manager",
                "google_project_iam_member.github_actions_artifact_registry_iam_policy_manager",
            },
            expected_tf_vars=tf_vars,
        )
        assert_target_apply_caller(
            self,
            ARTIFACT_REGISTRY,
            expected_name="Infrastructure / Artifact Registry IAM sync",
            push_paths=None,
            job_name="writer",
            expected_job_if=MAIN_DEPLOYMENT_EVENT,
            expected_needs="bootstrap",
            sync_name="Artifact Registry writer binding sync",
            refusal_prefix="Refusing automatic Artifact Registry IAM writer sync",
            expected_targets={
                "google_artifact_registry_repository_iam_member.github_actions_artifact_registry_writer",
            },
            expected_tf_vars=tf_vars,
            blocked_resources={"roles/artifactregistry.admin"},
        )


if __name__ == "__main__":
    unittest.main()
