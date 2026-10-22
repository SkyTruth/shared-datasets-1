locals {
  slack_webhook_secret_id = "shared-datasets-slack-webhook-url"

  cron_alert_created_slack_channel = (
    var.cron_alert_slack_channel_name == null
    ? []
    : [google_monitoring_notification_channel.cron_alert_slack[0].name]
  )

  cron_alert_notification_channels = concat(
    var.cron_alert_notification_channels,
    local.cron_alert_created_slack_channel,
  )

  operational_slack_rollout = jsondecode(file("${path.module}/../../../catalog/operational-slack-rollout.json"))
  operational_alert_notification_channels = (
    local.operational_slack_rollout.mode == "unified"
    ? []
    : local.cron_alert_notification_channels
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
      condition     = local.operational_slack_rollout.schema == "shared-datasets-operational-slack-rollout-v1" && contains(["native", "unified"], local.operational_slack_rollout.mode)
      error_message = "Operational routing requires an explicit reviewed native/unified rollout configuration."
    }
    precondition {
      condition     = local.operational_slack_rollout.mode != "unified" || local.operational_slack_rollout.cutover_evidence != null
      error_message = "Unified operational routing requires checked-in immutable cutover evidence verified by the protected saved-plan gate."
    }
    precondition {
      condition     = (!var.cron_alerts_enabled && !var.dataset_delete_alerts_enabled && !var.dataset_write_alerts_enabled) || length(local.cron_alert_notification_channels) > 0
      error_message = "Monitoring alerts are enabled, but no notification channel is configured. Set cron_alert_notification_channels to an existing Slack notification channel, or set cron_alert_slack_channel_name and cron_alert_slack_auth_token."
    }
  }
}

