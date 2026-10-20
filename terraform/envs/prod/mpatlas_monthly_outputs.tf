output "mpatlas_monthly_job_name" {
  value = module.mpatlas_monthly_job.name
}

output "mpatlas_monthly_job_id" {
  value = module.mpatlas_monthly_job.id
}

output "mpatlas_monthly_scheduler_id" {
  value = module.mpatlas_monthly_scheduler.id
}

output "mpatlas_monthly_job_service_account" {
  value = module.mpatlas_job_service_account.email
}
