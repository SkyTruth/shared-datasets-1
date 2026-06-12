output "cron_alert_policy_names" {
  value = [
    google_monitoring_alert_policy.scheduled_ingestion_cloud_run_failure.name,
    google_monitoring_alert_policy.scheduled_ingestion_scheduler_failure.name,
  ]
}

output "monitoring_alert_policy_names" {
  value = [
    google_monitoring_alert_policy.scheduled_ingestion_cloud_run_failure.name,
    google_monitoring_alert_policy.scheduled_ingestion_scheduler_failure.name,
    google_monitoring_alert_policy.metadata_service_error_logs.name,
    google_monitoring_alert_policy.dataset_object_deleted.name,
    google_monitoring_alert_policy.dataset_object_written_by_unapproved_principal.name,
  ]
}

output "slack_webhook_secret_id" {
  value = google_secret_manager_secret.slack_webhook_url.id
}
