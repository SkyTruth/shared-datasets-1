# The Terraform service account could create the cron alert policies during
# initial setup but cannot update them today, so adding or editing a policy
# fails the sync with a 403. Grant only the alert-policy permissions the sync
# needs, following the same custom-role pattern as the other bootstrap grants.
resource "google_project_iam_custom_role" "monitoring_alert_policy_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsMonitoringAlertPolicyManager"
  title       = "Shared Datasets Monitoring Alert Policy Manager"
  description = "Allows approved GitHub Actions Terraform to manage shared-datasets Cloud Monitoring alert policies. (foundation baseline benchmark v1)"
  permissions = [
    "logging.logMetrics.create", "logging.logMetrics.get", "logging.logMetrics.list", "logging.logMetrics.update",
    # Updating a log-based policy replaces its internal notification rule.
    "logging.notificationRules.create",
    "logging.notificationRules.delete",
    "monitoring.alertPolicies.create",
    "monitoring.alertPolicies.delete",
    "monitoring.alerts.get",
    "monitoring.alertPolicies.get",
    "monitoring.alertPolicies.list",
    "monitoring.alertPolicies.update",
    "monitoring.notificationChannels.create",
    "monitoring.notificationChannels.update",
    "pubsub.topics.create",
    "pubsub.topics.get",
    "pubsub.topics.getIamPolicy",
    "pubsub.topics.setIamPolicy",
    "pubsub.subscriptions.create",
    "pubsub.subscriptions.get",
    "pubsub.subscriptions.update",
    "pubsub.topics.attachSubscription",
    "secretmanager.secrets.create",
    "secretmanager.secrets.get",
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

# Correlation reads the delivered incident and its exact execution's error logs.
resource "google_project_iam_custom_role" "monitoring_incident_reader" {
  project     = var.project_id
  role_id     = "sharedDatasetsMonitoringIncidentReader"
  title       = "Shared Datasets Monitoring Incident Reader"
  permissions = ["monitoring.alerts.get", "logging.logEntries.list"]
}

resource "google_project_iam_member" "monitoring_slack_incident_reader" {
  project = var.project_id
  role    = google_project_iam_custom_role.monitoring_incident_reader.name
  member  = "serviceAccount:${google_service_account.monitoring_slack.email}"
}
