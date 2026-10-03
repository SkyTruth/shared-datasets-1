# Observer provisioning is complete. Keep the role definition for auditability;
# its temporary project binding is retired by the protected ingestion IAM sync.
# The original grant had absolute expiry 2026-10-06T00:00:00Z. Any future grant
# requires explicit approval and a reviewed PR; never renew it implicitly.
resource "google_project_iam_custom_role" "wdpa_observer_bootstrap" {
  project     = var.project_id
  role_id     = "sharedDatasetsWdpaObserverBootstrap"
  title       = "Shared Datasets WDPA Observer Bootstrap"
  description = "Temporary protected-workflow job IAM and Scheduler provisioning for the WDPA observer."
  permissions = [
    "cloudscheduler.jobs.create",
    "cloudscheduler.jobs.update",
    "run.jobs.setIamPolicy",
  ]
}
