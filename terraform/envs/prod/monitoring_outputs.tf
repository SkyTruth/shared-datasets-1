output "cron_alert_policy_names" {
  value = [
    google_monitoring_alert_policy.scheduled_ingestion_cloud_run_failure.name,
    google_monitoring_alert_policy.scheduled_ingestion_scheduler_failure.name,
  ]
}
