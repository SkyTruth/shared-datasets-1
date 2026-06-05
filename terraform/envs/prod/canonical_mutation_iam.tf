locals {
  shared_bucket_object_resource_prefix = "projects/_/buckets/${var.bucket_name}/objects/"
  shared_bucket_folder_resource_prefix = "projects/_/buckets/${var.bucket_name}/folders/"

  canonical_dataset_top_level_prefixes = sort([
    for name, _config in yamldecode(file("${path.module}/../../../catalog/categories.yaml")).categories :
    "${name}/"
    if name != "000-system"
  ])

  canonical_mutation_publisher_condition = join(" || ", concat(
    [
      "resource.name == '${local.shared_bucket_object_resource_prefix}README.md'",
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_catalog/')",
    ],
    [
      for prefix in local.canonical_dataset_top_level_prefixes :
      "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}${prefix}')"
    ],
  ))

  canonical_mutation_publisher_folder_condition = join(" || ", concat(
    [
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_catalog/')",
    ],
    [
      for prefix in local.canonical_dataset_top_level_prefixes :
      "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}${prefix}')"
    ],
  ))

  scratch_writer_condition = join(" || ", [
    "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/')",
    "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/')",
  ])
  pending_publish_source_condition = join(" || ", [
    "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/pending-publishes/')",
    "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/pending-publishes/')",
    "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}gcloud/tmp/parallel_composite_uploads/see_gcloud_storage_cp_help_for_details/')",
  ])
  pending_publish_cleanup_condition = join(" || ", [
    "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/pending-publishes/')",
    "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/pending-publishes/')",
    "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}_scratch/cleanup-audit/')",
    "resource.name.startsWith('${local.shared_bucket_folder_resource_prefix}_scratch/cleanup-audit/')",
    "resource.name.startsWith('${local.shared_bucket_object_resource_prefix}gcloud/tmp/parallel_composite_uploads/see_gcloud_storage_cp_help_for_details/')",
  ])

  shared_datasets_publisher_principal = "principal://iam.googleapis.com/projects/-/serviceAccounts/${module.shared_datasets_publisher_service_account.email}"

  scheduled_ingestion_writer_principals = [
    "principal://iam.googleapis.com/projects/-/serviceAccounts/${module.wdpa_job_service_account.email}",
    "principal://iam.googleapis.com/projects/-/serviceAccounts/${module.eamlis_job_service_account.email}",
    "principal://iam.googleapis.com/projects/-/serviceAccounts/${module.sea_ice_job_service_account.email}",
  ]

  google_project_service_agents_principal = "principalSet://cloudresourcemanager.googleapis.com/projects/${data.google_project.current.number}/type/ServiceAgent"

  canonical_write_allowed_principal_emails = [
    module.shared_datasets_publisher_service_account.email,
    module.wdpa_job_service_account.email,
    module.eamlis_job_service_account.email,
    module.sea_ice_job_service_account.email,
  ]

  canonical_mutation_deny_exception_principals = toset(concat(
    [
      local.shared_datasets_publisher_principal,
      local.google_project_service_agents_principal,
      "principalSet://goog/group/${var.shared_datasets_breakglass_group_email}",
    ],
    local.scheduled_ingestion_writer_principals,
    tolist(var.extra_canonical_mutation_deny_exception_principals),
  ))
}

resource "google_iam_workload_identity_pool" "github" {
  project = var.project_id

  workload_identity_pool_id = var.github_workload_identity_pool_id
  display_name              = "GitHub Actions"
  description               = "GitHub Actions OIDC identities for shared-datasets automation."
  disabled                  = false

  depends_on = [google_project_service.required]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project = var.project_id

  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = var.github_workload_identity_pool_provider_id
  display_name                       = "GitHub Actions"
  description                        = "Restricts GitHub OIDC tokens to the shared-datasets repository and publish environment."
  disabled                           = false

  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.actor"            = "assertion.actor"
    "attribute.environment"      = "assertion.environment"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
    "attribute.workflow"         = "assertion.workflow"
  }

  attribute_condition = "assertion.repository == '${var.github_repository}' && assertion.environment == '${var.github_publish_environment}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

module "shared_datasets_publisher_service_account" {
  source = "../../modules/service_account"

  project_id   = var.project_id
  account_id   = "shared-datasets-publisher"
  display_name = "Shared datasets approved publisher"

  depends_on = [google_project_service.required]
}

