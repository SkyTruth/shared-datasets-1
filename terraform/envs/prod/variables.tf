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

variable "shared_bucket_public_object_viewer_enabled" {
  description = "Keep the temporary bucket-wide allUsers objectViewer grant in place. Set false only after public managed-folder grants are applied and verified."
  type        = bool
  default     = true
}

variable "shared_dataset_consumer_projects" {
  description = "Consumer GCP projects that get a shared-datasets-reader service account and read access to the shared datasets bucket."
  type = map(object({
    project_id         = string
    service_account_id = optional(string, "shared-datasets-reader")
    display_name       = optional(string, "Shared datasets reader")
  }))
  default = {
    cerulean = {
      project_id   = "cerulean-338116"
      display_name = "Shared datasets reader for Cerulean"
    }
    thirty_by_thirty = {
      project_id   = "x30-399415"
      display_name = "Shared datasets reader for 30x30"
    }
    monitor = {
      project_id   = "skytruth-monitor"
      display_name = "Shared datasets reader for Monitor"
    }
    skytruth_tech = {
      project_id   = "skytruth-tech"
      display_name = "Shared datasets reader for SkyTruthTech"
    }
  }
}

variable "artifact_registry_repository" {
  description = "Artifact Registry Docker repository for ingestion job images."
  type        = string
  default     = "shared-datasets-jobs"
}

variable "pmtiles_cdn_host" {
  description = "Public hostname for CDN-mediated PMTiles browser access."
  type        = string
  default     = "tiles.skytruth.org"
}

variable "pmtiles_serving_mode" {
  description = "How tiered /pmtiles/* URLs are served: redirect uses Cloud Run 307 redirects to public GCS today; cdn uses the Cloud CDN backend bucket."
  type        = string
  default     = "redirect"

  validation {
    condition     = contains(["redirect", "cdn"], var.pmtiles_serving_mode)
    error_message = "pmtiles_serving_mode must be either redirect or cdn."
  }
}

variable "pmtiles_redirector_image" {
  description = "Container image URI for the temporary PMTiles redirector Cloud Run service. Override with an immutable tag for production deploys."
  type        = string
  default     = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/pmtiles-redirector@sha256:b8d3e905c29a4c8b7b44ed14c95dab4c9faad23f15cfca212a55182eb810c554"
}

variable "pmtiles_redirector_catalog_ttl_seconds" {
  description = "Seconds the PMTiles redirector caches the shared datasets catalog before refreshing."
  type        = number
  default     = 300
}

variable "pmtiles_cdn_allowed_origins" {
  description = "Exact browser origins allowed to make credentialed PMTiles range requests through the Cloud CDN backend bucket. External Cloud CDN URL-map CORS does not support regex origins."
  type        = list(string)
  default = [
    "http://localhost:3000",
    "https://localhost:3000",
    "https://feature-three.cerulean.skytruth.org",
    "https://test.cerulean.skytruth.org",
    "https://develop.cerulean.skytruth.org",
    "https://cerulean.skytruth.org",
    "https://30x30.skytruth.org",
    "https://monitor.skytruth.org",
  ]
}

variable "pmtiles_cdn_allowed_origin_regexes" {
  description = "Regular expressions for browser origins allowed by the temporary PMTiles redirector. Cloud CDN backend-bucket CORS cannot use these regexes."
  type        = list(string)
  default = [
    "^https://(?:[A-Za-z0-9-]+\\.)+skytruth\\.org$",
  ]
}

variable "pmtiles_cdn_signed_request_key_name" {
  description = "Cloud CDN signed request key name used by Cerulean when signing PMTiles cookies. The raw key value is not managed in Terraform state."
  type        = string
  default     = "shared-datasets-pmtiles-v1"
}

variable "pmtiles_cdn_secret_id" {
  description = "Secret Manager secret that stores the raw Cloud CDN signed request key for authorized cookie-signing runtimes. Versions are added outside Terraform."
  type        = string
  default     = "pmtiles-cdn-signed-request-key"
}

variable "pmtiles_cdn_grant_fill_service_account" {
  description = "Grant the Cloud CDN fill service account objectViewer access to the shared bucket. Enable after a signed request key has created the Google-managed service account."
  type        = bool
  default     = false
}

variable "cerulean_pmtiles_cookie_signer_service_accounts" {
  description = "Service account members allowed to read the PMTiles CDN signing key secret, such as serviceAccount:name@project.iam.gserviceaccount.com."
  type        = set(string)
  default     = []
}
