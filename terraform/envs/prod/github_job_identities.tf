locals {
  github_workflow_identities = jsondecode(file("${path.module}/github-workflow-identities.json"))
  # Reusable jobs are authorized by the immutable leaf, never by their caller.
  github_job_workflow_ref = "('job_workflow_ref' in assertion ? assertion.job_workflow_ref : assertion.workflow_ref)"
  github_identity_attributes = {
    for identity, workflows in local.github_workflow_identities : "attribute.${identity}" =>
    "${local.github_job_workflow_ref} in ${jsonencode([for workflow in workflows : "${var.github_repository}/.github/workflows/${workflow}@refs/heads/main"])} ? 'allowed' : 'denied'"
    if identity != "bucket_reader" && !startswith(identity, "pr_plan")
  }
  observer_permissions = [
    "cloudscheduler.jobs.get", "run.jobs.get", "run.jobs.list", "run.executions.get", "run.executions.list",
    "run.operations.get", "run.services.get", "secretmanager.secrets.get", "monitoring.alerts.get", "monitoring.timeSeries.list",
  ]
  github_job_permissions = {
    recovery   = concat(local.observer_permissions, ["secretmanager.secrets.getIamPolicy"])
    observer   = local.observer_permissions
    heartbeat  = local.observer_permissions
    reconciler = ["run.services.get", "monitoring.alerts.get"]
    cleanup    = ["storage.objects.list", "storage.objects.get", "storage.objects.create", "storage.objects.delete"]
    runtime = concat(local.observer_permissions, [
      "run.jobs.run", "run.jobs.runWithOverrides", "run.executions.cancel", "cloudscheduler.jobs.enable", "cloudscheduler.jobs.pause",
      "compute.urlMaps.invalidateCache", "compute.globalOperations.get",
      "artifactregistry.repositories.get", "artifactregistry.repositories.downloadArtifacts", "artifactregistry.repositories.uploadArtifacts",
      "artifactregistry.dockerimages.get", "artifactregistry.tags.get", "artifactregistry.tags.create", "artifactregistry.tags.update",
    ])
  }
}

resource "google_service_account" "github_job" {
  for_each     = local.github_job_permissions
  project      = var.project_id
  account_id   = "shared-datasets-${each.key}"
  display_name = "Protected GitHub ${each.key}"
}
resource "google_project_iam_custom_role" "github_job" {
  for_each    = local.github_job_permissions
  project     = var.project_id
  role_id     = "sharedDatasetsGithub${title(each.key)}"
  title       = "Protected GitHub ${each.key}"
  permissions = each.value
}
resource "google_project_iam_member" "github_job" {
  for_each = local.github_job_permissions
  project  = var.project_id
  role     = google_project_iam_custom_role.github_job[each.key].name
  member   = "serviceAccount:${google_service_account.github_job[each.key].email}"
  dynamic "condition" {
    for_each = each.key == "cleanup" ? [true] : []
    content {
      title      = "scratch_bucket_listing"
      expression = "resource.name == 'projects/_/buckets/${var.bucket_name}' || (${local.pending_publish_cleanup_condition})"
    }
  }
}
resource "google_service_account_iam_member" "github_job_wif" {
  for_each           = local.github_job_permissions
  service_account_id = google_service_account.github_job[each.key].name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.${each.key}/allowed"
}
resource "google_storage_bucket_iam_member" "github_job_reader" {
  for_each = toset(["observer", "heartbeat", "runtime"])
  bucket   = var.bucket_name
  role     = "roles/storage.objectViewer"
  member   = "serviceAccount:${google_service_account.github_job[each.key].email}"
  condition {
    title      = "published_observation_reads"
    expression = "resource.name == 'projects/_/buckets/${var.bucket_name}' || (${local.canonical_mutation_publisher_condition})"
  }
}

# Installed by manual protected PR plan identity provisioning. Only the
# dedicated recovery leaf may read potentially secret-bearing state.
resource "google_project_iam_member" "recovery_reader" {
  project = var.project_id
  role    = google_project_iam_custom_role.pr_plan_reader.name
  member  = google_service_account.github_job["recovery"].member
}
resource "google_storage_bucket_iam_member" "recovery_state_reader" {
  bucket = var.bucket_name
  role   = google_project_iam_custom_role.pr_plan_state_reader.name
  member = google_service_account.github_job["recovery"].member
  condition {
    title      = "exact_default_state_and_workspace_listing"
    expression = "resource.name == 'projects/_/buckets/${var.bucket_name}' || resource.name == 'projects/_/buckets/${var.bucket_name}/objects/000-system/terraform/state/prod/default.tfstate'"
  }
}

# Authoritative for this role: retire the old environment-wide subject grant.
resource "google_service_account_iam_binding" "github_actions_terraform_wif" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.github_actions_terraform_service_account_email}"
  role               = "roles/iam.workloadIdentityUser"
  members            = ["principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.terraform/allowed"]
  depends_on         = [google_iam_workload_identity_pool_provider.github]
}

resource "google_project_iam_custom_role" "github_identity_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsGithubIdentityManager"
  title       = "Protected GitHub workflow identity manager"
  permissions = ["iam.workloadIdentityPoolProviders.get", "iam.workloadIdentityPoolProviders.update"]
}
resource "google_project_iam_member" "github_identity_manager" {
  project = var.project_id
  role    = google_project_iam_custom_role.github_identity_manager.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}
