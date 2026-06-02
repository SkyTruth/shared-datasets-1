output "preview_bucket" {
  value = google_storage_bucket.preview_bucket.name
}

output "preview_firestore_database" {
  value = google_firestore_database.feature_preview.name
}

output "preview_service_name" {
  value = google_cloud_run_v2_service.feature_preview_service.name
}

output "preview_service_uri" {
  value = google_cloud_run_v2_service.feature_preview_service.uri
}

output "preview_catalog_viewer_service_name" {
  value = google_cloud_run_v2_service.feature_preview_catalog_viewer.name
}

output "preview_catalog_viewer_uri" {
  value = google_cloud_run_v2_service.feature_preview_catalog_viewer.uri
}

output "preview_service_account" {
  value = local.preview_service_account_email
}

output "preview_index_loader_service_account" {
  value = local.preview_loader_service_account
}
