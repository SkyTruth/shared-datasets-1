# Bootstrap input pinned by both approved WDPA reset inventories. The runtime
# verifies its generation and SHA-256; later releases use committed translations.
resource "google_storage_bucket_iam_member" "wdpa_reset_translation_reader" {
  bucket = data.google_storage_bucket.shared_bucket_iam.name
  role   = "roles/storage.objectViewer"
  member = module.wdpa_job_service_account.member

  condition {
    title       = "wdpa_approved_reset_translation_input"
    description = "Read only the approved WDPA reset supplement; no scratch-prefix access."
    expression  = "resource.name == '${local.shared_bucket_object_resource_prefix}_scratch/pending-publishes/wdpa-feature-id-reset/pr-154-20260930/gap-supplement.ndjson'"
  }
}
