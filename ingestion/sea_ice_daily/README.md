# Daily IMS Sea-Ice Job

> **First deployment:** install the reviewed feature-ID reset while the job is
> paused and drained. Deployment verifies installed publication state before
> building the image. See the [installation runbook](../../docs/feature-id-reset-installation.md).

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

`RUN_DATE` is the NOAA filename lookup anchor. The job probes backward from that
date until it finds an available 4 km IMS GeoTIFF. New-contract release folders
use the documented valid date, one day after the filename date. The `ice_date`
field and `source_filename_date` preserve the filename date. Historical releases
retain their original filename-date folders.

## Publishing Behavior

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
replace only the current observed generation. If a successful run record exists,
the job skips. If release objects exist without a successful run record, the job
fails before touching `latest/`.

## Container

Build from the repo root:

```bash
docker build -f ingestion/sea_ice_daily/Dockerfile -t sea-ice-daily .
```

## Cost Controls and Teardown

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
[protected Terraform guidance](../../.claude/skills/protected-terraform-apply/SKILL.md);
do not run a local production destroy or apply.

Infrastructure teardown preserves existing GCS releases, latest files, run
records, README files, and catalog rows. A separately requested canonical
deletion requires its own immutable reviewed dataset plan.

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
