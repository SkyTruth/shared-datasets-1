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
    origin          = var.pmtiles_cdn_allowed_origins
    method          = ["GET", "HEAD", "OPTIONS"]
    response_header = ["Accept-Ranges", "Cache-Control", "Content-Length", "Content-Range", "ETag", "Range"]
    max_age_seconds = 3600
  }

  soft_delete_policy {
    retention_duration_seconds = 604800
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
  shared_bucket_public_managed_folder_names = toset(concat(
    ["_catalog/"],
    [
      for row in local.shared_catalog_rows :
      "${dirname(dirname(replace(row.canonical_path, "gs://${var.bucket_name}/", "")))}/"
      if lower(row.access_tier) == "public" && startswith(row.canonical_path, "gs://${var.bucket_name}/")
    ]
  ))
}

resource "google_storage_managed_folder" "shared_bucket_public_prefixes" {
  for_each = local.shared_bucket_public_managed_folder_names

  bucket = google_storage_bucket.shared_bucket.name
  name   = each.value

  depends_on = [google_project_service.required]
}

resource "google_storage_managed_folder_iam_member" "shared_bucket_public_object_viewers" {
  for_each = google_storage_managed_folder.shared_bucket_public_prefixes

  bucket         = each.value.bucket
  managed_folder = each.value.name
  role           = "roles/storage.objectViewer"
  member         = "allUsers"
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
