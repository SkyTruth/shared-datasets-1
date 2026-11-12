# Daily IMS Sea-Ice Job

> **First deployment:** install the reviewed feature-ID reset while the job is
> paused and drained. Deployment verifies installed publication state before
> building the image. See the [installation runbook](../../docs/feature-id-reset-installation.md).

This job publishes the `ims-sea-ice-extent` asset from the NOAA/NSIDC IMS Daily
Northern Hemisphere Snow and Ice Analysis 4 km GeoTIFFs.

The job selects raw raster class `3`, which NSIDC describes as sea/lake ice. It
does not remove inland or lake ice and does not apply a land mask.

Deployment, schedule control and teardown procedures live in the
[deploy skill](../../.claude/skills/deploy-scheduled-ingestion/SKILL.md) and its
[job operations](../../.claude/skills/deploy-scheduled-ingestion/references/job-operations.md#sea-ice-daily-schedule-control-and-teardown).

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

`RUN_DATE` is the NOAA filename lookup anchor and defaults to today UTC. The job probes backward from that
date until it finds an available 4 km IMS GeoTIFF. New-contract release folders
use the documented valid date, one day after the filename date. The `ice_date`
field and `source_filename_date` preserve the filename date. Historical releases
retain their original filename-date folders.

## Publishing Behavior

`ingestion/common/lifecycle.py` owns the stage order and publication rules:
`resume → baseline → acquire → describe → build → gate → publish`.
The baseline stage verifies the latest committed publication's metadata contract
before source lookup; a broken baseline requires reviewed repair even when the
source would change. Acquisition downloads and expands the selected raster;
description fingerprints its decoded samples. The build checks for partial
dated releases, loads the generated-ID baseline and reviewed identity decisions,
then creates the vector bundle. Empty output fails with `output contains no features`.
The explicit `null` loss limit permits daily polygon turnover. An unexpected gate
reason fails with `LifecycleError`; sea ice has no hold objects or Slack hold flow.

Every record names the filename lookup anchor in `run_date`. Successful records
name the source's documented valid date in `release_date`; unchanged check-ins
name the baseline release. An unavailable-source check-in names anchor date plus
one day, the documented valid date of the newest requested filename. Transient
probe warnings remain in `source_request_warnings`. Unavailable check-ins still
update the release index's latest run.

New success records include `tools` entries for GDAL inspection, translation,
calculation, polygonization and vector conversion, Tippecanoe and its decoder,
and PMTiles. Each entry records the resolved executable path and suite version.
Tool resolution and version checks precede any durable resume, reservation or
claim. Deployed records without `tools` remain valid baseline inputs.

The reviewed asset README is embedded in the image and updated through the same
owned publication transaction after the release commits. Its checkpoint and
generation precondition make documentation updates recoverable with the bundle.

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
replace only the current observed generation. Completed or interrupted owned
transactions for the same Cloud Run execution resume their captured intent
before reading the source. A fresh execution with the same lookup anchor polls
and compares again; daily run-record names identify release dates rather than
attempts. Partial release objects fail before the build or any `latest/` write.

## Container

Build from the repo root:

```bash
docker build -f ingestion/sea_ice_daily/Dockerfile -t sea-ice-daily .
```

## Generated-ID publication

The job uses `OwnedGeneratedPublisher` under `generated-2026-v1`. The
[reset runbook](../../docs/feature-id-reset-installation.md) defines the one-time
transition and first-publication checks. Historical releases remain readable;
their IDs do not seed the new contract. Deployment requires installed reset
state. Missing state is an error.

The publisher reserves IDs and claims the asset before exposing artifacts.
Later builds read the exact committed manifest/metadata generations and retain
the counter when features disappear. Runtime identity binds `CLOUD_RUN_EXECUTION`
and the image's embedded `SHARED_DATASETS_EXECUTOR_SHA`. Retries resume the
captured intent; an interrupted checkpoint holds its claim and reservation.
Starting another execution does not abandon them. Historical success records
cannot skip the first new-contract release.

The first release date must match the available NOAA source's documented valid
date. Set `canary_run_date` to the preceding filename date; for example, the
September 29 raster produces the September 30 new-contract release. Keep
the schedule paused until that publication and its native artifact checks finish;
resuming through the protected workflow requires a completed publication receipt.

## Source equality

The [IMS fingerprint](../../docs/source-fingerprints.md#ims-sea-ice-ims-raster-v1)
includes the complete filename and normalized raster content. The job downloads
and decodes the source before comparison; an unchanged check skips polygonization
and publication and records an owned release-index check-in with reason
`source fingerprint unchanged` and the baseline row count. A new filename
always creates a daily item even with identical geometry. Corrected content under
an already published date requires reviewed repair. Release/feature identity and
the documented filename-to-valid-date offset are unchanged.
