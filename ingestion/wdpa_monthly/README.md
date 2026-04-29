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
