locals {
  # Name is an input contract, not a provisioning dependency of the shared
  # bucket. The usage deployment owns creating it before logging is enabled.
  dataset_usage_raw_bucket_name = "${var.bucket_name}-usage-raw"
  dataset_usage_config          = jsondecode(file("${path.module}/../../../catalog/dataset-usage.json"))
  dataset_usage_activation      = jsondecode(file("${path.module}/../../../catalog/dataset-usage-activation.json"))
  dataset_usage_collect         = local.dataset_usage_config.collection_enabled
  dataset_usage_verified = try(
    local.dataset_usage_collect &&
    local.dataset_usage_activation.configuration_sha256 == sha256(jsonencode({
      version = 1, config = local.dataset_usage_config,
      code = { for name in ["model.py", "run.py", "stream.py", "health.py"] :
      name => filesha256("${path.module}/../../../ingestion/dataset_usage/${name}") }
    })) &&
    local.dataset_usage_activation.verified_at != null &&
    toset(local.dataset_usage_activation.verified_sources) == toset(["gcs_audit", "cdn", "gcs_usage", "catalog"]) &&
    local.dataset_usage_activation.estimated_monthly_cost_usd >= 0 &&
    local.dataset_usage_activation.estimated_monthly_cost_usd <= 25,
    false
  )
  dataset_usage_probe_paths = [
    for tier in ["public", "restricted"] :
    [for row in local.shared_catalog_rows : replace(row.canonical_path, "gs://${var.bucket_name}/", "")
      if(tier == "public" ? row.access_tier == "public" : row.access_tier != "public")
    ][0]
  ]
}

resource "google_storage_bucket" "dataset_usage_raw" {
  project                     = var.project_id
  name                        = local.dataset_usage_raw_bucket_name
  location                    = "US"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  soft_delete_policy { retention_duration_seconds = 0 }
  lifecycle_rule {
    action { type = "Delete" }
    condition {
      age        = local.dataset_usage_config.raw_retention_days
      with_state = "ANY"
    }
  }
  lifecycle { prevent_destroy = true }
}

resource "google_storage_bucket" "dataset_usage_state" {
  project                     = var.project_id
  name                        = "${var.bucket_name}-usage-state"
  location                    = "US"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  soft_delete_policy { retention_duration_seconds = 0 }
  # Current last-observed timestamps survive inside every new ledger snapshot.
  lifecycle_rule {
    action { type = "Delete" }
    condition { age = 460 }
  }
  lifecycle { prevent_destroy = true }
}

resource "google_logging_project_sink" "dataset_usage" {
  project                = var.project_id
  name                   = "dataset-usage"
  destination            = "storage.googleapis.com/${google_storage_bucket.dataset_usage_raw.name}"
  filter                 = local.dataset_usage_config.sink_filter
  disabled               = !local.dataset_usage_collect
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
  filter      = local.dataset_usage_config.sink_filter
  disabled    = !local.dataset_usage_collect
  depends_on  = [google_storage_bucket_iam_member.dataset_usage_sink_writer]
}

resource "google_storage_bucket_iam_member" "dataset_usage_storage_logger" {
  bucket = google_storage_bucket.dataset_usage_raw.name
  role   = "roles/storage.objectCreator"
  member = "group:cloud-storage-analytics@google.com"
}

module "dataset_usage_service_account" {
  source       = "../../modules/service_account"
  project_id   = var.project_id
  account_id   = "dataset-usage"
  display_name = "Passive dataset usage processor"
}

module "dataset_usage_scheduler_service_account" {
  source       = "../../modules/service_account"
  project_id   = var.project_id
  account_id   = "dataset-usage-scheduler"
  display_name = "Dataset usage scheduler"
}

resource "google_storage_bucket_iam_member" "dataset_usage_raw_reader" {
  bucket = google_storage_bucket.dataset_usage_raw.name
  role   = "roles/storage.objectViewer"
  member = module.dataset_usage_service_account.member
}

resource "google_storage_bucket_iam_member" "dataset_usage_state_writer" {
  bucket = google_storage_bucket.dataset_usage_state.name
  role   = "roles/storage.objectUser"
  member = module.dataset_usage_service_account.member
}

resource "google_storage_bucket_iam_member" "dataset_usage_viewer" {
  bucket = google_storage_bucket.dataset_usage_state.name
  role   = "roles/storage.objectViewer"
  member = module.catalog_viewer_service_account.member
  condition {
    title      = "published_usage_reports_only"
    expression = "resource.name.startsWith('projects/_/buckets/${google_storage_bucket.dataset_usage_state.name}/objects/published/')"
  }
}

resource "google_storage_bucket_iam_member" "dataset_usage_probe_reader" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = module.dataset_usage_service_account.member
  condition {
    title      = "usage_probe_sentinels_only"
    expression = join(" || ", [for path in concat(local.dataset_usage_probe_paths, ["_catalog/shared-datasets-catalog.csv"]) : "resource.name == '${local.shared_bucket_object_resource_prefix}${path}'"])
  }
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

