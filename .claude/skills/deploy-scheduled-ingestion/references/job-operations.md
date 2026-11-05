# Job operations

[Change or add an ingestion deployment](../../../../docs/ci-deployment.md) owns
the common procedure. Read only the selected section here for activation,
schedule control or teardown. Runtime/container limits remain in each README;
[deployment contracts](../../../../docs/deployment-contracts.md#records-receipts-and-runtime-outcomes)
own canary windows, exact-image verification and signed terminal outcomes.

## mpatlas-monthly deployment

The [MPAtlas README](../../../../ingestion/mpatlas_monthly/README.md#runtime-and-protected-deployment)
owns first-install scope and source behavior. Its registered deployment may
create the job, scheduler invoker and scheduler plus the optional writer deny
exception. Confirm skipped unchanged-source check-ins preserve the prior release.

## eamlis-monthly deployment

The [EAMLIS README](../../../../ingestion/eamlis_monthly/README.md) owns source
quality/identity installation. A paused EAMLIS schedule defers deployment before
image preparation/apply unless `canary_run_date` is explicit. That override runs
one canary; it never resumes cron. Missing/unknown Scheduler state fails the gate.

## Schedule pause records

Record every maintenance pause in [`.github/scheduler-pauses.json`](../../../../.github/scheduler-pauses.json)
in a reviewed change, keyed by Scheduler job name, with CODEOWNER `owner`,
`reason` and bounded timezone-qualified `expires_at`. Remove it when resuming.
[Alert routing](../../../../docs/alert-routing.md#scheduled-freshness-and-capacity)
owns expiry/absence checks. Deployment holds do not authorize schedule pauses.

Pausing prevents future invocations; it does not drain active executions or
release publication claims. Preserve jobs, identities, state and published data.

## eamlis-monthly schedule control and teardown

Follow [pause records](#schedule-pause-records), then stop future runs:

```bash
gcloud scheduler jobs pause eamlis-monthly --location=us-central1 --project=shared-datasets-1
```

EAMLIS identity/source-quality gates must be satisfied before a separately
approved resume; code deployment alone cannot clear a hold.

## sea-ice-daily schedule control and teardown

Follow [pause records](#schedule-pause-records), then use the protected control:

```bash
gh workflow run ingestion-schedule-control.yml --ref main -f job=sea-ice-daily -f action=pause
```

Observe terminal control completion and verify the live Scheduler is paused.
Sea-ice deployment resumes only when its registered resume option is enabled
and canary, release and hygiene verification succeed.

## wdpa-monthly deployment

Use [the monthly runbook](../../../../docs/wdpa-monthly-runbook.md#deploy-code-and-activate-the-schedule)
for paused-worker activation and explicit current-month canary dates. No monthly
acceptance PR, pinned bundle or redeployment is required. The job template must
not persist `RUN_DATE` or `WDPA_PROMOTION_BUNDLE`. Installed publication state
and existing owned claims remain prerequisites, not reset opportunities.
The protected worker deployment cannot recreate the retired observer.

## wdpa-monthly schedule control and teardown

Follow [pause records](#schedule-pause-records), then stop future runs:

```bash
gcloud scheduler jobs pause wdpa-monthly --location=us-central1 --project=shared-datasets-1
```

Use the monthly runbook's protected resume only after signed canary readiness.

## Dataset usage

The [usage README](../../../../ingestion/dataset_usage/README.md) owns reader-first
migration, private report verification and cost/source activation. Its thin
wrapper calls the same executor as the publishers, using the registry's `usage`
plan guard and runtime-window command. Verify its private telemetry outcome;
it does not publish canonical dataset releases or resume Scheduler by default.

## Permanent infrastructure teardown

Use [the target-change procedure](../../../../docs/terraform-reconciliation.md#change-a-terraform-or-iam-target)
with exact worker, Scheduler and IAM deletion scopes in a reviewed PR. Extend a
constrained workflow if existing allowlists cannot cover the intended deletions.
Only the protected production workflow applies the approved plan; do not run a
local production destroy/apply. Preserve dataset releases, `latest/`, run records,
READMEs and catalog rows. Canonical deletion is a separate immutable reviewed
[dataset-plan operation](../../../../.github/dataset-plans/README.md).
