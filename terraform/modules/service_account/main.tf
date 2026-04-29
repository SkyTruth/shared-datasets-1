variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "account_id" {
  description = "Service account ID without domain suffix."
  type        = string
}

variable "display_name" {
  description = "Human-readable service account display name."
  type        = string
}

resource "google_service_account" "this" {
  project      = var.project_id
  account_id   = var.account_id
  display_name = var.display_name
}

output "email" {
  value = google_service_account.this.email
}

output "member" {
  value = google_service_account.this.member
}

