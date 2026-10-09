---
name: deploy-scheduled-ingestion
description: "Use before deploying or updating shared-datasets Cloud Run and Cloud Scheduler ingestion jobs, including Artifact Registry images, Terraform applies, manual job executions, alert coverage, and post-deploy verification."
---

# Deploy Scheduled Ingestion

## Feature-ID first deployment

WDPA and sea-ice deployment workflows run `scripts/publication_rollout_gate.py`
after existing project authentication and before image builds or Terraform.
It verifies installed publication state and receipts using the runtime validator.
Both WDPA assets must be initialized; sea ice is independent. EAMLIS is unaffected.
There is no hold-only registry or organization-IAM prerequisite.

For the one-time pre-launch transition, follow `docs/feature-id-reset-installation.md`:
pause and drain the affected job and older publisher/deployment runs, install the
immutable reviewed reset through the protected workflow, deploy, validate the
first publication, then resume. Existing administrators remain trusted; do not
rerun old workflows or restart old writers during the transition. Missing state
is a deployment error, not permission to reset it automatically.

Use this workflow for production ingestion jobs in `shared-datasets-1`.

## Rules

- Use Terraform for GCP assets: APIs, Artifact Registry, service accounts, IAM, Cloud Run Jobs, Cloud Scheduler jobs, and monitoring.
- Load `.claude/skills/protected-terraform-apply/SKILL.md` before any
  production Terraform apply discussion. Production infrastructure mutations go
  through reviewed PRs merged to `main` and protected GitHub Actions workflows,
  not local applies.
- Use remote Terraform state for shared environments; do not leave production state only on a local workstation.
- Use immutable container tags for deployments. Do not deploy `latest`.
- Build Cloud Run images for `linux/amd64`; make multi-stage Dockerfiles use `$BUILDPLATFORM`/`$TARGETOS`/`$TARGETARCH` for compiled helper binaries.
- Apply or verify the Artifact Registry repository before pushing a new image.
- Push the image before applying the Cloud Run Job that references it.
- Keep runtime service accounts narrow: only the job account should write its
  owned asset root and release-index object; scheduler gets only
  `roles/run.invoker`.
- Size Cloud Run Job CPU, memory, timeout, and local-temp cleanup for the full upstream source, not just test fixtures.
- Before redeploying after source-schema or format bugs, run a production-source fractional sandbox test locally. The sample must use the same conversion chain as production and must not publish sampled data unless explicitly guarded.
- Run one manual Cloud Run Job execution after deployment. Wait for short jobs; use async execution for known multi-hour jobs.
- Verify alert coverage after adding or changing scheduled jobs. Cron failure alerts should cover future jobs by default through project/region-level filters, labels, or another durable grouping; avoid per-job allowlists unless there is a documented reason.
- Distinguish Cloud Monitoring notification channels from the local Terraform apply-summary webhook. A working Monitoring Slack channel does not prove `shared-datasets-slack-webhook-url` has a Secret Manager version, and vice versa.
- Do not use Terraform for changing dataset files under `latest/`, `releases/`, or `runs/`.
- Every job that plans or applies production Terraform must use job-level
  concurrency with the literal group `prod-terraform-state`, `queue: max`, and
  `cancel-in-progress: false`. This includes reusable state syncs and all
  deployment/CDN jobs. Keep plan, JSON export, allowlist validation, and
  saved-plan apply in the same queued job; another state writer between plan
  and apply makes the saved plan stale.
- GitHub Actions retains up to 100 pending jobs with `queue: max`. The default
  queue replaces pending jobs even when `cancel-in-progress` is false. Queue
  overflow is still canceled: report it and dispatch the affected workflow
  again from reviewed `main` after capacity is available.
- Serialize the existing whole jobs, including image builds and canary checks.
  Longer queue waits are an accepted tradeoff. Put no matching queue on a
  reusable-workflow caller or its parent workflow: only the execution job owns
  it. Preserve bootstrap `needs` dependencies. Preview Terraform uses separate
  state and keeps its own queue.
- Before changing the shared queue, drain active and queued production runs
  using the old workflow configuration. Verify post-merge jobs complete without
  overlapping production writer jobs or canceled pending work.
