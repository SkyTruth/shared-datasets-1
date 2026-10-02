module "wdpa_observer_service_account" {
  source       = "../../modules/service_account"
  project_id   = var.project_id
  account_id   = "wdpa-execution-observer"
  display_name = "WDPA execution observer"
  depends_on   = [google_project_service.required]
}

resource "google_project_iam_custom_role" "wdpa_execution_reader" {
  project     = var.project_id
  role_id     = "wdpaExecutionReader"
  title       = "WDPA execution reader"
  permissions = ["run.executions.get", "run.executions.list"]
}

resource "google_cloud_run_v2_job_iam_member" "wdpa_observer_execution_reader" {
  project  = var.project_id
  location = var.region
  name     = module.wdpa_monthly_job.name
  role     = google_project_iam_custom_role.wdpa_execution_reader.name
  member   = module.wdpa_observer_service_account.member
}

resource "google_storage_bucket_iam_member" "wdpa_observer_status_writer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectUser"
  member = module.wdpa_observer_service_account.member
  condition {
    title       = "wdpa_execution_status_only"
    description = "Observe execution status without dataset, claim, receipt or allocation writes."
    expression  = "resource.name == '${local.shared_bucket_object_resource_prefix}_catalog/wdpa-monthly-execution.json'"
  }
}

# Deployment can use this identity only for the observer job.
resource "google_service_account_iam_member" "wdpa_observer_deployer" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.wdpa_observer_service_account.email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

module "wdpa_execution_observer_job" {
  source                = "../../modules/cloud_run_job"
  project_id            = var.project_id
  location              = var.region
  name                  = "wdpa-execution-observer"
  image                 = var.wdpa_monthly_image
  command               = ["python", "-m", "ingestion.wdpa_monthly.execution_observer"]
  service_account_email = module.wdpa_observer_service_account.email
  cpu                   = "1"
  memory                = "512Mi"
  timeout               = "120s"
  max_retries           = 0
  env = {
    GOOGLE_CLOUD_PROJECT   = var.project_id
    SHARED_DATASETS_BUCKET = var.bucket_name
    WDPA_JOB_REGION        = var.region
  }
  depends_on = [
    google_cloud_run_v2_job_iam_member.wdpa_observer_execution_reader,
    google_storage_bucket_iam_member.wdpa_observer_status_writer,
    google_service_account_iam_member.wdpa_observer_deployer,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "wdpa_observer_scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = module.wdpa_execution_observer_job.name
  role     = "roles/run.invoker"
  member   = module.wdpa_scheduler_service_account.member
}

module "wdpa_execution_observer_scheduler" {
  source                = "../../modules/scheduler_job"
  project_id            = var.project_id
  region                = var.region
  name                  = "wdpa-execution-observer"
  description           = "Observe WDPA executions independently of the processing worker every five minutes."
  schedule              = "*/5 * * * *"
  target_job_location   = var.region
  target_job_name       = module.wdpa_execution_observer_job.name
  service_account_email = module.wdpa_scheduler_service_account.email
  depends_on            = [google_cloud_run_v2_job_iam_member.wdpa_observer_scheduler_invoker]
}
