variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "location" {
  description = "Cloud Run region."
  type        = string
}

variable "name" {
  description = "Cloud Run Job name."
  type        = string
}

variable "image" {
  description = "Container image URI."
  type        = string
}

variable "service_account_email" {
  description = "Runtime service account email."
  type        = string
}

variable "env" {
  description = "Environment variables for the job container."
  type        = map(string)
  default     = {}
}

variable "cpu" {
  description = "CPU limit."
  type        = string
  default     = "4"
}

variable "memory" {
  description = "Memory limit."
  type        = string
  default     = "16Gi"
}

variable "timeout" {
  description = "Task timeout."
  type        = string
  default     = "14400s"
}

variable "max_retries" {
  description = "Cloud Run task retry count."
  type        = number
  default     = 0
}

resource "google_cloud_run_v2_job" "this" {
  project             = var.project_id
  location            = var.location
  name                = var.name
  deletion_protection = false

  template {
    task_count  = 1
    parallelism = 1

    template {
      service_account = var.service_account_email
      timeout         = var.timeout
      max_retries     = var.max_retries

      containers {
        image = var.image

        resources {
          limits = {
            cpu    = var.cpu
            memory = var.memory
          }
        }

        dynamic "env" {
          for_each = var.env
          content {
            name  = env.key
            value = env.value
          }
        }
      }
    }
  }
}

output "name" {
  value = google_cloud_run_v2_job.this.name
}

output "id" {
  value = google_cloud_run_v2_job.this.id
}

