# Job operations

Load only the section for the job being deployed or controlled. Runtime,
container recipes and processing limits remain in `ingestion/<job>/README.md`.

## eamlis-monthly deployment

Production deploys run through `.github/workflows/eamlis-monthly-deploy.yml`
in the `shared-datasets-production` environment. Merging reviewed changes that
touch this job, `ingestion/common/`, the copied `scripts/` modules, or the job
Terraform builds a fresh image from `main`, smoke-tests it, pushes an immutable
digest, applies only `module.eamlis_monthly_job.google_cloud_run_v2_job.this`,
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
after reviewed changes merge to `main`. The workflow promotes the accepted
immutable image; it does not rebuild it. For the October rollout it requires one
retained complete build and its exact-head reviewed promotion plan, then executes
the worker with `WDPA_PROMOTION_BUNDLE` and the build's `RUN_DATE`. Publication
copies that bundle's exact FGB, PMTiles and sidecar bytes through the existing
owned publisher, preserving claims, counters, generation preconditions and
recovery. It does not run source processing again. A different canary date is
rejected. The independent observer and unattended failure alerts remain enabled.

The deploy job waits for the protected ingestion IAM sync to bootstrap access
management on the one Slack secret and install runtime translation-notice
permissions. This prerequisite does not replace the retained-build acceptance
and promotion gates.

The job template pins `WDPA_PROMOTION_BUNDLE` to the accepted build reference
from `catalog/wdpa-processing-acceptance.json`; `RUN_DATE` remains an
execution-only override. Scheduled invocations therefore use the current UTC
month and can consume only a reviewed build for that month. A missing bundle or
a bundle for another month fails before publication with an actionable error;
the worker never falls back to processing source data. Each new month's retained
build and promotion authority must be reviewed and deployed before that month's
scheduled publication can succeed. Repeat invocations of a completed month
verify the owned receipts and return `skipped` without downloading or
republishing artifacts.

Do not deploy this job with a local Terraform apply or hand-built production image. See the
[single-build runbook](../../../../docs/wdpa-processing-validation.md#single-build-and-promotion).

## wdpa-monthly schedule control and teardown

Immediate stop:

```bash
gcloud scheduler jobs pause wdpa-monthly \
  --location=us-central1 \
  --project=shared-datasets-1
```

Pausing the scheduler stops future automatic monthly runs without deleting the
Cloud Run Job, service accounts, IAM, Terraform state, or published GCS data.

Permanent infrastructure teardown requires a reviewed PR and a constrained
protected production workflow with explicit worker, observer, scheduler and IAM
allowlists. Do not run a local Terraform destroy/apply or delete dataset releases,
latest files, run records, README files or catalog rows as part of cost teardown.
