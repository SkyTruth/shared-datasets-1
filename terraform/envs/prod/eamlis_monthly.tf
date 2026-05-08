module "eamlis_job_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "eamlis-monthly-job"
  display_name = "Monthly e-AMLIS Cloud Run Job"

  depends_on = [google_project_service.required]
}

module "eamlis_scheduler_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "eamlis-monthly-scheduler"
  display_name = "Monthly e-AMLIS Cloud Scheduler invoker"

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "eamlis_job_object_user" {
  bucket = var.bucket_name
  role   = "roles/storage.objectUser"
  member = module.eamlis_job_service_account.member

  condition {
    title       = "eamlis_owned_dataset_objects"
    description = "Restrict eAMLIS monthly job writes to its asset root and release index."
    expression = join(" || ", [
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/')",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/')",
      "resource.name == '${local.shared_bucket_object_resource_prefix}_catalog/releases/eamlis-abandoned-mine-land-inventory.json'",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_catalog/releases/')",
    ])
  }

  depends_on = [google_storage_bucket.shared_bucket]
}

module "eamlis_monthly_job" {
  source = "../../modules/cloud_run_job"

  project_id            = var.project_id
  location              = var.region
  name                  = "eamlis-monthly"
  image                 = var.eamlis_monthly_image
  service_account_email = module.eamlis_job_service_account.email
  cpu                   = "4"
  memory                = "16Gi"
  timeout               = "14400s"
  max_retries           = 0

  env = {
    GOOGLE_CLOUD_PROJECT   = var.project_id
    SHARED_DATASETS_BUCKET = var.bucket_name
    EAMLIS_LAYER_URL       = var.eamlis_layer_url
    EAMLIS_WHERE           = var.eamlis_where
    EAMLIS_PAGE_SIZE       = tostring(var.eamlis_page_size)
  }

  depends_on = [
    google_artifact_registry_repository.jobs,
    google_project_service.required,
    google_storage_bucket_iam_member.eamlis_job_object_user,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "eamlis_scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = module.eamlis_monthly_job.name
  role     = "roles/run.invoker"
  member   = module.eamlis_scheduler_service_account.member
}

module "eamlis_monthly_scheduler" {
  source = "../../modules/scheduler_job"

  project_id            = var.project_id
  region                = var.region
  name                  = "eamlis-monthly"
  description           = "Run the monthly OSMRE e-AMLIS publisher when the public source changes."
  schedule              = var.eamlis_monthly_schedule
  time_zone             = "UTC"
  target_job_location   = var.region
  target_job_name       = module.eamlis_monthly_job.name
  service_account_email = module.eamlis_scheduler_service_account.email

  depends_on = [
    google_cloud_run_v2_job_iam_member.eamlis_scheduler_invoker,
    google_project_service.required,
  ]
}
