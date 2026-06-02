resource "google_cloud_run_v2_service" "feature_preview_catalog_viewer" {
  project             = var.project_id
  location            = var.region
  name                = var.feature_preview_catalog_viewer_service_name
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
      image = var.preview_catalog_viewer_image

      ports {
        container_port = 8080
      }

      env {
        name  = "SHARED_DATASETS_BUCKET"
        value = google_storage_bucket.preview_bucket.name
      }

      env {
        name  = "SHARED_DATASETS_SITE_PREFIX"
        value = "_catalog/web"
      }

      env {
        name  = "CATALOG_VIEWER_SIGNING_SERVICE_ACCOUNT"
        value = local.preview_service_account_email
      }

      env {
        name  = "CATALOG_VIEWER_ALLOWED_EMAIL_DOMAINS"
        value = join(",", var.feature_preview_allowed_email_domains)
      }

      env {
        name  = "CATALOG_VIEWER_SIGNED_URL_TTL_SECONDS"
        value = tostring(var.preview_catalog_viewer_signed_url_ttl_seconds)
      }

      env {
        name  = "CATALOG_VIEWER_CATALOG_TTL_SECONDS"
        value = tostring(var.preview_catalog_viewer_catalog_ttl_seconds)
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

  depends_on = [google_storage_bucket.preview_bucket]
}

resource "google_cloud_run_v2_service_iam_member" "feature_preview_catalog_viewer_iap_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.feature_preview_catalog_viewer.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.iap_service_agent}"
}

resource "google_iap_web_cloud_run_service_iam_member" "feature_preview_catalog_viewer_accessors" {
  for_each = var.feature_preview_iap_accessor_members

  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.feature_preview_catalog_viewer.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}
