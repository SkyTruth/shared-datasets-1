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

output "pmtiles_serving_mode" {
  value = var.pmtiles_serving_mode
}

output "pmtiles_redirector_service_name" {
  value = try(google_cloud_run_v2_service.pmtiles_redirector[0].name, null)
}

output "pmtiles_cdn_signed_request_key_name" {
  value = var.pmtiles_cdn_signed_request_key_name
}

output "pmtiles_cdn_signing_key_secret_id" {
  value = google_secret_manager_secret.pmtiles_cdn_signed_request_key.secret_id
}

output "shared_bucket_public_managed_folders" {
  value = sort(tolist(local.shared_bucket_public_managed_folder_names))
}

output "shared_bucket_public_object_viewer_enabled" {
  value = var.shared_bucket_public_object_viewer_enabled
}

output "shared_dataset_consumer_service_accounts" {
  value = {
    for name, service_account in google_service_account.shared_dataset_consumers :
    name => service_account.email
  }
}
