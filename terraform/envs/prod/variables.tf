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

variable "github_actions_terraform_service_account_email" {
  description = "Service account used by approved GitHub Actions Terraform workflows that also push deploy images."
  type        = string
  default     = "shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com"
}

variable "catalog_viewer_image" {
  description = "Container image URI for the authenticated catalog viewer Cloud Run service. Override with an immutable tag for production deploys."
  type        = string
  default     = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/catalog-viewer:bootstrap-required"
}

variable "catalog_viewer_iap_accessor_members" {
  description = "IAM members allowed through direct Cloud Run IAP to the authenticated catalog viewer run.app URL."
  type        = set(string)
  default     = ["domain:skytruth.org"]
}

variable "catalog_viewer_allowed_email_domains" {
  description = "Email domains accepted by the catalog viewer service after IAP authentication for restricted PMTiles signing."
  type        = list(string)
  default     = ["skytruth.org"]
}

variable "catalog_viewer_signed_url_ttl_seconds" {
  description = "TTL for restricted PMTiles V4 signed GCS URLs returned by the catalog viewer."
  type        = number
  default     = 900
}

variable "catalog_viewer_catalog_ttl_seconds" {
  description = "Seconds the catalog viewer caches the generated catalog.json before re-reading GCS."
  type        = number
  default     = 60
}

variable "feature_metadata_firestore_location_id" {
  description = "Location for the Firestore Native database used as a rebuildable feature metadata serving index. This is immutable after creation."
  type        = string
  default     = "nam5"
}

variable "metadata_service_image" {
  description = "Container image URI for the IAP-protected feature metadata Cloud Run service. Override with an immutable tag for production deploys."
  type        = string
  default     = "us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/metadata-service:bootstrap-required"
}

variable "metadata_service_iap_accessor_members" {
  description = "IAM members allowed through direct Cloud Run IAP to the feature metadata service run.app URL."
  type        = set(string)
  default     = ["domain:skytruth.org"]
}

variable "metadata_service_allowed_email_domains" {
  description = "Email domains accepted by the metadata service after IAP authentication."
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

variable "github_repository" {
  description = "GitHub repository allowed to impersonate the approved publisher service account."
  type        = string
  default     = "SkyTruth/shared-datasets-1"
}

variable "github_publish_environment" {
  description = "GitHub environment whose approved workflow runs can impersonate the publisher service account."
  type        = string
  default     = "shared-datasets-production"
}

variable "github_workload_identity_pool_id" {
  description = "Workload Identity Pool ID used by GitHub Actions for this repository. Override if the existing pool is not named github."
  type        = string
  default     = "github"
}

variable "github_workload_identity_pool_provider_id" {
  description = "Workload Identity Pool provider ID for GitHub Actions OIDC tokens."
  type        = string
  default     = "github-actions"
}

variable "github_readonly_workload_identity_pool_provider_id" {
  description = "Workload Identity Pool provider ID for read-only GitHub Actions bucket checks."
  type        = string
  default     = "github-actions-readonly"
}

variable "shared_datasets_breakglass_group_email" {
  description = "Emergency-only Google group exempted from optional canonical destructive-action deny rules."
  type        = string
  default     = "shared-datasets-breakglass@skytruth.org"
}

variable "scratch_writer_members" {
  description = "IAM members allowed to write only noncanonical staging objects under _scratch/."
  type        = set(string)
  default = [
    "user:jona@skytruth.org",
  ]
}

variable "canonical_mutation_deny_policy_enabled" {
  description = "Whether to create the optional project-level deny policy for canonical object delete/move and managed-folder deletion. Requires roles/iam.denyAdmin at the organization scope, so the default project-scope apply leaves this disabled."
  type        = bool
  default     = false
}

variable "canonical_mutation_denied_principals" {
  description = "Optional deny-policy principals blocked from destructive canonical storage actions when canonical_mutation_deny_policy_enabled is true. The default covers all principals, with explicit exceptions in canonical_mutation_iam.tf."
  type        = list(string)
  default = [
    "principalSet://goog/public:all",
  ]
}

variable "extra_canonical_mutation_deny_exception_principals" {
  description = "Additional optional deny-policy exception principals for tightly controlled IaC or incident-response identities."
  type        = set(string)
  default     = []
}
