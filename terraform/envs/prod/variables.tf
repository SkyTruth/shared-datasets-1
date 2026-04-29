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

variable "wdpa_monthly_image" {
  description = "Container image URI for the WDPA monthly Cloud Run Job."
  type        = string
}

variable "wdpa_source_url_template" {
  description = "Protected Planet monthly WDPA/WDOECM source URL template."
  type        = string
  default     = "https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_WDOECM_{month_token}_Public_all_shp.zip"
}
