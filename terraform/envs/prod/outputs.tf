output "wdpa_monthly_job_name" {
  value = module.wdpa_monthly_job.name
}

output "wdpa_monthly_job_id" {
  value = module.wdpa_monthly_job.id
}

output "wdpa_monthly_scheduler_id" {
  value = module.wdpa_monthly_scheduler.id
}

output "wdpa_monthly_job_service_account" {
  value = module.wdpa_job_service_account.email
}

output "artifact_registry_repository" {
  value = google_artifact_registry_repository.jobs.name
}
