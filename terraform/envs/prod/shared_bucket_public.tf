resource "google_storage_bucket_iam_member" "shared_bucket_public_object_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = "allUsers"
}
