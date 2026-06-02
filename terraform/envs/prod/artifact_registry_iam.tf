resource "google_project_iam_custom_role" "artifact_registry_iam_policy_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsArtifactRegistryIamPolicyManager"
  title       = "Shared Datasets Artifact Registry IAM Policy Manager"
  description = "Allows approved GitHub Actions Terraform to manage IAM bindings on the shared datasets Artifact Registry repository."
  permissions = [
    "artifactregistry.repositories.getIamPolicy",
    "artifactregistry.repositories.setIamPolicy",
  ]
}

resource "google_project_iam_member" "github_actions_artifact_registry_iam_policy_manager" {
  project = var.project_id
  role    = google_project_iam_custom_role.artifact_registry_iam_policy_manager.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

resource "google_artifact_registry_repository_iam_member" "github_actions_artifact_registry_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.jobs.location
  repository = google_artifact_registry_repository.jobs.repository_id
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${var.github_actions_terraform_service_account_email}"

  depends_on = [
    google_project_iam_member.github_actions_artifact_registry_iam_policy_manager,
  ]
}
