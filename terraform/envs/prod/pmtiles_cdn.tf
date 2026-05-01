data "google_project" "current" {
  project_id = var.project_id
}

locals {
  pmtiles_catalog_rows = csvdecode(file("${path.module}/../../../catalog/shared-datasets-catalog.csv"))

  pmtiles_cdn_object_paths = {
    for row in local.pmtiles_catalog_rows :
    row.asset_slug => replace(
      replace(row.canonical_path, "/[^/]+$/", "${row.asset_slug}.pmtiles"),
      "gs://${var.bucket_name}/",
      ""
    )
    if row.status == "active" && lower(row.has_pmtiles) == "true" && contains(split(";", row.available_formats), "pmtiles")
  }
}

resource "google_compute_global_address" "pmtiles_cdn" {
  project     = var.project_id
  name        = "shared-datasets-pmtiles-cdn"
  description = "Global IP address for CDN-mediated shared PMTiles access."

  depends_on = [google_project_service.required]
}

resource "google_compute_managed_ssl_certificate" "pmtiles_cdn" {
  project = var.project_id
  name    = "shared-datasets-pmtiles-cdn"

  managed {
    domains = [var.pmtiles_cdn_host]
  }

  depends_on = [google_project_service.required]
}

resource "google_compute_backend_bucket" "pmtiles_cdn" {
  project     = var.project_id
  name        = "shared-datasets-pmtiles-cdn"
  bucket_name = var.bucket_name
  enable_cdn  = true
  description = "Cloud CDN backend bucket for shared PMTiles artifacts."

  custom_response_headers = [
    "Vary: Origin",
  ]

  cdn_policy {
    cache_mode                   = "CACHE_ALL_STATIC"
    client_ttl                   = 3600
    default_ttl                  = 3600
    max_ttl                      = 86400
    negative_caching             = true
    request_coalescing           = true
    serve_while_stale            = 86400
    signed_url_cache_max_age_sec = 86400
  }

  depends_on = [
    google_project_service.required,
    google_storage_bucket.shared_bucket,
  ]
}

resource "google_storage_bucket_iam_member" "shared_bucket_cloud_cdn_fill_object_viewer" {
  count = var.pmtiles_cdn_grant_fill_service_account ? 1 : 0

  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:service-${data.google_project.current.number}@cloud-cdn-fill.iam.gserviceaccount.com"

  depends_on = [google_project_service.required]
}

resource "google_compute_url_map" "pmtiles_cdn" {
  project     = var.project_id
  name        = "shared-datasets-pmtiles-cdn"
  description = "Expose flat /pmtiles/{asset}.pmtiles URLs backed by canonical shared dataset GCS objects."

  default_url_redirect {
    path_redirect          = "/pmtiles/"
    redirect_response_code = "FOUND"
    strip_query            = true
  }

  host_rule {
    hosts        = [var.pmtiles_cdn_host]
    path_matcher = "pmtiles"
  }

  path_matcher {
    name = "pmtiles"

    default_url_redirect {
      path_redirect          = "/pmtiles/"
      redirect_response_code = "FOUND"
      strip_query            = true
    }

    dynamic "path_rule" {
      for_each = local.pmtiles_cdn_object_paths

      content {
        paths   = ["/pmtiles/${path_rule.key}.pmtiles"]
        service = google_compute_backend_bucket.pmtiles_cdn.self_link

        route_action {
          cors_policy {
            allow_credentials = true
            allow_headers     = ["Range"]
            allow_methods     = ["GET", "HEAD", "OPTIONS"]
            allow_origins     = var.pmtiles_cdn_allowed_origins
            disabled          = false
            expose_headers    = ["Accept-Ranges", "Cache-Control", "Content-Length", "Content-Range", "ETag"]
            max_age           = 3600
          }

          url_rewrite {
            path_prefix_rewrite = "/${path_rule.value}"
          }
        }
      }
    }
  }

  dynamic "test" {
    for_each = local.pmtiles_cdn_object_paths

    content {
      host                = var.pmtiles_cdn_host
      path                = "/pmtiles/${test.key}.pmtiles"
      service             = google_compute_backend_bucket.pmtiles_cdn.self_link
      expected_output_url = "https://${var.pmtiles_cdn_host}/${test.value}"
    }
  }

  depends_on = [google_project_service.required]
}

resource "google_compute_url_map" "pmtiles_cdn_http_redirect" {
  project     = var.project_id
  name        = "shared-datasets-pmtiles-cdn-http"
  description = "Redirect HTTP PMTiles CDN requests to HTTPS."

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }

  depends_on = [google_project_service.required]
}

resource "google_compute_target_https_proxy" "pmtiles_cdn" {
  project          = var.project_id
  name             = "shared-datasets-pmtiles-cdn"
  url_map          = google_compute_url_map.pmtiles_cdn.self_link
  ssl_certificates = [google_compute_managed_ssl_certificate.pmtiles_cdn.self_link]
}

resource "google_compute_target_http_proxy" "pmtiles_cdn_http_redirect" {
  project = var.project_id
  name    = "shared-datasets-pmtiles-cdn-http"
  url_map = google_compute_url_map.pmtiles_cdn_http_redirect.self_link
}

resource "google_compute_global_forwarding_rule" "pmtiles_cdn_https" {
  project               = var.project_id
  name                  = "shared-datasets-pmtiles-cdn-https"
  ip_address            = google_compute_global_address.pmtiles_cdn.address
  ip_protocol           = "TCP"
  load_balancing_scheme = "EXTERNAL"
  port_range            = "443"
  target                = google_compute_target_https_proxy.pmtiles_cdn.self_link
}

resource "google_compute_global_forwarding_rule" "pmtiles_cdn_http" {
  project               = var.project_id
  name                  = "shared-datasets-pmtiles-cdn-http"
  ip_address            = google_compute_global_address.pmtiles_cdn.address
  ip_protocol           = "TCP"
  load_balancing_scheme = "EXTERNAL"
  port_range            = "80"
  target                = google_compute_target_http_proxy.pmtiles_cdn_http_redirect.self_link
}

resource "google_secret_manager_secret" "pmtiles_cdn_signed_request_key" {
  project   = var.project_id
  secret_id = var.pmtiles_cdn_secret_id

  labels = {
    purpose = "pmtiles-cdn-cookie-signing"
  }

  replication {
    auto {}
  }

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "pmtiles_cdn_cookie_signers" {
  for_each = var.cerulean_pmtiles_cookie_signer_service_accounts

  project   = var.project_id
  secret_id = google_secret_manager_secret.pmtiles_cdn_signed_request_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}
