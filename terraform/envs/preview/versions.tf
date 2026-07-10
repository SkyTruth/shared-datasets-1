terraform {
  required_version = ">= 1.6.0"

  backend "gcs" {
    bucket = "skytruth-shared-datasets-1-terraform-state"
    prefix = "000-system/terraform/state/preview"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.31"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
