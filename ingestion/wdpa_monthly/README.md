# Monthly WDPA Job

> **Deployment hold in PR #154:** this branch blocks all three ingestion deployment
> workflows before cloud authentication, image builds, Terraform, or canaries.
> The hold also blocks routine maintenance. Existing deployed jobs and schedules
> continue running; this is not an old-writer fence. The
> [fresh-start rollout plan](../../docs/proposals/feature-id-fresh-start.md) lists
> the evidence and reviewed controls required before lifting the hold.

This job publishes two bare-bones WDPA/WDOECM assets:

- `wdpa-marine`
- `wdpa-terrestrial`

It downloads the monthly Protected Planet shapefile zip, selects the source
point and polygon layers, and splits rows by the source split field. `MARINE`
is used when present; current WDPA/WDOECM shapefiles use `REALM`, with
`Marine` and `Coastal` routed to `wdpa-marine` and `Terrestrial` routed to
`wdpa-terrestrial`.

The output schema is the union of source fields. The current source has
polygon-only fields such as `GIS_M_AREA` and `GIS_AREA`; those fields are
preserved and are null for point rows rather than being dropped or renamed.

The job intentionally does not rename fields, buffer points, calculate areas,
build statistics tables, update Strapi, or write database payloads.

Name fields keep the upstream WDPA schema. `NAME` is the protected or conserved
area name as supplied by WDPA, generally in the original or local language/script
of the protected area. `NAME_ENG` is the upstream English-name source field in
canonical FGB and canonical metadata. Localized metadata sidecars preserve the
source field names, so translated display names are written back to `NAME_ENG`.
In localized sidecars, `NAME_ENG` is a misleading legacy name; consumers should
treat it as the active-locale display name, effectively `name_localized`, not as
an English-only value.

## Translation reuse

The job rebuilds `es`, `fr`, `id`, `pt`, `pt_br`, and `sw` metadata sidecars
together with each release. `ingestion/wdpa_monthly/translations.py` lists the
17 supported text fields. The translation-source CSV and all six sidecars are
part of the same owned publication as the geometry and canonical metadata.

Translations are matched first by the exact `SITE_PID` and original source-value
hash. Numeric `feature_id` values never join translations across releases or
identity contracts. An unambiguous existing translation of the same source text,
field, and locale can fill another record. Conflicting text translations remain
site-specific. Existing review states and notes are retained for direct matches;
phrase reuse is marked `reused_translation`.

The job downloads the exact canonical metadata and translation CSV generations
recorded in its last committed publication receipt. Only the explicit first-reset
state uses the verified June 9 legacy source bundles pinned in `translations.py`.
That state also binds a reviewed gap supplement by staged object path, generation,
and SHA-256. Both assets must approve the same supplement; the first build verifies
and consumes it without replacing established translations. The first new release
must have complete requested translations before the publisher reserves IDs.
Later builds use their committed CSV and do not reload the reset supplement.
Missing state or an incomplete committed bundle fails; it does not select arbitrary
`latest/` files. One disposable SQLite index serves both WDPA assets. Large CSVs
are streamed and the downloaded copies are locally compressed.

In subsequent releases, new or changed text without a verified translation retains the canonical value.
Its CSV row is empty with `review_state=translation_failed`; the run record reports
the unresolved counts. The job does not contact a translation provider or claim
that fallback text is translated. Follow the [rebuild report and gap workflow](../../docs/proposals/wdpa-translation-rebuild-2026-09-29.md)
to fill these tasks without retranslating the full dataset. New machine results
remain labeled as machine output rather than human review.

## Runtime

Entrypoint:

```bash
python -m ingestion.wdpa_monthly.run
```

Required environment:

```bash
GOOGLE_CLOUD_PROJECT=shared-datasets-1
SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
```

Optional environment:

```bash
RUN_DATE=YYYY-MM-DD
WDPA_SOURCE_URL_TEMPLATE=https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_WDOECM_{month_token}_Public_all_shp.zip
```

`RUN_DATE` controls the release date and source month token. When `RUN_DATE` is
unset, the job uses the first day of the current UTC month so repeated scheduled
attempts in the source availability window target one stable release path. The
default source template supports `{run_date}`, `{year}`, `{month}`, and
`{month_token}`.

## Publishing behavior

For each asset, the job writes:

```text
100-geographic-reference/130-protected-areas/{asset}/releases/YYYY-MM-DD/{asset}.{artifact}
100-geographic-reference/130-protected-areas/{asset}/latest/{asset}.{artifact}
100-geographic-reference/130-protected-areas/{asset}/runs/YYYY-MM-DD.json
```

The twelve artifacts are FGB, PMTiles, canonical metadata, schema, manifest,
the translation-source CSV, and six locale metadata sidecars.

Release uploads use no-clobber GCS generation preconditions. `latest/` uploads
replace only the current observed generation. If a successful run record exists,
that asset is skipped. If release objects exist without a successful run record,
the job fails before touching `latest/`.

The upstream Protected Planet ZIP for a new month is not guaranteed to exist on
the first day of the month. HTTP 403/404 source responses are treated as "not
available yet"; the job exits successfully with skipped records and writes no
GCS objects. The production scheduler runs daily on days 1-10 of each month, so
the first run after the source appears publishes the stable month-start release,
and later attempts skip because the success run record exists.

