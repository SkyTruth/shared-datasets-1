variable "wdpa_build_image" {
  description = "Immutable image installed by the protected monthly build workflow."
  type        = string
  default     = "unused-until-wdpa-build-deploy"
}

variable "wdpa_build_image_config_digest" {
  description = "Verified Docker configuration digest for the monthly builder."
  type        = string
  default     = ""
}

locals {
  wdpa_builder_email = "wdpa-monthly-build@${var.project_id}.iam.gserviceaccount.com"
}

module "wdpa_builder_service_account" {
  source       = "../../modules/service_account"
  project_id   = var.project_id
  account_id   = "wdpa-monthly-build"
  display_name = "WDPA monthly builder (scratch writes only)"
  depends_on   = [google_project_service.required]
}

resource "google_project_iam_custom_role" "wdpa_monthly_build_stager" {
  project     = var.project_id
  role_id     = "wdpaMonthlyBuildStager"
  title       = "WDPA monthly immutable staging"
  permissions = ["storage.folders.create", "storage.objects.create"]
}

resource "google_storage_bucket_iam_member" "wdpa_monthly_build_stager" {
  bucket = var.bucket_name
  role   = google_project_iam_custom_role.wdpa_monthly_build_stager.name
  member = module.wdpa_builder_service_account.member
  condition {
    title = "wdpa_monthly_builds_only"
    expression = join(" || ", [
      "resource.name.startsWith('projects/_/buckets/${var.bucket_name}/objects/_scratch/wdpa-builds/wdpa-monthly-build-')",
      "resource.name.startsWith('projects/_/buckets/${var.bucket_name}/folders/_scratch/wdpa-builds/wdpa-monthly-build-')",
    ])
  }
}

resource "google_storage_bucket_iam_member" "wdpa_monthly_build_inputs" {
  bucket = var.bucket_name
  role   = "projects/${var.project_id}/roles/wdpaBuildReader"
  member = module.wdpa_builder_service_account.member
  condition {
    title = "wdpa_committed_inputs_read_only"
    expression = join(" || ", [for slug in ["wdpa-marine", "wdpa-terrestrial"] :
      "resource.name.startsWith('projects/_/buckets/${var.bucket_name}/objects/100-geographic-reference/130-protected-areas/${slug}/')"
    ])
  }
}

resource "google_storage_bucket_iam_member" "wdpa_monthly_build_evidence_reader" {
  bucket = var.bucket_name
  role   = "projects/${var.project_id}/roles/wdpaBuildReader"
  member = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  condition {
    title      = "wdpa_monthly_build_evidence"
    expression = "resource.name.startsWith('projects/_/buckets/${var.bucket_name}/objects/_scratch/wdpa-builds/wdpa-monthly-build-')"
  }
}

resource "google_service_account_iam_member" "wdpa_builder_deployer" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${local.wdpa_builder_email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  depends_on         = [module.wdpa_builder_service_account]
}

module "wdpa_monthly_build_job" {
  source                = "../../modules/cloud_run_job"
  project_id            = var.project_id
  location              = var.region
  name                  = "wdpa-monthly-build"
  image                 = var.wdpa_build_image
  command               = ["python", "-m", "ingestion.wdpa_monthly.build"]
  service_account_email = local.wdpa_builder_email
  cpu                   = "4"
  memory                = "8Gi"
  ephemeral_disk_size   = "100Gi"
  timeout               = "86400s"
  max_retries           = 0
  env = {
    GOOGLE_CLOUD_PROJECT           = var.project_id
    TMPDIR                         = "/work/tmp"
    SHARED_DATASETS_WORKDIR        = "/work/shared-datasets-1"
    WDPA_BUILD_IMAGE               = var.wdpa_build_image
    WDPA_BUILD_IMAGE_CONFIG_DIGEST = var.wdpa_build_image_config_digest
  }
  # IAM is installed separately by the constrained ingestion IAM workflow.
}
