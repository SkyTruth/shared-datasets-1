# WDPA monthly operations

Use this runbook for `wdpa-monthly` in project `shared-datasets-1`, region
`us-central1`. The worker publishes approved retained artifacts for `wdpa-marine`
and `wdpa-terrestrial`; monthly source processing happens in the isolated build.

## Image and publication prerequisites

The accepted producer image is pinned in
[`catalog/wdpa-processing-acceptance.json`](../catalog/wdpa-processing-acceptance.json)
at `build.cloud_image`:

```text
us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/wdpa-validation@sha256:f72002f4a6d627bab367e28304797a2da4927dd1648cc2efe405660fa19849ff
```

`build.image_digest` pins its configuration digest; `build.source_tree_sha256`
pins the producer fingerprint. `build.artifact_bundle` pins the retained bundle's
URI, generation, size and SHA-256; `build.run_date` is currently `2026-10-01`.
`promotion_plan` and `promotion_pr` bind its immutable reviewed authority.

The recorded successful October publication image is
`wdpa-monthly@sha256:5b5161c60743c47c8b604171435991eedf6031b884fdad6b8bad1ab987433486`
in the same registry repository; its full URI is `publication_image` in
[publication verification](wdpa-processing-evidence/wdpa-monthly-f2xnm/publication-verification.json).
Acceptance pins the producer, not this publication layer. The deploy workflow
uses [`Dockerfile.promotion`](../ingestion/wdpa_monthly/Dockerfile.promotion) to
add reviewed publication code to that producer and deploys the resulting registry
digest. It does not regenerate dataset artifacts. Inspect the current job image:

```bash
gcloud run jobs describe wdpa-monthly --region=us-central1 \
  --project=shared-datasets-1 \
  --format='value(spec.template.spec.template.spec.containers[0].image)'
```

Before publication, require all of the following:

- Both assets have installed `generated-2026-v1` publication state; missing state
  is an error. Use the [feature-ID installation runbook](feature-id-reset-installation.md).
- Version 3 acceptance proves one terminal-success complete retained build,
  matching deterministic compatibility sample, native artifact/locale contracts,
  independent realm/India counts, image fingerprints and approved disk quota.
  The build stays at 4 CPU / 8 GiB / 100 GiB DISK / zero retries / 24 hours,
  with measured kernel peak within 8 GiB and scratch <80 GiB. Preferred 6.4 GiB
  headroom is advisory. Samples cannot replace complete-build evidence.
- The [owned promotion plan](../.github/dataset-plans/README.md#owned-wdpa-build-promotion)
  has exact-head authority verified by `scripts/wdpa_build_authorization.py`.
  Both realm bundles are validated before the root descriptor is committed.
- `WDPA_PROMOTION_BUNDLE` matches acceptance. Publication verifies staged bytes
  and live predecessors through the owned publisher. Preserve a verified already
  committed realm; use the captured build date and never rebuild in the canary.
- No pending/running worker execution conflicts with the canary. Installed IAM,
  the independent observer and unattended failure alerts are ready.

## Deploy an accepted month

Follow the [scheduled-ingestion deployment skill](../.claude/skills/deploy-scheduled-ingestion/SKILL.md)
for protected deployment, permissions and verification. Changes require review
and merge to `main`; production infrastructure changes use protected workflows.

[`ci.yml`](../.github/workflows/ci.yml) calls `wdpa-monthly-deploy.yml` after a
trusted `main` push with successful `ci-ready`, a selected WDPA deployment, and
ingestion/registry IAM prerequisites. Manual dispatch uses `main` and requires
`executor_sha`, `source_run_id` (the main-push CI run) and `source_run_attempt`
(its successful `ci-ready` attempt). The deploy workflow verifies acceptance,
promotion authority, publication state, permissions and the runtime window
before preparing the publication layer and applying its allowlisted saved plan.

A new month's build and promotion authority must be reviewed and deployed before
that month's schedule can succeed. The isolated build workflow is
`wdpa-processing-validation-deploy.yml`, gated by reviewed staged validation;
its identity creates immutable objects only under `_scratch/wdpa-builds/`.
Keep staged and complete-build evidence for their actual producer image.

## Execute and inspect

[`wdpa_monthly.tf`](../terraform/envs/prod/wdpa_monthly.tf) pins the bundle in the
job template. `RUN_DATE` stays execution-only; without it the worker targets the
first day of the current UTC month. Scheduler `wdpa-monthly` runs at 09:00 UTC
on days 1–10 (`0 9 1-10 * *`). A missing or wrong-month bundle fails before
publication. A completed month verifies both owned receipts and returns skipped.

Check pending/running executions and ownership before a deliberate execution.
Use the accepted `build.run_date` for a dated execution:

```bash
gcloud run jobs executions list --job=wdpa-monthly \
  --region=us-central1 --project=shared-datasets-1
gcloud run jobs execute wdpa-monthly --region=us-central1 \
  --project=shared-datasets-1 --update-env-vars=RUN_DATE=2026-10-01 --async
gcloud run jobs executions describe <execution-name> \
  --region=us-central1 --project=shared-datasets-1
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="wdpa-monthly" AND labels."run.googleapis.com/execution_name"="<execution-name>"' \
  --project=shared-datasets-1 --limit=50
```

Record the execution name and follow it to terminal status. The deployment
workflow watches an asynchronous canary for ten minutes; a running result
remains `verification_pending`. A paused schedule skips the default canary;
`canary_run_date` permits a deliberate execution only at the captured build date.
An existing execution skips the canary unless explicit `cancel_running_canary`
replaces known-bad code; cancellation does not release a publication claim.

## Observer and verification

[`wdpa-execution-observer`](../ingestion/wdpa_monthly/execution_observer.py) runs
every five minutes with 1 CPU / 512 MiB / 120 seconds / zero retries. It reads
worker executions and writes only
`gs://skytruth-shared-datasets-1/_catalog/wdpa-monthly-execution.json` with
generation preconditions and `public, max-age=0, must-revalidate` headers.
Schema 1 contains `job_name`, `observed_at`, `latest_execution` and
`latest_completed_execution`; entries expose IDs, timestamps, state and an
allowlisted reason code. Raw messages/configuration are excluded. A new running
attempt preserves the last completed result; API/IAM errors fail the observer
and leave stale status. Catalog observations older than 15 minutes show as stale.

Verify each asset's `_catalog/releases/{asset}.json` (`latest_release` and
`latest_run`), owned receipts, manifest generations and `latest/` byte metadata
under `100-geographic-reference/130-protected-areas/{asset}/`. Worker success,
asset publication and observer freshness are separate checks. Test changed alert
policies separately with protected `cron-alert-delivery-test.yml` and actual
Slack delivery; routine deployments do not require an injected failure probe.

## Recovery and evidence

Follow [CI/deployment recovery](ci-recovery.md) and
[feature-ID recovery](feature-id-reset-installation.md). Inspect captured inputs,
executor, generations, claims, reservations and checkpoints before retrying.
Resume interrupted publication through its original owner. If that executor or
evidence is unavailable, obtain reviewed recovery. Never delete state, rewind
counters, change the date to bypass ownership or overwrite partial releases.

Keep machine evidence at `docs/wdpa-processing-evidence/`,
`catalog/wdpa-processing-acceptance.json`, `catalog/wdpa-staged-validation.json`,
`docs/wdpa-processing-*.json` and `.github/dataset-plans/`. The public-inputs JSON
is a container input; evidence and plans remain at their validator-consumed paths.
Completed rollout narratives are archived under `docs/history/`.
