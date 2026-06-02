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
  default     = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/feature-preview-service:bootstrap-required"
}

variable "preview_catalog_viewer_image" {
  description = "Container image URI for the preview catalog viewer Cloud Run service."
  type        = string
  default     = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/preview-catalog-viewer:bootstrap-required"
}

variable "feature_preview_service_name" {
  description = "Cloud Run service name for the replaceable preview slot."
  type        = string
  default     = "feature-preview-service"
}

variable "feature_preview_catalog_viewer_service_name" {
  description = "Cloud Run service name for the replaceable preview catalog viewer."
  type        = string
  default     = "feature-preview-catalog-viewer"
}

variable "feature_preview_service_account_id" {
  description = "Service account ID for the preview service."
  type        = string
  default     = "feature-preview-service"
}

variable "feature_preview_loader_service_account_id" {
  description = "Service account ID for preview load workflows."
  type        = string
  default     = "feature-preview-loader"
}

variable "feature_preview_firestore_database_id" {
  description = "Named Firestore Native database used only by the preview slot."
  type        = string
  default     = "feature-preview"
}

variable "feature_preview_collection_root" {
  description = "Root Firestore collection for preview documents."
  type        = string
  default     = "feature_preview_index"
}

variable "feature_preview_iap_accessor_members" {
  description = "IAM members allowed through direct Cloud Run IAP to the preview service run.app URL."
  type        = set(string)
  default     = ["domain:skytruth.org"]
}

variable "feature_preview_allowed_email_domains" {
  description = "Email domains accepted by the preview service after IAP authentication."
  type        = list(string)
  default     = ["skytruth.org"]
}

variable "preview_catalog_viewer_signed_url_ttl_seconds" {
  description = "TTL for preview catalog viewer signed GCS URLs."
  type        = number
  default     = 900
}

variable "preview_catalog_viewer_catalog_ttl_seconds" {
  description = "Seconds the preview catalog viewer caches catalog.json before re-reading GCS."
  type        = number
  default     = 30
}

variable "feature_preview_max_ids" {
  description = "Maximum feature IDs accepted by one feature lookup request."
  type        = number
  default     = 500
}

variable "feature_preview_max_fields" {
  description = "Maximum projected fields accepted by one feature lookup request."
  type        = number
  default     = 500
}

variable "feature_preview_max_response_bytes" {
  description = "Maximum JSON response size for one feature lookup request."
  type        = number
  default     = 10485760
}

variable "preview_ref" {
  description = "Git ref deployed into the replaceable preview slot, for Cloud Run environment variables only."
  type        = string
  default     = ""
}

variable "preview_deploy_sha" {
  description = "Git SHA deployed into the replaceable preview slot, for Cloud Run environment variables only."
  type        = string
  default     = ""
}
