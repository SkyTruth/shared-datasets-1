variable "monitoring_slack_relay" {
  description = "Protected independent relay deployment metadata after secret seeding. Null keeps the Job and Scheduler absent; this preparation always uses shadow delivery and a paused schedule. Contains no token payload."
  type = object({
    image                        = string
    secret_version               = string
    slack_channel                = string
    slack_team                   = string
    slack_user                   = string
    health_policy_name           = string
    health_policy_created_at     = string
    unapproved_write_policy_name = string
    deletion_policy_name         = string
    activated_at                 = string
  })
  default = null

  validation {
    condition = var.monitoring_slack_relay == null ? true : (
      can(regex("^us-central1-docker\\.pkg\\.dev/shared-datasets-1/shared-datasets-jobs/monitoring-slack-relay@sha256:[0-9a-f]{64}$", var.monitoring_slack_relay.image)) &&
      can(regex("^[1-9][0-9]*$", var.monitoring_slack_relay.secret_version)) &&
      can(regex("^C[A-Z0-9]{8,}$", var.monitoring_slack_relay.slack_channel)) &&
      can(regex("^T[A-Z0-9]+$", var.monitoring_slack_relay.slack_team)) &&
      can(regex("^U[A-Z0-9]+$", var.monitoring_slack_relay.slack_user)) &&
      can(regex("^projects/shared-datasets-1/alertPolicies/[1-9][0-9]*$", var.monitoring_slack_relay.health_policy_name)) &&
      can(regex("^projects/shared-datasets-1/alertPolicies/[1-9][0-9]*$", var.monitoring_slack_relay.unapproved_write_policy_name)) &&
      can(regex("^projects/shared-datasets-1/alertPolicies/[1-9][0-9]*$", var.monitoring_slack_relay.deletion_policy_name)) &&
      length(toset([var.monitoring_slack_relay.health_policy_name, var.monitoring_slack_relay.unapproved_write_policy_name, var.monitoring_slack_relay.deletion_policy_name])) == 3 &&
      can(regex("Z$", var.monitoring_slack_relay.health_policy_created_at)) && can(timecmp(var.monitoring_slack_relay.health_policy_created_at, "2026-01-01T00:00:00Z")) &&
      can(regex("Z$", var.monitoring_slack_relay.activated_at)) && can(timecmp(var.monitoring_slack_relay.activated_at, "2026-01-01T00:00:00Z"))
    )
    error_message = "Relay metadata requires the exact immutable relay image, numeric secret version, pinned Slack identities, three distinct project policy names and valid UTC timestamps verified by the protected workflow."
  }
}
