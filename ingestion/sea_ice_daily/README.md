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
TIPPECANOE_ZOOM_ARGS="-Z0 -z7"
TIPPECANOE_EXTRA_ARGS="--drop-densest-as-needed --extend-zooms-if-still-dropping"
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
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/latest/ims-sea-ice-extent.fgb
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/latest/ims-sea-ice-extent.pmtiles
200-imagery-derived/250-weather-climate/ims-sea-ice-extent/runs/YYYY-MM-DD.json
```

Release uploads use no-clobber GCS generation preconditions. `latest/` uploads
replace only the current observed generation. If a successful run record exists,
the job skips. If release objects exist without a successful run record, the job
fails before touching `latest/`.

## Container

Build from the repo root:

```bash
docker build -f ingestion/sea_ice_daily/Dockerfile -t sea-ice-daily .
```
