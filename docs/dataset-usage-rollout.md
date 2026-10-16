# Passive usage rollout

This chat owns integration and recovery. The tracker uses native bucket logs
alone. Downstream consumers, including 30x30, Cerulean, Monitor and other existing
users, make no tracking changes: no reporting SDK upgrade, telemetry hook, cache
instrumentation, backend reporting route or tracking IAM grant is required.

## What is measured

Keep observed reads/partial reads, metadata checks, failures and response bytes
separate. Infer an application label only when a mapped referrer was observed;
otherwise retain unknown attribution. Bucket traffic is not application active
days. CDN/application cache hits, downloaded copies and offline use are outside
coverage, and missing activity never proves non-use.

The private raw/state buckets, daily collector, Scheduler, dashboard, 30/90-day
views, seven-day raw retention and 460-day aggregate/snapshot retention remain.
No organization audit-policy or sink review is required. Shared security auditing,
CDN logging, signing and existing dataset access stay independent.

## Withdrawn application reporting

30x30 [PR 531](https://github.com/SkyTruth/skytruth-30x30/pull/531) is closed without
merging. The prepared Cerulean reporting adapters and SDK pins are withdrawn;
their worktrees are restored to their prerequisite heads, preserving the existing
owners' unrelated work. Cerulean UI PR 363 and backend PR 202 are not tracking
prerequisites and must not be merged or modified to finish this rollout.

Remove reporting helpers/hooks from future SDK releases, catalog browser/backend
reporting, receipt ingestion, producer registration, application active-day UI,
and receipt-based readiness/completion gates. Already published SDK versions stay
immutable; passive tracking requires no consumer upgrade.

Remove the catalog reporter's environment and all eight exact shared tracking
writer grants through reviewed protected deployment. Preserve dataset access and
consumer-project resources. Retain the exact-grant deletion allowance only until
absence is verified, then remove it. Keep previous ledger tables and reports as
historical evidence with an `app_usage` collection cutoff.

## Deployment and completion

1. Prepare the native-only reader, worker, migration, SDK removals, scoped IAM
   withdrawal and docs as one focused shared-datasets change. Record exact branch,
   head, base and this chat as integration/recovery owner. There are no downstream
   consumer PR dependencies.
2. Freeze a clean committed head after current-main synchronization and run full
   `uv run python scripts/ci_preflight.py --base <main-sha> --head <head-sha>`.
   Respect its exclusive local validation slot and normal review/CI protections.
3. Deploy the compatible dashboard first. It reads existing report versions and
   exposes only passive traffic. Verify its exact signed deployment before the
   worker switches to schema 3. Remove reporting configuration and scoped writer
   grants only through the saved-plan allowlisted production workflow.
4. Verify preserved ledger history and immutable publication, absence of the
   reporter endpoint/marker and all writer grants, and normal consumer access.
   Retain existing publication and recovery evidence. Do not manufacture reports
   or change consumer code as a collection test.
5. Bind authenticated and anonymous sample reads to the current ledger-recorded
   maintenance nonce. Verify matching delivered native CSV records, probe exclusion
   and source health. Read success alone is not delivery proof. Keep Scheduler
   paused until the native readiness checks pass.
6. Measure incremental raw/state storage, operations, worker/viewer runtime and
   tracker logging against the $25/month target. The initial $15.84/month estimate
   in [the trial forecast](dataset-usage-trial-cost-2026-10-14.json) is a conservative
   forecast, not an observed bill or hard cap. Recheck actual trial costs.
7. Verify scheduled publication across two healthy native UTC days. Only then
   remove the exact six disabled legacy export/probe resources and their obsolete
   permissions. Remove temporary cleanup permissions and compatibility after
   verification. Preserve both buckets and history.

Native delivery retains a 48-hour health allowance; two healthy probe UTC days
may require more than two wall-clock days. Rollback pauses passive collection and
preserves evidence. It does not restore application reporting or the
organization-dependent audit design.
