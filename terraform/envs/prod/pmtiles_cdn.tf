data "google_project" "current" {
  project_id = var.project_id
}

locals {
  shared_catalog_rows        = csvdecode(file("${path.module}/../../../catalog/shared-datasets-catalog.csv"))
  pmtiles_catalog_rows       = local.shared_catalog_rows
  pmtiles_redirector_enabled = var.pmtiles_serving_mode == "redirect"
  pmtiles_redirector_count   = 1

  pmtiles_cdn_object_paths = {
    for row in local.pmtiles_catalog_rows :
    "${lower(row.access_tier)}/${row.asset_slug}" => replace(
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

resource "google_compute_managed_ssl_certificate" "pmtiles_cdn_20260504" {
  project = var.project_id
  name    = "shared-datasets-pmtiles-cdn-20260504"

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

module "pmtiles_redirector_service_account" {
  count = local.pmtiles_redirector_count

  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "pmtiles-redirector"
  display_name = "PMTiles redirector Cloud Run service"

  depends_on = [google_project_service.required]
}

resource "google_cloud_run_v2_service" "pmtiles_redirector" {
  count = local.pmtiles_redirector_count

  project             = var.project_id
  location            = var.region
  name                = "pmtiles-redirector"
  deletion_protection = false
  ingress             = "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER"

  template {
    service_account = module.pmtiles_redirector_service_account[0].email

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }

    containers {
      image = var.pmtiles_redirector_image

      ports {
        container_port = 8080
      }

      env {
        name  = "PMTILES_ALLOWED_ORIGINS"
        value = join(",", var.pmtiles_cdn_allowed_origins)
      }

      env {
        name  = "PMTILES_ALLOWED_ORIGIN_REGEXES"
        value = join(",", var.pmtiles_cdn_allowed_origin_regexes)
      }

      env {
        name  = "PMTILES_CATALOG_TTL_SECONDS"
        value = tostring(var.pmtiles_redirector_catalog_ttl_seconds)
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
    ignore_changes = [scaling]
  }

  depends_on = [
    google_artifact_registry_repository.jobs,
    google_project_service.required,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "pmtiles_redirector_public_invoker" {
  count = local.pmtiles_redirector_count

  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.pmtiles_redirector[0].name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_compute_region_network_endpoint_group" "pmtiles_redirector" {
  count = local.pmtiles_redirector_count

  project               = var.project_id
  name                  = "shared-datasets-pmtiles-redirector"
  network_endpoint_type = "SERVERLESS"
  region                = var.region

  cloud_run {
    service = google_cloud_run_v2_service.pmtiles_redirector[0].name
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "google_compute_backend_service" "pmtiles_redirector" {
  count = local.pmtiles_redirector_count

  project               = var.project_id
  name                  = "shared-datasets-pmtiles-redirector"
  description           = "Temporary Cloud Run redirector for stable PMTiles URLs."
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTP"

  backend {
    group = google_compute_region_network_endpoint_group.pmtiles_redirector[0].id
  }
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
  description = "Expose the shared dataset catalog and tiered PMTiles URLs backed by canonical shared dataset GCS objects."

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

    path_rule {
      paths = [
        "/_catalog/shared-datasets-catalog.csv",
        "/_catalog/web/catalog.json",
        "/_catalog/*",
      ]
      service = google_compute_backend_bucket.pmtiles_cdn.self_link

      route_action {
        cors_policy {
          allow_credentials = false
          allow_headers     = ["Accept", "Content-Type", "Origin"]
          allow_methods     = ["GET", "HEAD", "OPTIONS"]
          allow_origins     = ["*"]
          disabled          = false
          expose_headers    = ["Cache-Control", "Content-Length", "Content-Type", "ETag"]
          max_age           = 3600
        }
      }
    }

    path_rule {
      paths   = ["/artifacts/*"]
      service = google_compute_backend_bucket.pmtiles_cdn.self_link

      route_action {
        cors_policy {
          allow_credentials = false
          allow_headers     = ["Accept", "Content-Type", "Origin"]
          allow_methods     = ["GET", "HEAD", "OPTIONS"]
          allow_origins     = ["*"]
          disabled          = false
          expose_headers    = ["Accept-Ranges", "Cache-Control", "Content-Encoding", "Content-Length", "Content-Range", "Content-Type", "ETag"]
          max_age           = 3600
        }

        url_rewrite {
          path_prefix_rewrite = "/"
        }
      }
    }

    path_rule {
      paths   = ["/private/*"]
      service = google_compute_backend_bucket.pmtiles_cdn.self_link

      route_action {
        cors_policy {
          allow_credentials = false
          allow_headers     = ["Accept", "Content-Type", "Origin"]
          allow_methods     = ["GET", "HEAD", "OPTIONS"]
          allow_origins     = local.pmtiles_browser_allowed_origins
          disabled          = false
          expose_headers    = ["Accept-Ranges", "Cache-Control", "Content-Encoding", "Content-Length", "Content-Range", "Content-Type", "ETag"]
          max_age           = 3600
        }

        url_rewrite {
          path_prefix_rewrite = "/"
        }
      }
    }

    dynamic "path_rule" {
      for_each = local.pmtiles_redirector_enabled ? toset(["redirect"]) : toset([])

      content {
        paths   = ["/pmtiles/*"]
        service = google_compute_backend_service.pmtiles_redirector[0].self_link
      }
    }

    dynamic "path_rule" {
      for_each = local.pmtiles_redirector_enabled ? {} : local.pmtiles_cdn_object_paths

      content {
        paths   = ["/pmtiles/${path_rule.key}.pmtiles"]
        service = google_compute_backend_bucket.pmtiles_cdn.self_link

        route_action {
          cors_policy {
            allow_credentials = split("/", path_rule.key)[0] != "public"
            allow_headers     = ["Range"]
            allow_methods     = ["GET", "HEAD", "OPTIONS"]
            allow_origins     = split("/", path_rule.key)[0] != "public" ? local.pmtiles_browser_allowed_origins : ["*"]
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
    for_each = local.pmtiles_redirector_enabled ? {} : local.pmtiles_cdn_object_paths

    content {
      host                = var.pmtiles_cdn_host
      path                = "/pmtiles/${test.key}.pmtiles"
      service             = google_compute_backend_bucket.pmtiles_cdn.self_link
      expected_output_url = "https://${var.pmtiles_cdn_host}/${test.value}"
    }
  }

  test {
    host                = var.pmtiles_cdn_host
    path                = "/_catalog/shared-datasets-catalog.csv"
    service             = google_compute_backend_bucket.pmtiles_cdn.self_link
    expected_output_url = "https://${var.pmtiles_cdn_host}/_catalog/shared-datasets-catalog.csv"
  }

  test {
    host                = var.pmtiles_cdn_host
    path                = "/_catalog/web/catalog.json"
    service             = google_compute_backend_bucket.pmtiles_cdn.self_link
    expected_output_url = "https://${var.pmtiles_cdn_host}/_catalog/web/catalog.json"
  }

  test {
    host                = var.pmtiles_cdn_host
    path                = "/artifacts/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz"
    service             = google_compute_backend_bucket.pmtiles_cdn.self_link
    expected_output_url = "https://${var.pmtiles_cdn_host}/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz"
  }

  test {
    host                = var.pmtiles_cdn_host
    path                = "/private/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz"
    service             = google_compute_backend_bucket.pmtiles_cdn.self_link
    expected_output_url = "https://${var.pmtiles_cdn_host}/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz"
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
  project = var.project_id
  name    = "shared-datasets-pmtiles-cdn"
  url_map = google_compute_url_map.pmtiles_cdn.self_link
  ssl_certificates = [
    google_compute_managed_ssl_certificate.pmtiles_cdn_20260504.self_link,
  ]
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
  load_balancing_scheme = "EXTERNAL_MANAGED"
  port_range            = "443"
  target                = google_compute_target_https_proxy.pmtiles_cdn.self_link
}

resource "google_compute_global_forwarding_rule" "pmtiles_cdn_http" {
  project               = var.project_id
  name                  = "shared-datasets-pmtiles-cdn-http"
  ip_address            = google_compute_global_address.pmtiles_cdn.address
  ip_protocol           = "TCP"
  load_balancing_scheme = "EXTERNAL_MANAGED"
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

resource "google_project_iam_member" "github_actions_pmtiles_cdn_secret_iam_policy_manager" {
  project = var.project_id
  role    = "roles/secretmanager.admin"
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"

  condition {
    title       = "pmtiles_cdn_signed_request_key_iam_policy_admin"
    description = "Allow protected Terraform workflows to manage IAM on the PMTiles CDN signing key only."
    expression = join(" || ", [
      "resource.name == 'projects/${var.project_id}/secrets/${var.pmtiles_cdn_secret_id}'",
      "resource.name == 'projects/${data.google_project.current.number}/secrets/${var.pmtiles_cdn_secret_id}'",
    ])
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "pmtiles_cdn_catalog_viewer_signer" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.pmtiles_cdn_signed_request_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = module.catalog_viewer_service_account.member

  depends_on = [
    google_project_iam_member.github_actions_pmtiles_cdn_secret_iam_policy_manager,
  ]
}
