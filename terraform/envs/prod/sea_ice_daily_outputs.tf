output "sea_ice_daily_job_name" {
  value = module.sea_ice_daily_job.name
}

output "sea_ice_daily_job_id" {
  value = module.sea_ice_daily_job.id
}

output "sea_ice_daily_scheduler_id" {
  value = module.sea_ice_daily_scheduler.id
}

output "sea_ice_daily_job_service_account" {
  value = module.sea_ice_job_service_account.email
}
