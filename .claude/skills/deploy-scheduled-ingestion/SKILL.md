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
- Apply or verify the Artifact Registry repository before pushing a new image.
- Push the image before applying the Cloud Run Job that references it.
- Keep runtime service accounts narrow: only the job account should write dataset objects; scheduler gets only `roles/run.invoker`.
- Run one manual Cloud Run Job execution after deployment and verify bucket outputs.
- Do not use Terraform for changing dataset files under `latest/`, `releases/`, or `runs/`.

## Standard sequence

1. Load `.claude/skills/gcp-shared-datasets/SKILL.md` if the job writes GCS objects.
2. Validate locally:

```bash
terraform fmt -check -recursive
terraform -chdir=terraform/envs/prod init
terraform -chdir=terraform/envs/prod validate
```

3. Create or update infrastructure that the image push depends on:

```bash
terraform -chdir=terraform/envs/prod apply \
  -target=google_artifact_registry_repository.jobs \
  -var="wdpa_monthly_image=<final-image-uri>"
```

4. Build and push an immutable image:

```bash
IMAGE=us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/<job-name>:YYYYMMDDHHMMSS
gcloud auth configure-docker us-central1-docker.pkg.dev
docker build --platform linux/amd64 -f ingestion/<job>/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"
```

5. Apply full infrastructure with the exact image URI:

```bash
terraform -chdir=terraform/envs/prod plan  -var="wdpa_monthly_image=$IMAGE"
terraform -chdir=terraform/envs/prod apply -var="wdpa_monthly_image=$IMAGE"
```

6. Execute the job once and wait:

```bash
gcloud run jobs execute <job-name> --region=us-central1 --project=shared-datasets-1 --wait
```

7. Verify:

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
