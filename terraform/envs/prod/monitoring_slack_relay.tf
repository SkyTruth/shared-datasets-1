# Independent Monitoring lifecycle relay. This preparation installs no new
# notification route: delivery stays shadow and its Scheduler stays paused.
# Runtime state is separate from dataset objects and from GitHub availability.
locals {
  monitoring_slack_secret_id         = "shared-datasets-slack-incident-bot-token"
  monitoring_slack_state_bucket_name = "${var.bucket_name}-monitoring-slack-state"
  monitoring_slack_job_name          = "monitoring-slack-relay"
  monitoring_slack_runtime_email     = "monitoring-slack-relay@${var.project_id}.iam.gserviceaccount.com"
  monitoring_slack_scheduler_email   = "monitoring-slack-scheduler@${var.project_id}.iam.gserviceaccount.com"
  monitoring_slack_state_objects = [
    "monitoring-slack/state-v1.json",
    "monitoring-slack/capabilities-v1.json",
  ]
  monitoring_slack_config = var.monitoring_slack_relay == null ? null : {
    schema  = "monitoring-slack-config-v1"
    project = var.project_id

    relay_job   = "projects/${var.project_id}/locations/${var.region}/jobs/${local.monitoring_slack_job_name}"
    relay_image = var.monitoring_slack_relay.image

    policies = [
      { name = var.monitoring_slack_relay.health_policy_name, kind = "health" },
      { name = var.monitoring_slack_relay.unapproved_write_policy_name, kind = "security" },
      { name = var.monitoring_slack_relay.deletion_policy_name, kind = "security" },
    ]
    health_metric            = "custom.googleapis.com/shared_datasets/operational_reconciliation_heartbeat_v2"
    health_policy_created_at = var.monitoring_slack_relay.health_policy_created_at
    state_bucket             = local.monitoring_slack_state_bucket_name
    slack_secret_version     = "projects/${data.google_project.current.number}/secrets/${local.monitoring_slack_secret_id}/versions/${var.monitoring_slack_relay.secret_version}"
    slack_channel            = var.monitoring_slack_relay.slack_channel
    slack_team               = var.monitoring_slack_relay.slack_team
    slack_user               = var.monitoring_slack_relay.slack_user
    activated_at             = var.monitoring_slack_relay.activated_at
    mode                     = "shadow"
  }
}

# UNAPPROVED PROPOSAL: secrets.create is checked on the project. A resource.name
# condition cannot confine creation to a not-yet-existing secret. This separate
# fixed-expiry write grant requires explicit human approval before merge/apply.
# Protected workflow allowlists constrain the actual create to the secret below.
resource "google_project_iam_custom_role" "monitoring_slack_secret_creator" {
  project     = var.project_id
  role_id     = "sharedDatasetsMonitoringSlackSecretCreator"
  title       = "Temporary Monitoring Slack Secret Creator"
  description = "Protected initial bot-secret creation only; expires through its separate binding."
  permissions = ["secretmanager.secrets.create"]
}

resource "google_project_iam_member" "github_actions_monitoring_slack_secret_creator" {
  project = var.project_id
  role    = "projects/${var.project_id}/roles/${google_project_iam_custom_role.monitoring_slack_secret_creator.role_id}"
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  condition {
    title       = "monitoring-slack-secret-create-expires"
    description = "Expire temporary secret creation authority at 2026-10-23 02:00 UTC."
    expression  = "request.time < timestamp('2026-10-23T02:00:00Z')"
  }
}

resource "google_project_iam_custom_role" "monitoring_slack_secret_manager" {
  project     = var.project_id
  role_id     = "sharedDatasetsMonitoringSlackSecretManager"
  title       = "Monitoring Slack Secret Metadata and Seed Manager"
  description = "Manage only the dedicated secret IAM and seed versions; no secret payload read."
  permissions = [
    "secretmanager.secrets.get",
    "secretmanager.secrets.getIamPolicy",
    "secretmanager.secrets.setIamPolicy",
    "secretmanager.versions.add",
    "secretmanager.versions.list",
  ]
}

resource "google_project_iam_member" "github_actions_monitoring_slack_secret_manager" {
  project = var.project_id
  role    = "projects/${var.project_id}/roles/${google_project_iam_custom_role.monitoring_slack_secret_manager.role_id}"
  member  = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  condition {
    title       = "monitoring-slack-bot-secret-only"
    description = "No metadata, version creation or IAM access to any other secret."
    expression = join(" || ", [
      "resource.name == 'projects/${var.project_id}/secrets/${local.monitoring_slack_secret_id}'",
      "resource.name == 'projects/${data.google_project.current.number}/secrets/${local.monitoring_slack_secret_id}'",
    ])
  }
}

