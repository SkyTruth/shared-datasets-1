locals {
  operational_observer_log_buckets    = toset(["_Default", "_Required"])
  operational_heartbeat_metric        = "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2"
  legacy_operational_heartbeat_metric = "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat"
  # IAM permits at most 12 logical operators per conditional role binding.
  # Each prefix/suffix branch consumes two including the outer Object guard.
  # Six assets per group keeps both read surfaces within that fixed quota.
  operational_observer_asset_groups = { for index, group in chunklist([
    for row in local.shared_catalog_rows : {
      slug = row.asset_slug
      root = split("/releases/", split("/latest/", replace(row.canonical_path, "gs://${var.bucket_name}/", ""))[0])[0]
    } if startswith(row.canonical_path, "gs://${var.bucket_name}/") && (strcontains(row.canonical_path, "/latest/") || strcontains(row.canonical_path, "/releases/"))
  ], 6) : tostring(index) => group }
  operational_observer_receipt_conditions = { for index, group in local.operational_observer_asset_groups : index => join(" && ", [
    "resource.type == 'storage.googleapis.com/Object'",
    "(${join(" || ", [for scope in group : "(resource.name.startsWith('${local.shared_bucket_object_resource_prefix}${scope.root}/runs/') && resource.name.endsWith('.json'))"])})",
  ]) }
  operational_observer_manifest_conditions = { for index, group in local.operational_observer_asset_groups : index => join(" && ", [
    "resource.type == 'storage.googleapis.com/Object'",
    "(${join(" || ", [for scope in group : "(resource.name.startsWith('${local.shared_bucket_object_resource_prefix}${scope.root}/releases/') && resource.name.endsWith('/${scope.slug}.manifest.json'))"])})",
  ]) }
  operational_observer_log_filter = <<-EOT
resource.labels.project_id="${var.project_id}"
resource.labels.location="${var.region}"
(
  (
    resource.type="cloud_run_job"
    (
      log_id("run.googleapis.com/stderr")
      OR log_id("cloudaudit.googleapis.com/activity")
      OR log_id("cloudaudit.googleapis.com/data_access")
    )
  )
  OR (resource.type="cloud_scheduler_job" log_id("cloudscheduler.googleapis.com/executions"))
)
EOT
}

# RunJob is DATA_WRITE in the current Run API. Without this capture setting a
# cold startup can fail before runtime stderr exists and disappear from shadow.
resource "google_project_iam_audit_config" "operational_run_data_write" {
  project = var.project_id
  service = "run.googleapis.com"
  audit_log_config {
    log_type = "DATA_WRITE"
  }
}

# No project-wide Logs Viewer or production mutation role is granted.
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

resource "google_service_account_iam_member" "operational_observer_wif" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.operational_observer_service_account.email}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.observer_repository/${var.github_repository}"
}

resource "google_logging_log_view" "operational_observations" {
  for_each    = local.operational_observer_log_buckets
  parent      = "projects/${var.project_id}"
  location    = "global"
  bucket      = each.value
  name        = "shared-datasets-operations"
  description = "Regional job stderr, Run audit and Scheduler execution families; collector queries select operational facts."
  filter      = local.operational_observer_log_filter
  depends_on  = [google_project_service.required, google_project_iam_audit_config.operational_run_data_write]
}

