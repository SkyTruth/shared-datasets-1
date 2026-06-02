data "google_project" "current" {
  project_id = var.project_id
}

locals {
  iap_service_agent              = "service-${data.google_project.current.number}@gcp-sa-iap.iam.gserviceaccount.com"
  preview_service_account_email  = "${var.metadata_service_account_id}@${var.project_id}.iam.gserviceaccount.com"
  preview_service_account_member = "serviceAccount:${local.preview_service_account_email}"
  preview_loader_service_account = "${var.metadata_index_loader_service_account_id}@${var.project_id}.iam.gserviceaccount.com"
  preview_loader_member          = "serviceAccount:${local.preview_loader_service_account}"
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

removed {
  from = module.metadata_service_account.google_service_account.this

  lifecycle {
    destroy = false
  }
}

removed {
  from = module.metadata_index_loader_service_account.google_service_account.this

  lifecycle {
    destroy = false
  }
}

removed {
  from = google_service_account_iam_member.metadata_index_loader_github_wif

  lifecycle {
    destroy = false
  }
}

resource "google_storage_bucket_iam_member" "metadata_service_preview_object_viewer" {
  bucket = google_storage_bucket.preview_bucket.name
  role   = "roles/storage.objectViewer"
  member = local.preview_service_account_member
}

resource "google_storage_bucket_iam_member" "metadata_index_loader_preview_object_viewer" {
  bucket = google_storage_bucket.preview_bucket.name
  role   = "roles/storage.objectViewer"
  member = local.preview_loader_member
}

resource "google_storage_bucket_iam_member" "metadata_index_loader_preview_index_load_creator" {
  bucket = google_storage_bucket.preview_bucket.name
  role   = "roles/storage.objectCreator"
  member = local.preview_loader_member
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
    service_account = local.preview_service_account_email

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      image = var.preview_service_image

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
        name  = "PREVIEW_REF"
        value = var.preview_ref
      }

      env {
        name  = "PREVIEW_DEPLOY_SHA"
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
