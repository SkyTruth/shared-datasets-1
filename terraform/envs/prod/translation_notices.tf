locals {
  translation_notice_jobs = {
    wdpa = {
      member = module.wdpa_job_service_account.member
      assets = ["wdpa-marine", "wdpa-terrestrial"]
    }
    eamlis = {
      member = module.eamlis_job_service_account.member
      assets = ["eamlis-abandoned-mine-land-inventory"]
    }
  }
}

resource "google_secret_manager_secret_iam_member" "translation_notice" {
  for_each = local.translation_notice_jobs

  project   = var.project_id
  secret_id = google_secret_manager_secret.slack_webhook_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value.member
}

resource "google_storage_bucket_iam_member" "translation_debt_writer" {
  for_each = local.translation_notice_jobs

  bucket = var.bucket_name
  role   = "roles/storage.objectCreator"
  member = each.value.member

  condition {
    title       = "${each.key}_translation_debt_exports"
    description = "Create debt CSVs only for this job's assets; no overwrite or delete permission."
    expression = join(" || ", concat(
      ["resource.name == '${local.shared_bucket_folder_resource_prefix}_scratch/translation-debt/'"],
      flatten([for asset in each.value.assets : [
        "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/translation-debt/${asset}/')",
        "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/translation-debt/${asset}/')",
      ]]),
    ))
  }
}

resource "google_storage_bucket_iam_member" "publisher_translation_debt_writer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectCreator"
  member = module.shared_datasets_publisher_service_account.member

  condition {
    title       = "reviewed_translation_debt_exports"
    description = "Create maintenance exports after reviewed publications; no overwrite or delete permission."
    expression = join(" || ", [
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/translation-debt/')",
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/translation-debt/')",
    ])
  }
}