resource "google_logging_log_view_iam_member" "operational_observer" {
  for_each = local.operational_observer_log_buckets
  parent   = "projects/${var.project_id}"
  location = "global"
  bucket   = each.value
  name     = google_logging_log_view.operational_observations[each.key].name
  role     = "roles/logging.viewAccessor"
  member   = module.operational_observer_service_account.member
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

resource "google_project_iam_member" "operational_observer_reader" {
  project = var.project_id
  role    = google_project_iam_custom_role.operational_observer_reader.name
  member  = module.operational_observer_service_account.member
}

resource "google_project_iam_custom_role" "operational_observer_receipts" {
  project     = var.project_id
  role_id     = "sharedDatasetsOperationalReceiptReader"
  title       = "Shared Datasets Operational Receipt Reader"
  description = "Read registered asset run receipts and canonical release manifests; no listing or dataset row artifact access."
  permissions = ["storage.objects.get"]
}

resource "google_storage_bucket_iam_member" "operational_observer_receipts" {
  for_each = local.operational_observer_receipt_conditions
  bucket   = var.bucket_name
  role     = google_project_iam_custom_role.operational_observer_receipts.name
  member   = module.operational_observer_service_account.member
  condition {
    title       = "registered-asset-operational-receipts-${each.key}"
    description = "Read JSON receipt objects only inside each catalog-registered asset's runs directory."
    expression  = each.value
  }
}

resource "google_storage_bucket_iam_member" "operational_observer_manifests" {
  for_each = local.operational_observer_manifest_conditions
  bucket   = var.bucket_name
  role     = google_project_iam_custom_role.operational_observer_receipts.name
  member   = module.operational_observer_service_account.member
  condition {
    title       = "registered-asset-release-manifests-${each.key}"
    description = "Read only each registered asset's canonical release manifest, excluding dataset row bytes and sidecars."
    expression  = each.value
  }
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

resource "google_monitoring_alert_policy" "operational_reconciliation_unhealthy" {
  project               = var.project_id
  display_name          = "Shared datasets operational reconciliation stopped"
  combiner              = "OR"
  enabled               = var.cron_alerts_enabled
  severity              = "ERROR"
  notification_channels = local.cron_alert_notification_channels
  conditions {
    display_name = "No complete reconciliation heartbeat for 30 minutes"
    condition_absent {
      filter   = "resource.type=\"global\" AND resource.labels.project_id=\"${var.project_id}\" AND metric.type=\"${local.operational_heartbeat_metric}\""
      duration = "1800s"
      trigger {
        count = 1
      }
    }
  }
  documentation {
    mime_type = "text/markdown"
    subject   = "Operational alert coverage needs attention"
    content   = "The reconciliation pipeline has not completed successfully for 30 minutes. Inspect its scheduled main workflow, filtered-log enumeration, signed persistence and heartbeat delivery. Missing notifications do not prove healthy datasets. Seed and verify a heartbeat after policy creation, then require 24 hours of shadow coverage before operational Slack cutover. Independent security alerts remain enabled."
  }
  alert_strategy {
    auto_close           = "604800s"
    notification_prompts = ["OPENED"]
  }
  user_labels = {
    component = "operational-reconciliation"
    service   = "shared-datasets"
    kind      = "pipeline-health"
  }
  depends_on = [google_project_service.required, google_monitoring_metric_descriptor.operational_reconciliation_heartbeat_v2, terraform_data.cron_alert_channel_configured]
}

# New setup grants are limited to log-view management and metric descriptors.
# Existing protected IAM bootstrap owns identity/project/bucket policy changes;
# saved-plan permission probes must prove those prerequisites before mutation.
resource "google_project_iam_custom_role" "operational_log_view_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsOperationalLogViewManager"
  title       = "Shared Datasets Operational Log View Manager"
  description = "Protected setup of two filtered operational log views; no deletion or project IAM authority."
  permissions = [
    "logging.buckets.get",
    "logging.views.create", "logging.views.get", "logging.views.list", "logging.views.update",
    "logging.views.getIamPolicy", "logging.views.setIamPolicy",
  ]
}

resource "google_project_iam_member" "github_actions_operational_log_view_manager" {
  project = var.project_id
  role    = google_project_iam_custom_role.operational_log_view_manager.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  condition {
    title       = "operational-view-buckets-only"
    description = "No access to views in other Logging buckets."
    expression = join(" || ", [for bucket in local.operational_observer_log_buckets :
      "(resource.name == 'projects/${var.project_id}/locations/global/buckets/${bucket}' || resource.name.startsWith('projects/${var.project_id}/locations/global/buckets/${bucket}/views/'))"
    ])
  }
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

resource "google_project_iam_member" "github_actions_operational_metric_descriptor_manager" {
  project = var.project_id
  role    = google_project_iam_custom_role.operational_metric_descriptor_manager.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}
