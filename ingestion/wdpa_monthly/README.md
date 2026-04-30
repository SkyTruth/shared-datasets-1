# Monthly WDPA Job

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
TIPPECANOE_EXTRA_ARGS="--drop-densest-as-needed --extend-zooms-if-still-dropping"
TIPPECANOE_ZOOM_ARGS="-Z0 -z8"
```

`RUN_DATE` controls the release date and source month token. When `RUN_DATE` is
unset, the job uses the first day of the current UTC month so repeated scheduled
attempts in the source availability window target one stable release path. The
default source template supports `{run_date}`, `{year}`, `{month}`, and
`{month_token}`.

## Publishing behavior

For each asset, the job writes:

```text
100-geographic-reference/130-protected-areas/{asset}/releases/YYYY-MM-DD/{asset}.fgb
100-geographic-reference/130-protected-areas/{asset}/releases/YYYY-MM-DD/{asset}.pmtiles
100-geographic-reference/130-protected-areas/{asset}/latest/{asset}.fgb
100-geographic-reference/130-protected-areas/{asset}/latest/{asset}.pmtiles
100-geographic-reference/130-protected-areas/{asset}/runs/YYYY-MM-DD.json
```

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
