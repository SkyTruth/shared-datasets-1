data "google_project" "current" {
  project_id = var.project_id
}

locals {
  github_workload_identity_pool_name       = "projects/${data.google_project.current.number}/locations/global/workloadIdentityPools/${var.github_workload_identity_pool_id}"
  iap_service_agent                        = "service-${data.google_project.current.number}@gcp-sa-iap.iam.gserviceaccount.com"
  preview_firestore_database_resource_name = "projects/${var.project_id}/databases/${var.feature_metadata_firestore_database_id}"
}

resource "google_storage_bucket" "preview_bucket" {
  project                     = var.project_id
  name                        = var.preview_bucket_name
  location                    = "US"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = true

  soft_delete_policy {
    retention_duration_seconds = 604800
  }

  hierarchical_namespace {
    enabled = true
  }
}

resource "google_firestore_database" "feature_metadata_preview" {
  project                     = var.project_id
  name                        = var.feature_metadata_firestore_database_id
  location_id                 = "nam5"
  type                        = "FIRESTORE_NATIVE"
  delete_protection_state     = "DELETE_PROTECTION_DISABLED"
  deletion_policy             = "DELETE"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"
}

module "metadata_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = var.metadata_service_account_id
  display_name = "Shared datasets feature metadata preview service"
}

module "metadata_index_loader_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = var.metadata_index_loader_service_account_id
  display_name = "Shared datasets feature metadata preview index loader"
}

resource "google_project_iam_member" "metadata_service_firestore_viewer" {
  project = var.project_id
  role    = "roles/datastore.viewer"
  member  = module.metadata_service_account.member

  condition {
    title       = "metadata_preview_firestore_read"
    description = "Limit preview metadata service reads to the preview Firestore database."
    expression  = "resource.name == '${local.preview_firestore_database_resource_name}' || resource.name.startsWith('${local.preview_firestore_database_resource_name}/')"
  }

  depends_on = [google_firestore_database.feature_metadata_preview]
}

resource "google_project_iam_member" "metadata_index_loader_firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = module.metadata_index_loader_service_account.member

  condition {
    title       = "metadata_preview_firestore_write"
    description = "Limit preview metadata index writes to the preview Firestore database."
    expression  = "resource.name == '${local.preview_firestore_database_resource_name}' || resource.name.startsWith('${local.preview_firestore_database_resource_name}/')"
  }

  depends_on = [google_firestore_database.feature_metadata_preview]
}

resource "google_storage_bucket_iam_member" "metadata_service_preview_object_viewer" {
  bucket = google_storage_bucket.preview_bucket.name
  role   = "roles/storage.objectViewer"
  member = module.metadata_service_account.member
}

resource "google_storage_bucket_iam_member" "metadata_index_loader_preview_object_viewer" {
  bucket = google_storage_bucket.preview_bucket.name
  role   = "roles/storage.objectViewer"
  member = module.metadata_index_loader_service_account.member
}

resource "google_storage_bucket_iam_member" "metadata_index_loader_preview_index_load_creator" {
  bucket = google_storage_bucket.preview_bucket.name
  role   = "roles/storage.objectCreator"
  member = module.metadata_index_loader_service_account.member
}

resource "google_service_account_iam_member" "metadata_index_loader_github_wif" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.metadata_index_loader_service_account.email}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${local.github_workload_identity_pool_name}/subject/repo:${var.github_repository}:environment:${var.github_environment}"
}

resource "google_cloud_run_v2_service" "metadata_service_preview" {
  project             = var.project_id
  location            = var.region
  name                = var.metadata_service_name
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  launch_stage        = "BETA"
  iap_enabled         = true

  template {
    service_account = module.metadata_service_account.email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      image = var.metadata_service_image

      ports {
        container_port = 8080
      }

      env {
        name  = "SHARED_DATASETS_BUCKET"
        value = google_storage_bucket.preview_bucket.name
      }

      env {
        name  = "FEATURE_METADATA_FIRESTORE_DATABASE"
        value = google_firestore_database.feature_metadata_preview.name
      }

      env {
        name  = "FEATURE_METADATA_COLLECTION_ROOT"
        value = var.feature_metadata_collection_root
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

      env {
        name  = "METADATA_PREVIEW_REF"
        value = var.preview_ref
      }

      env {
        name  = "METADATA_PREVIEW_DEPLOY_SHA"
        value = var.preview_deploy_sha
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

  depends_on = [google_firestore_database.feature_metadata_preview]
}

resource "google_cloud_run_v2_service_iam_member" "metadata_service_preview_iap_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.metadata_service_preview.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.iap_service_agent}"
}

resource "google_iap_web_cloud_run_service_iam_member" "metadata_service_preview_accessors" {
  for_each = var.metadata_service_iap_accessor_members

  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.metadata_service_preview.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}
