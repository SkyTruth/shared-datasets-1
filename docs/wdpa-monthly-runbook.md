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

The production worker remains 4 CPU / 8 GiB / 100 GiB DISK / zero retries / 24 hours.
Validation requires kernel memory peak within 8 GiB and scratch below 80 GiB;
6.4 GiB memory headroom is advisory. Samples never replace full-build validation.
Missing telemetry or a missing/different scratch mount fails before publication.
Both assets must already have installed `generated-2026-v1` publication state.

## Deploy code and activate the schedule

Follow the [scheduled-ingestion deployment skill](../.claude/skills/deploy-scheduled-ingestion/SKILL.md).
After review and merge, successful main CI retains the exact tested Linux AMD64
WDPA image. `wdpa-monthly-deploy.yml` loads those bytes, verifies their configuration
and registry digest, and applies only its allowlisted saved Terraform plan through
`shared-datasets-production`. It does not build a second deployment image.
The ingestion IAM prerequisite gives the existing worker create-only access to
its staging prefix; canonical publication permissions remain unchanged.

For the one-time migration from the paused October publisher, complete protected
IAM sync, then dispatch the protected WDPA deployment with an explicit current-month
`canary_run_date=YYYY-MM-01` so deployment and canary share one verified attempt.
Verify its terminal result and both release indexes, then resume with
**Ingestion / Schedule control**, `job=wdpa-monthly`, `action=resume`.
While paused, deployment without an explicit canary date defers before mutation;
it must not install code and leave an unverifiable deployment record.
The dormant observer stays paused.

Manual deployment dispatch uses `main`, `executor_sha`, `source_run_id` (the
successful main-push CI run) and `source_run_attempt`. `RUN_DATE` is an optional
execution-only diagnostic override; the job template must never pin a date or
`WDPA_PROMOTION_BUNDLE`. A regular monthly run needs neither override.

## Monitoring and recovery

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

Keep machine evidence at `docs/wdpa-processing-evidence/`,
`catalog/wdpa-processing-acceptance.json`, `catalog/wdpa-staged-validation.json`,
`docs/wdpa-processing-public-inputs.json` and `.github/dataset-plans/`.
The public-inputs JSON is a container input; evidence
and plans remain at their validator-consumed paths.
Completed rollout narratives are archived under `docs/history/`.
