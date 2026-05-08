variable "cron_alerts_enabled" {
  description = "Whether scheduled ingestion cron failure alert policies should be enabled."
  type        = bool
  default     = true
}

variable "cron_alert_notification_channels" {
  description = "Existing Cloud Monitoring notification channel resource names for cron alerts, for example projects/shared-datasets-1/notificationChannels/123456789. Prefer this for Slack channels created through the Google Cloud console OAuth flow."
  type        = list(string)
  default     = []
}

variable "cron_alert_slack_channel_name" {
  description = "Optional Slack channel name for a Terraform-managed Cloud Monitoring Slack notification channel, for example #shared-datasets-alerts. Prefer cron_alert_notification_channels when a channel already exists."
  type        = string
  default     = null
}

variable "cron_alert_slack_auth_token" {
  description = "Slack auth token for cron_alert_slack_channel_name. This is marked sensitive, but Terraform state can still contain provider-managed secret material; prefer an existing Cloud Monitoring Slack notification channel when possible."
  type        = string
  default     = null
  sensitive   = true
}

variable "slack_webhook_secret_accessors" {
  description = "IAM members allowed to read the Slack webhook Secret Manager secret for local operational summaries."
  type        = set(string)
  default = [
    "domain:skytruth.org",
  ]
}

variable "dataset_delete_alert_excluded_prefixes" {
  description = "Bucket object prefixes excluded from dataset delete alerts."
  type        = list(string)
  default = [
    "_scratch/",
    "000-system/terraform/state/",
  ]
}

variable "dataset_delete_alerts_enabled" {
  description = "Whether shared dataset object delete alerting should be enabled."
  type        = bool
  default     = true
}

variable "dataset_write_alerts_enabled" {
  description = "Whether canonical object write alerting for unapproved principals should be enabled."
  type        = bool
  default     = true
}

variable "dataset_schema_alerts_enabled" {
  description = "Whether shared dataset schema change alerting should be enabled."
  type        = bool
  default     = true
}
