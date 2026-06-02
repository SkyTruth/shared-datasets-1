output "metadata_preview_bucket" {
  value = google_storage_bucket.preview_bucket.name
}

output "metadata_preview_firestore_database" {
  value = google_firestore_database.feature_metadata_preview.name
}

output "metadata_preview_service_name" {
  value = google_cloud_run_v2_service.metadata_service_preview.name
}

output "metadata_preview_service_uri" {
  value = google_cloud_run_v2_service.metadata_service_preview.uri
}

output "metadata_preview_service_account" {
  value = module.metadata_service_account.email
}

output "metadata_preview_index_loader_service_account" {
  value = module.metadata_index_loader_service_account.email
}
