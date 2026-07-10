resource "google_service_account" "shared_dataset_consumers" {
  for_each = var.shared_dataset_consumer_projects

  project      = each.value.project_id
  account_id   = each.value.service_account_id
  display_name = each.value.display_name
  description  = "Reads shared SkyTruth datasets from ${var.bucket_name} using ADC/service-account auth."
}

locals {
  shared_dataset_consumer_folder_grants = {
    for pair in setproduct(keys(google_service_account.shared_dataset_consumers), local.shared_bucket_managed_folder_names) :
    "${pair[0]}:${pair[1]}" => {
      consumer = pair[0]
      folder   = pair[1]
    }
  }
}

resource "google_storage_managed_folder_iam_member" "shared_dataset_consumer_object_viewers" {
  for_each = local.shared_dataset_consumer_folder_grants

  bucket         = var.bucket_name
  managed_folder = google_storage_managed_folder.shared_bucket_public_prefixes[each.value.folder].name
  role           = "roles/storage.objectViewer"
  member         = google_service_account.shared_dataset_consumers[each.value.consumer].member

  depends_on = [google_storage_bucket_iam_member.github_actions_pmtiles_managed_folder_sync]
}
