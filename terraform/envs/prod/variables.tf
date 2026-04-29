variable "project_id" {
  description = "GCP project ID."
  type        = string
  default     = "shared-datasets-1"
}

variable "region" {
  description = "Default GCP region."
  type        = string
  default     = "us-central1"
}

variable "bucket_name" {
  description = "Shared datasets bucket name."
  type        = string
  default     = "skytruth-shared-datasets-1"
}

variable "artifact_registry_repository" {
  description = "Artifact Registry Docker repository for ingestion job images."
  type        = string
  default     = "shared-datasets-jobs"
}
