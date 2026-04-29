variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "Cloud Scheduler region."
  type        = string
}

variable "name" {
  description = "Cloud Scheduler job name."
  type        = string
}

variable "description" {
  description = "Cloud Scheduler job description."
  type        = string
  default     = ""
}

variable "schedule" {
  description = "Cron schedule."
  type        = string
}

variable "time_zone" {
  description = "Cron time zone."
  type        = string
  default     = "UTC"
}

variable "target_job_location" {
  description = "Cloud Run Job region."
  type        = string
}

variable "target_job_name" {
  description = "Cloud Run Job name."
  type        = string
}

variable "service_account_email" {
  description = "Scheduler service account used for OAuth."
  type        = string
}

resource "google_cloud_scheduler_job" "this" {
  project     = var.project_id
  region      = var.region
  name        = var.name
  description = var.description
  schedule    = var.schedule
  time_zone   = var.time_zone

  attempt_deadline = "320s"

  retry_config {
    retry_count = 3
  }

  http_target {
    http_method = "POST"
    uri = format(
      "https://%s-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/%s/jobs/%s:run",
      var.target_job_location,
      var.project_id,
      var.target_job_name,
    )
    headers = {
      "Content-Type" = "application/json"
    }
    body = base64encode("{}")

    oauth_token {
      service_account_email = var.service_account_email
    }
  }
}

output "id" {
  value = google_cloud_scheduler_job.this.id
}

