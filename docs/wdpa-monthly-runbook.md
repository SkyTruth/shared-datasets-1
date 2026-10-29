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
- No pending/running worker execution conflicts with the canary. Installed IAM
  and shared unattended failure alerts are ready.

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
that month's schedule can succeed. Use the recurring build process below.
The one-time October campaign remains retired and its evidence remains readable.
Monthly deployment verifies the hash-bound terminal execution snapshot in the
retained `cloud-facts.json`, then promotes the accepted bundle without rebuilding
artifacts. It does not query the retired producer execution. Keep staged and
complete-build evidence for their actual producer image.

## Prepare each month

The maintainer starting a build owns its terminal verification, approval handoff
and any recovery until both publications finish. Name that owner in the promotion
PR. Builds are deliberately manual; an enabled publisher schedule does not
download or build a new upstream month.

1. Before the next window, use **Ingestion / Schedule control**, `job=wdpa-monthly`,
   `action=pause` on reviewed `main` if the next month's bundle is not ready.
   Pausing preserves all releases and does not cancel an execution or abandon a
   publication claim. Keep it paused while preparing the replacement.
2. After the builder code and IAM PR merges and main CI succeeds, verify the
   protected **Infrastructure / Ingestion deploy IAM sync** has installed the
   `wdpa-monthly-build` identity, create-only scratch grants and committed-input
   readers. The builder has no canonical write authority. It uses the existing
   approved 4 CPU / 8 GiB / 100 GiB DISK quota and zero retries.
3. When the upstream release is available, dispatch **Ingestion / WDPA monthly
   build** (`wdpa-monthly-build.yml`) on `main` with `mode=build`, explicit
   `run_date=YYYY-MM-01`, and the tested `executor_sha`, `source_run_id` and
   `source_run_attempt`. Its protected queued job checks that main CI passed,
   builds and smoke-tests the producer, pins the registry/configuration digests,
   and applies only `module.wdpa_monthly_build_job` through an allowlisted saved
   plan. It refuses to replace an active build and starts the execution asynchronously.
4. Follow the execution named in the workflow summary to terminal success.
   The builder freezes the committed identity and translation generations,
   downloads the requested source once, runs the deterministic compatibility
   sample and complete build, and commits the descriptor only after both realms
   validate. Independent source counts replace fixed October counts. Artifacts
   are immutable under `_scratch/wdpa-builds/wdpa-monthly-build-<execution>/`.
   These are review inputs, not published dataset contracts.
5. Dispatch the same workflow with `mode=collect`, that execution name, and its
   original tested revision/CI identity. It verifies terminal success, producer
   fingerprint and descriptor generations/hashes. Download `wdpa-monthly-review`:
   it contains the acceptance JSON, immutable plan, retained cloud facts and
   descriptor, plus a readable PR fence. Preserve their repo-relative paths in
   a focused promotion PR. Set `promotion_pr` to the new PR number before final
   preflight/review; the collected draft deliberately has no approval authority.
6. Obtain exact-head maintainer approval and merge. The changed acceptance file
   selects the existing protected publication deploy. The publisher still
   verifies approval, live predecessors and exact staged bytes. A paused schedule
   skips the default canary: after the protected deployment, deliberately execute
   the deployed job with the accepted `RUN_DATE` as described below. Verify both
   release indexes, owned receipts and `latest/` metadata, then resume through
   **Ingestion / Schedule control** while that month's window is still current.

The workflow's `mode=readiness` and scheduled check at 08:00 UTC on days 1–10 and
25–31 report the month needed for the **next** scheduler invocation, the accepted
date, deployed-bundle agreement and scheduler state. An enabled schedule with a
missing handoff fails through the existing unattended workflow alerts. A paused
schedule is explicitly reported without a repeated readiness failure. Resume
requires verified promotion authority and a matching deployed month; an October
canary cannot establish November readiness. These checks never publish or resume
the schedule automatically.

For a failed build, inspect its execution and inputs before retrying. Scratch
artifacts from an incomplete build have no committed descriptor and no promotion
authority. Each new execution stages into its own prefix. If a baseline changes
before promotion, rebuild and review a new plan; never edit retained bytes or
reset publication state. Interrupted canonical publication still belongs to its
original owner and follows the recovery section below.

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

## Execution and publication verification

WDPA uses the shared Cloud Run execution-failure and Scheduler dispatch-failure
alerts. Check the actual worker execution around an update using the commands
above. Deployment canaries have a bounded watch; the hourly deployment-completion
workflow follows only pending deployment records to terminal status.

The catalog uses each asset's release index for **Latest release** and **Last
check-in**, just like the other scheduled datasets. It does not poll a separate
WDPA execution feed or mark an idle observer as unhealthy.

The former `wdpa-execution-observer` schedule is paused and its health policy
disabled. The existing definitions and narrow permissions remain because the
protected workflows allow updates but block deletion. The old
`_catalog/wdpa-monthly-execution.json` object and endpoint are no longer read by
the catalog. Do not resume the observer during routine deployments. For rollout
order and verification, see [alert routing](alert-routing.md#wdpa-execution-driven-monitoring).

Verify each asset's `_catalog/releases/{asset}.json` (`latest_release` and
`latest_run`), owned receipts, manifest generations and `latest/` byte metadata
under `100-geographic-reference/130-protected-areas/{asset}/`. Worker success
and asset publication are separate checks. Verify worker
alert delivery against an actual failed execution's Monitoring incident,
transport receipt and matching Slack message; do not inject production failures.

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
