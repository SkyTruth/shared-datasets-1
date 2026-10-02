# These permissions are project-wide. Cloud Scheduler creation is authorized on
# the parent location, and resource.name conditions do not scope these APIs.
# Merge only after explicit approval of this temporary write grant. A new
# reviewed PR is required to extend the fixed expiry; never renew it implicitly.
resource "google_project_iam_custom_role" "wdpa_observer_bootstrap" {
  project     = var.project_id
  role_id     = "sharedDatasetsWdpaObserverBootstrap"
  title       = "Shared Datasets WDPA Observer Bootstrap"
  description = "Temporary protected-workflow job IAM and Scheduler provisioning for the WDPA observer."
  permissions = [
    "cloudscheduler.jobs.create",
    "cloudscheduler.jobs.update",
    "run.jobs.setIamPolicy",
  ]
}

resource "google_project_iam_member" "github_actions_wdpa_observer_bootstrap" {
  project = var.project_id
  role    = google_project_iam_custom_role.wdpa_observer_bootstrap.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  condition {
    title       = "wdpa_observer_bootstrap_until_20261006"
    description = "Expire project-wide observer provisioning writes at 2026-10-06 00:00 UTC."
    expression  = "request.time < timestamp('2026-10-06T00:00:00Z')"
  }
}
