resource "google_project_iam_custom_role" "scheduled_ingestion_deployer" {
  project     = var.project_id
  role_id     = "sharedDatasetsScheduledIngestionDeployer"
  title       = "Shared Datasets Scheduled Ingestion Deployer"
  description = "Allows approved GitHub Actions Terraform to update and verify scheduled ingestion Cloud Run jobs."
  permissions = [
    "cloudscheduler.jobs.enable",
    "cloudscheduler.jobs.get",
    "run.executions.get",
    "run.executions.list",
    "run.jobs.get",
    "run.jobs.run",
    "run.jobs.update",
    "run.operations.get",
    "run.operations.list",
    "run.tasks.get",
    "run.tasks.list",
  ]
}

resource "google_project_iam_member" "github_actions_scheduled_ingestion_deployer" {
  project = var.project_id
  role    = google_project_iam_custom_role.scheduled_ingestion_deployer.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}
