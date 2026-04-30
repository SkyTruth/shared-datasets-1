output "eamlis_monthly_job_name" {
  value = module.eamlis_monthly_job.name
}

output "eamlis_monthly_job_id" {
  value = module.eamlis_monthly_job.id
}

output "eamlis_monthly_scheduler_id" {
  value = module.eamlis_monthly_scheduler.id
}

output "eamlis_monthly_job_service_account" {
  value = module.eamlis_job_service_account.email
}
