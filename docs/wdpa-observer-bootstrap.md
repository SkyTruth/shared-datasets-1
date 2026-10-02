# Proposed WDPA observer deployment bootstrap

The production Terraform identity can create/update Cloud Run jobs but cannot
set job IAM or create/update Scheduler jobs. The observer needs two job bindings
(read WDPA executions and permit its scheduler to invoke it) and a five-minute
Scheduler job. Its existing runtime contract stays read-only execution access
plus generation-preconditioned writes to the one execution-status object.

The proposal grants `shared-datasets-terraform@shared-datasets-1.iam.gserviceaccount.com`
only these temporary write permissions in `shared-datasets-1`:

| Permission | Deployment use |
| --- | --- |
| `run.jobs.setIamPolicy` | Install the observer's execution-reader and scheduler-invoker bindings |
| `cloudscheduler.jobs.create` | Create the observer's Scheduler job |
| `cloudscheduler.jobs.update` | Reconcile that job's reviewed configuration |

The binding has an absolute expiry of **2026-10-06 00:00 UTC**. It cannot renew
itself; extension requires another reviewed change and explicit approval.
`run.jobs.getIamPolicy` is a separate permanent read permission in the existing
deployer role so Terraform can refresh job bindings after the write grant expires.
No dataset, storage, deletion, runtime service-account or job execution
permissions are added by this proposal.

These writes are **project-wide while the binding is effective**: they can edit
IAM on any Cloud Run job and create/update any Scheduler job in this project.
The protected deployment's resource allowlist constrains its plan, but is not an
IAM restriction. [Scheduler creation](https://docs.cloud.google.com/scheduler/docs/reference/rest/v1/projects.locations.jobs/create)
requires authorization on the parent location. Google's
[supported resource attributes](https://docs.cloud.google.com/iam/docs/conditions-resource-attributes)
do not provide a documented `resource.name` restriction for these APIs; the
proposal does not pretend to have one. Cloud Run does support
[job-level IAM](https://docs.cloud.google.com/run/docs/securing/managing-access),
which the actual observer runtime and scheduler grants continue to use.

This is a proposal pending explicit approval, not an applied grant. AGENTS.md
requires human input before broad write permissions. After approval and green
PR checks, merge through the normal path; `scheduled-ingestion-deploy-iam-sync.yml`
uses the protected production environment and queue. Its allowlist adds only the
bootstrap custom role and conditional binding. Inspect the saved plan, then
verify the live permission set and expiry after the protected apply.

The refreshed October 2 read-only plan contains two creations (custom role and
conditional binding), one update (add the read-only job IAM permission to the
deployer role) and zero deletions. It changes no worker, observer job, Scheduler,
runtime identity or dataset permissions. Terraform 1.8.5 formatting/validation
and the focused IAM, deployment workflow and repository guardrail tests passed.

Full processing acceptance still gates the existing worker/observer deployment;
this IAM change cannot open that gate. After observer provisioning is verified,
remove the temporary binding through a reviewed cleanup PR and the same
protected workflow. The absolute expiry denies these writes even if cleanup is
delayed. An unbound custom role confers no access. Keep the observer's narrow
runtime permissions and the separate failure and identity-decision alerts.
