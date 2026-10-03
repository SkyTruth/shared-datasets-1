variable "wdpa_validation_image" {
  description = "Immutable image for isolated WDPA processing validation, without publishing."
  type        = string
  default     = "unused-until-validation-deploy"
}

variable "wdpa_validation_runtime_inspection" {
  description = "Inspect cgroup files in the existing isolated image without source processing."
  type        = bool
  default     = false
}

variable "wdpa_validation_staging_probe" {
  description = "Verify create-only scratch uploads with the build identity before processing."
  type        = bool
  default     = false
}

variable "wdpa_validation_image_config_digest" {
  description = "Verified Docker configuration digest of the tested build image."
  type        = string
  default     = ""
}

# Create-only staging; no dataset writes, object reads, replacement or deletion.
resource "google_project_iam_custom_role" "wdpa_build_stager" {
  project     = var.project_id
  role_id     = "wdpaBuildStager"
  title       = "WDPA immutable build stager"
  permissions = ["storage.folders.create", "storage.objects.create"]
}

resource "google_project_iam_custom_role" "wdpa_build_reader" {
  project     = var.project_id
  role_id     = "wdpaBuildReader"
  title       = "WDPA immutable build reader"
  permissions = ["storage.objects.get"]
}

resource "google_storage_bucket_iam_member" "wdpa_build_stager" {
  bucket = var.bucket_name
  role   = "projects/${var.project_id}/roles/wdpaBuildStager"
  member = module.wdpa_validation_service_account.member
  condition {
    title      = "wdpa_noncanonical_builds_only"
    expression = "resource.name.startsWith('projects/_/buckets/${var.bucket_name}/objects/_scratch/wdpa-builds/')"
  }
  depends_on = [google_project_iam_custom_role.wdpa_build_stager]
}

resource "google_storage_bucket_iam_member" "wdpa_build_folder_stager" {
  bucket = var.bucket_name
  role   = "projects/${var.project_id}/roles/wdpaBuildStager"
  member = module.wdpa_validation_service_account.member
  condition {
    title      = "wdpa_noncanonical_build_folders_only"
    expression = "resource.name.startsWith('projects/_/buckets/${var.bucket_name}/folders/_scratch/wdpa-builds/')"
  }
  depends_on = [google_project_iam_custom_role.wdpa_build_stager]
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

# Public frozen inputs need no reads. This identity only stages immutable builds.
locals {
  wdpa_validation_account_id    = "wdpa-processing-validation"
  wdpa_validation_account_email = "${local.wdpa_validation_account_id}@${var.project_id}.iam.gserviceaccount.com"
}

module "wdpa_validation_service_account" {
  source       = "../../modules/service_account"
  project_id   = var.project_id
  account_id   = local.wdpa_validation_account_id
  display_name = "WDPA processing validation (no dataset permissions)"
  depends_on   = [google_project_service.required]
}

resource "google_service_account_iam_member" "wdpa_validation_deployer" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${local.wdpa_validation_account_email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  depends_on         = [module.wdpa_validation_service_account]
}

module "wdpa_processing_validation_job" {
  source                = "../../modules/cloud_run_job"
  project_id            = var.project_id
  location              = var.region
  name                  = "wdpa-processing-validation"
  image                 = var.wdpa_validation_image
  command               = var.wdpa_validation_runtime_inspection ? jsondecode(file("${path.module}/../../../catalog/wdpa-runtime-inspection.json")).command : var.wdpa_validation_staging_probe ? jsondecode(file("${path.module}/../../../catalog/wdpa-staging-probe.json")).command : ["python", "scripts/cloud_wdpa_validation.py"]
  service_account_email = local.wdpa_validation_account_email
  cpu                   = "4"
  memory                = "8Gi"
  ephemeral_disk_size   = "100Gi"
  timeout               = "86400s"
  max_retries           = 0
  env = merge({
    TMPDIR                  = "/work/tmp"
    SHARED_DATASETS_WORKDIR = "/work/shared-datasets-1"
    }, var.wdpa_validation_runtime_inspection ? {} : {
    WDPA_BUILD_IMAGE               = var.wdpa_validation_image
    WDPA_BUILD_IMAGE_CONFIG_DIGEST = var.wdpa_validation_image_config_digest
  })
  depends_on = [module.wdpa_validation_service_account, google_service_account_iam_member.wdpa_validation_deployer]
}
