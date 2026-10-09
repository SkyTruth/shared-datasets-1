# The monthly worker still reads the immutable accepted build bundle.
resource "google_project_iam_custom_role" "wdpa_build_reader" {
  project     = var.project_id
  role_id     = "wdpaBuildReader"
  title       = "WDPA immutable build reader"
  permissions = ["storage.objects.get"]
}

resource "google_storage_bucket_iam_member" "wdpa_build_reader" {
  bucket = var.bucket_name
  role   = "projects/${var.project_id}/roles/wdpaBuildReader"
  member = module.wdpa_job_service_account.member
  condition {
    title      = "wdpa_noncanonical_builds_only"
    expression = "resource.name.startsWith('projects/_/buckets/${var.bucket_name}/objects/_scratch/wdpa-builds/')"
  }
  depends_on = [google_project_iam_custom_role.wdpa_build_reader]
}
