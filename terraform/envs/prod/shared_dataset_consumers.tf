resource "google_service_account" "shared_dataset_consumers" {
  for_each = var.shared_dataset_consumer_projects

  project      = each.value.project_id
  account_id   = each.value.service_account_id
  display_name = each.value.display_name
  description  = "Reads shared SkyTruth datasets from ${var.bucket_name} using ADC/service-account auth."
}

resource "google_storage_bucket_iam_member" "shared_dataset_consumer_object_viewers" {
  for_each = google_service_account.shared_dataset_consumers

  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = each.value.member

  depends_on = [google_storage_bucket.shared_bucket]
}
