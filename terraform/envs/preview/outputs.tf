output "preview_bucket" {
  value = google_storage_bucket.preview_bucket.name
}

output "preview_firestore_database" {
  value = google_firestore_database.feature_metadata_preview.name
}

output "preview_service_name" {
  value = google_cloud_run_v2_service.metadata_service_preview.name
}

output "preview_service_uri" {
  value = google_cloud_run_v2_service.metadata_service_preview.uri
}

output "preview_service_account" {
  value = local.preview_service_account_email
}

output "preview_index_loader_service_account" {
  value = local.preview_loader_service_account
}

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
  value = local.preview_service_account_email
}

output "metadata_preview_index_loader_service_account" {
  value = local.preview_loader_service_account
}
