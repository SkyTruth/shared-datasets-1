# Temporary rollback retention: preserve inert identity definitions, log views and
# historical metric descriptors while the protected workflow revokes every added
# observer grant and removes its heartbeat policy and Run DATA_WRITE audit config.
# No retirement step deletes dataset objects or grants new permissions.

locals {
  operational_observer_log_buckets    = toset(["_Default", "_Required"])
  operational_heartbeat_metric        = "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2"
  legacy_operational_heartbeat_metric = "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat"
}

locals {
  # A view has one flat conjunction. The exact union of these independent
  # family views preserves the access boundary without nested Boolean filters.
  operational_observer_log_families = jsondecode(file("${path.module}/../../../catalog/operational-log-views.json"))
  operational_observer_log_views = merge([
    for bucket in local.operational_observer_log_buckets : {
      for family, selector in local.operational_observer_log_families : "${bucket}-${family}" => {
        bucket = bucket
        name   = "shared-datasets-operations-${family}"
        filter = join(" AND\n", [
          "resource.labels.project_id=\"${var.project_id}\"",
          "resource.labels.location=\"${var.region}\"",
          "resource.type=\"${selector.resource_type}\"",
          "log_id(\"${selector.log_id}\")",
        ])
      }
    }
  ]...)
}

resource "google_iam_workload_identity_pool_provider" "operational_observer" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-operational-observer"
  display_name                       = "GitHub operational observer"
  description                        = "Main-only notification observation; no production mutation authority."
  attribute_mapping = {
    "google.subject"                = "assertion.sub"
    "attribute.observer_repository" = "assertion.repository"
  }
  attribute_condition = join(" && ", [
    "assertion.repository == '${var.github_repository}'",
    "assertion.ref == 'refs/heads/main'",
    "assertion.workflow_ref == '${var.github_repository}/.github/workflows/unattended-workflow-alert.yml@refs/heads/main'",
  ])
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

module "operational_observer_service_account" {
  source       = "../../modules/service_account"
  project_id   = var.project_id
  account_id   = "shared-datasets-alert-observer"
  display_name = "Shared datasets operational observer"
  depends_on   = [google_project_service.required]
}

resource "google_logging_log_view" "operational_observations" {
  for_each    = local.operational_observer_log_views
  parent      = "projects/${var.project_id}"
  location    = "global"
  bucket      = each.value.bucket
  name        = each.value.name
  description = "One regional operational log family; collector queries select operational facts."
  filter      = each.value.filter
  depends_on  = [google_project_service.required]
}

resource "google_project_iam_custom_role" "operational_observer_reader" {
  project     = var.project_id
  role_id     = "sharedDatasetsOperationalObserver"
  title       = "Shared Datasets Operational Observer"
  description = "Read execution metadata and emit reconciliation health; no execution start or production mutation."
  permissions = [
    "run.executions.get",
    "run.executions.list",
    "monitoring.timeSeries.create",
  ]
}

resource "google_project_iam_custom_role" "operational_observer_receipts" {
  project     = var.project_id
  role_id     = "sharedDatasetsOperationalReceiptReader"
  title       = "Shared Datasets Operational Receipt Reader"
  description = "Read registered asset run receipts and canonical release manifests; no listing or dataset row artifact access."
  permissions = ["storage.objects.get"]
}

resource "google_monitoring_metric_descriptor" "operational_reconciliation_heartbeat" {
  project      = var.project_id
  type         = local.legacy_operational_heartbeat_metric
  metric_kind  = "GAUGE"
  value_type   = "INT64"
  unit         = "1"
  display_name = "Shared datasets complete operational reconciliation"
  description  = "Only complete scans, policy evaluation and durable checkpoint. Seed after absence-policy creation."
  # Persisted descriptor from the unfenced worker. Keep it managed; its samples
  # cannot satisfy the current absence policy and must never be replayed.
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_monitoring_metric_descriptor" "operational_reconciliation_heartbeat_v2" {
  project      = var.project_id
  type         = local.operational_heartbeat_metric
  metric_kind  = "GAUGE"
  value_type   = "INT64"
  unit         = "1"
  display_name = "Shared datasets fenced complete operational reconciliation"
  description  = "Complete signed cycles whose installed policy receipt still matches the worker protocol and configuration. Seed after absence-policy installation."
}

resource "google_project_iam_custom_role" "operational_provider_creator" {
  project     = var.project_id
  role_id     = "sharedDatasetsOperationalProviderCreator"
  title       = "Shared Datasets Temporary Operational Provider Creator"
  description = "Temporary protected setup creation only; no update, delete or pool mutation."
  permissions = ["iam.workloadIdentityPoolProviders.create"]
}

resource "google_project_iam_custom_role" "operational_provider_reader" {
  project     = var.project_id
  role_id     = "sharedDatasetsOperationalProviderReader"
  title       = "Shared Datasets Operational Provider Reader"
  description = "Read provider metadata for protected setup; no mutation or secret access."
  permissions = ["iam.workloadIdentityPoolProviders.get"]
}

resource "google_project_iam_custom_role" "operational_log_view_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsOperationalLogViewManager"
  title       = "Shared Datasets Operational Log View Manager"
  description = "Protected setup of eight family views across two Logging buckets; no deletion or project IAM authority."
  permissions = [
    "logging.buckets.get",
    "logging.views.create", "logging.views.get", "logging.views.list", "logging.views.update",
    "logging.views.getIamPolicy", "logging.views.setIamPolicy",
  ]
}

resource "google_project_iam_custom_role" "operational_metric_descriptor_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsOperationalMetricDescriptorManager"
  title       = "Shared Datasets Operational Metric Descriptor Manager"
  description = "Protected heartbeat descriptor setup; no metric deletion, time-series write or IAM authority."
  permissions = [
    "monitoring.metricDescriptors.create",
    "monitoring.metricDescriptors.get",
    "monitoring.metricDescriptors.list",
  ]
}

resource "google_project_iam_custom_role" "readonly_provider_updater" {
  project     = var.project_id
  role_id     = "sharedDatasetsReadonlyTrustUpdater"
  title       = "Shared Datasets Temporary Read-only Trust Updater"
  description = "Temporary protected trust repair only; no create, delete or pool mutation."
  permissions = ["iam.workloadIdentityPoolProviders.update"]
}