resource "google_secret_manager_secret" "monitoring_slack_bot_token" {
  project   = var.project_id
  secret_id = local.monitoring_slack_secret_id
  labels    = { purpose = "monitoring-slack-incident-relay" }
  replication {
    auto {}
  }
  lifecycle { prevent_destroy = true }
  # Secret payloads/versions are never Terraform resources or inputs. The
  # protected seed step adds one version from Actions stdin after this stage.
  depends_on = [
    google_project_service.required,
    google_project_iam_member.github_actions_monitoring_slack_secret_creator,
    google_project_iam_member.github_actions_monitoring_slack_secret_manager,
  ]
}

module "monitoring_slack_service_account" {
  source       = "../../modules/service_account"
  project_id   = var.project_id
  account_id   = "monitoring-slack-relay"
  display_name = "Independent Monitoring Slack incident relay"
  depends_on   = [google_project_service.required]
}

resource "google_secret_manager_secret_iam_member" "monitoring_slack_bot_token" {
  count     = var.monitoring_slack_relay == null ? 0 : 1
  project   = var.project_id
  secret_id = google_secret_manager_secret.monitoring_slack_bot_token.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${local.monitoring_slack_runtime_email}"
  condition {
    title       = "monitoring-slack-pinned-bot-version"
    description = "Access only the exact numeric bot-token version selected after protected seeding."
    expression = join(" || ", [
      "resource.name == 'projects/${var.project_id}/secrets/${local.monitoring_slack_secret_id}/versions/${var.monitoring_slack_relay.secret_version}'",
      "resource.name == 'projects/${data.google_project.current.number}/secrets/${local.monitoring_slack_secret_id}/versions/${var.monitoring_slack_relay.secret_version}'",
    ])
  }
  depends_on = [google_project_iam_member.github_actions_monitoring_slack_secret_manager, module.monitoring_slack_service_account]
}

resource "google_storage_bucket" "monitoring_slack_state" {
  project                     = var.project_id
  name                        = local.monitoring_slack_state_bucket_name
  location                    = "US"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning { enabled = true }
  lifecycle { prevent_destroy = true }
  depends_on = [google_project_service.required]
}

resource "google_project_iam_custom_role" "monitoring_slack_reader" {
  project     = var.project_id
  role_id     = "sharedDatasetsMonitoringSlackReader"
  title       = "Independent Monitoring Slack Reader"
  description = "Read Monitoring alerts and health evidence; no metric, alert, policy or infrastructure mutation."
  permissions = [
    "monitoring.alerts.get",
    "monitoring.alerts.list",
    "monitoring.alertPolicies.get",
    "monitoring.timeSeries.list",
  ]
}

# Monitoring's list APIs are project-scoped. The runtime validates the exact
# reviewed three-policy allowlist before accepting observations or recovery.
resource "google_project_iam_member" "monitoring_slack_reader" {
  project    = var.project_id
  role       = "projects/${var.project_id}/roles/${google_project_iam_custom_role.monitoring_slack_reader.role_id}"
  member     = "serviceAccount:${local.monitoring_slack_runtime_email}"
  depends_on = [module.monitoring_slack_service_account]
}

resource "google_project_iam_custom_role" "monitoring_slack_state" {
  project     = var.project_id
  role_id     = "sharedDatasetsMonitoringSlackState"
  title       = "Monitoring Slack Private CAS State"
  description = "Read and replace two exact private receipt objects; no listing or dataset access."
  # GCS replacement requires create AND delete even though the runtime never
  # issues DELETE. Every write uses a generation match (including create=0).
  permissions = ["storage.objects.get", "storage.objects.create", "storage.objects.delete"]
}

# Receipt controls inspect the exact original sender execution before changing
# private delivery evidence. This role is bound on this Job alone, not on the
# project; it cannot enumerate executions, start jobs or supply overrides.
resource "google_project_iam_custom_role" "monitoring_slack_execution_reader" {
  project     = var.project_id
  role_id     = "sharedDatasetsMonitoringSlackExecutionReader"
  title       = "Monitoring Slack Own Execution Reader"
  description = "Inspect an exact relay sender execution for receipt-only recovery; no list or execution authority."
  permissions = ["run.executions.get"]
}

