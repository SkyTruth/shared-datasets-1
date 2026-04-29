module "sea_ice_job_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "sea-ice-daily-job"
  display_name = "Daily IMS sea-ice Cloud Run Job"

  depends_on = [google_project_service.required]
}

module "sea_ice_scheduler_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "sea-ice-daily-scheduler"
  display_name = "Daily IMS sea-ice Cloud Scheduler invoker"

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "sea_ice_job_object_user" {
  bucket = var.bucket_name
  role   = "roles/storage.objectUser"
  member = module.sea_ice_job_service_account.member
}

module "sea_ice_daily_job" {
  source = "../../modules/cloud_run_job"

  project_id            = var.project_id
  location              = var.region
  name                  = "sea-ice-daily"
  image                 = var.sea_ice_daily_image
  service_account_email = module.sea_ice_job_service_account.email
  cpu                   = "4"
  memory                = "16Gi"
  timeout               = "14400s"
  max_retries           = 0

  env = {
    GOOGLE_CLOUD_PROJECT        = var.project_id
    SHARED_DATASETS_BUCKET      = var.bucket_name
    SEA_ICE_SOURCE_URL_TEMPLATE = var.sea_ice_source_url_template
    SEA_ICE_MAX_LOOKBACK_DAYS   = tostring(var.sea_ice_max_lookback_days)
  }

  depends_on = [
    google_artifact_registry_repository.jobs,
    google_project_service.required,
    google_storage_bucket_iam_member.sea_ice_job_object_user,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "sea_ice_scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = module.sea_ice_daily_job.name
  role     = "roles/run.invoker"
  member   = module.sea_ice_scheduler_service_account.member
}

module "sea_ice_daily_scheduler" {
  source = "../../modules/scheduler_job"

  project_id            = var.project_id
  region                = var.region
  name                  = "sea-ice-daily"
  description           = "Run the IMS sea-ice extent publisher daily."
  schedule              = var.sea_ice_daily_schedule
  time_zone             = "UTC"
  target_job_location   = var.region
  target_job_name       = module.sea_ice_daily_job.name
  service_account_email = module.sea_ice_scheduler_service_account.email

  depends_on = [
    google_cloud_run_v2_job_iam_member.sea_ice_scheduler_invoker,
    google_project_service.required,
  ]
}
