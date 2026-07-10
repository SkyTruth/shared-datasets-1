resource "google_storage_bucket" "terraform_state" {
  project                     = var.project_id
  name                        = "skytruth-shared-datasets-1-terraform-state"
  location                    = "US"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  versioning {
    enabled = true
  }

  soft_delete_policy {
    retention_duration_seconds = 2592000
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "terraform_state_workflow_admin" {
  bucket = google_storage_bucket.terraform_state.name
  role   = "roles/storage.admin"
  member = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

resource "google_storage_bucket_iam_member" "terraform_state_breakglass_admin" {
  bucket = google_storage_bucket.terraform_state.name
  role   = "roles/storage.admin"
  member = "group:${var.shared_datasets_breakglass_group_email}"
}