## Container

Build from the repo root:

```bash
docker build -f ingestion/wdpa_monthly/Dockerfile -t wdpa-monthly .
```

## Deploy

Production deploys run through `.github/workflows/wdpa-monthly-deploy.yml` in
the `shared-datasets-production` environment. Merging reviewed changes that
touch this job, `ingestion/common/`, the copied `scripts/` modules, reviewed
`catalog/feature-identity-resolutions/` decisions, or the job Terraform builds
a fresh image from `main`, smoke-tests it, pushes an immutable digest, applies
only `module.wdpa_monthly_job.google_cloud_run_v2_job.this`, and starts an
async canary execution. The image ships the feature-identity resolutions
directory, so merging reviewed ambiguity decisions redeploys the job and the
next scheduled attempt picks them up. Use the workflow's `canary_run_date`
dispatch input for a deliberate backfill or metadata-contract repair run. Do
not deploy this job with a local `terraform apply` or by pushing hand-built
images.

## Cost Controls and Teardown

Immediate stop:

```bash
gcloud scheduler jobs pause wdpa-monthly \
  --location=us-central1 \
  --project=shared-datasets-1
```

Pausing the scheduler stops future automatic monthly runs without deleting the
Cloud Run Job, service accounts, IAM, Terraform state, or published GCS data.

To remove the scheduled job infrastructure with Terraform, first provide the
required image variables for the prod environment:

```bash
export TF_VAR_wdpa_monthly_image="$(gcloud run jobs describe wdpa-monthly \
  --region=us-central1 \
  --project=shared-datasets-1 \
  --format='value(spec.template.spec.template.spec.containers[0].image)')"
export TF_VAR_sea_ice_daily_image="$(gcloud run jobs describe sea-ice-daily \
  --region=us-central1 \
  --project=shared-datasets-1 \
  --format='value(spec.template.spec.template.spec.containers[0].image)')"
```

Then destroy only the WDPA cron resources:

```bash
terraform -chdir=terraform/envs/prod destroy \
  -target=module.wdpa_monthly_scheduler \
  -target=google_cloud_run_v2_job_iam_member.scheduler_invoker \
  -target=module.wdpa_monthly_job \
  -target=google_storage_bucket_iam_member.wdpa_job_object_user \
  -target=module.wdpa_scheduler_service_account \
  -target=module.wdpa_job_service_account
```

If the teardown should be permanent, remove or comment the WDPA Terraform blocks
before the next untargeted apply; otherwise Terraform will recreate them. Do not
delete existing GCS releases, latest files, run records, README files, or catalog
rows as part of cost teardown unless the team explicitly decides to remove the
dataset assets.

## Local Fractional Sandbox

For fast debugging against the real upstream source, download/extract the source
ZIPs under a local scratch directory and run a deterministic FID sample through
the same FGB and PMTiles conversion chain without publishing to GCS:

```bash
docker run --platform linux/amd64 --rm -i \
  -e TMPDIR=/data/tmp \
  -e WDPA_SAMPLE_FRACTION=0.001 \
  -e WDPA_SAMPLE_SEED=7919 \
  -e LOCAL_WDPA_WORKDIR=/data/wdpa-sample-output \
  -v "$PWD":/work \
  -v /private/tmp/wdpa-monthly-local:/data \
  -w /work \
  wdpa-monthly \
  python scripts/local_wdpa_sample.py
```

The sample harness never instantiates a GCS client and leaves outputs in the
local work directory.

## Generated-ID deployment prerequisite

Both WDPA assets use `OwnedGeneratedPublisher` and the new identity contract
`generated-2026-v1`. The approved [pre-launch fresh start](../../docs/proposals/feature-id-fresh-start.md)
retires the previous identity history. Historical releases remain readable;
their IDs and legacy `ext_id` mappings cannot seed this contract. Each asset
requires an explicitly reviewed reset inventory and installed publication state.
Missing state stops the job; it cannot silently restart numbering.

The publisher reserves IDs and claims the asset before checkpointing or exposing
the release bundle. Later builds read the exact current manifest and referenced
metadata generations, and retain the counter when features disappear. Runtime
identity comes from `CLOUD_RUN_EXECUTION` and the image's embedded
`SHARED_DATASETS_EXECUTOR_SHA`. Retries resume the same captured intent. A crash
before all local inputs have durable checkpoints stops with the claim held;
starting another execution does not abandon the reservation.

The first new release must replace every captured `latest/` object. The user
chose to rebuild the ten old locale aliases identified in the
[readiness report](../../docs/proposals/feature-id-readiness-2026-09-29.md).
The publisher requires all six locale outputs. Local September-snapshot
candidates now have complete requested-row coverage and validated CSV/locale
joins. Machine wording still needs review; the complete new-ID release and its
native validation remain prerequisites for cutover. Historical releases stay intact.

Deployment remains blocked on the protected reset installation path, exclusion
of older writers, and native artifact and serving checks. Both WDPA assets must
be ready before this shared job resumes. The local adapter and an offline review
envelope are not production cutover authority.
