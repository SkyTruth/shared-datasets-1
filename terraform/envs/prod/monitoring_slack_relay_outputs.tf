output "monitoring_slack_secret_name" {
  description = "Metadata-only dedicated secret. Versions are seeded outside Terraform through the protected workflow."
  value       = google_secret_manager_secret.monitoring_slack_bot_token.id
}

output "monitoring_slack_service_account" {
  value = module.monitoring_slack_service_account.email
}

output "monitoring_slack_scheduler_service_account" {
  value = module.monitoring_slack_scheduler_service_account.email
}

output "monitoring_slack_state_bucket" {
  value = google_storage_bucket.monitoring_slack_state.name
}

output "monitoring_slack_job_name" {
  value = var.monitoring_slack_relay == null ? null : module.monitoring_slack_job[0].name
}
