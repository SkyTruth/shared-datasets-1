locals {
  scheduled_ingestion_jobs = {
    sea_ice_daily = {
      cloud_run_job_name              = module.sea_ice_daily_job.name
      scheduler_job_name              = "sea-ice-daily"
      scheduler_service_account_email = module.sea_ice_scheduler_service_account.email
    }
    wdpa_monthly = {
      cloud_run_job_name              = module.wdpa_monthly_job.name
      scheduler_job_name              = "wdpa-monthly"
      scheduler_service_account_email = module.wdpa_scheduler_service_account.email
    }
  }

  cron_alert_created_slack_channel = (
    var.cron_alert_slack_channel_name == null
    ? []
    : [google_monitoring_notification_channel.cron_alert_slack[0].name]
  )

  cron_alert_notification_channels = concat(
    var.cron_alert_notification_channels,
    local.cron_alert_created_slack_channel,
  )

  scheduled_ingestion_cloud_run_failure_filter = join(" OR ", [
    for job in values(local.scheduled_ingestion_jobs) :
    "(resource.labels.job_name=\"${job.cloud_run_job_name}\" AND protoPayload.response.metadata.annotations.\"run.googleapis.com/creator\"=\"${job.scheduler_service_account_email}\")"
  ])

  scheduled_ingestion_scheduler_failure_filter = join(" OR ", [
    for job in values(local.scheduled_ingestion_jobs) :
    "resource.labels.job_id=\"${job.scheduler_job_name}\""
  ])
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
      condition     = !var.cron_alerts_enabled || length(local.cron_alert_notification_channels) > 0
      error_message = "Cron alerts are enabled, but no notification channel is configured. Set cron_alert_notification_channels to an existing Slack notification channel, or set cron_alert_slack_channel_name and cron_alert_slack_auth_token."
    }
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
    display_name = "Scheduler-created Cloud Run Job execution failed"

    condition_matched_log {
      filter = <<-EOT
resource.type="cloud_run_job"
resource.labels.project_id="${var.project_id}"
resource.labels.location="${var.region}"
severity>=ERROR
protoPayload.serviceName="run.googleapis.com"
protoPayload.methodName="/Jobs.RunJob"
protoPayload.status.code=10
(${local.scheduled_ingestion_cloud_run_failure_filter})
EOT
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Shared datasets cron execution failed"
    content   = <<-EOT
A scheduler-created shared-datasets Cloud Run Job execution failed.

Check the failed execution and logs:

```bash
gcloud run jobs executions list --job=<job-name> --region=${var.region} --project=${var.project_id}
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="<job-name>" AND severity>=ERROR' --project=${var.project_id} --limit=20
```

This policy filters to executions whose creator is the Cloud Scheduler service account, so manual canary failures are not treated as cron failures.
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
(${local.scheduled_ingestion_scheduler_failure_filter})
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
    auto_close           = "604800s"
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
