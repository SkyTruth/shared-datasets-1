locals {
  github_readonly_legacy_condition = join(" && ", [
    "assertion.repository == '${var.github_repository}'",
    "(assertion.workflow == 'Catalog drift guard' || assertion.workflow == 'Bucket hygiene audit' || assertion.workflow == 'Dataset breaking change alert')",
  ])
  github_readonly_workflow_condition = join(" && ", [
    "assertion.repository == '${var.github_repository}'",
    "(${join(" || ", [
      "(assertion.ref == 'refs/heads/main' && ${local.github_job_workflow_ref} in ${jsonencode([for workflow in local.github_workflow_identities.bucket_reader : "${var.github_repository}/.github/workflows/${workflow}@refs/heads/main" if workflow != "dataset-breaking-change-alert.yml"])})",
      "(assertion.event_name == 'pull_request' && assertion.ref.matches('^refs/pull/[0-9]+/merge$') && assertion.workflow_ref == ${local.github_job_workflow_ref} && assertion.workflow_ref == '${var.github_repository}/.github/workflows/dataset-breaking-change-alert.yml@' + assertion.ref)",
    ])})",
  ])
}

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
    "attribute.bucket_reader"    = "${local.github_readonly_workflow_condition} ? 'allowed' : 'denied'"
  }

  # The held-candidate preview is a new reader whose display name the legacy
  # condition rejects. Preserve all old tokens and add that workflow token set.
  attribute_condition = "(${local.github_readonly_legacy_condition}) || (${local.github_readonly_workflow_condition})"

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

# Preserve the legacy grant until main-run identity evidence permits phase 2.
resource "google_service_account_iam_member" "github_readonly_wif" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.github_readonly_service_account.email}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
}

resource "google_service_account_iam_member" "github_readonly_wif_workflow" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.github_readonly_service_account.email}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.bucket_reader/allowed"
}

resource "google_storage_bucket_iam_member" "github_readonly_object_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = module.github_readonly_service_account.member

  depends_on = [google_storage_bucket.shared_bucket]
}

# Separate the existing condition sets instead of exceeding IAM's 12 logical
# operator limit when listing, canonical reads and staging reads are combined.
resource "google_storage_bucket_iam_member" "github_readonly_canonical_object_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = module.github_readonly_service_account.member

  condition {
    title      = "advisory_and_audit_canonical_objects"
    expression = local.canonical_mutation_publisher_condition
  }

  depends_on = [google_storage_bucket.shared_bucket]
}

resource "google_storage_bucket_iam_member" "github_readonly_pending_publish_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = module.github_readonly_service_account.member

  condition {
    title      = "advisory_and_audit_pending_publishes"
    expression = "resource.name == 'projects/_/buckets/${var.bucket_name}' || (${local.pending_publish_source_condition})"
  }

  depends_on = [google_storage_bucket.shared_bucket]
}