resource "google_storage_bucket_iam_member" "monitoring_slack_state" {
  bucket = google_storage_bucket.monitoring_slack_state.name
  role   = "projects/${var.project_id}/roles/${google_project_iam_custom_role.monitoring_slack_state.role_id}"
  member = "serviceAccount:${local.monitoring_slack_runtime_email}"
  condition {
    title       = "monitoring-slack-exact-state-objects"
    description = "Only the independent incident ledger and its private capability marker."
    expression = "resource.type == 'storage.googleapis.com/Object' && (${join(" || ", [for object in local.monitoring_slack_state_objects :
      "resource.name == 'projects/_/buckets/${local.monitoring_slack_state_bucket_name}/objects/${object}'"
    ])})"
  }
  depends_on = [module.monitoring_slack_service_account]
}

resource "google_service_account_iam_member" "monitoring_slack_deployer" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${local.monitoring_slack_runtime_email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  depends_on         = [module.monitoring_slack_service_account]
}

module "monitoring_slack_scheduler_service_account" {
  source       = "../../modules/service_account"
  project_id   = var.project_id
  account_id   = "monitoring-slack-scheduler"
  display_name = "Independent Monitoring Slack Scheduler invoker"
  depends_on   = [google_project_service.required]
}

resource "google_service_account_iam_member" "monitoring_slack_scheduler_deployer" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${local.monitoring_slack_scheduler_email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.github_actions_terraform_service_account_email}"
  depends_on         = [module.monitoring_slack_scheduler_service_account]
}

# Absent until the protected workflow has seeded and verified a numeric secret
# version and supplies a tested immutable image plus exact public identities.
module "monitoring_slack_job" {
  count                 = var.monitoring_slack_relay == null ? 0 : 1
  source                = "../../modules/cloud_run_job"
  project_id            = var.project_id
  location              = var.region
  name                  = local.monitoring_slack_job_name
  image                 = var.monitoring_slack_relay.image
  command               = ["python", "-m", "services.monitoring_slack.run"]
  service_account_email = local.monitoring_slack_runtime_email
  cpu                   = "1"
  memory                = "512Mi"
  timeout               = "240s"
  max_retries           = 0
  env                   = { MONITORING_SLACK_CONFIG = jsonencode(local.monitoring_slack_config) }
  depends_on = [
    google_secret_manager_secret_iam_member.monitoring_slack_bot_token,
    google_project_iam_member.monitoring_slack_reader,
    google_storage_bucket_iam_member.monitoring_slack_state,
    google_service_account_iam_member.monitoring_slack_deployer,
  ]
}

resource "google_cloud_run_v2_job_iam_member" "monitoring_slack_scheduler" {
  count      = var.monitoring_slack_relay == null ? 0 : 1
  project    = var.project_id
  location   = var.region
  name       = module.monitoring_slack_job[0].name
  role       = "roles/run.invoker"
  member     = "serviceAccount:${local.monitoring_slack_scheduler_email}"
  depends_on = [module.monitoring_slack_scheduler_service_account]
}

resource "google_cloud_run_v2_job_iam_member" "monitoring_slack_execution_reader" {
  count    = var.monitoring_slack_relay == null ? 0 : 1
  project  = var.project_id
  location = var.region
  name     = module.monitoring_slack_job[0].name
  role     = "projects/${var.project_id}/roles/${google_project_iam_custom_role.monitoring_slack_execution_reader.role_id}"
  member   = "serviceAccount:${local.monitoring_slack_runtime_email}"
}

module "monitoring_slack_scheduler" {
  count                 = var.monitoring_slack_relay == null ? 0 : 1
  source                = "../../modules/scheduler_job"
  project_id            = var.project_id
  region                = var.region
  name                  = local.monitoring_slack_job_name
  description           = "Independently poll the three health/security Monitoring policies; paused during shadow preparation."
  schedule              = "*/5 * * * *"
  time_zone             = "UTC"
  paused                = true
  retry_count           = 0
  target_job_location   = var.region
  target_job_name       = module.monitoring_slack_job[0].name
  service_account_email = local.monitoring_slack_scheduler_email
  depends_on = [
    google_cloud_run_v2_job_iam_member.monitoring_slack_scheduler,
    google_service_account_iam_member.monitoring_slack_scheduler_deployer,
  ]
}
