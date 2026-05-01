output "artifact_registry_repository" {
  value = google_artifact_registry_repository.jobs.name
}

output "pmtiles_cdn_host" {
  value = var.pmtiles_cdn_host
}

output "pmtiles_cdn_ip_address" {
  value = google_compute_global_address.pmtiles_cdn.address
}

output "pmtiles_cdn_backend_bucket" {
  value = google_compute_backend_bucket.pmtiles_cdn.name
}

output "pmtiles_cdn_signed_request_key_name" {
  value = var.pmtiles_cdn_signed_request_key_name
}

output "pmtiles_cdn_signing_key_secret_id" {
  value = google_secret_manager_secret.pmtiles_cdn_signed_request_key.secret_id
}
