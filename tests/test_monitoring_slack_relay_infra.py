"""Authority and safe-default contract for the independent relay's additive IaC."""
from pathlib import Path
import re
import unittest

from scripts import release_contracts


ROOT = Path(__file__).resolve().parents[1]
PROD = ROOT / "terraform/envs/prod"
TEXT = (PROD / "monitoring_slack_relay.tf").read_text()
VARIABLES = (PROD / "monitoring_slack_relay_variables.tf").read_text()


def block(kind, name):
    start = TEXT.index(f'{kind} "{name}"')
    end = re.search(r'\n(?:resource|module) ', TEXT[start + 1:])
    return TEXT[start:] if end is None else TEXT[start:start + 1 + end.start()]


class IndependentRelayInfrastructureTests(unittest.TestCase):
    def test_create_proposal_is_one_fixed_expiry_permission_on_terraform_only(self):
        self.assertEqual(release_contracts.role_permissions(TEXT, "monitoring_slack_secret_creator"), {"secretmanager.secrets.create"})
        value = block('resource "google_project_iam_member"', "github_actions_monitoring_slack_secret_creator")
        self.assertIn('member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"', value)
        self.assertIn("request.time < timestamp('2026-10-23T02:00:00Z')", value)
        self.assertNotIn("resource.name", value)
        self.assertNotIn("timestamp()", TEXT)
        self.assertIn("UNAPPROVED PROPOSAL", TEXT)

    def test_deployer_secret_scope_cannot_read_payload_or_manage_other_secrets(self):
        self.assertEqual(release_contracts.role_permissions(TEXT, "monitoring_slack_secret_manager"), {
            "secretmanager.secrets.get", "secretmanager.secrets.getIamPolicy",
            "secretmanager.secrets.setIamPolicy", "secretmanager.versions.add", "secretmanager.versions.list",
        })
        value = block('resource "google_project_iam_member"', "github_actions_monitoring_slack_secret_manager")
        for project in ("var.project_id", "data.google_project.current.number"):
            self.assertIn("resource.name == 'projects/${" + project + "}/secrets/${local.monitoring_slack_secret_id}'", value)
        self.assertNotIn("startsWith", value)
        self.assertNotIn("secretmanager.versions.access", TEXT)

    def test_runtime_accessor_is_created_only_after_numeric_version_is_supplied(self):
        value = block('resource "google_secret_manager_secret_iam_member"', "monitoring_slack_bot_token")
        self.assertIn('count     = var.monitoring_slack_relay == null ? 0 : 1', value)
        self.assertIn('role      = "roles/secretmanager.secretAccessor"', value)
        self.assertIn('member    = "serviceAccount:${local.monitoring_slack_runtime_email}"', value)
        for project in ("var.project_id", "data.google_project.current.number"):
            self.assertIn("resource.name == 'projects/${" + project + "}/secrets/${local.monitoring_slack_secret_id}/versions/${var.monitoring_slack_relay.secret_version}'", value)
        self.assertNotIn("latest", value)
        self.assertNotIn("startsWith", value)
        secret = block('resource "google_secret_manager_secret"', "monitoring_slack_bot_token")
        self.assertIn("prevent_destroy = true", secret)
        self.assertNotIn('resource "google_secret_manager_secret_version"', TEXT)
        for text in (TEXT, VARIABLES):
            self.assertNotIn("secret_data", text)
            self.assertNotIn("auth_token", text)
        self.assertIn('slack_secret_version     = "projects/${data.google_project.current.number}/secrets/', TEXT)

    def test_runtime_monitoring_reader_has_only_real_read_permissions(self):
        self.assertEqual(release_contracts.role_permissions(TEXT, "monitoring_slack_reader"), {
            "monitoring.alerts.get", "monitoring.alerts.list", "monitoring.alertPolicies.get", "monitoring.timeSeries.list",
        })
        value = block('resource "google_project_iam_member"', "monitoring_slack_reader")
        self.assertRegex(value, r'member\s*= "serviceAccount:\$\{local.monitoring_slack_runtime_email\}"')
        for permission in ("monitoring.timeSeries.create", "run.jobs.run", "storage.objects.list", "iam.serviceAccounts.getAccessToken"):
            self.assertNotIn(permission, TEXT)

    def test_state_is_private_and_authority_is_two_exact_objects(self):
        bucket = block('resource "google_storage_bucket"', "monitoring_slack_state")
        for required in ('uniform_bucket_level_access = true', 'public_access_prevention    = "enforced"',
                         'force_destroy               = false', 'versioning { enabled = true }', 'prevent_destroy = true'):
            self.assertIn(required, bucket)
        # GCS replacement requires both create and delete. The runtime uses CAS,
        # not DELETE: https://docs.cloud.google.com/storage/docs/access-control/iam-permissions
        self.assertEqual(release_contracts.role_permissions(TEXT, "monitoring_slack_state"), {
            "storage.objects.get", "storage.objects.create", "storage.objects.delete",
        })
        self.assertIn('"monitoring-slack/state-v1.json"', TEXT)
        self.assertIn('"monitoring-slack/capabilities-v1.json"', TEXT)
        value = block('resource "google_storage_bucket_iam_member"', "monitoring_slack_state")
        self.assertIn("bucket = google_storage_bucket.monitoring_slack_state.name", value)
        self.assertIn("resource.type == 'storage.googleapis.com/Object'", value)
        self.assertIn("for object in local.monitoring_slack_state_objects", value)
        self.assertIn("resource.name == 'projects/_/buckets/${local.monitoring_slack_state_bucket_name}/objects/${object}'", value)
        self.assertNotIn("startsWith", value)
        for broad in ("roles/storage.objectUser", "roles/storage.admin", "allUsers", "var.bucket_name\n"):
            self.assertNotIn(broad, value)

    def test_job_is_absent_by_default_and_shadow_when_deployed(self):
        self.assertIn("default = null", VARIABLES)
        self.assertIn('mode                     = "shadow"', TEXT)
        value = block("module", "monitoring_slack_job")
        for required in ('count                 = var.monitoring_slack_relay == null ? 0 : 1',
                         'command               = ["python", "-m", "services.monitoring_slack.run"]',
                         'max_retries           = 0', 'timeout               = "240s"',
                         'env                   = { MONITORING_SLACK_CONFIG = jsonencode(local.monitoring_slack_config) }'):
            self.assertIn(required, value)
        self.assertNotIn("secret_env", value)
        self.assertNotIn("bootstrap-required", TEXT + VARIABLES)
        self.assertIn("@sha256:[0-9a-f]{64}$", VARIABLES)
        self.assertIn('^[1-9][0-9]*$', VARIABLES)

    def test_scheduler_is_paused_and_can_only_invoke_the_exact_job(self):
        value = block("module", "monitoring_slack_scheduler")
        for required in ('paused                = true', 'retry_count           = 0', 'schedule              = "*/5 * * * *"',
                         'target_job_name       = module.monitoring_slack_job[0].name',
                         'service_account_email = local.monitoring_slack_scheduler_email'):
            self.assertIn(required, value)
        invoker = block('resource "google_cloud_run_v2_job_iam_member"', "monitoring_slack_scheduler")
        self.assertRegex(invoker, r'role\s*= "roles/run.invoker"')
        self.assertRegex(invoker, r'name\s*= module.monitoring_slack_job\[0\].name')
        self.assertRegex(invoker, r'member\s*= "serviceAccount:\$\{local.monitoring_slack_scheduler_email\}"')
        for name in ("monitoring_slack_deployer", "monitoring_slack_scheduler_deployer"):
            value = block('resource "google_service_account_iam_member"', name)
            self.assertIn('role               = "roles/iam.serviceAccountUser"', value)
            self.assertIn('member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"', value)

    def test_additive_preparation_has_no_route_or_current_protocol_mutation(self):
        for resource in ("google_monitoring_alert_policy", "google_monitoring_notification_channel", "google_logging_log_view",
                         "google_iam_workload_identity_pool_provider", "google_pubsub_topic", "google_pubsub_subscription"):
            self.assertNotIn('resource "' + resource + '"', TEXT)
        for keyword in ("notification_channels", "operational_observer", "shared_bucket", "github_workload_identity"):
            self.assertNotIn(keyword, TEXT)
        for identity in ("monitoring-slack-relay", "monitoring-slack-scheduler"):
            self.assertIn('account_id   = "' + identity + '"', TEXT)
        # Avoid graph edges into existing policy/runtime resources. Protected
        # deployment verifies metadata against the checked-in three-policy set.
        self.assertNotIn("google_monitoring_alert_policy.", TEXT)
        self.assertIn('schema  = "monitoring-slack-config-v1"', TEXT)
        self.assertEqual(TEXT.count('kind = "health"'), 1)
        self.assertEqual(TEXT.count('kind = "security"'), 2)


if __name__ == "__main__":
    unittest.main()
