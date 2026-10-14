# Disabled legacy exports are retained only until replacement proof is reviewed.
# Remove this file in the final exact-resource retirement PR; preserve buckets.
locals {
  dataset_usage_legacy_probe_filter = "resource.name == 'projects/_/buckets/skytruth-shared-datasets-1/objects/100-geographic-reference/110-boundaries/lsib/latest/lsib.fgb' || resource.name == 'projects/_/buckets/skytruth-shared-datasets-1/objects/300-infrastructure-industrial/320-mining/wri-forest-atlas-drc-mining-permits/latest/wri-forest-atlas-drc-mining-permits.fgb' || resource.name == 'projects/_/buckets/skytruth-shared-datasets-1/objects/_catalog/shared-datasets-catalog.csv'"
  dataset_usage_legacy_filter       = "(resource.type=\"gcs_bucket\" AND resource.labels.bucket_name=\"skytruth-shared-datasets-1\" AND logName:\"cloudaudit.googleapis.com%2Fdata_access\" AND protoPayload.methodName=\"storage.objects.get\") OR\n(resource.type=\"http_load_balancer\" AND resource.labels.url_map_name=\"shared-datasets-pmtiles-cdn\" AND httpRequest.requestMethod=(\"GET\" OR \"HEAD\")) OR\n(((resource.type=\"cloud_run_revision\" AND resource.labels.service_name=\"catalog-viewer\") OR (resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"dataset-usage\")) AND jsonPayload.event=(\"dataset_usage\" OR \"dataset_usage_probe\"))"
}

# Preserve the existing conditional IAM identity until the public-only grant
# has passed replacement verification. Changing its condition forces replacement.
resource "google_storage_bucket_iam_member" "dataset_usage_probe_reader" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = module.dataset_usage_service_account.member
  condition {
    title      = "usage_probe_sentinels_only"
    expression = local.dataset_usage_legacy_probe_filter
  }
}

resource "google_logging_project_sink" "dataset_usage" {
  project                = var.project_id
  name                   = "dataset-usage"
  destination            = "storage.googleapis.com/${google_storage_bucket.dataset_usage_raw.name}"
  filter                 = local.dataset_usage_legacy_filter
  disabled               = true
  unique_writer_identity = true
}

resource "google_storage_bucket_iam_member" "dataset_usage_sink_writer" {
  bucket = google_storage_bucket.dataset_usage_raw.name
  role   = "roles/storage.objectCreator"
  member = google_logging_project_sink.dataset_usage.writer_identity
}

# Only the duplicate in the project's _Default sink is excluded. The separate
# complete GCS export remains unsampled, and other sinks retain their policies.
resource "google_logging_project_exclusion" "dataset_usage_duplicate" {
  project     = var.project_id
  name        = "dataset-usage-exported-copy"
  description = "Keep tracker evidence in its short-lived private GCS export."
  filter      = local.dataset_usage_legacy_filter
  disabled    = true
  depends_on  = [google_storage_bucket_iam_member.dataset_usage_sink_writer]
}

resource "google_project_iam_custom_role" "dataset_usage_health" {
  project = var.project_id
  role_id = "sharedDatasetsUsageHealth"
  title   = "Dataset usage collection health reader"
  permissions = [
    "logging.sinks.get", "logging.exclusions.get", "monitoring.timeSeries.list",
    "resourcemanager.projects.getIamPolicy",
  ]
}

resource "google_project_iam_member" "dataset_usage_health" {
  project = var.project_id
  role    = google_project_iam_custom_role.dataset_usage_health.name
  member  = module.dataset_usage_service_account.member
}
