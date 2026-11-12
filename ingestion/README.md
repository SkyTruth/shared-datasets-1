# ingestion/

This directory contains production scheduled ingestion jobs, the
[passive dataset usage collector](dataset_usage/README.md), and shared helpers.
The usage collector writes private telemetry only; the publishing semantics below
apply to jobs that produce dataset releases.

The [MPAtlas monthly refresh](mpatlas_monthly/README.md) checks the public zone
API on the second day of each month and publishes only changed feature content.

Use [the ingestion deployment procedure](../docs/ci-deployment.md) when adding
or changing a deploy target. Job READMEs own source/runtime behavior; the
procedure owns registry wiring, validation, protected rollout and verification.

## Layout

Each production cron job gets its own package:

```text
ingestion/<job_slug>/
  README.md
  Dockerfile
  run.py
tests/test_<job_slug>.py
terraform/envs/prod/<job_slug>.tf
```

Use `ingestion/common/` for reusable internals that are not tied to one source
or asset, such as:

- retained-candidate holds, execution claims and reviewed resolution
- generation-preconditioned GCS publishing
- run-record writes
- logging setup
- subprocess helpers
- content type selection
- hashing
- temporary-file cleanup

Keep source-specific parsing, filtering, schema choices, asset slugs, canonical
paths, conversion rules, environment variables, and scheduler configuration in
the owning job package.

`ingestion/common/lifecycle.py` defines the release lifecycle: the stage order,
the rules between stages and the run-record shape. `tests/test_lifecycle.py`
holds one test per rule. `mpatlas_monthly`, `eamlis_monthly` and `sea_ice_daily`
run through `lifecycle.run`.
`wdpa_monthly` fingerprints after its build, so it keeps its own orchestrator
until it has a pre-build fingerprint.

Every run record keeps the day the job ran in `run_date` and the release it
describes in `release_date`. The release index takes `latest_run.date` from
`run_date` and `latest_release.date` from `release_date`. Manual releases use the
UTC day their run record is prepared. Release paths use the release date.
EAMLIS and MPAtlas run-record object names use the attempt date; owned WDPA and
sea-ice publication records use the release date. An unchanged check-in names
the baseline in `release_date` and writes no dataset artifacts. Historical records
without `run_date` use `release_date` for `latest_run.date`; earlier skipped
records retain their stored dates.

Sea ice verifies its committed baseline's metadata contract before source lookup.
Its native tools are resolved and versioned before owned-publication resume,
reservation or claim. New success records include each executable's path and
version; deployed success records without `tools` remain readable. Daily polygon
turnover has no lost-object percentage limit, as recorded by the explicit `null`
policy in `catalog/ingestion-quality.json`. Sea ice has no retained-candidate hold
flow; an unexpected hold reason fails the run.

`ingestion/common/source_quality.py` supplies one retained-candidate hold flow
for EAMLIS and MPAtlas. The registry binds asset paths, labels, Cloud Run jobs
and optional job-owned identity hooks. `Guard` persists holds and observations,
serializes publication and formats one Slack request before claiming delivery.
EAMLIS candidate failures preserve the hold's observation and add failure
details. `HoldFlow` stages immutable candidates and validates exact `source_loss_review` promotion
plans; the protected publisher clears a hold only after finalized-byte and
release-index verification. Both jobs use the lifecycle's lost-object limit:
new objects never offset missing baseline feature IDs. EAMLIS retains its
identity discovery, reservation and activation checks. Published-state contract
failures require reviewed repair before source acquisition.
`ingestion/common/published_baseline.py` reads the dated manifest at the generation
pinned by the Guard's release-index snapshot and checks its indexed size when
present. Index hashes can be stale after reviewed metadata updates and provide
no authority. The validated manifest pins the dated canonical schema and metadata
by generation, size and SHA-256. Validated sidecar counts must equal the manifest
count and the index's positive row count. A counted, generation-pinned baseline
needs no run record for authentication; an uncounted manual release fails at the
Guard. Run records and `latest/` bytes provide no baseline authority. Completed
success retries preserve an index already naming their release, including reviewed
metadata updates; missing release-index entries still use run-record recovery.
Fingerprint computation errors retain their original exception. Each job owns
its identity checks and fingerprint definition; `lifecycle.py` owns stage order
and gates. See
[EAMLIS hold state and review](../docs/eamlis-source-quality.md) and
[MPAtlas commands](mpatlas_monthly/README.md#review-a-held-release).

## Default Publishing Semantics

The [source fingerprint contract](../docs/source-fingerprints.md) defines each
source's comparison inputs, check-ins and recovery boundaries.

Cron jobs should publish only meaningful dataset changes. When the source or
generated output is unchanged, write a skipped run record for observability and
leave release and `latest/` dataset artifacts unchanged. Do not repeat this
behavior in asset `update_cadence` metadata; use cadence values such as `daily`,
`weekly`, or `monthly`.

Jobs that poll an upstream source over an availability window should distinguish
the stable target release date from the scheduler attempt date. For example, an
early-month monthly source should publish under one month-start release path,
while later attempts for the same source period should only write a skipped
run/check-in record for the actual attempt date.

Every success and meaningful skip must refresh the asset release index under
`_catalog/releases/{asset-slug}.json`. After changing a job, verify both
`latest_release` and `latest_run`, and inspect the custom metadata on `latest/`
objects so it matches the current release bytes.

Normal cron runs must not require Git commits or tracked catalog date edits.
Keep repo asset docs and the CSV catalog focused on static registry metadata;
the bucket release index is the source of truth for latest release, last
check-in, source version, row count, and file hashes.

## Live Job Boundaries

Do not import from one job package into another. For example, a new protected
areas job should not import from `ingestion.wdpa_monthly`; reusable behavior
belongs in `ingestion/common/`.

Do not edit a functioning live job to support a new job unless the work is an
explicit behavior-preserving refactor. Preserve Cloud Run job names, scheduler
names, service account identities, asset slugs, GCS paths, output formats,
schemas, entrypoints, and run-record shape unless the user approves changing
those live surfaces.

When `ingestion/common/` changes, run the focused tests for every production job
that imports it.

Use narrow runtime identities: a publisher writes only its owned asset root and
release-index object, while Scheduler receives only `roles/run.invoker`.
Infrastructure uses remote shared Terraform state, separate from dataset files.
Build Cloud Run images for Linux AMD64; multi-stage compiled helpers must honor
`$BUILDPLATFORM`, `$TARGETOS` and `$TARGETARCH`. Registry/IAM bootstrap must finish
before the tested image is pushed, and image push must precede the referencing
job's saved-plan apply.