- Keep `scripts/terraform_retry.sh` for bounded lock-error retries. It is not a
  substitute for serialization and cannot repair `Saved plan is stale`.
  Non-lock failures must exit immediately; do not automatically regenerate or
  apply a new plan outside the existing allowlist validation sequence.
- A deploy must not start a canary while an execution of the same job is
  running. Skip by default and say so; a duplicate rebuilds an entire release
  before discovering it is redundant. Cancel the in-flight run only via the
  explicit `cancel_running_canary` input, when replacing known-bad code.

## Job boundaries

- Keep each production cron job in its own package under `ingestion/<job_slug>/`, with a README, `run.py`, a Dockerfile when containerized, focused tests, and distinct Terraform blocks such as `terraform/envs/prod/<job_slug>.tf`.
- Put shared runtime and publishing behavior in `ingestion/common/`: GCS generation-precondition helpers, run-record writes, logging setup, subprocess helpers, content type selection, hashes, and temp cleanup.
- Keep source-specific parsing, filtering, schema choices, asset slugs, canonical paths, conversion rules, environment variables, and scheduler configuration inside the owning job package and its Terraform file.
- For new vector/table ingestion jobs, present the standard feature identity
  decision table from `AGENTS.md` and
  `docs/standards/asset-layout-and-formats.md` before publication. Prefer a
  verified unique non-null source field whose values satisfy the `feature_id`
  rules; otherwise obtain curator approval for a monotonic decimal sequence
  using an approved assignment key or the stored geometry/properties hash pair.
  Preserve source/provider fields in the canonical data and choose high-value
  `search_fields`. Keep `feature_id`, `geometry_hash`, and `properties_hash`
  in the FGB and metadata sidecar; PMTiles carry geometry and `feature_id` only.
  Consumers group equivalent footprints using `geometry_hash` from metadata.
- Do not import from another job package, such as `ingestion.wdpa_monthly`, unless the task is explicitly maintaining that job.
- Do not edit a functioning live job to support a new job unless the user explicitly requests a behavior-preserving refactor.
- Preserve live surfaces unless explicitly approved: Cloud Run job names, scheduler names, service account identities, asset slugs, canonical GCS paths, output formats, schemas, entrypoints, and run-record shape.
- Any change to `ingestion/common/` must run focused tests for every production job that imports it.
- Default cron publishing semantics: if the source or generated output is unchanged, write a skipped run record for observability and do not write new release or `latest/` dataset artifacts. Keep this as job behavior, not asset `update_cadence` metadata.
- For source-availability windows, retries, or polling schedules that target one upstream period, keep the target release date stable for that period and record the actual scheduler attempt date in the skipped run/check-in payload.
- Every success and meaningful skip must update `_catalog/releases/{asset-slug}.json`; verify both `latest_release` and `latest_run` after deployment.
- After a publish or deploy canary, inspect custom metadata on `latest/` objects. It must match the bytes now stored at `latest/`, not stale metadata from the previous generation.
- Normal cron runs must not require Git commits or tracked catalog date edits. Do not update repo asset docs just to advance latest-release, source-version, row-count, or hash fields; those belong in `_catalog/releases/{asset-slug}.json` and run records. Stable source identity, license, or citation changes still belong in asset docs and generated catalog outputs.

## Large-source sizing

For very large geospatial sources such as WDPA/WDOECM, do not assume Cloud Run
will finish within a short default timeout. Measure at least one representative
local fractional run, then treat full-source conversion as a multi-hour batch
job unless there is direct evidence otherwise.

Cloud Run writable filesystem usage counts against container memory unless
a disk-backed volume is configured. WDPA requires its 100 GiB Preview DISK
volume at `/work`, and validates that mount before downloads. Prefer a
conversion order that deletes large intermediates as soon as they are no longer
needed, and avoid keeping source archives, GPKG, GeoJSONSeq, FGB, and MBTiles
alive at the same time.

Use the reviewed WDPA resource target, and never increase it to bypass acceptance:

- Cloud Run Job task resources: `4` CPU and enforced `8Gi` memory. Preserve the
  measured kernel lifetime peak; exceeding preferred 6.4 GiB headroom is an
  advisory warning, not artifact rejection or a reason to rebuild retained bytes.
- Ephemeral DISK: `100Gi` at `/work`; measured scratch must stay below 80 GiB.
  Obtain the additional per-instance disk quota before rollout.
