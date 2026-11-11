module "mpatlas_job_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "mpatlas-monthly-job"
  display_name = "Monthly MPAtlas Cloud Run Job"

  # Existing shared Terraform bootstrap authority owns first-time identities.
  # Its owner completes before ingestion IAM sync; no new project-wide grant.
  depends_on = [google_project_service.required, google_project_iam_member.github_actions_dataset_usage_deployer]
}

module "mpatlas_scheduler_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "mpatlas-monthly-scheduler"
  display_name = "Monthly MPAtlas Cloud Scheduler invoker"

  depends_on = [google_project_service.required, google_project_iam_member.github_actions_dataset_usage_deployer]
}

resource "google_storage_bucket_iam_member" "mpatlas_job_object_user" {
  bucket = data.google_storage_bucket.shared_bucket_iam.name
  role   = "roles/storage.objectUser"
  member = module.mpatlas_job_service_account.member

  condition {
    title       = "mpatlas_owned_dataset_objects"
    description = "Restrict MPAtlas monthly job writes to its asset root and release index."
    expression = join(" || ", [
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}100-geographic-reference/130-protected-areas/mpatlas-zone-assessments/')",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}100-geographic-reference/130-protected-areas/mpatlas-zone-assessments/')",
      "resource.name == '${local.shared_bucket_object_resource_prefix}_catalog/releases/mpatlas-zone-assessments.json'",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_catalog/releases/')",
    ])
  }
}

resource "google_storage_bucket_iam_member" "mpatlas_source_quality_object_user" {
  bucket = data.google_storage_bucket.shared_bucket_iam.name
  role   = "roles/storage.objectUser"
  member = module.mpatlas_job_service_account.member

  condition {
    title       = "mpatlas_source_quality_objects"
    description = "Restrict MPAtlas source-quality writes to its hold object and held candidates."
    expression = join(" || ", [
      "resource.name == '${local.shared_bucket_object_resource_prefix}_catalog/ingestion-holds/mpatlas-zone-assessments.json'",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_catalog/ingestion-holds/')",
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/pending-publishes/mpatlas-zone-assessments/source-loss-')",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/pending-publishes/mpatlas-zone-assessments/')",
    ])
  }
}

module "mpatlas_monthly_job" {
  source = "../../modules/cloud_run_job"

  project_id            = var.project_id
  location              = var.region
  name                  = "mpatlas-monthly"
  image                 = var.mpatlas_monthly_image
  service_account_email = module.mpatlas_job_service_account.email
  cpu                   = "4"
  memory                = "8Gi"
  timeout               = "3600s"
  max_retries           = 0

  secret_env = {
    SHARED_DATASETS_SLACK_WEBHOOK_URL = google_secret_manager_secret.slack_webhook_url.secret_id
  }

  env = {
    GOOGLE_CLOUD_PROJECT   = var.project_id
    SHARED_DATASETS_BUCKET = var.bucket_name
  }

  depends_on = [
    google_artifact_registry_repository.jobs,
    google_project_service.required,
    google_storage_bucket_iam_member.mpatlas_job_object_user,
    google_storage_bucket_iam_member.mpatlas_source_quality_object_user,
    google_secret_manager_secret_iam_member.mpatlas_hold_notice,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "mpatlas_scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = module.mpatlas_monthly_job.name
  role     = "roles/run.invoker"
  member   = module.mpatlas_scheduler_service_account.member
}

module "mpatlas_monthly_scheduler" {
  source = "../../modules/scheduler_job"

  project_id            = var.project_id
  region                = var.region
  name                  = "mpatlas-monthly"
  description           = "Check MPAtlas monthly and publish only changed feature content when the public source changes."
  schedule              = var.mpatlas_monthly_schedule
  time_zone             = "UTC"
  target_job_location   = var.region
  target_job_name       = module.mpatlas_monthly_job.name
  service_account_email = module.mpatlas_scheduler_service_account.email

  depends_on = [
    google_cloud_run_v2_job_iam_member.mpatlas_scheduler_invoker,
    google_project_service.required,
  ]
}

resource "google_service_account_iam_member" "mpatlas_deployer_act_as" {
  for_each = {
    job       = module.mpatlas_job_service_account.email
    scheduler = module.mpatlas_scheduler_service_account.email
  }
  service_account_id = "projects/${var.project_id}/serviceAccounts/${each.value}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

resource "google_cloud_run_v2_job_iam_member" "mpatlas_quality_execution_reader" {
  project  = var.project_id
  location = var.region
  # Existing job identity is literal so the IAM sync cannot pull a job/image
  # mutation into its dependency closure. This grants read access only.
  name   = "mpatlas-monthly"
  role   = "roles/run.viewer"
  member = module.mpatlas_job_service_account.member
}

# Ordered bootstrap gives the protected Terraform identity policy access to
# this one existing job before the runtime viewer binding is planned/applied.
resource "google_project_iam_custom_role" "mpatlas_quality_iam_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsMpatlasQualityIamManager"
  title       = "MPAtlas quality job IAM manager"
  description = "Manage the MPAtlas job policy for source-quality claim recovery."
  permissions = ["run.jobs.getIamPolicy", "run.jobs.setIamPolicy"]
}

resource "google_project_iam_member" "github_actions_mpatlas_quality_iam_manager" {
  project = var.project_id
  role    = google_project_iam_custom_role.mpatlas_quality_iam_manager.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  condition {
    title       = "mpatlas_quality_job_only"
    description = "Limit policy management to the existing MPAtlas Cloud Run job."
    expression = join(" || ", [
      "resource.name == 'projects/${var.project_id}/locations/${var.region}/jobs/mpatlas-monthly'",
      "resource.name == 'projects/${data.google_project.current.number}/locations/${var.region}/jobs/mpatlas-monthly'",
    ])
  }
}

resource "google_secret_manager_secret_iam_member" "mpatlas_hold_notice" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.slack_webhook_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = module.mpatlas_job_service_account.member

  depends_on = [google_project_iam_member.github_actions_translation_notice_iam_manager]
}
