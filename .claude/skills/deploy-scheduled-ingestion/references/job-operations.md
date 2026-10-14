# Job operations

Load only the section for the job being deployed or controlled. Runtime,
container recipes and processing limits remain in `ingestion/<job>/README.md`.

## eamlis-monthly deployment

Production deploys run through `.github/workflows/eamlis-monthly-deploy.yml`
in the `shared-datasets-production` environment. Merging reviewed changes that
touch this job, `ingestion/common/`, the copied `scripts/` modules, or the job
Terraform routes deployment through successful main CI. The deploy workflow
uses `scripts/tested_image_authorization.py` to verify and load the exact image
retained by CI's `production-images` suite, smoke-tests and pushes those bytes
without rebuilding, pins the immutable registry digest, applies only
`module.eamlis_monthly_job.google_cloud_run_v2_job.this`,
runs a waited canary execution, and validates the latest release metadata
contract plus asset-scoped bucket hygiene. Use the workflow's `canary_run_date`
dispatch input for a deliberate backfill or metadata-contract repair run. Do
not deploy this job with a local `terraform apply` or by pushing hand-built
images.

## eamlis-monthly schedule control and teardown

Immediate stop:

```bash
gcloud scheduler jobs pause eamlis-monthly \
  --location=us-central1 \
  --project=shared-datasets-1
```

Pausing the scheduler stops future automatic monthly runs without deleting the
Cloud Run Job, service accounts, IAM, Terraform state, or published GCS data.

## sea-ice-daily schedule control and teardown

Pause future scheduled runs through the protected workflow:

```bash
gh workflow run ingestion-schedule-control.yml --ref main \
  -f job=sea-ice-daily \
  -f action=pause
```

Wait for the workflow to complete and verify the scheduler is paused. Pausing
stops future automatic runs but does not cancel an execution already running.
It preserves the job, identities, Terraform state, and published data.

For permanent teardown, remove the sea-ice resources from Terraform in a
reviewed PR and summarize a local review plan's resource deletions. The protected
production workflow must support those exact deletions; if its allowlist does
not, extend the constrained workflow in the same PR. Apply only after
`jonaraphael` review and merge to `main`, through the
`shared-datasets-production` environment. Follow
[protected Terraform guidance](../../protected-terraform-apply/SKILL.md);
do not run a local production destroy or apply.

Infrastructure teardown preserves existing GCS releases, latest files, run
records, README files, and catalog rows. A separately requested canonical
deletion requires its own immutable reviewed dataset plan.

## wdpa-monthly deployment

Production deploys use protected `.github/workflows/wdpa-monthly-deploy.yml`
after reviewed changes merge to `main`. The workflow verifies the accepted
producer image, retained bundle and exact-head owned promotion authority, then
uses `Dockerfile.promotion` to add reviewed publication code and pins the
resulting registry digest. It never regenerates dataset artifacts. The canary
uses `WDPA_PROMOTION_BUNDLE` and the accepted build's `RUN_DATE`; a different
canary date is rejected. Publication preserves claims, counters, generation
preconditions and already committed realm receipts.

Require successful `ci-ready` and the ingestion/registry IAM prerequisites.
Manual dispatch from `main` requires `executor_sha`, `source_run_id` (the
main-push CI run) and `source_run_attempt` (its successful `ci-ready` attempt).
Use workflow help and the exact reviewed workflow for current inputs. Check
pending/running worker executions and ownership before the canary; follow any
asynchronous execution to terminal status through the skill's verification steps.

The template pins the accepted bundle from `catalog/wdpa-processing-acceptance.json`;
`RUN_DATE` remains execution-only. The default current UTC month must match the
accepted bundle. A missing or wrong-month bundle fails before publication.
A completed month verifies both receipts and returns `skipped` without artifact
downloads. Each new month requires newly reviewed build and promotion authority.
The one-time October producer/workflow is retired: a future build needs a
separately reviewed path. Monthly deployment verifies the retained hash-bound
terminal execution snapshot rather than querying the retired producer execution.

Use the [monthly runtime/evidence contract](../../../../docs/wdpa-monthly-runbook.md)
for acceptance pins and owned recovery facts. Do not deploy with a local
production Terraform apply or hand-built image. The independent observer remains
enabled; its rolling recovery-window health policy is separate from immediate
worker failure alerts.

## wdpa-monthly schedule control and teardown

Immediate stop:

```bash
gcloud scheduler jobs pause wdpa-monthly \
  --location=us-central1 \
  --project=shared-datasets-1
```

Pausing stops future automatic monthly runs; it does not drain running
executions or release publication claims. It preserves the job, identities,
Terraform state and published data.

Permanent infrastructure teardown requires a reviewed PR and a constrained
protected production workflow with explicit worker, observer, scheduler and IAM
allowlists. Do not run a local Terraform destroy/apply or delete dataset releases,
latest files, run records, README files or catalog rows as part of cost teardown.
