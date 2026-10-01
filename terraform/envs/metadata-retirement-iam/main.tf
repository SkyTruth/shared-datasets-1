terraform {
  required_version = ">= 1.7.0"

  backend "gcs" {
    bucket = "skytruth-shared-datasets-1"
    prefix = "000-system/terraform/state/metadata-retirement-iam"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.31"
    }
  }
}

provider "google" {
  project = "shared-datasets-1"
}

variable "retirement_account_ids" {
  description = "Remaining immutable service account IDs from the validated retirement plan; empty after retirement."
  type        = set(string)
  default     = []
  validation {
    condition = length(setsubtract(var.retirement_account_ids, [
      "117696104962177306505",
      "104364831142635248810",
      "115924014602363410114",
    ])) == 0
    error_message = "Deletion authority is limited to the three reviewed metadata identities."
  }
}

locals {
  retired_accounts = {
    "117696104962177306505" = "metadata-index-loader"
    "104364831142635248810" = "metadata-index-loader-preview"
    "115924014602363410114" = "metadata-service-preview"
  }
}

# The provider's IAM binding schema requires an email, so verify that the live
# email still denotes the reviewed immutable identity before granting authority.
data "google_service_account" "retirement_target" {
  for_each   = var.retirement_account_ids
  project    = "shared-datasets-1"
  account_id = local.retired_accounts[each.key]
  lifecycle {
    postcondition {
      condition     = self.unique_id == each.key
      error_message = "The retired service account was recreated; review its new identity before granting deletion authority."
    }
  }
}

# Account-level authority cannot delete active preview or other project accounts.
# Separate state keeps this bootstrap from invalidating the saved retirement plan.
resource "google_service_account_iam_member" "retirement_deleter" {
  for_each           = var.retirement_account_ids
  service_account_id = data.google_service_account.retirement_target[each.key].name
  role               = "roles/iam.serviceAccountDeleter"
  member             = "serviceAccount:shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com"
}
