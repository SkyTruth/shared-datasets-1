variable "project_id" {
  description = "GCP project ID that owns the replaceable feature branch preview slot."
  type        = string
  default     = "shared-datasets-1"
}

variable "region" {
  description = "Default GCP region for preview Cloud Run."
  type        = string
  default     = "us-central1"
}

variable "preview_bucket_name" {
  description = "Disposable bucket used by the preview service."
  type        = string
  default     = "skytruth-shared-datasets-1-preview"
}

variable "preview_service_image" {
  description = "Container image URI for the preview Cloud Run service."
  type        = string
  default     = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/metadata-service:bootstrap-required"
}

variable "metadata_service_name" {
  description = "Cloud Run service name for the replaceable preview slot."
  type        = string
  default     = "metadata-service-preview"
}

variable "metadata_service_account_id" {
  description = "Service account ID for the preview service."
  type        = string
  default     = "metadata-service-preview"
}

variable "metadata_index_loader_service_account_id" {
  description = "Service account ID for preview load workflows."
  type        = string
  default     = "metadata-index-loader-preview"
}

variable "feature_metadata_firestore_database_id" {
  description = "Named Firestore Native database used only by the preview slot."
  type        = string
  default     = "feature-metadata-preview"
}

variable "feature_metadata_collection_root" {
  description = "Root Firestore collection for preview documents."
  type        = string
  default     = "feature_metadata"
}

variable "metadata_service_iap_accessor_members" {
  description = "IAM members allowed through direct Cloud Run IAP to the preview service run.app URL."
  type        = set(string)
  default     = ["domain:skytruth.org"]
}

variable "metadata_service_allowed_email_domains" {
  description = "Email domains accepted by the preview metadata service after IAP authentication."
  type        = list(string)
  default     = ["skytruth.org"]
}

variable "feature_metadata_max_ids" {
  description = "Maximum feature IDs accepted by one metadata lookup request."
  type        = number
  default     = 500
}

variable "feature_metadata_max_fields" {
  description = "Maximum projected fields accepted by one metadata lookup request."
  type        = number
  default     = 500
}

variable "feature_metadata_max_response_bytes" {
  description = "Maximum JSON response size for one metadata lookup request."
  type        = number
  default     = 10485760
}

variable "preview_ref" {
  description = "Git ref deployed into the replaceable preview slot, for Cloud Run environment metadata only."
  type        = string
  default     = ""
}

variable "preview_deploy_sha" {
  description = "Git SHA deployed into the replaceable preview slot, for Cloud Run environment metadata only."
  type        = string
  default     = ""
}
