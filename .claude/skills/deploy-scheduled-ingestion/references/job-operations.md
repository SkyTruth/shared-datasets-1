# Job operations

Load only the section for the job being deployed or controlled. Runtime,
container recipes and processing limits remain in `ingestion/<job>/README.md`.

## mpatlas-monthly deployment

Follow [the MPAtlas job contract](../../../../ingestion/mpatlas_monthly/README.md).
Reviewed main changes deploy through `mpatlas-monthly-deploy.yml` after tested
image, ingestion IAM, registry and cron-monitoring prerequisites. The workflow
creates/updates its job, scheduler invoker, monthly scheduler and optional
canonical deny-policy writer exception, consumes
the retained CI image, runs a waited canary and validates the latest release.
The job compares raw source SHA-256, then canonical feature hashes against the
existing published metadata, and publishes only changed content. Verify skipped
check-ins leave the prior release intact. Do not use a local production apply.

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

When EAMLIS Scheduler is paused, deployment defers its automatic canary unless
an explicit `canary_run_date` is supplied. It still verifies the deployed digest
and existing release contract. This preserves a maintenance pause and avoids
trying a legacy completed run date after identity activation. It never resumes
the schedule. Missing or unexpected Scheduler state fails the gate.

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

The protected `wdpa-monthly-deploy.yml` loads the exact tested Linux AMD64 image
retained by successful main CI, verifies its registry/configuration digest and
applies the allowlisted saved plan. Require ingestion/registry IAM prerequisites,
installed publication state and no active conflicting worker execution.
Manual dispatch on `main` requires `executor_sha`, `source_run_id` and
`source_run_attempt` from the successful main-push CI attempt.

The scheduled worker downloads, samples, fully builds, validates and publishes
the current UTC month. No monthly acceptance PR, pinned bundle or redeployment is
required. The job template must not set `RUN_DATE` or `WDPA_PROMOTION_BUNDLE`.
Already completed months and unchanged content produce skipped check-ins.
Unavailable upstream data is retried at the next scheduled invocation.

Follow the [monthly runbook](../../../../docs/wdpa-monthly-runbook.md) for the
one-time migration from the paused publisher: protected IAM sync, one protected
deployment with an explicit canary date, terminal verification, then protected
schedule resume. A paused schedule without an explicit canary date defers the
whole deployment before mutation.
Preserve owned claims, counters, generation preconditions and committed receipts.
The old manual build workflow is retired; October evidence remains historical.

Do not deploy with local production Terraform or a locally pushed image.
The former catalog observer stays paused and its health policy disabled.
WDPA uses shared worker/Scheduler alerts and per-asset release-index check-ins.

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
