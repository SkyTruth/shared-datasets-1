# Reviewed one-time retirement only. These IAM delete permissions cannot be
# bounded to resource names. The protected saved-plan scope is exact; the grant
# has an absolute expiry and the workflow revokes it after success or failure.
variable "operational_retirement_authority_enabled" {
  description = "Enable temporary authority only inside the protected retirement attempt; the final/revocation plan leaves it disabled."
  type        = bool
  default     = false
}

locals {
  operational_retirement_role_id = "sharedDatasetsOperationalRetirement"
}

resource "google_project_iam_custom_role" "operational_retirement" {
  project     = var.project_id
  role_id     = local.operational_retirement_role_id
  title       = "Temporary Operational Infrastructure Retirement"
  description = "Read and delete retired observer resources during the reviewed protected retirement; never grants dataset or execution writes."
  permissions = [
    "iam.roles.delete",
    "iam.serviceAccounts.delete",
    "iam.serviceAccounts.get",
    "iam.workloadIdentityPoolProviders.delete",
    "iam.workloadIdentityPoolProviders.get",
    "logging.views.delete",
    "logging.views.get",
    "monitoring.metricDescriptors.delete",
    "monitoring.metricDescriptors.get",
  ]
}

resource "google_project_iam_member" "github_actions_operational_retirement" {
  count   = var.operational_retirement_authority_enabled ? 1 : 0
  project = var.project_id
  # The preceding protected role-creation job owns ordering. Revocation must
  # remain independent of role creation, including on an empty/failed bootstrap.
  role   = "projects/${var.project_id}/roles/${local.operational_retirement_role_id}"
  member = "serviceAccount:${var.github_actions_terraform_service_account_email}"

  condition {
    title       = "operational_retirement_expires"
    description = "Expire reviewed one-time retirement authority at 2026-11-03 00:00 UTC; the workflow also revokes it immediately after the attempt."
    expression  = "request.time < timestamp('2026-11-03T00:00:00Z')"
  }
}
