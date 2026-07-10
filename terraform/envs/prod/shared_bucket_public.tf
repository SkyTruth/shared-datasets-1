import {
  to = google_storage_bucket.shared_bucket
  id = var.bucket_name
}

resource "google_storage_bucket" "shared_bucket" {
  project                     = var.project_id
  name                        = var.bucket_name
  location                    = "US"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "inherited"
  force_destroy               = false

  cors {
    origin          = local.pmtiles_browser_allowed_origins
    method          = ["GET", "HEAD", "OPTIONS"]
    response_header = ["Accept-Ranges", "Cache-Control", "Content-Length", "Content-Range", "ETag", "Range"]
    max_age_seconds = 3600
  }

  soft_delete_policy {
    retention_duration_seconds = 2592000
  }

  hierarchical_namespace {
    enabled = true
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required]
}

locals {
  shared_bucket_managed_folder_names = toset(concat(
    ["_catalog/"],
    [
      for row in local.shared_catalog_rows :
      "${dirname(dirname(replace(row.canonical_path, "gs://${var.bucket_name}/", "")))}/"
      if startswith(row.canonical_path, "gs://${var.bucket_name}/")
    ]
  ))

  shared_bucket_public_managed_folder_names = toset(concat(
    ["_catalog/"],
    [
      for row in local.shared_catalog_rows :
      "${dirname(dirname(replace(row.canonical_path, "gs://${var.bucket_name}/", "")))}/"
      if lower(row.access_tier) == "public" && startswith(row.canonical_path, "gs://${var.bucket_name}/")
    ]
  ))
}

resource "google_project_iam_custom_role" "pmtiles_managed_folder_sync" {
  project     = var.project_id
  role_id     = "sharedDatasetsPmtilesManagedFolderSync"
  title       = "Shared Datasets PMTiles Managed Folder Sync"
  description = "Allows approved GitHub Actions Terraform to create and manage IAM on PMTiles CDN managed folders."
  permissions = [
    "storage.managedFolders.create",
    "storage.managedFolders.get",
    "storage.managedFolders.getIamPolicy",
    "storage.managedFolders.list",
    "storage.managedFolders.setIamPolicy",
  ]

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "github_actions_pmtiles_managed_folder_sync" {
  bucket = var.bucket_name
  role   = google_project_iam_custom_role.pmtiles_managed_folder_sync.name
  member = "serviceAccount:${var.github_actions_terraform_service_account_email}"

  depends_on = [google_storage_bucket.shared_bucket]
}

resource "google_storage_managed_folder" "shared_bucket_public_prefixes" {
  for_each = local.shared_bucket_managed_folder_names

  bucket = google_storage_bucket.shared_bucket.name
  name   = each.value

  depends_on = [
    google_project_service.required,
    google_storage_bucket_iam_member.github_actions_pmtiles_managed_folder_sync,
  ]
}

resource "google_storage_managed_folder_iam_member" "shared_bucket_public_object_viewers" {
  for_each = {
    for name, folder in google_storage_managed_folder.shared_bucket_public_prefixes :
    name => folder
    if contains(local.shared_bucket_public_managed_folder_names, name)
  }

  bucket         = each.value.bucket
  managed_folder = each.value.name
  role           = "roles/storage.objectViewer"
  member         = "allUsers"

  depends_on = [
    google_storage_bucket_iam_member.github_actions_pmtiles_managed_folder_sync,
  ]
}

moved {
  from = google_storage_bucket_iam_member.shared_bucket_public_object_viewer
  to   = google_storage_bucket_iam_member.shared_bucket_public_object_viewer[0]
}

resource "google_storage_bucket_iam_member" "shared_bucket_public_object_viewer" {
  count = var.shared_bucket_public_object_viewer_enabled ? 1 : 0

  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
