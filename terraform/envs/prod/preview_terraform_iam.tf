import {
  to = google_project_iam_custom_role.preview_terraform
  id = "projects/shared-datasets-1/roles/sharedDatasetsPreviewTerraform"
}

resource "google_project_iam_custom_role" "preview_terraform" {
  project     = var.project_id
  role_id     = "sharedDatasetsPreviewTerraform"
  title       = "Shared Datasets Preview Terraform"
  description = "Allows approved GitHub Actions Terraform to manage the replaceable feature branch preview slot."
  permissions = [
    "iam.serviceAccounts.actAs",
    "iam.serviceAccounts.create",
    "iam.serviceAccounts.get",
    "iam.serviceAccounts.getIamPolicy",
    "iam.serviceAccounts.list",
    "iam.serviceAccounts.setIamPolicy",
    "iam.serviceAccounts.update",
    "iam.workloadIdentityPools.get",
    "iap.web.getIamPolicy",
    "iap.web.setIamPolicy",
    "iap.webServiceVersions.getIamPolicy",
    "iap.webServiceVersions.setIamPolicy",
    "iap.webServices.getIamPolicy",
    "iap.webServices.setIamPolicy",
    "iap.webTypes.getIamPolicy",
    "iap.webTypes.setIamPolicy",
    "resourcemanager.projects.get",
    "resourcemanager.projects.getIamPolicy",
    "resourcemanager.projects.setIamPolicy",
    "run.locations.list",
    "run.operations.get",
    "run.operations.list",
    "run.services.create",
    "run.services.delete",
    "run.services.get",
    "run.services.getIamPolicy",
    "run.services.list",
    "run.services.setIamPolicy",
    "run.services.update",
    "storage.buckets.create",
    "storage.buckets.delete",
    "storage.buckets.get",
    "storage.buckets.getIamPolicy",
    "storage.buckets.list",
    "storage.buckets.setIamPolicy",
    "storage.buckets.update",
    "storage.folders.create",
    "storage.folders.delete",
    "storage.folders.get",
    "storage.folders.list",
    "storage.managedFolders.delete",
    "storage.managedFolders.get",
    "storage.managedFolders.list",
    "storage.objects.delete",
    "storage.objects.get",
    "storage.objects.list",
  ]

}

module "feature_preview_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "feature-preview-service"
  display_name = "Shared datasets feature branch preview service"

}

module "feature_preview_loader_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "feature-preview-loader"
  display_name = "Shared datasets feature branch preview loader"

}

resource "google_project_iam_member" "github_actions_preview_terraform" {
  project = var.project_id
  role    = google_project_iam_custom_role.preview_terraform.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

resource "google_service_account_iam_member" "feature_preview_loader_github_wif" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.feature_preview_loader_service_account.email}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/repo:${var.github_repository}:environment:${var.github_publish_environment}"
}

resource "google_service_account_iam_member" "feature_preview_service_self_sign_blob" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.feature_preview_service_account.email}"
  role               = google_project_iam_custom_role.catalog_viewer_sign_blob.name
  member             = module.feature_preview_service_account.member
}
