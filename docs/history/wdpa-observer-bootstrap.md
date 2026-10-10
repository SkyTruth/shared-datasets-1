Historical record, completed 2026-10-03. Not a runbook; see docs/wdpa-monthly-runbook.md.

# Approved WDPA observer deployment bootstrap

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

The operator explicitly approved this exact temporary project-wide grant on
October 3, 2026. Approval does not itself apply the grant. After refreshed
review and green exact-head PR checks, merge through the normal path;
`scheduled-ingestion-deploy-iam-sync.yml`
uses the protected production environment and queue. Its allowlist adds only the
bootstrap custom role and conditional binding. Inspect the saved plan, then
verify the live permission set and expiry after the protected apply.

The refreshed October 3 read-only plan contains two creations (custom role and
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

## Archived ingestion README context

The following sections were retained from `ingestion/wdpa_monthly/README.md`
at revision `aa77eb89a93c54029c4ef003fe4096a196bda63b` when operations and rollout history were separated.
They record the prior guidance; use the monthly runbook for current operations.

### Execution observations and failure recovery

`wdpa-execution-observer` uses 1 CPU / 512 MiB, a 120-second timeout and a
five-minute scheduler. It reads WDPA executions independently of the worker and
writes only `gs://skytruth-shared-datasets-1/_catalog/wdpa-monthly-execution.json`
with generation preconditions and revalidation headers. It has no dataset,
release-index, claim, receipt or allocation permissions. An exact-object storage
binding and deny-policy exception permit replacing only that status document.
Before provisioning, review the protected Terraform identity's create permissions
for the observer job, scheduler and custom execution-reader role. Its existing
scheduled-ingestion role supports job updates, not those creations. Bootstrap
must use a reviewed protected workflow; do not add broad project permissions or
use a local production apply to bypass that requirement.

The version1 document contains `schema_version`, `job_name`, `observed_at`,
`latest_execution` and `latest_completed_execution`. Entries include ID,
created/started/completed timestamps, state (`pending`, `running`, `succeeded`,
`failed`, `cancelled`, `unknown`) and an allowlisted API reason code. Raw messages
and execution configuration are never published. A newer running attempt retains
the last completed failure. API or IAM failures leave the old observation stale
and fail the observer job, so the general execution alert covers them too.

Both WDPA catalog details show execution observations separately from the asset
check-in and published release. A successful marine publication does not imply
a successful overall job. Visible details revalidate every minute; observations
older than 15 minutes are marked stale. A status-document failure does not alter
any release or allocation state.
The protected viewer serves `execution-status.js` and maps
`/wdpa-monthly-execution.json` to the same observer object under `_catalog/`;
it reads that observation afresh on each request. After merge, deploy the viewer
image through `catalog-viewer-deploy.yml` before the web bundle through
`catalog-web-deploy.yml`, both protected workflows. Verify status on the static
and protected catalog pages after deployment.

Routine rollout does not inject a deliberate failure or require a previous
Slack notification as an input. Monitoring delivery is tested separately with
`cron-alert-delivery-test.yml` from reviewed `main`, using the pre-write failure
overrides. Verify the terminal failed execution, observer JSON (for the worker),
and actual Slack delivery before declaring an alert-policy change verified.

The multi-hour validation run and the worker canary remain unmarked and alertable:
they run asynchronously, and the worker workflow watches only the first ten
minutes. Synchronous staging preflight failures are reported by their failed
GitHub workflow. Follow every detached execution to its terminal state and inspect
release indexes and generation/hash metadata when it publishes. Do not grant the
observer write access to worker data to repair missing execution status.
