locals {
  cron_alert_created_slack_channel = (
    var.cron_alert_slack_channel_name == null
    ? []
    : [google_monitoring_notification_channel.cron_alert_slack[0].name]
  )

  cron_alert_notification_channels = concat(
    var.cron_alert_notification_channels,
    local.cron_alert_created_slack_channel,
  )

  dataset_delete_excluded_prefix_filter = join(" AND ", [
    for prefix in var.dataset_delete_alert_excluded_prefixes :
    "NOT protoPayload.resourceName=~\"/objects/${replace(prefix, "/", "\\/")}\""
  ])

  dataset_write_allowed_principal_filter = join(" AND ", [
    for email in local.canonical_write_allowed_principal_emails :
    "protoPayload.authenticationInfo.principalEmail!=\"${email}\""
  ])

  dataset_write_method_filter = <<-EOT
(
  protoPayload.methodName="storage.objects.create" OR
  protoPayload.methodName="storage.objects.copy" OR
  protoPayload.methodName="storage.objects.rewrite" OR
  protoPayload.methodName="storage.objects.update" OR
  protoPayload.methodName="storage.objects.move"
)
EOT
}

resource "google_monitoring_notification_channel" "cron_alert_slack" {
  count = var.cron_alert_slack_channel_name == null ? 0 : 1

  project      = var.project_id
  display_name = "Shared datasets cron alerts"
  description  = "Slack notifications for unexpected shared-datasets scheduled ingestion failures."
  type         = "slack"
  enabled      = true

  labels = {
    channel_name = var.cron_alert_slack_channel_name
  }

  sensitive_labels {
    auth_token = var.cron_alert_slack_auth_token
  }

  depends_on = [google_project_service.required]
}

resource "terraform_data" "cron_alert_channel_configured" {
  input = local.cron_alert_notification_channels

  lifecycle {
    precondition {
      condition     = (!var.cron_alerts_enabled && !var.dataset_delete_alerts_enabled && !var.dataset_write_alerts_enabled && !var.dataset_schema_alerts_enabled) || length(local.cron_alert_notification_channels) > 0
      error_message = "Monitoring alerts are enabled, but no notification channel is configured. Set cron_alert_notification_channels to an existing Slack notification channel, or set cron_alert_slack_channel_name and cron_alert_slack_auth_token."
    }
  }
}

