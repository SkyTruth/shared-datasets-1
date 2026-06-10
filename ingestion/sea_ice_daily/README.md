# Daily IMS Sea-Ice Job

This job publishes the `ims-sea-ice-extent` asset from the NOAA/NSIDC IMS Daily
Northern Hemisphere Snow and Ice Analysis 4 km GeoTIFFs.

The job selects raw raster class `3`, which NSIDC describes as sea/lake ice. It
does not remove inland or lake ice and does not apply a land mask.

## Runtime

Entrypoint:

```bash
python -m ingestion.sea_ice_daily.run
```

Required environment:

```bash
GOOGLE_CLOUD_PROJECT=shared-datasets-1
SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
```

Optional environment:

```bash
RUN_DATE=YYYY-MM-DD
SEA_ICE_SOURCE_URL_TEMPLATE=https://noaadata.apps.nsidc.org/NOAA/G02156/GIS/4km/{yyyy}/{file_name}
SEA_ICE_MAX_LOOKBACK_DAYS=14
```

`RUN_DATE` is the source lookup anchor. The job probes backward from that date
until it finds an available 4 km IMS GeoTIFF. Release folders and the `ice_date`
field use the GeoTIFF filename date. The run record also stores the
source-documented valid date, which is the filename date plus one day for these
GeoTIFFs.

## Publishing Behavior

The job writes:

```text
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/releases/YYYY-MM-DD/ims-sea-ice-extent.fgb
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/releases/YYYY-MM-DD/ims-sea-ice-extent.pmtiles
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/releases/YYYY-MM-DD/ims-sea-ice-extent.metadata.ndjson.gz
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/releases/YYYY-MM-DD/ims-sea-ice-extent.schema.json
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/releases/YYYY-MM-DD/ims-sea-ice-extent.manifest.json
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/latest/ims-sea-ice-extent.fgb
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/latest/ims-sea-ice-extent.pmtiles
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/latest/ims-sea-ice-extent.metadata.ndjson.gz
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/latest/ims-sea-ice-extent.schema.json
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/latest/ims-sea-ice-extent.manifest.json
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/runs/YYYY-MM-DD.json
```

The FGB contains the source class value, `ice_date`, and generated
`feature_id`, `geometry_hash`, and `properties_hash` columns. The metadata
sidecar projects the source class value and date by `feature_id`. The PMTiles
artifact is generated directly with Tippecanoe from the GeoJSONSeq tile source
and contains only `feature_id`.

Release uploads use no-clobber GCS generation preconditions. `latest/` uploads
replace only the current observed generation. If a successful run record exists,
the job skips. If release objects exist without a successful run record, the job
fails before touching `latest/`.

## Container

Build from the repo root:

```bash
docker build -f ingestion/sea_ice_daily/Dockerfile -t sea-ice-daily .
```

## Cost Controls and Teardown

Immediate stop:

```bash
gcloud scheduler jobs pause sea-ice-daily \
  --location=us-central1 \
  --project=shared-datasets-1
```

Pausing the scheduler stops future automatic daily runs without deleting the
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

Then destroy only the sea-ice cron resources:

```bash
terraform -chdir=terraform/envs/prod destroy \
  -target=module.sea_ice_daily_scheduler \
  -target=google_cloud_run_v2_job_iam_member.sea_ice_scheduler_invoker \
  -target=module.sea_ice_daily_job \
  -target=google_storage_bucket_iam_member.sea_ice_job_object_user \
  -target=module.sea_ice_scheduler_service_account \
  -target=module.sea_ice_job_service_account
```

If the teardown should be permanent, remove or comment the sea-ice Terraform
blocks before the next untargeted apply; otherwise Terraform will recreate them.
Do not delete existing GCS releases, latest files, run records, README files, or
catalog rows as part of cost teardown unless the team explicitly decides to
remove the dataset asset.