- The October validation campaign is retired. Monthly deployment requires the
  pinned accepted registry image, version 3 `catalog/wdpa-processing-acceptance.json`,
  retained evidence and the immutable `.github/dataset-plans/wdpa-build-{bundle-sha256}.json`
  promotion plan. `scripts/wdpa_processing_gate.py` validates acceptance;
  `scripts/wdpa_build_authorization.py` verifies the exact reviewed plan.
  `scripts/release_contracts.py --target wdpa` verifies the hash-bound terminal
  execution snapshot before promotion; it does not query the retired producer.
- Production consumes that exact `WDPA_PROMOTION_BUNDLE` without regenerating
  artifacts. The publication-only image inherits the verified producer image and
  copies only approved publication dependencies. Verify needed staged files and
  live predecessors; preserve committed realms through their owned receipts.
  Keep the monthly worker's prefix-scoped retained-bundle read permission.
- Observe terminal status through the independent execution observer. After an
  alert-policy change, verify delivery separately through
  `cron-alert-delivery-test.yml`; routine deploys do not inject failure probes.
- Cloud Run Job task timeout: at least `86400s` (24 hours).
- Retries: `0` while first validating idempotency and partial-release behavior;
  add retries only after failures are known to be safe to replay.

When a manual post-deploy execution is expected to take hours, start it
asynchronously instead of waiting in the agent session:

```bash
gcloud run jobs execute wdpa-monthly \
  --region=us-central1 \
  --project=shared-datasets-1 \
  --update-env-vars=RUN_DATE=YYYY-MM-DD \
  --async
```

Record the execution name, then monitor with:

```bash
gcloud run jobs executions describe <execution-name> \
  --region=us-central1 \
  --project=shared-datasets-1

gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="wdpa-monthly" AND labels."run.googleapis.com/execution_name"="<execution-name>"' \
  --project=shared-datasets-1 \
  --limit=50
```

If an execution is still running and a safer image or config must be deployed,
cancel the obsolete execution explicitly. Before starting a replacement canary,
inspect publication ownership; cancellation does not release an active claim.

If the current-day release already has partial objects without a success run
record, do not overwrite or delete them from the deployment workflow.

- WDPA and sea ice use `OwnedGeneratedPublisher`. Interrupted publications
  retain their claim, reserved IDs, captured intent, and checkpoints. Retries
  must resume the original execution's transaction with its captured inputs and
  executor contract. A new execution or different `RUN_DATE` cannot bypass that
  ownership. If the original execution cannot be retried, report the blocker and
  obtain reviewed recovery; do not delete publication state or reset counters.
  Follow the owning job README and `docs/feature-id-reset-installation.md`.
- For jobs without durable publication ownership, inspect exact object
  generations and follow the reviewed repair process in
  `.claude/skills/publish-shared-dataset/SKILL.md`. A backfill `RUN_DATE` is
  appropriate only for a separately intended release after confirming there is
  no active publication claim.

## Alerting

Scheduled ingestion alert policies should be maintained as part of the deployment surface.

Preferred behavior:

- Cloud Run Job execution failure alerts cover all Cloud Run Jobs in the shared-datasets project and region, or all jobs with a stable scheduled-ingestion label.
- Cloud Scheduler dispatch failure alerts cover all Scheduler jobs in the shared-datasets project and region, or all jobs with a stable scheduled-ingestion label.
- New cron jobs should not require editing a monitoring allowlist just to receive basic failure alerts.
- Synchronous GitHub canaries may pass the execution-only
  `SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL` marker when the trusted deployment
  workflow waits for terminal status and fails with the job. Never persist the
  marker in a job template. Detached executions and missing metadata stay alertable.
- If alert policies intentionally exclude manual canaries, verify scheduled execution coverage another way before calling the deployment complete.

After changing a job or alert policy, verify the live filters:

```bash
gcloud monitoring policies describe <cloud-run-failure-policy-name> \
  --project=shared-datasets-1 \
  --format='value(conditions[0].conditionMatchedLog.filter)'

gcloud monitoring policies describe <scheduler-failure-policy-name> \
  --project=shared-datasets-1 \
  --format='value(conditions[0].conditionMatchedLog.filter)'
```

If the user expects Slack delivery, verify the relevant path:

