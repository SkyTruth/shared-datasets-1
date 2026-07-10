locals {
  preview_firestore_database_resource_name = "projects/${var.project_id}/databases/feature-preview"
  feature_preview_bucket_name              = "skytruth-shared-datasets-1-preview"
  feature_preview_service_name             = "feature-preview-service"
  feature_preview_catalog_viewer_name      = "feature-preview-catalog-viewer"
}

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
    "resourcemanager.projects.get",
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
  ]
}

resource "google_project_iam_custom_role" "preview_iap_terraform" {
  project     = var.project_id
  role_id     = "sharedDatasetsPreviewIapTerraform"
  title       = "Shared Datasets Preview IAP Terraform"
  description = "Allows approved preview Terraform to manage IAP policy on the two preview services."
  permissions = [
    "iap.webServices.getIamPolicy",
    "iap.webServices.setIamPolicy",
  ]
}

import {
  to = google_storage_bucket.feature_preview
  id = local.feature_preview_bucket_name
}

resource "google_storage_bucket" "feature_preview" {
  project                     = var.project_id
  name                        = local.feature_preview_bucket_name
  location                    = "US"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  hierarchical_namespace {
    enabled = true
  }

  cors {
    origin          = ["*"]
    method          = ["GET", "HEAD", "OPTIONS"]
    response_header = ["Content-Length", "Content-Range", "ETag", "Range"]
    max_age_seconds = 3600
  }

  lifecycle {
    prevent_destroy = true
  }
}

import {
  to = google_firestore_database.feature_preview
  id = local.preview_firestore_database_resource_name
}

resource "google_firestore_database" "feature_preview" {
  project                     = var.project_id
  name                        = "feature-preview"
  location_id                 = "nam5"
  type                        = "FIRESTORE_NATIVE"
  delete_protection_state     = "DELETE_PROTECTION_ENABLED"
  deletion_policy             = "ABANDON"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"

  lifecycle {
    prevent_destroy = true
  }
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

resource "google_project_iam_member" "github_actions_preview_iap_terraform" {
  project = var.project_id
  role    = google_project_iam_custom_role.preview_iap_terraform.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"

  condition {
    title       = "preview_iap_services_only"
    description = "Limit IAP IAM mutation to the two preview Cloud Run services."
    expression  = "resource.service == 'iap.googleapis.com' && (resource.name.endsWith('/services/${local.feature_preview_service_name}') || resource.name.endsWith('/services/${local.feature_preview_catalog_viewer_name}'))"
  }
}

resource "google_service_account_iam_member" "github_actions_preview_service_act_as" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.feature_preview_service_account.email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

resource "google_service_account_iam_member" "github_actions_preview_loader_act_as" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.feature_preview_loader_service_account.email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
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

resource "google_storage_bucket_iam_member" "feature_preview_service_object_viewer" {
  bucket = google_storage_bucket.feature_preview.name
  role   = "roles/storage.objectViewer"
  member = module.feature_preview_service_account.member
}

resource "google_storage_bucket_iam_member" "feature_preview_loader_object_viewer" {
  bucket = google_storage_bucket.feature_preview.name
  role   = "roles/storage.objectViewer"
  member = module.feature_preview_loader_service_account.member
}

resource "google_storage_bucket_iam_member" "feature_preview_loader_index_load_creator" {
  bucket = google_storage_bucket.feature_preview.name
  role   = "roles/storage.objectUser"
  member = module.feature_preview_loader_service_account.member
}

resource "google_project_iam_member" "feature_preview_service_firestore_viewer" {
  project = var.project_id
  role    = "roles/datastore.viewer"
  member  = module.feature_preview_service_account.member

  condition {
    title       = "preview_firestore_read"
    description = "Limit preview service reads to the preview Firestore database."
    expression  = "resource.name == '${local.preview_firestore_database_resource_name}' || resource.name.startsWith('${local.preview_firestore_database_resource_name}/')"
  }
}

resource "google_project_iam_member" "feature_preview_loader_firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = module.feature_preview_loader_service_account.member

  condition {
    title       = "preview_firestore_write"
    description = "Limit preview loader writes to the preview Firestore database."
    expression  = "resource.name == '${local.preview_firestore_database_resource_name}' || resource.name.startsWith('${local.preview_firestore_database_resource_name}/')"
  }
}
