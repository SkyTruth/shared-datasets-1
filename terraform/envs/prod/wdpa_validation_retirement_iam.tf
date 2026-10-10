# One-time authority for the reviewed six-resource retirement. IAM roles and
# service accounts do not support resource.name conditions. Keep these delete
# permissions separate from the persistent ingestion deployer and expire the
# project binding; the protected saved-plan allowlist restricts actual changes.
resource "google_project_iam_custom_role" "wdpa_validation_retirement" {
  project     = var.project_id
  role_id     = "sharedDatasetsWdpaValidationRetirement"
  title       = "WDPA Validation Retirement"
  description = "Temporary delete authority for the approved isolated WDPA validation retirement."
  permissions = [
    "iam.roles.delete",
    "iam.serviceAccounts.delete",
    "run.jobs.delete",
  ]
}

resource "google_project_iam_member" "github_actions_wdpa_validation_retirement" {
  project = var.project_id
  role    = google_project_iam_custom_role.wdpa_validation_retirement.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"

  condition {
    title       = "wdpa_validation_retirement_expires"
    description = "Expire one-time retirement authority at 2026-10-11 12:00 UTC."
    expression  = "request.time < timestamp('2026-10-11T12:00:00Z')"
  }
}
