locals {
  catalog_viewer_iap_service_agent = "service-${data.google_project.current.number}@gcp-sa-iap.iam.gserviceaccount.com"
  catalog_viewer_service_name      = "catalog-viewer"
  pmtiles_browser_allowed_origins = distinct(concat(
    var.pmtiles_cdn_allowed_origins,
    [data.google_cloud_run_v2_service.catalog_viewer_live.uri],
  ))
  catalog_viewer_object_viewer_condition = join(" || ", concat(
    [
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_catalog/')",
    ],
    [
      for prefix in local.canonical_dataset_top_level_prefixes :
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}${prefix}')"
    ],
  ))
}

data "google_cloud_run_v2_service" "catalog_viewer_live" {
  project  = var.project_id
  location = var.region
  name     = local.catalog_viewer_service_name
}

module "catalog_viewer_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "catalog-viewer"
  display_name = "Shared datasets catalog viewer"

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "catalog_viewer_object_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = module.catalog_viewer_service_account.member

  condition {
    title       = "catalog_and_canonical_dataset_read"
    description = "Allow catalog viewer reads of generated catalog web objects and canonical dataset objects only."
    expression  = local.catalog_viewer_object_viewer_condition
  }

  depends_on = [google_storage_bucket.shared_bucket]
}

resource "google_project_iam_custom_role" "catalog_viewer_sign_blob" {
  project     = var.project_id
  role_id     = "sharedDatasetsCatalogViewerSignBlob"
  title       = "Shared Datasets Catalog Viewer Sign Blob"
  description = "Allows the catalog viewer runtime to sign short-lived GCS PMTiles URLs with its own identity."
  permissions = ["iam.serviceAccounts.signBlob"]

  depends_on = [google_project_service.required]
}

resource "google_service_account_iam_member" "catalog_viewer_self_sign_blob" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.catalog_viewer_service_account.email}"
  role               = google_project_iam_custom_role.catalog_viewer_sign_blob.name
  member             = module.catalog_viewer_service_account.member
}

resource "google_cloud_run_v2_service" "catalog_viewer" {
  project             = var.project_id
  location            = var.region
  name                = local.catalog_viewer_service_name
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_ALL"
  launch_stage        = "BETA"
  iap_enabled         = true

  template {
    service_account = module.catalog_viewer_service_account.email

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }

    containers {
      image = var.catalog_viewer_image

      ports {
        container_port = 8080
      }

      env {
        name  = "SHARED_DATASETS_BUCKET"
        value = var.bucket_name
      }

      env {
        name  = "SHARED_DATASETS_SITE_PREFIX"
        value = "_catalog/web"
      }

      env {
        name  = "CATALOG_VIEWER_SIGNING_SERVICE_ACCOUNT"
        value = module.catalog_viewer_service_account.email
      }

      env {
        name  = "CATALOG_VIEWER_ALLOWED_EMAIL_DOMAINS"
        value = join(",", var.catalog_viewer_allowed_email_domains)
      }

      env {
        name  = "CATALOG_VIEWER_SIGNED_URL_TTL_SECONDS"
        value = tostring(var.catalog_viewer_signed_url_ttl_seconds)
      }

      env {
        name  = "CATALOG_VIEWER_METADATA_CDN_BASE_URL"
        value = "https://${var.pmtiles_cdn_host}/private"
      }

      env {
        name  = "CATALOG_VIEWER_CDN_SIGNING_KEY_NAME"
        value = var.pmtiles_cdn_signed_request_key_name
      }

      env {
        name  = "CATALOG_VIEWER_CDN_SIGNING_SECRET_ID"
        value = "${google_secret_manager_secret.pmtiles_cdn_signed_request_key.id}/versions/latest"
      }

      env {
        name  = "CATALOG_VIEWER_METADATA_CDN_SIGNED_URL_TTL_SECONDS"
        value = tostring(var.catalog_viewer_signed_url_ttl_seconds)
      }

      env {
        name  = "CATALOG_VIEWER_CATALOG_TTL_SECONDS"
        value = tostring(var.catalog_viewer_catalog_ttl_seconds)
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
    google_project_service.required,
    google_secret_manager_secret_iam_member.pmtiles_cdn_catalog_viewer_signer,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "catalog_viewer_iap_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.catalog_viewer.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${local.catalog_viewer_iap_service_agent}"

  depends_on = [google_project_service.required]
}

resource "google_iap_web_cloud_run_service_iam_member" "catalog_viewer_accessors" {
  for_each = var.catalog_viewer_iap_accessor_members

  project                = var.project_id
  location               = var.region
  cloud_run_service_name = google_cloud_run_v2_service.catalog_viewer.name
  role                   = "roles/iap.httpsResourceAccessor"
  member                 = each.value
}
