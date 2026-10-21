from pathlib import Path
import re
import unittest

import yaml

from scripts import ci_contract, deployment_permissions, release_contracts
from scripts.terraform_plan_permissions import PROJECT, PROJECT_URL, plan_checks
from test_terraform_plan_permissions import plan, row
from workflow_helpers import assert_target_apply_caller

ROOT = Path(__file__).resolve().parents[1]
TERRAFORM = ROOT / "terraform/envs/prod/operational_observer.tf"


class ObserverBoundaryTests(unittest.TestCase):
    def test_observer_identity_has_exact_read_and_heartbeat_authority(self):
        text = TERRAFORM.read_text()
        self.assertEqual(release_contracts.role_permissions(text, "operational_observer_reader"), {"run.executions.get", "run.executions.list", "monitoring.timeSeries.create"})
        self.assertEqual(release_contracts.role_permissions(text, "operational_observer_receipts"), {"storage.objects.get"})
        self.assertIn("roles/logging.viewAccessor", text)
        for broad in ("roles/logging.viewer", "roles/editor", "roles/owner", "roles/storage.objectViewer", "run.jobs.run", "storage.objects.create"):
            self.assertNotIn(broad, text)
        self.assertIn("resource.type == 'storage.googleapis.com/Object'", text)
        self.assertIn("/runs/') && resource.name.endsWith('.json')", text)
        self.assertIn("/runs/", text)
        self.assertIn("local.shared_catalog_rows", text)

    def test_unique_wif_attribute_prevents_other_pool_providers_granting_observer_access(self):
        text = TERRAFORM.read_text()
        self.assertIn("assertion.ref == 'refs/heads/main'", text)
        self.assertIn("/.github/workflows/unattended-workflow-alert.yml@refs/heads/main", text)
        self.assertIn("/attribute.observer_repository/", text)
        owners = [path.name for path in TERRAFORM.parent.glob("*.tf") if '"attribute.observer_repository"' in path.read_text()]
        self.assertEqual(owners, [TERRAFORM.name])

    def test_views_cover_new_regional_jobs_without_a_job_name_allowlist(self):
        text = TERRAFORM.read_text().split('# No project-wide', 1)[0]
        self.assertIn('toset(["_Default", "_Required"])', text)
        self.assertIn('resource.type="cloud_run_job"', text)
        self.assertIn('resource.type="cloud_scheduler_job"', text)
        self.assertIn('resource.labels.project_id="${var.project_id}"', text)
        self.assertIn('resource.labels.location="${var.region}"', text)
        self.assertNotIn("job_name", text)
        self.assertNotIn("jsonPayload", text)
        self.assertNotIn("protoPayload", text)
        self.assertNotIn("severity", text)
        for log_id in ("run.googleapis.com/stderr", "cloudaudit.googleapis.com/activity", "cloudaudit.googleapis.com/data_access", "cloudscheduler.googleapis.com/executions"):
            self.assertIn(f'log_id("{log_id}")', text)
        self.assertNotIn('log_id("run.googleapis.com/stdout")', text)

    def test_health_is_independent_30_minute_absence_and_preserves_native_routes(self):
        text = TERRAFORM.read_text()
        self.assertIn('duration = "1800s"', text)
        self.assertIn("condition_absent", text)
        self.assertIn("notification_channels = local.cron_alert_notification_channels", text)
        self.assertIn("Seed and verify a heartbeat after policy creation", text)
        monitoring = (TERRAFORM.parent / "monitoring.tf").read_text()
        for policy in ("scheduled_ingestion_cloud_run_failure", "scheduled_ingestion_scheduler_failure", "release_awaiting_identity_decision", "dataset_object_deleted", "dataset_object_written_by_unapproved_principal"):
            body = monitoring.split(f'"{policy}"', 1)[1].split('\nresource ', 1)[0]
            self.assertIn("notification_channels = local.cron_alert_notification_channels", body)

    def test_observation_setup_uses_protected_exact_saved_plan_boundary(self):
        targets = {
            "google_iam_workload_identity_pool_provider.operational_observer",
            "google_project_iam_audit_config.operational_run_data_write",
            "module.operational_observer_service_account.google_service_account.this",
            "google_service_account_iam_member.operational_observer_wif",
            'google_logging_log_view.operational_observations["_Default"]',
            'google_logging_log_view.operational_observations["_Required"]',
            'google_logging_log_view_iam_member.operational_observer["_Default"]',
            'google_logging_log_view_iam_member.operational_observer["_Required"]',
            "google_project_iam_custom_role.operational_observer_reader",
            "google_project_iam_member.operational_observer_reader",
            "google_project_iam_custom_role.operational_observer_receipts",
            *(f'google_storage_bucket_iam_member.operational_observer_{surface}["{index}"]' for surface in ("receipts", "manifests") for index in range(5)),
            "google_monitoring_metric_descriptor.operational_reconciliation_heartbeat",
        }
        assert_target_apply_caller(self, ROOT / ".github/workflows/cron-alert-policy-sync.yml", expected_name="Cron alert policy sync", job_name="observations", push_paths=None,
            expected_job_if="${{ github.ref == 'refs/heads/main' && (github.event_name == 'push' || github.event_name == 'workflow_dispatch') }}", expected_needs="bootstrap",
            sync_name="Operational observation setup", refusal_prefix="Refusing automatic operational observation setup", expected_targets=targets, expected_block_deletes=True,
            blocked_resources={"google_storage_bucket.shared_bucket", "google_cloud_run_v2_job.this"})
        self.assertIn("cron_alert_policy", ci_contract.select_deployments(["terraform/envs/prod/operational_observer.tf"]))

    def test_storage_read_scope_is_exact_paired_manifest_and_receipt_chunks(self):
        text = TERRAFORM.read_text()
        caller = yaml.safe_load((ROOT / ".github/workflows/cron-alert-policy-sync.yml").read_text())["jobs"]["observations"]["with"]
        self.assertEqual(release_contracts.operational_storage_contract(ROOT, text, caller), [])
        for original, changed in (("/releases/') && resource.name.endsWith('/${scope.slug}.manifest.json')", "/releases/')"),
                                  ("/runs/') && resource.name.endsWith('.json')", "/runs/')"),
                                  ("resource.type == 'storage.googleapis.com/Object'", "true"),
                                  ("], 6) : tostring(index)", "], 7) : tostring(index)"),
                                  ("root = split", "root = dirname")):
            with self.subTest(changed=changed):
                self.assertNotEqual(original, changed)
                self.assertIn(original, text)
                self.assertTrue(release_contracts.operational_storage_contract(ROOT, text.replace(original, changed), caller))
        incomplete = {**caller, "targets": caller["targets"].replace('        ', '').replace('google_storage_bucket_iam_member.operational_observer_manifests["4"]', '')}
        self.assertTrue(any("every exact" in item for item in release_contracts.operational_storage_contract(ROOT, text, incomplete)))

    def test_saved_manifest_binding_requires_only_existing_bucket_policy_authority(self):
        value = {"bucket": "skytruth-shared-datasets-1", "role": "projects/shared-datasets-1/roles/sharedDatasetsOperationalReceiptReader",
                 "member": "serviceAccount:shared-datasets-alert-observer@shared-datasets-1.iam.gserviceaccount.com",
                 "condition": [{"title": "registered-asset-release-manifests-0", "expression": "resource.type == 'storage.googleapis.com/Object'"}]}
        actual = dict(plan_checks(plan(row("google_storage_bucket_iam_member", ["create"], value,
                                          address='google_storage_bucket_iam_member.operational_observer_manifests["0"]')), project_number="123456789"))
        self.assertEqual(len(actual), 1)
        url, required = next(iter(actual.items()))
        self.assertTrue(url.startswith("https://storage.googleapis.com/storage/v1/b/skytruth-shared-datasets-1/iam/testPermissions?"))
        self.assertEqual(set(required), {"storage.buckets.getIamPolicy", "storage.buckets.setIamPolicy"})

    def test_exact_new_resource_permission_probes_reject_foreign_boundaries(self):
        view = {"parent": f"projects/{PROJECT}", "location": "global", "bucket": "_Default", "name": "shared-datasets-operations"}
        metric = {"project": PROJECT, "type": "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat"}
        provider = {"project": PROJECT, "workload_identity_pool_id": "github", "workload_identity_pool_provider_id": "github-operational-observer"}
        actual = dict(plan_checks(plan(row("google_logging_log_view", ["create"], view), row("google_monitoring_metric_descriptor", ["create"], metric), row("google_iam_workload_identity_pool_provider", ["create"], provider)), project_number="123456789"))
        self.assertEqual(set(actual[f"https://logging.googleapis.com/v2/projects/{PROJECT}/locations/global/buckets/_Default/views/_AllLogs:testIamPermissions"]), {"logging.views.create", "logging.views.get"})
        self.assertEqual(set(actual[PROJECT_URL]), {"monitoring.metricDescriptors.create", "monitoring.metricDescriptors.get"})
        self.assertIn("iam.workloadIdentityPoolProviders.create", actual[f"https://iam.googleapis.com/v1/projects/{PROJECT}/locations/global/workloadIdentityPools/github:testIamPermissions"])
        iam = dict(plan_checks(plan(row("google_logging_log_view_iam_member", ["update"], view)), project_number="123456789"))
        self.assertEqual(set(next(iter(iam.values()))), {"logging.views.getIamPolicy", "logging.views.setIamPolicy"})
        existing = dict(plan_checks(plan(row("google_logging_log_view_iam_member", ["create"], view)), project_number="123456789"))
        self.assertTrue(next(iter(existing)).endswith("/views/shared-datasets-operations:testIamPermissions"))
        for kind, value in (("google_logging_log_view", {**view, "bucket": "unrelated"}), ("google_logging_log_view_iam_member", {**view, "parent": "projects/foreign"}), ("google_monitoring_metric_descriptor", {**metric, "type": "unrelated"}), ("google_iam_workload_identity_pool_provider", {**provider, "workload_identity_pool_provider_id": "publisher"})):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                plan_checks(plan(row(kind, ["create"], value)), project_number="123456789")

    def test_setup_grants_do_not_add_iam_or_delete_authority(self):
        text = TERRAFORM.read_text()
        permissions = release_contracts.role_permissions(text, "operational_log_view_manager") | release_contracts.role_permissions(text, "operational_metric_descriptor_manager")
        self.assertFalse(any(value.startswith(("iam.", "resourcemanager.", "storage.")) or value.endswith(".delete") for value in permissions))
        self.assertIn("/views/", text)
        checks = deployment_permissions.checks("operational-observer-setup")
        self.assertTrue(any("workloadIdentityPools/github:testIamPermissions" in url for url, _ in checks))
        caller = yaml.safe_load((ROOT / ".github/workflows/cron-alert-policy-sync.yml").read_text())
        self.assertEqual(caller["jobs"]["sync"]["needs"], ["bootstrap", "observations"])
        self.assertTrue(re.search(r'condition\s*\{[^}]*operational-view-buckets-only', text))

    def test_run_data_write_capture_is_protected_and_unexempted(self):
        text = TERRAFORM.read_text()
        body = text.split('resource "google_project_iam_audit_config" "operational_run_data_write"', 1)[1].split('\nresource ', 1)[0]
        self.assertIn('service = "run.googleapis.com"', body)
        self.assertIn('log_type = "DATA_WRITE"', body)
        self.assertNotIn("exempted_members", body)
        checks = dict(plan_checks(plan(row("google_project_iam_audit_config", ["create"], {"project": PROJECT, "service": "run.googleapis.com", "audit_log_config": [{"log_type": "DATA_WRITE"}]})), project_number="123456789"))
        self.assertEqual(set(checks[PROJECT_URL]), {"resourcemanager.projects.getIamPolicy", "resourcemanager.projects.setIamPolicy"})
