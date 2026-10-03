resource "google_project_iam_custom_role" "scheduled_ingestion_deployer" {
  project     = var.project_id
  role_id     = "sharedDatasetsScheduledIngestionDeployer"
  title       = "Shared Datasets Scheduled Ingestion Deployer"
  description = "Allows approved GitHub Actions Terraform to create, update and verify scheduled ingestion Cloud Run jobs."
  permissions = [
    "cloudscheduler.jobs.enable",
    "cloudscheduler.jobs.get",
    "cloudscheduler.jobs.pause",
    "run.executions.get",
    "run.executions.list",
    # Job creation is checked on the parent project/location. The protected
    # validation workflow permits only its three isolated resources; this does
    # not grant additional runtime, dataset, job IAM or deletion permissions.
    "run.jobs.create",
    "run.jobs.get",
    "run.jobs.getIamPolicy",
    "run.jobs.list",
    "run.jobs.run",
    "run.jobs.runWithOverrides",
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

# Bootstrap separately: planning the runtime grants already reads the secret.
resource "google_project_iam_custom_role" "translation_notice_iam_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsTranslationNoticeIamManager"
  title       = "Shared Datasets Translation Notice IAM Manager"
  description = "Read Slack secret metadata and manage its runtime accessor bindings."
  permissions = [
    "secretmanager.secrets.get",
    "secretmanager.secrets.getIamPolicy",
    "secretmanager.secrets.setIamPolicy",
  ]
}

resource "google_project_iam_member" "github_actions_translation_notice_iam_manager" {
  project = var.project_id
  role    = google_project_iam_custom_role.translation_notice_iam_manager.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"

  condition {
    title       = "translation_notice_secret_iam"
    description = "Manage accessor bindings only on the existing shared datasets Slack secret."
    expression = join(" || ", [
      "resource.name == 'projects/${var.project_id}/secrets/${local.slack_webhook_secret_id}'",
      "resource.name == 'projects/${data.google_project.current.number}/secrets/${local.slack_webhook_secret_id}'",
    ])
  }
}
