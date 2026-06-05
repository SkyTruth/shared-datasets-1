locals {
  metadata_service_object_viewer_condition = join(" || ", concat(
    [
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_catalog/releases/')",
    ],
    [
      for prefix in local.canonical_dataset_top_level_prefixes :
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}${prefix}')"
    ],
  ))

  metadata_index_loader_record_creator_condition = join(" && ", [
    "resource.name.extract('${local.shared_bucket_object_resource_prefix}{asset_path}/index-loads/') != ''",
    "resource.name.endsWith('.json')",
    "!resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/')",
  ])
}

resource "google_firestore_database" "feature_metadata" {
  project                     = var.project_id
  name                        = "(default)"
  location_id                 = var.feature_metadata_firestore_location_id
  type                        = "FIRESTORE_NATIVE"
  delete_protection_state     = "DELETE_PROTECTION_ENABLED"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"

  depends_on = [google_project_service.required["firestore.googleapis.com"]]
}

module "metadata_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "metadata-service"
  display_name = "Shared datasets feature metadata service"

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

module "metadata_index_loader_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "metadata-index-loader"
  display_name = "Shared datasets feature metadata index loader"

  depends_on = [google_project_service.required["iam.googleapis.com"]]
}

resource "google_project_iam_member" "metadata_service_firestore_viewer" {
  project = var.project_id
  role    = "roles/datastore.viewer"
  member  = module.metadata_service_account.member

  depends_on = [google_firestore_database.feature_metadata]
}

resource "google_project_iam_member" "metadata_index_loader_firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = module.metadata_index_loader_service_account.member

  depends_on = [google_firestore_database.feature_metadata]
}

resource "google_storage_bucket_iam_member" "metadata_service_object_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = module.metadata_service_account.member

  condition {
    title       = "metadata_service_release_index_read"
    description = "Allow metadata service reads of generated release index/catalog metadata only."
    expression  = local.metadata_service_object_viewer_condition
  }
}

resource "google_storage_bucket_iam_member" "metadata_index_loader_object_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = module.metadata_index_loader_service_account.member

  condition {
    title       = "metadata_index_loader_bundle_read"
    description = "Allow metadata index loader reads of canonical feature metadata bundle objects only."
    expression  = local.metadata_service_object_viewer_condition
  }
}

resource "google_storage_bucket_iam_member" "metadata_index_loader_index_load_creator" {
  bucket = var.bucket_name
  role   = "roles/storage.objectCreator"
  member = module.metadata_index_loader_service_account.member

  condition {
    title       = "metadata_index_load_records_create"
    description = "Allow metadata index loader to create canonical index-load records only."
    expression  = local.metadata_index_loader_record_creator_condition
  }
}

resource "google_service_account_iam_member" "metadata_index_loader_github_wif" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.metadata_index_loader_service_account.email}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/repo:${var.github_repository}:environment:${var.github_publish_environment}"
}

resource "google_cloud_run_v2_service" "metadata_service" {
  project             = var.project_id
  location            = var.region
  name                = "metadata-service"
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  launch_stage        = "BETA"
  iap_enabled         = true

  template {
    service_account = module.metadata_service_account.email

    scaling {
      min_instance_count = 0
      max_instance_count = 20
    }

    containers {
      image = var.metadata_service_image

      ports {
        container_port = 8080
      }

      env {
        name  = "SHARED_DATASETS_BUCKET"
        value = var.bucket_name
      }

      env {
        name  = "FEATURE_METADATA_COLLECTION_ROOT"
        value = "feature_metadata"
      }

      env {
        name  = "METADATA_ALLOWED_EMAIL_DOMAINS"
        value = join(",", var.metadata_service_allowed_email_domains)
      }

      env {
        name  = "FEATURE_METADATA_MAX_IDS"
        value = tostring(var.feature_metadata_max_ids)
      }

      env {
        name  = "FEATURE_METADATA_MAX_FIELDS"
        value = tostring(var.feature_metadata_max_fields)
      }

      env {
        name  = "FEATURE_METADATA_MAX_RESPONSE_BYTES"
        value = tostring(var.feature_metadata_max_response_bytes)
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "512Mi"
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [launch_stage, scaling]
  }

  depends_on = [
    google_artifact_registry_repository.jobs,
    google_firestore_database.feature_metadata,
    google_project_service.required["run.googleapis.com"],
  ]
}

resource "google_cloud_run_v2_service_iam_member" "metadata_service_iap_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.metadata_service.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.catalog_viewer_iap_service_agent}"

  depends_on = [
    google_project_service.required["iap.googleapis.com"],
    google_project_service.required["run.googleapis.com"],
  ]
}

resource "google_iap_web_cloud_run_service_iam_member" "metadata_service_accessors" {
  for_each = var.metadata_service_iap_accessor_members

  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.metadata_service.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}