resource "google_service_account_iam_member" "shared_datasets_publisher_github_wif" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${module.shared_datasets_publisher_service_account.email}"
  role               = "roles/iam.workloadIdentityUser"
  member             = "principal://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/subject/repo:${var.github_repository}:environment:${var.github_publish_environment}"
}

resource "google_project_iam_custom_role" "shared_datasets_publisher_object_lister" {
  project     = var.project_id
  role_id     = "sharedDatasetsPublisherObjectLister"
  title       = "Shared Datasets Publisher Object Lister"
  description = "Allows the approved publisher to list bucket object names for scratch cleanup audits."
  permissions = ["storage.objects.list"]

  depends_on = [google_project_service.required]
}

resource "google_storage_bucket_iam_member" "shared_datasets_publisher_object_lister" {
  bucket = var.bucket_name
  role   = google_project_iam_custom_role.shared_datasets_publisher_object_lister.name
  member = module.shared_datasets_publisher_service_account.member

  depends_on = [google_storage_bucket.shared_bucket]
}

resource "google_storage_bucket_iam_member" "shared_datasets_publisher_object_user" {
  bucket = var.bucket_name
  role   = "roles/storage.objectUser"
  member = module.shared_datasets_publisher_service_account.member

  condition {
    title       = "canonical_publish_prefixes"
    description = "Allow approved publisher writes to canonical shared-datasets objects, excluding _scratch/."
    expression  = local.canonical_mutation_publisher_condition
  }

  depends_on = [google_storage_bucket.shared_bucket]
}

resource "google_storage_bucket_iam_member" "shared_datasets_publisher_folder_user" {
  bucket = var.bucket_name
  role   = "roles/storage.objectUser"
  member = module.shared_datasets_publisher_service_account.member

  condition {
    title       = "canonical_publish_folders"
    description = "Allow approved publisher folder operations under canonical shared-datasets prefixes."
    expression  = local.canonical_mutation_publisher_folder_condition
  }

  depends_on = [google_storage_bucket.shared_bucket]
}

resource "google_storage_bucket_iam_member" "shared_datasets_publisher_pending_publish_viewer" {
  bucket = var.bucket_name
  role   = "roles/storage.objectViewer"
  member = module.shared_datasets_publisher_service_account.member

  condition {
    title       = "pending_publish_sources_read_only"
    description = "Allow approved publisher reads from staged pending-publish objects and orphaned gcloud composite temp parts only."
    expression  = local.pending_publish_source_condition
  }

  depends_on = [google_storage_bucket.shared_bucket]
}

resource "google_storage_bucket_iam_member" "shared_datasets_publisher_pending_publish_cleanup_user" {
  bucket = var.bucket_name
  role   = "roles/storage.objectUser"
  member = module.shared_datasets_publisher_service_account.member

  condition {
    title       = "pending_publish_cleanup"
    description = "Allow approved publisher cleanup of pending-publish scratch objects, cleanup warning markers, and orphaned gcloud composite temp parts."
    expression  = local.pending_publish_cleanup_condition
  }

  depends_on = [google_storage_bucket.shared_bucket]
}

resource "google_storage_bucket_iam_member" "shared_bucket_scratch_writers" {
  for_each = var.scratch_writer_members

  bucket = var.bucket_name
  role   = "roles/storage.objectUser"
  member = each.value

  condition {
    title       = "scratch_only"
    description = "Allow noncanonical staging writes only under _scratch/."
    expression  = local.scratch_writer_condition
  }

  depends_on = [google_storage_bucket.shared_bucket]
}

resource "google_iam_deny_policy" "canonical_destructive_actions" {
  count = var.canonical_mutation_deny_policy_enabled ? 1 : 0

  parent       = urlencode("cloudresourcemanager.googleapis.com/projects/${var.project_id}")
  name         = "shared-datasets-canonical-destructive-actions"
  display_name = "Shared datasets canonical destructive action guardrail"

  rules {
    description = "Deny object delete/move and managed folder deletion unless using the approved publisher, scheduled ingestion, service-agent, or break-glass path."

    deny_rule {
      denied_principals    = var.canonical_mutation_denied_principals
      exception_principals = sort(tolist(local.canonical_mutation_deny_exception_principals))
      denied_permissions = [
        "storage.googleapis.com/objects.delete",
        "storage.googleapis.com/objects.move",
        "storage.googleapis.com/managedFolders.delete",
      ]
    }
  }

  depends_on = [
    google_project_service.required,
    google_storage_bucket.shared_bucket,
  ]
}
