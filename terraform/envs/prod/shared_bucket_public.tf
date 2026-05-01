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
    origin          = ["*"]
    method          = ["GET", "HEAD", "OPTIONS"]
    response_header = ["Content-Length", "Content-Range", "ETag", "Range"]
    max_age_seconds = 3600
  }

  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "shared_bucket_public_object_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
