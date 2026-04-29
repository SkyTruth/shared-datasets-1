# Monthly WDPA Job

This job publishes two bare-bones WDPA/WDOECM assets:

- `wdpa-marine`
- `wdpa-terrestrial`

It downloads the monthly Protected Planet shapefile zip, selects source geometry
layers that contain `MARINE`, verifies the selected layer schemas match, and
splits rows only by `MARINE`.

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

`RUN_DATE` controls the release date and source month token. The default source
template supports `{run_date}`, `{year}`, `{month}`, and `{month_token}`.

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

## Container

Build from the repo root:

```bash
docker build -f ingestion/wdpa_monthly/Dockerfile -t wdpa-monthly .
```
