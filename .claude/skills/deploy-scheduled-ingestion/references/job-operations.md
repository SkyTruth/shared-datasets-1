# Job operations

[Deployment](../../../../docs/ci-deployment.md) owns the common procedure; each README owns runtime limits.
[deployment contracts](../../../../docs/deployment-contracts.md#records-receipts-and-runtime-outcomes)
own canary windows, exact-image verification and signed terminal outcomes.

## mpatlas-monthly deployment

The [MPAtlas README](../../../../ingestion/mpatlas_monthly/README.md#runtime-and-protected-deployment)
owns first-install scope and source behavior: job, scheduler invoker, scheduler and optional
writer deny exception. Confirm unchanged-source check-ins preserve the prior release.

## eamlis-monthly deployment

The [EAMLIS README](../../../../ingestion/eamlis_monthly/README.md) owns source
quality/identity installation. A paused schedule defers image preparation/apply unless
`canary_run_date` is explicit. This runs one canary without resuming cron; unknown Scheduler state fails.

## Schedule pause records

Record every maintenance pause in [`.github/scheduler-pauses.json`](../../../../.github/scheduler-pauses.json)
in a reviewed change, keyed by Scheduler job name, with CODEOWNER `owner`, `reason` and bounded
timezone-qualified `expires_at`. Remove it when resuming.
[Alert routing](../../../../docs/alert-routing.md#scheduled-freshness-and-capacity)
owns expiry/absence checks. Deployment holds do not authorize schedule pauses.

Pausing prevents future invocations without draining executions or releasing claims. Preserve jobs, identities, state and data.

Use protected [`ingestion-schedule-control.yml`](../../../../.github/workflows/ingestion-schedule-control.yml)
from `main` for every registered publisher: `eamlis-monthly`, `wdpa-monthly`,
`sea-ice-daily` and `mpatlas-monthly`. Follow pause records, then select the job:

```bash
gh workflow run ingestion-schedule-control.yml --ref main -f job=sea-ice-daily -f action=pause
```

Observe terminal control completion and verify the live Scheduler is paused. An approved
`action=resume` must pass publication gates, EAMLIS identity/source quality and WDPA signed canary
readiness; code deployment alone cannot clear a hold. Sea-ice automatic resume also requires
its registered resume option and successful canary, release and hygiene verification.

## wdpa-monthly deployment

Use [the monthly runbook](../../../../docs/wdpa-monthly-runbook.md#deploy-code-and-activate-the-schedule)
for paused-worker activation and explicit current-month canary dates. No monthly acceptance PR,
pinned bundle or redeployment is required. Never persist `RUN_DATE` or `WDPA_PROMOTION_BUNDLE`
in the job template. Preserve installed publication state and existing owned claims.
The worker deployment cannot recreate the retired observer.

## Dataset usage

The [usage README](../../../../ingestion/dataset_usage/README.md) owns reader-first
migration, private report verification and cost/source activation. Its wrapper uses the shared executor's
`usage` plan guard and runtime window. Verify private telemetry; it neither publishes canonical
releases nor resumes Scheduler by default.

## Permanent infrastructure teardown

Use [the target-change procedure](../../../../docs/terraform-reconciliation.md#change-a-terraform-or-iam-target)
with exact worker, Scheduler and IAM deletion scopes in a reviewed PR. Extend the constrained workflow
if allowlists cannot cover deletion; only protected production applies the approved plan.
Never run local production destroy/apply. Preserve releases, `latest/`, run records, READMEs and catalog rows.
Canonical deletion is a separate immutable reviewed
[dataset-plan operation](../../../../.github/dataset-plans/README.md).
