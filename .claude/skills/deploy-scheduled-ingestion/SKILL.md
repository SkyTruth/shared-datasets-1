---
name: deploy-scheduled-ingestion
description: "Use before deploying or updating shared-datasets Cloud Run and Cloud Scheduler ingestion jobs, including Artifact Registry images, Terraform applies, manual job executions, and post-deploy verification."
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
- Run one manual Cloud Run Job execution after deployment and verify bucket outputs.
- Do not use Terraform for changing dataset files under `latest/`, `releases/`, or `runs/`.

## Large-source sizing

For very large geospatial sources such as WDPA/WDOECM, do not assume Cloud Run
will finish within a short default timeout. Measure at least one representative
local fractional run, then treat full-source conversion as a multi-hour batch
job unless there is direct evidence otherwise.

Use these defaults for the simplified WDPA monthly job unless the code has been
made materially faster:

- Cloud Run Job task resources: `8` CPU and `32Gi` memory.
- Cloud Run Job task timeout: at least `86400s` (24 hours).
- Retries: `0` while first validating idempotency and partial-release behavior;
  add retries only after failures are known to be safe to replay.

When a manual post-deploy execution is expected to take hours, it is acceptable
to start it asynchronously instead of waiting in the agent session:

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

gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="wdpa-monthly"' \
  --project=shared-datasets-1 \
  --limit=50
```

If the current-day release already has partial objects without a success run
record, do not overwrite or delete them from the deployment workflow. Use a
deliberate backfill/canary `RUN_DATE` for the manual async execution, or ask for
explicit approval to clean up the partial release.

## Standard sequence

1. Load `.claude/skills/gcp-shared-datasets/SKILL.md` if the job writes GCS objects.
2. Validate locally:

```bash
terraform fmt -check -recursive
terraform -chdir=terraform/envs/prod init
terraform -chdir=terraform/envs/prod validate
```

3. For large source files, run a fractional sandbox test before deploying. Mount the downloaded source and repo into the same Linux image that Cloud Run will use, set sample-only environment variables, and build FGB/PMTiles locally without GCS publishing:

```bash
docker run --platform linux/amd64 --rm -i \
  -e TMPDIR=/data/tmp \
  -e WDPA_SAMPLE_FRACTION=0.001 \
  -e WDPA_SAMPLE_SEED=7919 \
  -v "$PWD":/work \
  -v /private/tmp/wdpa-monthly-local:/data \
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

7. Execute the job once and wait:

```bash
gcloud run jobs execute <job-name> --region=us-central1 --project=shared-datasets-1 --wait
```

8. Verify:

```bash
gcloud scheduler jobs describe <job-name> --location=us-central1 --project=shared-datasets-1
gcloud run jobs executions list --job=<job-name> --region=us-central1 --project=shared-datasets-1
gcloud storage ls gs://skytruth-shared-datasets-1/<asset-root>/latest/
gcloud storage ls gs://skytruth-shared-datasets-1/<asset-root>/runs/
```

## Completion notes

In the final response or PR, include:

- Container image URI deployed.
- Terraform resources applied.
- Manual execution result.
- Scheduler schedule and timezone.
- Remote GCS paths verified.
- Any known skipped or failed outputs.
