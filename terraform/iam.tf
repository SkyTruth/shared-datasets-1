resource "google_storage_bucket_iam_member" "cerulean_orchestrator_aoi_readers" {
  for_each = var.cerulean_orchestrator_service_accounts

  bucket = var.shared_datasets_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${each.value}"
}
