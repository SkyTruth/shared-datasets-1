variable "project_id" {
  type    = string
  default = "shared-datasets-1"
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "bucket_name" {
  type    = string
  default = "skytruth-shared-datasets-1"
}

variable "publisher_service_account_email" {
  type    = string
  default = "shared-datasets-publisher@shared-datasets-1.iam.gserviceaccount.com"
}

locals {
  shared_bucket_object_resource_prefix = "projects/_/buckets/${var.bucket_name}/objects/"
  shared_bucket_folder_resource_prefix = "projects/_/buckets/${var.bucket_name}/folders/"
  gcloud_composite_temp_condition      = "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}gcloud/tmp/parallel_composite_uploads/see_gcloud_storage_cp_help_for_details/')"

  pending_publish_source_condition = join(" || ", [
    "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/pending-publishes/')",
    "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/pending-publishes/')",
    local.gcloud_composite_temp_condition,
  ])

  pending_publish_cleanup_condition = join(" || ", [
    "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/pending-publishes/')",
    "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/pending-publishes/')",
    "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/cleanup-audit/')",
    "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/cleanup-audit/')",
    local.gcloud_composite_temp_condition,
  ])
}

# Partial prod-state root used only by the protected scratch cleanup IAM workflow.
resource "google_storage_bucket_iam_member" "shared_datasets_publisher_pending_publish_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.publisher_service_account_email}"

  condition {
    title       = "pending_publish_sources_read_only"
    description = "Allow approved publisher reads from staged pending-publish objects and orphaned gcloud composite temp parts only."
    expression  = local.pending_publish_source_condition
  }
}

resource "google_storage_bucket_iam_member" "shared_datasets_publisher_pending_publish_cleanup_user" {
  bucket = var.bucket_name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${var.publisher_service_account_email}"

  condition {
    title       = "pending_publish_cleanup"
    description = "Allow approved publisher cleanup of pending-publish scratch objects, cleanup warning markers, and orphaned gcloud composite temp parts."
    expression  = local.pending_publish_cleanup_condition
  }
}