resource "google_storage_bucket_iam_member" "dataset_usage_configuration_reader" {
  bucket = var.bucket_name
  role   = google_project_iam_custom_role.dataset_usage_bucket_health.name
  member = module.dataset_usage_service_account.member
}

resource "google_storage_bucket_iam_member" "dataset_usage_raw_configuration_reader" {
  bucket = google_storage_bucket.dataset_usage_raw.name
  role   = google_project_iam_custom_role.dataset_usage_bucket_health.name
  member = module.dataset_usage_service_account.member
}

module "dataset_usage_job" {
  source                = "../../modules/cloud_run_job"
  project_id            = var.project_id
  location              = var.region
  name                  = "dataset-usage"
  image                 = var.dataset_usage_image
  service_account_email = module.dataset_usage_service_account.email
  cpu                   = "1"
  memory                = "1Gi"
  timeout               = "1800s"
  max_retries           = 0
  env = {
    GOOGLE_CLOUD_PROJECT       = var.project_id
    SHARED_DATASETS_BUCKET     = var.bucket_name
    DATASET_USAGE_RAW_BUCKET   = google_storage_bucket.dataset_usage_raw.name
    DATASET_USAGE_STATE_BUCKET = google_storage_bucket.dataset_usage_state.name
  }
  depends_on = [
    google_storage_bucket_iam_member.dataset_usage_raw_reader,
    google_storage_bucket_iam_member.dataset_usage_state_writer,
    google_storage_bucket_iam_member.dataset_usage_probe_reader,
    google_project_iam_member.dataset_usage_health,
    google_storage_bucket_iam_member.dataset_usage_configuration_reader,
    google_storage_bucket_iam_member.dataset_usage_raw_configuration_reader,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "dataset_usage_scheduler_invoker" {
  project  = var.project_id
  location = var.region
  name     = module.dataset_usage_job.name
  role     = "roles/run.invoker"
  member   = module.dataset_usage_scheduler_service_account.member
}

module "dataset_usage_scheduler" {
  source                = "../../modules/scheduler_job"
  project_id            = var.project_id
  region                = var.region
  name                  = "dataset-usage"
  description           = "Aggregate passive dataset observations; no lifecycle mutations."
  schedule              = "0 9 * * *"
  time_zone             = "UTC"
  target_job_location   = var.region
  target_job_name       = module.dataset_usage_job.name
  service_account_email = module.dataset_usage_scheduler_service_account.email
  paused                = !local.dataset_usage_verified
  retry_count           = 0
  depends_on            = [google_cloud_run_v2_job_iam_member.dataset_usage_scheduler_invoker]
}

variable "dataset_usage_image" {
  description = "Digest of the exact tested dataset usage image."
  type        = string
  default     = "unused-until-protected-dataset-usage-deploy"
}

resource "google_project_iam_custom_role" "dataset_usage_deployer" {
  project = var.project_id
  role_id = "sharedDatasetsUsageDeployer"
  title   = "Dataset usage infrastructure deployer"
  permissions = [
    "logging.sinks.create", "logging.sinks.get", "logging.sinks.list", "logging.sinks.update",
    "logging.exclusions.create", "logging.exclusions.get", "logging.exclusions.update",
    "storage.buckets.create", "storage.buckets.get", "storage.buckets.getIamPolicy", "storage.buckets.setIamPolicy", "storage.buckets.update",
    "cloudscheduler.jobs.create", "cloudscheduler.jobs.get", "cloudscheduler.jobs.update", "cloudscheduler.jobs.pause", "cloudscheduler.jobs.enable",
    "iam.serviceAccounts.create", "iam.serviceAccounts.get", "iam.serviceAccounts.getIamPolicy", "iam.serviceAccounts.setIamPolicy",
    "run.jobs.create", "run.jobs.get", "run.jobs.getIamPolicy", "run.jobs.setIamPolicy", "run.jobs.update", "run.jobs.run", "run.jobs.runWithOverrides",
    "run.executions.get", "run.executions.list", "run.operations.get",
  ]
}

resource "google_project_iam_member" "github_actions_dataset_usage_deployer" {
  project = var.project_id
  role    = google_project_iam_custom_role.dataset_usage_deployer.name
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

resource "google_service_account_iam_member" "dataset_usage_deployer_act_as" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.dataset_usage_service_account.email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

resource "google_service_account_iam_member" "dataset_usage_scheduler_deployer_act_as" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.dataset_usage_scheduler_service_account.email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

resource "google_project_iam_custom_role" "dataset_usage_bucket_health" {
  project     = var.project_id
  role_id     = "sharedDatasetsUsageBucketHealth"
  title       = "Dataset usage bucket configuration reader"
  permissions = ["storage.buckets.get"]
}
