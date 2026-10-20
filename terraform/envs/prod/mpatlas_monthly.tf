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
  bucket = var.bucket_name
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

  depends_on = [google_storage_bucket.shared_bucket]
}

module "mpatlas_monthly_job" {
  source = "../../modules/cloud_run_job"

  project_id            = var.project_id
  location              = var.region
  name                  = "mpatlas-monthly"
  image                 = var.mpatlas_monthly_image
  service_account_email = module.mpatlas_job_service_account.email
  cpu                   = "4"
  memory                = "16Gi"
  timeout               = "14400s"
  max_retries           = 0

  env = {
    GOOGLE_CLOUD_PROJECT   = var.project_id
    SHARED_DATASETS_BUCKET = var.bucket_name
  }

  depends_on = [
    google_artifact_registry_repository.jobs,
    google_project_service.required,
    google_storage_bucket_iam_member.mpatlas_job_object_user,
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