- Cloud Monitoring Slack alerts use Monitoring notification channels.
- Protected Terraform workflow summaries use `scripts/slack_notify.py` and the `shared-datasets-slack-webhook-url` Secret Manager secret, unless `SHARED_DATASETS_SLACK_WEBHOOK_URL` is set locally.

A safe controlled alert test is to execute a newly deployed job with an env override that fails before any GCS write, then confirm the matching log and Slack notification. Do not use a failure mode that can write partial releases, overwrite `latest/`, delete objects, or create confusing run records.

## Standard sequence

1. Load `.claude/skills/gcp-shared-datasets/SKILL.md` if the job writes GCS objects.
2. Validate locally:

```bash
uv run python -m unittest discover -s tests
terraform fmt -check -recursive
terraform -chdir=terraform/envs/prod init
terraform -chdir=terraform/envs/prod validate
```

For a narrow job change, it is acceptable to run the focused job tests instead
of full discovery. For `ingestion/common/` changes, run tests for every
production job that imports the shared helpers.

3. For changes to a large-source producer, use the owning job's local native
   fixtures and production-source fractional tests before deploying. The WDPA
   monthly path promotes its accepted retained bundle; do not rerun the retired
   October campaign as a deployment prerequisite.

4. If infrastructure is needed before an image can be pushed, add that
   prerequisite to Terraform and open a focused PR. After review and merge, let
   the protected workflow apply it before pushing the image.

5. Build and push an immutable image after the required repository and auth
   surface exists:

```bash
IMAGE=us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/<job-name>:YYYYMMDDHHMMSS
gcloud auth configure-docker us-central1-docker.pkg.dev
docker build --platform linux/amd64 -f ingestion/<job>/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"
```

6. Update Terraform to reference the exact image URI and run a local review
   plan, but do not apply it locally:

```bash
terraform -chdir=terraform/envs/prod plan  -var="wdpa_monthly_image=$IMAGE"
```

Open a PR with the Terraform change and plan summary. The protected production
workflow applies after `jonaraphael` review and merge to `main`. If no workflow
covers the resource class, add a constrained workflow in the PR instead of using
a local apply.

7. Execute the job once. For short jobs, wait for completion:

```bash
gcloud run jobs execute <job-name> --region=us-central1 --project=shared-datasets-1 --wait
```

For known multi-hour jobs, start asynchronously and record the execution name:

```bash
gcloud run jobs execute <job-name> --region=us-central1 --project=shared-datasets-1 --async
```

8. Verify runtime and data-plane effects:

```bash
gcloud scheduler jobs describe <job-name> --location=us-central1 --project=shared-datasets-1
gcloud run jobs executions list --job=<job-name> --region=us-central1 --project=shared-datasets-1
gcloud run jobs executions describe <execution-name> --region=us-central1 --project=shared-datasets-1
gcloud storage ls gs://skytruth-shared-datasets-1/<asset-root>/latest/
gcloud storage ls gs://skytruth-shared-datasets-1/<asset-root>/runs/
```

Also inspect:

```bash
gcloud storage cat gs://skytruth-shared-datasets-1/_catalog/releases/<asset-slug>.json
gcloud storage objects describe gs://skytruth-shared-datasets-1/<asset-root>/latest/<asset-slug>.fgb
```

Confirm `latest_release` points at the newest successful release, `latest_run`
records the most recent success or meaningful skip, and `latest/` object custom
metadata matches the current release.
Do not treat a missing repo `last_updated` edit as deployment drift for a cron
job.

9. Verify alert coverage for the deployed job and future jobs:
   - Read the live Cloud Run and Scheduler failure alert filters.
   - Confirm the new job is covered without depending on a stale allowlist.
   - If a controlled failure test is safe, run it and verify the matching log and Slack delivery.
   - Confirm any bad one-off env overrides were not persisted to the job.

## Completion notes

In the final response or PR, include:

- Container image URI deployed.
- Terraform resources applied.
- Manual execution result, or async execution name and current status.
- Scheduler schedule and timezone.
- Remote GCS paths verified.
- Alert policy coverage verified, including whether filters are future-proof or job-specific.
- Slack delivery status when relevant, distinguishing Monitoring notification channels from the protected Terraform workflow summary webhook.
- Any known skipped, cancelled, failed, or still-running executions.
- Any partial release paths intentionally left untouched.
