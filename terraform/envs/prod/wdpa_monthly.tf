module "wdpa_job_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "wdpa-monthly-job"
  display_name = "WDPA monthly Cloud Run Job"

  depends_on = [google_project_service.required]
}

module "wdpa_scheduler_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "wdpa-monthly-scheduler"
  display_name = "WDPA monthly Cloud Scheduler invoker"

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "wdpa_job_object_user" {
  bucket = var.bucket_name
  role   = "roles/storage.objectUser"
  member = module.wdpa_job_service_account.member
}

module "wdpa_monthly_job" {
  source = "../../modules/cloud_run_job"

  project_id            = var.project_id
  location              = var.region
  name                  = "wdpa-monthly"
  image                 = var.wdpa_monthly_image
  service_account_email = module.wdpa_job_service_account.email
  cpu                   = "8"
  memory                = "32Gi"
  timeout               = "86400s"
  max_retries           = 0

  env = {
    GOOGLE_CLOUD_PROJECT     = var.project_id
    SHARED_DATASETS_BUCKET   = var.bucket_name
    WDPA_SOURCE_URL_TEMPLATE = var.wdpa_source_url_template
  }

  depends_on = [
    google_artifact_registry_repository.jobs,
    google_project_service.required,
    google_storage_bucket_iam_member.wdpa_job_object_user,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = module.wdpa_monthly_job.name
  role     = "roles/run.invoker"
  member   = module.wdpa_scheduler_service_account.member
}

module "wdpa_monthly_scheduler" {
  source = "../../modules/scheduler_job"

  project_id            = var.project_id
  region                = var.region
  name                  = "wdpa-monthly"
  description           = "Run the simplified monthly WDPA publisher daily during the early-month source availability window."
  schedule              = "0 9 1-10 * *"
  time_zone             = "UTC"
  target_job_location   = var.region
  target_job_name       = module.wdpa_monthly_job.name
  service_account_email = module.wdpa_scheduler_service_account.email

  depends_on = [
    google_cloud_run_v2_job_iam_member.scheduler_invoker,
    google_project_service.required,
  ]
}
