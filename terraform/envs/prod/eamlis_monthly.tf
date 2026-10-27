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
    description = "Restrict EAMLIS writes to its dataset, index, quality control and held candidates."
    expression = join(" || ", [
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/')",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/')",
      "resource.name == '${local.shared_bucket_object_resource_prefix}_catalog/releases/eamlis-abandoned-mine-land-inventory.json'",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_catalog/releases/')",
      "resource.name == '${local.shared_bucket_object_resource_prefix}_catalog/ingestion-holds/eamlis-abandoned-mine-land-inventory.json'",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_catalog/ingestion-holds/')",
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/pending-publishes/eamlis-abandoned-mine-land-inventory/source-loss-')",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/pending-publishes/eamlis-abandoned-mine-land-inventory/')",
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

  secret_env = {
    SHARED_DATASETS_SLACK_WEBHOOK_URL = google_secret_manager_secret.slack_webhook_url.secret_id
  }

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
    google_secret_manager_secret_iam_member.translation_notice["eamlis"],
    google_storage_bucket_iam_member.translation_debt_writer["eamlis"],
  ]
}

resource "google_cloud_run_v2_job_iam_member" "eamlis_quality_execution_reader" {
  project  = var.project_id
  location = var.region
  # Existing job identity is literal so the IAM sync cannot pull a job/image
  # mutation into its dependency closure. This grants read access only.
  name   = "eamlis-monthly"
  role   = "roles/run.viewer"
  member = module.eamlis_job_service_account.member
}

# Ordered bootstrap gives the protected Terraform identity policy access to
# this one existing job before the runtime viewer binding is planned/applied.
resource "google_project_iam_custom_role" "eamlis_quality_iam_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsEamlisQualityIamManager"
  title       = "EAMLIS quality job IAM manager"
  description = "Manage the EAMLIS job policy for source-quality claim recovery."
  permissions = ["run.jobs.getIamPolicy", "run.jobs.setIamPolicy"]
}

resource "google_project_iam_member" "github_actions_eamlis_quality_iam_manager" {
  project = var.project_id
  role    = google_project_iam_custom_role.eamlis_quality_iam_manager.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  condition {
    title       = "eamlis_quality_job_only"
    description = "Limit policy management to the existing EAMLIS Cloud Run job."
    expression = join(" || ", [
      "resource.name == 'projects/${var.project_id}/locations/${var.region}/jobs/eamlis-monthly'",
      "resource.name == 'projects/${data.google_project.current.number}/locations/${var.region}/jobs/eamlis-monthly'",
    ])
  }
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
