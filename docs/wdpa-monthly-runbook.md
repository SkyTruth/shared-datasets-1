# WDPA monthly operations

The `wdpa-monthly` Cloud Run job in `shared-datasets-1`, `us-central1`, downloads,
validates and publishes the current UTC month's WDPA source automatically for
`wdpa-marine` and `wdpa-terrestrial`. Scheduler runs at 09:00 UTC on days 1–10
(`0 9 1-10 * *`). New months require no data approval PR, bundle configuration
change or redeployment. Code and infrastructure changes still use reviewed PRs
and the protected production workflows.

## Monthly execution

1. Resume any transaction owned by this execution. Refuse another execution's
   unfinished publication or a date older than the committed release.
2. If both realms already have verified successful receipts for this month,
   write skipped check-ins without downloading or building again.
3. Freeze the committed identity and translation inputs at their exact generations
   and download the upstream source once. An unavailable source produces skipped
   check-ins; the next scheduled attempt tries again. Other failures fail the job.
4. Run the deterministic 0.001 compatibility sample (seed 7919), then the complete
   build of both realms. Require independent source counts, native artifact and
   locale contracts, stable feature identities and complete translations.
5. Stage exact validated files under `_scratch/wdpa-builds/<execution>/` using
   create-only generation preconditions. Commit a hash-bound descriptor only
   after both realms and the resource checks pass. Staging is internal evidence,
   not a shared dataset contract.
6. Release local build scratch, verify the descriptor and staged generations/hashes
   again, check both live predecessors, then publish through the owned publisher.
   Write dated releases before `latest/`; unchanged content produces only skipped
   check-ins. Already committed realms retain their receipts and artifacts.

Complete decision discovery for both realms runs before the compatibility
sample or artifact build; see [publication readiness](publication-readiness.md).
Existing production execution ownership remains authoritative across code changes.

The production worker uses 4 CPU / 8 GiB / 100 GiB DISK / zero retries / 12 hours,
allowing 3 h 39 m above the observed 8 h 21 m maximum.
Validation requires kernel memory peak within 8 GiB and scratch below 80 GiB;
6.4 GiB memory headroom is advisory. Samples never replace full-build validation.
Missing telemetry or a missing/different scratch mount fails before publication.
Both assets must already have installed `generated-2026-v1` publication state.

## Deploy code and activate the schedule

Follow [the ingestion deployment procedure](ci-deployment.md) for code changes.
The remaining steps here are specific to WDPA's paused-worker activation.

For the one-time migration from the paused October publisher, complete protected
IAM sync, then dispatch the protected WDPA deployment with an explicit current-month
`canary_run_date=YYYY-MM-01` so deployment and canary share one verified attempt.
Verify its terminal result and both release indexes, then resume with
**Ingestion / Schedule control**, `job=wdpa-monthly`, `action=resume`.
Schedule resume verifies the latest signed deployment and its exact-image
successful canary against the live worker. An October publication alone cannot
activate a worker still pinned to an October bundle or a persistent `RUN_DATE`.
An unavailable November source may produce a verified skipped canary; the
enabled schedule then retries during days 1–10 without a new monthly approval.
Paused/active execution gates and detached canary windows follow
[the shared runtime contract](deployment-contracts.md#records-receipts-and-runtime-outcomes).
`RUN_DATE` remains an execution-only diagnostic override; the job template must
never persist it or `WDPA_PROMOTION_BUNDLE`. The obsolete observer is removed
through protected retirement; worker deployment cannot recreate it.

## Monitoring and recovery

For the historical malformed November check-in, use
[the captured recovery contract](../.github/dataset-plans/README.md#captured-wdpa-check-in-recovery)
only when the exact original receipt/state match. It repairs the original
transaction without resetting IDs or activating a new release. The completed
[activation evidence](history/wdpa-november-activation-2026-11-03.md) is not
permission to replay it against another transaction.

A feature identity review stop exits zero with `identity_decision_required` and
requires reviewed release-scoped curation. A signed successful execution alone
does not establish publication readiness. Inspect the actual canary receipts and
both release indexes before schedule resume. After accepted curation reaches
main, the protected identity decision reconciliation replies to the original
Slack request with the choice, decision maker and rationale, then settles only
review alerts correlated to that execution/asset/release. Preserve the original
request, case summaries, context and source/review links; a status marker must
retain that content, and the decision belongs in the same-thread reply.
**Decision recorded** does not establish publication success or native
Monitoring closure. See
[decision alert routing](alert-routing.md#identity-decision-threads).

Shared worker and Scheduler alerts report failures. Per-asset release indexes
provide the latest check-in. Source unavailability is a skip, not a successful
new release; inspect the reason if the source remains absent through day 10.
Malformed input or failed validation leaves published datasets untouched and
fails visibly. Each new attempt uses its own staging prefix.

The manual monthly build workflow and publication-only image are retired.
October acceptance, plans and processing evidence remain historical audit records;
they no longer gate the current month. Legacy builder infrastructure is retained
without an active workflow; removing it requires a separate reviewed teardown.

## Recovery and evidence

Follow [CI/deployment recovery](ci-recovery.md) and
[feature-ID recovery](feature-id-reset-installation.md). Inspect captured inputs,
executor, generations, claims, reservations and checkpoints before retrying.
Resume interrupted publication through its original owner. If that executor or
evidence is unavailable, obtain reviewed recovery. Never delete state, rewind
counters, change the date to bypass ownership or overwrite partial releases.

The [completed November activation evidence](history/wdpa-november-activation-2026-11-03.md)
retains the failed-worker chain, accepted curation, successful publication,
verified schedule resume and original-parent reconciliation. These are historical
observations; use current signed records and live Scheduler state for new work.

Keep machine evidence at `docs/wdpa-processing-evidence/`,
`catalog/wdpa-processing-acceptance.json`, `catalog/wdpa-staged-validation.json`,
`docs/wdpa-processing-public-inputs.json` and `.github/dataset-plans/`.
The public-inputs JSON is a container input; evidence
and plans remain at their validator-consumed paths.
Completed rollout narratives are archived under `docs/history/`.
