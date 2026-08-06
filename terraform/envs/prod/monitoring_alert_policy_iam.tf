# The Terraform service account could create the cron alert policies during
# initial setup but cannot update them today, so adding or editing a policy
# fails the sync with a 403. Grant only the alert-policy permissions the sync
# needs, following the same custom-role pattern as the other bootstrap grants.
resource "google_project_iam_custom_role" "monitoring_alert_policy_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsMonitoringAlertPolicyManager"
  title       = "Shared Datasets Monitoring Alert Policy Manager"
  description = "Allows approved GitHub Actions Terraform to manage shared-datasets Cloud Monitoring alert policies."
  permissions = [
    "monitoring.alertPolicies.create",
    "monitoring.alertPolicies.delete",
    "monitoring.alertPolicies.get",
    "monitoring.alertPolicies.list",
    "monitoring.alertPolicies.update",
    "monitoring.notificationChannels.get",
    "monitoring.notificationChannels.list",
  ]

  depends_on = [google_project_service.required]
}

resource "google_project_iam_member" "github_actions_monitoring_alert_policy_manager" {
  project = var.project_id
  role    = google_project_iam_custom_role.monitoring_alert_policy_manager.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}
