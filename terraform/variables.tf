variable "project_id" {
  description = "GCP project that owns the shared datasets bucket."
  type        = string
  default     = "shared-datasets-1"
}

variable "shared_datasets_bucket" {
  description = "Name of the shared datasets Cloud Storage bucket."
  type        = string
  default     = "skytruth-shared-datasets-1"
}

variable "cerulean_orchestrator_service_accounts" {
  description = "Cerulean Cloud Run orchestrator service accounts allowed to read shared AOI datasets."
  type        = set(string)
  default = [
    "test-cr-orch@cerulean-338116.iam.gserviceaccount.com",
    "staging-cr-orch@cerulean-338116.iam.gserviceaccount.com",
    "production-cr-orch@cerulean-338116.iam.gserviceaccount.com",
    "prod20240903-cr-orch@cerulean-338116.iam.gserviceaccount.com",
  ]
}
