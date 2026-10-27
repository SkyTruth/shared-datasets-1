# The deployer already has the other reads needed by its targeted workflows.
# A full normal plan also refreshes these retained infrastructure classes.
resource "google_project_iam_custom_role" "terraform_state_reader" {
  project     = var.project_id
  role_id     = "sharedDatasetsTerraformStateReader"
  title       = "Shared Datasets Terraform State Reader"
  description = "Reads retained infrastructure metadata for full production Terraform plans."
  permissions = [
    "compute.globalAddresses.get",
    "compute.regionNetworkEndpointGroups.get",
    "compute.sslCertificates.get",
    "compute.targetHttpProxies.get",
    "iam.workloadIdentityPoolProviders.get",
  ]
}

resource "google_project_iam_member" "github_actions_terraform_state_reader" {
  project = var.project_id
  role    = google_project_iam_custom_role.terraform_state_reader.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}