resource "google_secret_manager_secret" "slack_webhook_url" {
  project   = var.project_id
  secret_id = local.slack_webhook_secret_id

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

  notification_channels = local.operational_alert_notification_channels

  conditions {
    display_name = "Cloud Run Job execution failed"

    condition_matched_log {
      filter = <<-EOT
resource.type="cloud_run_job"
resource.labels.project_id="${var.project_id}"
resource.labels.location="${var.region}"
NOT resource.labels.job_name="${local.wdpa_execution_observer_job_name}"
NOT resource.labels.job_name="${local.monitoring_slack_job_name}"
severity>=ERROR
protoPayload.serviceName="run.googleapis.com"
protoPayload.methodName="/Jobs.RunJob"
protoPayload.status.code>0
NOT (
  protoPayload.response.metadata.annotations."run.googleapis.com/creator"="${var.github_actions_terraform_service_account_email}"
  AND protoPayload.response.spec.template.spec.containers.env.name="SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL"
)
EOT
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Shared datasets cron execution failed"
    content   = <<-EOT
A shared-datasets scheduled-ingestion Cloud Run Job execution failed. Something went wrong; this is not a routine pause.

Check the failed execution and logs:

```bash
gcloud run jobs executions list --job=<job-name> --region=${var.region} --project=${var.project_id}
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="<job-name>" AND severity>=ERROR' --project=${var.project_id} --limit=20
```

This policy covers unattended Cloud Run Job execution failures throughout the project and region, including future jobs and detached deployment canaries.

The five-minute WDPA execution observer has a separate health policy: it alerts only after no successful completion for a 15-minute window and a two-minute confirmation period. A successful observer run clears that condition.

A trusted deployment execution is quiet only when it has the execution-only SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL environment marker. That workflow must wait for the result and fail when the execution fails. Missing creator/marker fields remain alertable. Never put this marker in a persistent job template or on an asynchronous execution that outlives its workflow.

A release that stops to ask for a maintainer identity decision does **not** reach this policy. That case exits successfully and is reported by "Shared datasets release waiting on a maintainer decision" instead.
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

resource "google_monitoring_alert_policy" "wdpa_execution_observer_unhealthy" {
  project      = var.project_id
  display_name = "WDPA execution observer has stopped succeeding"
  combiner     = "OR"
  enabled      = var.cron_alerts_enabled
  severity     = "ERROR"

  notification_channels = local.operational_alert_notification_channels

  conditions {
    display_name = "No successful WDPA observer completion for 15 minutes"

    condition_threshold {
      filter                  = <<-EOT
resource.type="cloud_run_job"
resource.labels.project_id="${var.project_id}"
resource.labels.location="${var.region}"
resource.labels.job_name="${local.wdpa_execution_observer_job_name}"
metric.type="run.googleapis.com/job/completed_execution_count"
metric.labels.result="succeeded"
EOT
      comparison              = "COMPARISON_LT"
      threshold_value         = 1
      duration                = "120s"
      evaluation_missing_data = "EVALUATION_MISSING_DATA_ACTIVE"

      # This DELTA metric includes zero-valued samples. Count successful
      # completions, rather than treating any metric sample as a heartbeat.
      aggregations {
        alignment_period   = "900s"
        per_series_aligner = "ALIGN_SUM"
      }

      trigger {
        count = 1
      }
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "WDPA execution observations are stale"
    content   = <<-EOT
The WDPA execution observer normally completes successfully every five minutes. No successful completion was reported in the last 15 minutes, and that condition persisted for two minutes. Metric ingestion and alert evaluation can add further delay.

Individual failed executions stay in Cloud Run and Logging without an immediate Slack notification. A later successful completion clears this health condition. Missing metric data also counts as unhealthy, so a stopped scheduler or repeated container startup failures remain visible.

Check the observer, scheduler and current observation:

```bash
gcloud run jobs executions list --job=wdpa-execution-observer --region=${var.region} --project=${var.project_id}
gcloud scheduler jobs describe wdpa-execution-observer --location=${var.region} --project=${var.project_id}
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="wdpa-execution-observer"' --project=${var.project_id} --limit=30
gcloud storage cat gs://${var.bucket_name}/_catalog/wdpa-monthly-execution.json
```

This observer has no dataset publication permissions. Its health does not establish the outcome of a WDPA worker execution. Keep the observation document fresh before trusting execution status in the catalog.
EOT
  }

  alert_strategy {
    auto_close           = "604800s"
    notification_prompts = ["OPENED"]
  }

  user_labels = {
    component = "scheduled-ingestion"
    service   = "shared-datasets"
    kind      = "observer-health"
  }

  depends_on = [
    google_project_service.required,
    terraform_data.cron_alert_channel_configured,
  ]
}

resource "google_monitoring_alert_policy" "release_awaiting_identity_decision" {
  project      = var.project_id
  display_name = "Shared datasets release waiting on a maintainer decision"
  combiner     = "OR"
  enabled      = var.cron_alerts_enabled
  severity     = "WARNING"

  notification_channels = local.operational_alert_notification_channels

  conditions {
    display_name = "Release paused for a feature identity decision"

    condition_matched_log {
      filter = <<-EOT
resource.type="cloud_run_job"
resource.labels.project_id="${var.project_id}"
resource.labels.location="${var.region}"
textPayload:"shared_datasets_release_blocked=identity_decision_required"
EOT
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "A shared datasets release is waiting on a decision"
    content   = <<-EOT
**This is a request for a decision, not a failure.** A scheduled ingestion job built a release, found features whose identity it will not guess at, and stopped before writing anything.

Nothing is broken and nothing is at risk: the published dataset is unchanged, and the job keeps retrying on its normal schedule. The release publishes as soon as decisions land.

Why it asks: shared-datasets keeps a stable `feature_id` for each feature across releases. When a source changes a record so its identity is genuinely uncertain, guessing would silently break every saved reference to that feature, so a maintainer confirms instead.

What to do: add reviewed decisions to `catalog/feature-identity-resolutions/{asset-slug}.json` and merge them. `catalog/feature-identity-resolutions/README.md` explains the options, and the job logs one `identity ambiguity evidence` line per case.

The accompanying Slack message from the job lists the specific features awaiting a decision.
EOT
  }

  alert_strategy {
    auto_close           = "86400s"
    notification_prompts = ["OPENED"]

    notification_rate_limit {
      period = "86400s"
    }
  }

  user_labels = {
    component = "scheduled-ingestion"
    service   = "shared-datasets"
    kind      = "decision-required"
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

  notification_channels = local.operational_alert_notification_channels

  conditions {
    display_name = "Cloud Scheduler failed to start a scheduled ingestion job"

    condition_matched_log {
      filter = <<-EOT
resource.type="cloud_scheduler_job"
resource.labels.project_id="${var.project_id}"
resource.labels.location="${var.region}"
NOT resource.labels.job_id="${local.wdpa_execution_observer_job_name}"
NOT resource.labels.job_id="${local.monitoring_slack_job_name}"
severity>=ERROR
EOT
    }
  }

  documentation {
    mime_type = "text/markdown"
    subject   = "Shared datasets scheduler dispatch failed"
    content   = <<-EOT
Cloud Scheduler failed while attempting to start a shared-datasets ingestion job.

The five-minute WDPA execution observer uses its separate health policy, which allows a 15-minute success window and a two-minute confirmation period before notifying.

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
NOT (
  protoPayload.authenticationInfo.principalEmail="${module.wdpa_observer_service_account.email}"
  AND protoPayload.resourceName="${local.shared_bucket_object_resource_prefix}_catalog/wdpa-monthly-execution.json"
)
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

# Keep the exact former schema policy managed. Removing it from configuration
# left its live Slack route enabled; import and disable it through the protected
# policy sync instead of deleting its history or creating another policy.
import {
  to = google_monitoring_alert_policy.dataset_schema_changed
  id = "projects/shared-datasets-1/alertPolicies/10109535963709095250"
}

resource "google_monitoring_alert_policy" "dataset_schema_changed" {
  project      = var.project_id
  display_name = "Shared dataset schema changed"
  combiner     = "OR"
  enabled      = false
  severity     = "WARNING"

  notification_channels = []

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
    subject   = "Schema diagnostics stay quiet"
    content   = "Retired Slack route. Schema deltas remain structured diagnostics; consumer-impacting changes use the reviewed breaking-change publication contract."
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

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [google_project_service.required]
}