resource "google_secret_manager_secret" "slack_webhook_url" {
  project   = var.project_id
  secret_id = "shared-datasets-slack-webhook-url"

  replication {
    auto {}
  }

  depends_on = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "slack_webhook_accessors" {
  for_each = var.slack_webhook_secret_accessors

  project   = var.project_id
  secret_id = google_secret_manager_secret.slack_webhook_url.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = each.value
}

resource "google_project_iam_audit_config" "storage_data_write" {
  project = var.project_id
  service = "storage.googleapis.com"

  audit_log_config {
    log_type = "DATA_WRITE"
  }
}

resource "google_monitoring_alert_policy" "scheduled_ingestion_cloud_run_failure" {
  project      = var.project_id
  display_name = "Scheduled ingestion Cloud Run execution failed"
  combiner     = "OR"
  enabled      = var.cron_alerts_enabled
  severity     = "ERROR"

  notification_channels = local.cron_alert_notification_channels

  conditions {
    display_name = "Cloud Run Job execution failed"

    condition_matched_log {
      filter = <<-EOT
resource.type="cloud_run_job"
resource.labels.project_id="${var.project_id}"
resource.labels.location="${var.region}"
severity>=ERROR
protoPayload.serviceName="run.googleapis.com"
protoPayload.methodName="/Jobs.RunJob"
protoPayload.status.code=10
EOT
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Shared datasets cron execution failed"
    content   = <<-EOT
A shared-datasets scheduled-ingestion Cloud Run Job execution failed.

Check the failed execution and logs:

```bash
gcloud run jobs executions list --job=<job-name> --region=${var.region} --project=${var.project_id}
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="<job-name>" AND severity>=ERROR' --project=${var.project_id} --limit=20
```

This policy covers all Cloud Run Job execution failures in the shared-datasets project and region, including future scheduled-ingestion jobs and manual deploy canaries.
EOT
  }

  alert_strategy {
    auto_close           = "3600s"
    notification_prompts = ["OPENED"]

    notification_rate_limit {
      period = "3600s"
    }
  }

  user_labels = {
    component = "scheduled-ingestion"
    service   = "shared-datasets"
  }

  depends_on = [
    google_project_service.required,
    terraform_data.cron_alert_channel_configured,
  ]
}

resource "google_monitoring_alert_policy" "scheduled_ingestion_scheduler_failure" {
  project      = var.project_id
  display_name = "Scheduled ingestion dispatch failed"
  combiner     = "OR"
  enabled      = var.cron_alerts_enabled
  severity     = "ERROR"

  notification_channels = local.cron_alert_notification_channels

  conditions {
    display_name = "Cloud Scheduler failed to start a scheduled ingestion job"

    condition_matched_log {
      filter = <<-EOT
resource.type="cloud_scheduler_job"
resource.labels.project_id="${var.project_id}"
resource.labels.location="${var.region}"
severity>=ERROR
EOT
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Shared datasets scheduler dispatch failed"
    content   = <<-EOT
Cloud Scheduler failed while attempting to start a shared-datasets ingestion job.

Check the scheduler and Cloud Run job:

```bash
gcloud scheduler jobs describe <job-name> --location=${var.region} --project=${var.project_id}
gcloud logging read 'resource.type="cloud_scheduler_job" AND resource.labels.job_id="<job-name>"' --project=${var.project_id} --limit=20
```
EOT
  }

  alert_strategy {
    auto_close           = "3600s"
    notification_prompts = ["OPENED"]

    notification_rate_limit {
      period = "3600s"
    }
  }

  user_labels = {
    component = "scheduled-ingestion"
    service   = "shared-datasets"
  }

  depends_on = [
    google_project_service.required,
    terraform_data.cron_alert_channel_configured,
  ]
}

resource "google_monitoring_alert_policy" "dataset_object_deleted" {
  project      = var.project_id
  display_name = "Shared dataset object deleted"
  combiner     = "OR"
  enabled      = var.dataset_delete_alerts_enabled
  severity     = "ERROR"

  notification_channels = local.cron_alert_notification_channels

  conditions {
    display_name = "GCS object deleted outside excluded operational prefixes"

    condition_matched_log {
      filter = <<-EOT
resource.type="gcs_bucket"
resource.labels.bucket_name="${var.bucket_name}"
protoPayload.serviceName="storage.googleapis.com"
protoPayload.methodName="storage.objects.delete"
${local.dataset_delete_excluded_prefix_filter}
EOT

      label_extractors = {
        object_resource = "EXTRACT(protoPayload.resourceName)"
        principal_email = "EXTRACT(protoPayload.authenticationInfo.principalEmail)"
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "⚠️ Shared dataset object deleted"
    content   = <<-EOT
A shared dataset object was deleted from `${var.bucket_name}` outside the excluded operational prefixes.

Object resource: $${log.extracted_label.object_resource}
Principal: $${log.extracted_label.principal_email}

Review the matching audit log entry:

```bash
gcloud logging read 'resource.type="gcs_bucket" AND resource.labels.bucket_name="${var.bucket_name}" AND protoPayload.methodName="storage.objects.delete"' --project=${var.project_id} --limit=20
```
EOT
  }

  alert_strategy {
    auto_close           = "604800s"
    notification_prompts = ["OPENED"]

    notification_rate_limit {
      period = "3600s"
    }
  }

  user_labels = {
    component = "dataset-objects"
    service   = "shared-datasets"
  }

  depends_on = [
    google_project_iam_audit_config.storage_data_write,
    google_project_service.required,
    terraform_data.cron_alert_channel_configured,
  ]
}

resource "google_monitoring_alert_policy" "dataset_object_written_by_unapproved_principal" {
  project      = var.project_id
  display_name = "Shared dataset object written by unapproved principal"
  combiner     = "OR"
  enabled      = var.dataset_write_alerts_enabled
  severity     = "ERROR"

  notification_channels = local.cron_alert_notification_channels

  conditions {
    display_name = "Canonical GCS object write outside approved publisher and ingestion identities"

    condition_matched_log {
      filter = <<-EOT
resource.type="gcs_bucket"
resource.labels.bucket_name="${var.bucket_name}"
protoPayload.serviceName="storage.googleapis.com"
${local.dataset_write_method_filter}
${local.dataset_delete_excluded_prefix_filter}
${local.dataset_write_allowed_principal_filter}
EOT

      label_extractors = {
        object_resource = "EXTRACT(protoPayload.resourceName)"
        principal_email = "EXTRACT(protoPayload.authenticationInfo.principalEmail)"
        method_name     = "EXTRACT(protoPayload.methodName)"
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Shared dataset object written by unapproved principal"
    content   = <<-EOT
A shared dataset object was written outside the approved publisher and scheduled-ingestion identities.

Method: $${log.extracted_label.method_name}
Object resource: $${log.extracted_label.object_resource}
Principal: $${log.extracted_label.principal_email}

Review the matching audit log entry:

```bash
gcloud logging read 'resource.type="gcs_bucket" AND resource.labels.bucket_name="${var.bucket_name}" AND protoPayload.serviceName="storage.googleapis.com"' --project=${var.project_id} --limit=20
```
EOT
  }

  alert_strategy {
    auto_close           = "604800s"
    notification_prompts = ["OPENED"]

    notification_rate_limit {
      period = "3600s"
    }
  }

  user_labels = {
    component = "dataset-objects"
    service   = "shared-datasets"
  }

  depends_on = [
    google_project_iam_audit_config.storage_data_write,
    google_project_service.required,
    terraform_data.cron_alert_channel_configured,
  ]
}

resource "google_monitoring_alert_policy" "dataset_schema_changed" {
  project      = var.project_id
  display_name = "Shared dataset schema changed"
  combiner     = "OR"
  enabled      = var.dataset_schema_alerts_enabled
  severity     = "WARNING"

  notification_channels = local.cron_alert_notification_channels

  conditions {
    display_name = "Manual publish detected a canonical field/schema delta"

    condition_matched_log {
      filter = <<-EOT
resource.type="global"
logName="projects/${var.project_id}/logs/shared-datasets-alerts"
severity>=WARNING
jsonPayload.alert_type="dataset_schema_changed"
EOT

      label_extractors = {
        asset_slug = "EXTRACT(jsonPayload.asset_slug)"
        changes    = "EXTRACT(jsonPayload.changes_text)"
        new_fields = "EXTRACT(jsonPayload.new_fields_text)"
        old_fields = "EXTRACT(jsonPayload.old_fields_text)"
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Shared dataset schema changed"
    content   = <<-EOT
A canonical shared dataset schema changed.

Asset: $${log.extracted_label.asset_slug}

Changes:
$${log.extracted_label.changes}

Old fields:
$${log.extracted_label.old_fields}

New fields:
$${log.extracted_label.new_fields}

Review the matching structured log entry:

```bash
gcloud logging read 'logName="projects/${var.project_id}/logs/shared-datasets-alerts" AND jsonPayload.alert_type="dataset_schema_changed"' --project=${var.project_id} --limit=20
```
EOT
  }

  alert_strategy {
    auto_close           = "604800s"
    notification_prompts = ["OPENED"]

    notification_rate_limit {
      period = "3600s"
    }
  }

  user_labels = {
    component = "dataset-schema"
    service   = "shared-datasets"
  }

  depends_on = [
    google_project_service.required,
    terraform_data.cron_alert_channel_configured,
  ]
}
