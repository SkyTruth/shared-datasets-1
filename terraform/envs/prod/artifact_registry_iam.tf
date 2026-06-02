resource "google_artifact_registry_repository_iam_member" "github_actions_artifact_registry_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.jobs.location
  repository = google_artifact_registry_repository.jobs.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}
