# Dedicated transport and private receipts; no dataset read/write permissions.
variable "monitoring_slack_image" {
  type    = string
  default = "unused-by-non-monitoring-targets"
}

variable "monitoring_slack_token_version" {
  type    = string
  default = "unused-by-non-monitoring-targets"
}

variable "monitoring_slack_channel_id" {
  type    = string
  default = ""
}

resource "google_service_account" "monitoring_slack" {
  project      = var.project_id
  account_id   = "monitoring-slack"
  display_name = "Monitoring Slack message owner"
}

resource "google_service_account" "monitoring_slack_push" {
  project      = var.project_id
  account_id   = "monitoring-slack-push"
  display_name = "Authenticated Monitoring Pub/Sub delivery"
}

resource "google_service_account_iam_member" "monitoring_slack_deployer" {
  for_each           = { runtime = google_service_account.monitoring_slack.account_id, push = google_service_account.monitoring_slack_push.account_id }
  service_account_id = "projects/${var.project_id}/serviceAccounts/${each.value}@${var.project_id}.iam.gserviceaccount.com"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

resource "google_storage_bucket" "monitoring_slack" {
  project                     = var.project_id
  name                        = "${var.bucket_name}-monitoring-slack"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning {
    enabled = true
  }
}

resource "google_storage_bucket_iam_member" "monitoring_slack_state" {
  bucket = google_storage_bucket.monitoring_slack.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.monitoring_slack.email}"
}

# The protected owner-only reconciliation command can finish log alert edits.
resource "google_storage_bucket_iam_member" "monitoring_slack_recovery" {
  bucket = google_storage_bucket.monitoring_slack.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${var.github_actions_terraform_service_account_email}"
}

resource "google_secret_manager_secret" "monitoring_slack" {
  project   = var.project_id
  secret_id = "shared-datasets-slack-bot-token"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required]
}

# Conditional project bindings avoid granting the deployer authority to alter
# unrelated secret IAM. Secret values never enter Terraform state.
resource "google_project_iam_member" "monitoring_slack_token" {
  project = var.project_id
  role    = "roles/secretmanager.secretAccessor"
  member  = "serviceAccount:${google_service_account.monitoring_slack.email}"
  condition {
    title      = "monitoring_slack_token_only"
    expression = "resource.name.startsWith('projects/${data.google_project.current.number}/secrets/shared-datasets-slack-bot-token/versions/')"
  }
}

resource "google_project_iam_member" "monitoring_slack_token_writer" {
  project = var.project_id
  role    = "roles/secretmanager.secretVersionAdder"
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  # Prechecks use the secret; AddSecretVersion authorizes versions/NEW.
  condition {
    title      = "monitoring_slack_token_versions_only"
    expression = "resource.name == 'projects/${data.google_project.current.number}/secrets/shared-datasets-slack-bot-token' || resource.name.startsWith('projects/${data.google_project.current.number}/secrets/shared-datasets-slack-bot-token/versions/')"
  }
}

# Adopt the already-enabled API without issuing an enable operation.
import {
  to = google_project_service.monitoring_slack_pubsub
  id = "${var.project_id}/pubsub.googleapis.com"
}

resource "google_project_service" "monitoring_slack_pubsub" {
  project            = var.project_id
  service            = "pubsub.googleapis.com"
  disable_on_destroy = false
}

resource "google_pubsub_topic" "monitoring_slack" {
  project    = var.project_id
  name       = "monitoring-slack"
  depends_on = [google_project_service.monitoring_slack_pubsub]
}

resource "google_cloud_run_v2_service" "monitoring_slack" {
  project             = var.project_id
  name                = "monitoring-slack"
  location            = var.region
  deletion_protection = true
  ingress             = "INGRESS_TRAFFIC_ALL"
  template {
    service_account                  = google_service_account.monitoring_slack.email
    max_instance_request_concurrency = 1
    timeout                          = "60s"
    scaling {
      max_instance_count = 1
    }
    containers {
      image = var.monitoring_slack_image
      env {
        name  = "MONITORING_SLACK_STATE_BUCKET"
        value = google_storage_bucket.monitoring_slack.name
      }
      env {
        name  = "SHARED_DATASETS_SLACK_CHANNEL_ID"
        value = var.monitoring_slack_channel_id
      }
      env {
        name = "SHARED_DATASETS_SLACK_BOT_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.monitoring_slack.secret_id
            version = var.monitoring_slack_token_version
          }
        }
      }
    }
  }
  lifecycle {
    precondition {
      condition     = can(regex("^${var.region}-docker.pkg.dev/${var.project_id}/${var.artifact_registry_repository}/monitoring-slack@sha256:[0-9a-f]{64}$", var.monitoring_slack_image))
      error_message = "Monitoring Slack must deploy the exact tested registry image digest."
    }
    precondition {
      condition     = can(regex("^[CG][A-Z0-9]{8,}$", var.monitoring_slack_channel_id))
      error_message = "Monitoring Slack requires a fixed Slack channel ID."
    }
  }
  depends_on = [google_project_iam_member.monitoring_slack_token, google_storage_bucket_iam_member.monitoring_slack_state]
}

resource "google_cloud_run_v2_service_iam_member" "monitoring_slack_push" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.monitoring_slack.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.monitoring_slack_push.email}"
}

resource "google_pubsub_subscription" "monitoring_slack" {
  project                    = var.project_id
  name                       = "monitoring-slack"
  topic                      = google_pubsub_topic.monitoring_slack.id
  ack_deadline_seconds       = 90
  message_retention_duration = "604800s"
  expiration_policy {
    ttl = ""
  }
  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }
  push_config {
    push_endpoint = google_cloud_run_v2_service.monitoring_slack.uri
    oidc_token {
      service_account_email = google_service_account.monitoring_slack_push.email
      audience              = google_cloud_run_v2_service.monitoring_slack.uri
    }
  }
  depends_on = [google_cloud_run_v2_service_iam_member.monitoring_slack_push]
}

resource "google_monitoring_notification_channel" "monitoring_slack" {
  project      = var.project_id
  display_name = "Editable Monitoring Slack incidents"
  type         = "pubsub"
  enabled      = true
  labels = {
    topic = google_pubsub_topic.monitoring_slack.id
  }
  # Native Slack policies remain untouched if the authenticated bot cannot start.
  depends_on = [google_pubsub_subscription.monitoring_slack]
}

resource "google_pubsub_topic_iam_member" "monitoring_slack_publisher" {
  project = var.project_id
  topic   = google_pubsub_topic.monitoring_slack.name
  role    = "roles/pubsub.publisher"
  member  = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-monitoring-notification.iam.gserviceaccount.com"
  # Pub/Sub uses resource-level IAM, not Secret Manager's resource conditions.
  # Creating the first Pub/Sub notification channel creates this service agent.
  depends_on = [google_monitoring_notification_channel.monitoring_slack]
}
