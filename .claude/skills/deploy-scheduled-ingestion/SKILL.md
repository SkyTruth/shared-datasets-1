---
name: deploy-scheduled-ingestion
description: "Use before deploying or updating shared-datasets Cloud Run and Cloud Scheduler ingestion jobs, including Artifact Registry images, Terraform applies, manual job executions, alert coverage, and post-deploy verification."
---

# Deploy Scheduled Ingestion

Use this workflow for production ingestion jobs in `shared-datasets-1`.

## Rules

- Use Terraform for GCP assets: APIs, Artifact Registry, service accounts, IAM, Cloud Run Jobs, Cloud Scheduler jobs, and monitoring.
- Use remote Terraform state for shared environments; do not leave production state only on a local workstation.
- Use immutable container tags for deployments. Do not deploy `latest`.
- Build Cloud Run images for `linux/amd64`; make multi-stage Dockerfiles use `$BUILDPLATFORM`/`$TARGETOS`/`$TARGETARCH` for compiled helper binaries.
- Apply or verify the Artifact Registry repository before pushing a new image.
- Push the image before applying the Cloud Run Job that references it.
- Keep runtime service accounts narrow: only the job account should write dataset objects; scheduler gets only `roles/run.invoker`.
- Size Cloud Run Job CPU, memory, timeout, and local-temp cleanup for the full upstream source, not just test fixtures.
- Before redeploying after source-schema or format bugs, run a production-source fractional sandbox test locally. The sample must use the same conversion chain as production and must not publish sampled data unless explicitly guarded.
- Run one manual Cloud Run Job execution after deployment. Wait for short jobs; use async execution for known multi-hour jobs.
- Verify alert coverage after adding or changing scheduled jobs. Cron failure alerts should cover future jobs by default through project/region-level filters, labels, or another durable grouping; avoid per-job allowlists unless there is a documented reason.
- Distinguish Cloud Monitoring notification channels from the local Terraform apply-summary webhook. A working Monitoring Slack channel does not prove `shared-datasets-slack-webhook-url` has a Secret Manager version, and vice versa.
- Do not use Terraform for changing dataset files under `latest/`, `releases/`, or `runs/`.

## Job boundaries

- Keep each production cron job in its own package under `ingestion/<job_slug>/`, with a README, `run.py`, a Dockerfile when containerized, focused tests, and distinct Terraform blocks such as `terraform/envs/prod/<job_slug>.tf`.
- Put shared runtime and publishing behavior in `ingestion/common/`: GCS generation-precondition helpers, run-record writes, logging setup, subprocess helpers, content type selection, hashes, and temp cleanup.
- Keep source-specific parsing, filtering, schema choices, asset slugs, canonical paths, conversion rules, environment variables, and scheduler configuration inside the owning job package and its Terraform file.
- Do not import from another job package, such as `ingestion.wdpa_monthly`, unless the task is explicitly maintaining that job.
- Do not edit a functioning live job to support a new job unless the user explicitly requests a behavior-preserving refactor.
- Preserve live surfaces unless explicitly approved: Cloud Run job names, scheduler names, service account identities, asset slugs, canonical GCS paths, output formats, schemas, entrypoints, and run-record shape.
- Any change to `ingestion/common/` must run focused tests for every production job that imports it.
- Default cron publishing semantics: if the source or generated output is unchanged, write a skipped run record for observability and do not write new release or `latest/` dataset artifacts. Keep this as job behavior, not asset `update_cadence` metadata.

## Large-source sizing

For very large geospatial sources such as WDPA/WDOECM, do not assume Cloud Run
will finish within a short default timeout. Measure at least one representative
local fractional run, then treat full-source conversion as a multi-hour batch
job unless there is direct evidence otherwise.

Cloud Run writable filesystem usage counts against container memory. Prefer a
conversion order that deletes large intermediates as soon as they are no longer
needed, and avoid keeping source archives, GPKG, GeoJSONSeq, FGB, and MBTiles
alive at the same time.

Use these defaults for the simplified WDPA monthly job unless the code has been
made materially faster:

- Cloud Run Job task resources: `8` CPU and `32Gi` memory.
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
cancel the obsolete execution explicitly before starting a replacement canary.

If the current-day release already has partial objects without a success run
record, do not overwrite or delete them from the deployment workflow. Use a
deliberate backfill/canary `RUN_DATE` for the manual async execution, or ask for
explicit approval to clean up the partial release.

## Alerting

Scheduled ingestion alert policies should be maintained as part of the deployment surface.

Preferred behavior:

- Cloud Run Job execution failure alerts cover all Cloud Run Jobs in the shared-datasets project and region, or all jobs with a stable scheduled-ingestion label.
- Cloud Scheduler dispatch failure alerts cover all Scheduler jobs in the shared-datasets project and region, or all jobs with a stable scheduled-ingestion label.
- New cron jobs should not require editing a monitoring allowlist just to receive basic failure alerts.
- Manual deploy canary failures should alert unless a specific test is intentionally isolated and documented.
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
- Terraform apply summaries use `scripts/slack_notify.py` and the `shared-datasets-slack-webhook-url` Secret Manager secret, unless `SHARED_DATASETS_SLACK_WEBHOOK_URL` is set locally.

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

3. For large source files, run a fractional sandbox test before deploying. Mount the downloaded source and repo into the same Linux image that Cloud Run will use, set sample-only environment variables, and build FGB/PMTiles locally without GCS publishing:

```bash
export WDPA_LOCAL_DATA_DIR=/tmp/wdpa-monthly-local

docker run --platform linux/amd64 --rm -i \
  -e TMPDIR=/data/tmp \
  -e WDPA_SAMPLE_FRACTION=0.001 \
  -e WDPA_SAMPLE_SEED=7919 \
  -v "$PWD":/work \
  -v "$WDPA_LOCAL_DATA_DIR":/data \
  -w /work \
  "$IMAGE" \
  python scripts/local_wdpa_sample.py
```

Use `WDPA_SAMPLE_FRACTION=0.001` for fast smoke loops and increase only when the bug requires more coverage. Sampling should be deterministic so row-count validation compares the same sampled predicate used for FGB and PMTiles generation.

4. Create or update infrastructure that the image push depends on:

```bash
terraform -chdir=terraform/envs/prod apply \
  -target=google_artifact_registry_repository.jobs \
  -var="wdpa_monthly_image=<final-image-uri>"
```

5. Build and push an immutable image:

```bash
IMAGE=us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/<job-name>:YYYYMMDDHHMMSS
gcloud auth configure-docker us-central1-docker.pkg.dev
docker build --platform linux/amd64 -f ingestion/<job>/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"
```

6. Apply full infrastructure with the exact image URI:

```bash
terraform -chdir=terraform/envs/prod plan  -var="wdpa_monthly_image=$IMAGE"
terraform -chdir=terraform/envs/prod apply -var="wdpa_monthly_image=$IMAGE"
```

Prefer the repo wrapper for local production applies:

```bash
uv run python scripts/terraform_prod_apply.py --var <job_image_variable>="$IMAGE"
```

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
- Slack delivery status when relevant, distinguishing Monitoring notification channels from the Terraform apply-summary webhook.
- Any known skipped, cancelled, failed, or still-running executions.
- Any partial release paths intentionally left untouched.
