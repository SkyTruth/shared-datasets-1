variable "wdpa_validation_image" {
  description = "Immutable image for isolated WDPA processing validation, without publishing."
  type        = string
  default     = "unused-until-validation-deploy"
}

# Public frozen inputs need no bucket IAM. This identity cannot publish datasets.
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
}

module "wdpa_processing_validation_job" {
  source                = "../../modules/cloud_run_job"
  project_id            = var.project_id
  location              = var.region
  name                  = "wdpa-processing-validation"
  image                 = var.wdpa_validation_image
  command               = ["python", "scripts/cloud_wdpa_validation.py"]
  service_account_email = local.wdpa_validation_account_email
  cpu                   = "4"
  memory                = "8Gi"
  ephemeral_disk_size   = "100Gi"
  timeout               = "86400s"
  max_retries           = 0
  env = {
    TMPDIR                  = "/work/tmp"
    SHARED_DATASETS_WORKDIR = "/work/shared-datasets-1"
  }
  depends_on = [module.wdpa_validation_service_account, google_service_account_iam_member.wdpa_validation_deployer]
}
