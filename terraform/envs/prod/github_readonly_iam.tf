resource "google_iam_workload_identity_pool_provider" "github_readonly" {
  project = var.project_id

  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = var.github_readonly_workload_identity_pool_provider_id
  display_name                       = "GitHub Actions read-only"
  description                        = "Restricts GitHub OIDC tokens to read-only shared-datasets bucket checks."
  disabled                           = false

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.actor"            = "assertion.actor"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
    "attribute.workflow"         = "assertion.workflow"
  }

  attribute_condition = join(" && ", [
    "assertion.repository == '${var.github_repository}'",
    "(assertion.workflow == 'Catalog drift guard' || assertion.workflow == 'Bucket hygiene audit')",
  ])

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

module "github_readonly_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "shared-datasets-gh-readonly"
  display_name = "Shared datasets GitHub read-only"

  depends_on = [google_project_service.required]
}

resource "google_service_account_iam_member" "github_readonly_wif" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.github_readonly_service_account.email}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_storage_bucket_iam_member" "github_readonly_object_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = module.github_readonly_service_account.member

  depends_on = [google_storage_bucket.shared_bucket]
}
