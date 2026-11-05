# Owner decision 2026-11-05: same-repo PR authors may read production state.
# A separate pool prevents this PR subject from matching any production-pool
# principalSet or environment binding. No Secret Manager or write access.
resource "google_iam_workload_identity_pool" "pr_plan" {
  project                   = var.project_id
  workload_identity_pool_id = "github-pr-plan"
  display_name              = "GitHub PR read-only plans"
  depends_on                = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "pr_plan" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.pr_plan.workload_identity_pool_id
  workload_identity_pool_provider_id = "ci-pr-plan"
  attribute_mapping = {
    "google.subject" = "assertion.sub"
  }
  # Bind both the CI caller and its exact reusable planner on the same PR ref.
  attribute_condition = join(" && ", [
    "assertion.repository == '${var.github_repository}'",
    "assertion.event_name == 'pull_request'",
    "assertion.ref.matches('^refs/pull/[0-9]+/merge$')",
    "assertion.workflow_ref in ${jsonencode([for workflow in local.github_workflow_identities.pr_plan_caller : "${var.github_repository}/.github/workflows/${workflow}@"])}.map(prefix, prefix + assertion.ref)",
    "assertion.job_workflow_ref in ${jsonencode([for workflow in local.github_workflow_identities.pr_plan : "${var.github_repository}/.github/workflows/${workflow}@"])}.map(prefix, prefix + assertion.ref)",
  ])
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account" "pr_plan" {
  project      = var.project_id
  account_id   = "shared-datasets-pr-plan"
  display_name = "Read-only PR Terraform planner"
}

resource "google_service_account_iam_member" "pr_plan_wif" {
  service_account_id = google_service_account.pr_plan.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.pr_plan.name}/subject/repo:${var.github_repository}:pull_request"
}

resource "google_project_iam_custom_role" "pr_plan_reader" {
  project = var.project_id
  role_id = "sharedDatasetsPrPlanReader"
  title   = "Read-only PR plan metadata and IAM"
  permissions = [
    "resourcemanager.projects.get", "resourcemanager.projects.getIamPolicy",
    "iam.roles.get", "iam.roles.list", "iam.serviceAccounts.get", "iam.serviceAccounts.getIamPolicy",
    "iam.workloadIdentityPools.get", "iam.workloadIdentityPools.getAttestationRules",
    "iam.workloadIdentityPoolProviders.get", "iam.denypolicies.get", "iam.denypolicies.list",
    "serviceusage.services.get", "serviceusage.services.list", "serviceusage.services.use",
    "storage.buckets.get", "storage.buckets.getIamPolicy",
    "storage.managedFolders.get", "storage.managedFolders.list", "storage.managedFolders.getIamPolicy",
    "artifactregistry.repositories.get", "artifactregistry.repositories.getIamPolicy",
    "run.jobs.get", "run.jobs.getIamPolicy", "run.services.get", "run.services.getIamPolicy", "run.operations.get",
    "cloudscheduler.jobs.get", "cloudscheduler.locations.get",
    "compute.globalAddresses.get", "compute.regionNetworkEndpointGroups.get", "compute.sslCertificates.get",
    "compute.targetHttpProxies.get", "compute.targetHttpsProxies.get", "compute.globalForwardingRules.get",
    "compute.urlMaps.get", "compute.backendBuckets.get", "compute.backendBuckets.getIamPolicy",
    "compute.backendServices.get", "compute.backendServices.getIamPolicy", "compute.securityPolicies.get",
    "logging.sinks.get", "logging.exclusions.get", "logging.logMetrics.get", "logging.views.get",
    "monitoring.alertPolicies.get", "monitoring.notificationChannels.get", "monitoring.metricDescriptors.get",
    "pubsub.topics.get", "pubsub.topics.getIamPolicy", "pubsub.subscriptions.get", "pubsub.subscriptions.getIamPolicy",
    "iap.webServices.getIamPolicy",
  ]
}

resource "google_project_iam_member" "pr_plan_reader" {
  project = var.project_id
  role    = google_project_iam_custom_role.pr_plan_reader.name
  member  = google_service_account.pr_plan.member
}

resource "google_project_iam_custom_role" "pr_plan_state_reader" {
  project     = var.project_id
  role_id     = "sharedDatasetsPrPlanStateReader"
  title       = "Read PR plan state only"
  permissions = ["storage.objects.get", "storage.objects.list"]
}

resource "google_storage_bucket_iam_member" "pr_plan_state_reader" {
  bucket = var.bucket_name
  role   = google_project_iam_custom_role.pr_plan_state_reader.name
  member = google_service_account.pr_plan.member
  condition {
    title = "exact_default_state_and_workspace_listing"
    # The GCS backend enumerates workspace names during init. list is evaluated
    # on the bucket; object get remains restricted to this single state object.
    expression = "resource.name == 'projects/_/buckets/${var.bucket_name}' || resource.name == 'projects/_/buckets/${var.bucket_name}/objects/000-system/terraform/state/prod/default.tfstate'"
  }
}

resource "google_project_service" "pr_plan_troubleshooter" {
  project            = var.project_id
  service            = "policytroubleshooter.googleapis.com"
  disable_on_destroy = false
}

# Provisioned first by the existing protected state-read IAM workflow. The PR
# identity never receives this role and cannot impersonate the deployer.
resource "google_project_iam_custom_role" "pr_plan_provisioner" {
  project = var.project_id
  role_id = "sharedDatasetsPrPlanProvisioner"
  title   = "Provision isolated PR planning identity"
  permissions = [
    "iam.workloadIdentityPools.create", "iam.workloadIdentityPools.get", "iam.workloadIdentityPools.update",
    "iam.workloadIdentityPoolProviders.create", "iam.workloadIdentityPoolProviders.get", "iam.workloadIdentityPoolProviders.update",
    "serviceusage.services.enable", "serviceusage.services.get", "serviceusage.operations.get",
  ]
}

resource "google_project_iam_member" "pr_plan_provisioner" {
  project = var.project_id
  role    = google_project_iam_custom_role.pr_plan_provisioner.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

output "github_pr_plan_workload_identity_provider" {
  value     = google_iam_workload_identity_pool_provider.pr_plan.name
  sensitive = true
}

output "github_pr_plan_service_account" {
  value     = google_service_account.pr_plan.email
  sensitive = true
}
