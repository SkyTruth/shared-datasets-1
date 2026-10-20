# Monthly e-AMLIS Job

This job publishes the existing `eamlis-abandoned-mine-land-inventory` asset from
the public OSMRE e-AMLIS ArcGIS hosted feature layer:

```text
https://services.arcgis.com/Vsy5ieu7PwNdunLd/arcgis/rest/services/eAMLISExternalView/FeatureServer/0
```

The source filter is `LAT_DEG > 0`, matching the public app's current map layer.

Deployment, schedule control and teardown procedures live in the
[deploy skill](../../.claude/skills/deploy-scheduled-ingestion/SKILL.md) and its
[job operations](../../.claude/skills/deploy-scheduled-ingestion/references/job-operations.md#eamlis-monthly-deployment).

## Runtime

Entrypoint:

```bash
python -m ingestion.eamlis_monthly.run
```

Required environment:

```bash
GOOGLE_CLOUD_PROJECT=shared-datasets-1
SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
```

Optional environment:

```bash
RUN_DATE=YYYY-MM-DD
EAMLIS_LAYER_URL=https://services.arcgis.com/Vsy5ieu7PwNdunLd/arcgis/rest/services/eAMLISExternalView/FeatureServer/0
EAMLIS_WHERE="LAT_DEG > 0"
EAMLIS_PAGE_SIZE=2000
```

`RUN_DATE` controls the release and run-record date. When unset, the job uses
the current UTC date.

## Publishing Behavior

The job first checks the requested date's run record. An existing `success` or
`skipped` record is a completed outcome: retries return that original record
without polling ArcGIS, rebuilding artifacts, or replacing the record. This also
applies when the source changes later that day. Such changes require a different,
unused `RUN_DATE`, normally the next scheduled run. Other existing statuses fail
and require investigation rather than being treated as completed.

For an unused date, the job downloads the selected ArcGIS rows and pins the
translation CSV before comparing the complete content fingerprint with the last
successful publication. Equality skips vector/sidecar generation and publication,
writes an immutable skipped run and updates the release-index check-in. Edit
metadata and summary statistics are diagnostic only. A missing metadata contract
still requires a refresh. See the [fingerprint definition and limitations](../../docs/source-fingerprints.md#e-amlis-eamlis-content-v1).

The release feature identity is copied directly from the source `OBJECTID`
field. Published `feature_id` values are plain alphanumeric strings such as
`1`, not legacy `src:OBJECTID:1` handles. The canonical FlatGeobuf includes all
source fields plus `feature_id`, `geometry_hash`, and `properties_hash`; PMTiles
are metadata-lookup tiles containing geometry and `feature_id` only.

When changed, the job writes:

```text
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/releases/YYYY-MM-DD/eamlis-abandoned-mine-land-inventory.fgb
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/releases/YYYY-MM-DD/eamlis-abandoned-mine-land-inventory.pmtiles
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/releases/YYYY-MM-DD/eamlis-abandoned-mine-land-inventory.metadata.ndjson.gz
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/releases/YYYY-MM-DD/eamlis-abandoned-mine-land-inventory.schema.json
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/releases/YYYY-MM-DD/eamlis-abandoned-mine-land-inventory.manifest.json
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/latest/eamlis-abandoned-mine-land-inventory.fgb
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/latest/eamlis-abandoned-mine-land-inventory.pmtiles
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/latest/eamlis-abandoned-mine-land-inventory.metadata.ndjson.gz
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/latest/eamlis-abandoned-mine-land-inventory.schema.json
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/latest/eamlis-abandoned-mine-land-inventory.manifest.json
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/runs/YYYY-MM-DD.json
_catalog/releases/eamlis-abandoned-mine-land-inventory.json
```

The PMTiles artifact is built by writing GDAL MBTiles at zooms 0 through 8 and
then converting that archive with `pmtiles convert`. The canonical FGB remains
the analytical source.

The asset's declared Spanish `PA_NAME` translation is rebuilt within every
changed release. The job reads the observed generation of the current CSV and
reconciles it with the new canonical metadata before generating each maintained
locale sidecar once. Reconciliation preserves every existing row and its
provenance, including successful current translations and historical rows. For
missing current keys it reuses a translation only when the exact field, locale,
and source-value hash have one distinct successful translated value across the
CSV history. Feature IDs do not establish matches across source refreshes.
Conflicting values, unavailable matches, and existing failed current tasks remain
source-text fallback; failures and excluded untranslated labels never count as
completed. Reused rows retain donor review state and notes with a reuse reference.
The job makes no translation-provider calls and leaves feature IDs, canonical
metadata, geometry and tiles untouched by reconciliation. It records coverage
in its manifest before uploading any release artifact. A missing initial CSV starts
with a header-only source and explicit translation debt. Other download or CSV
validation failures stop before publication. Unchanged-source/output skips do
not create new artifacts. Gaps retain source text and do not block publication.

Reviewed translation edits stage the CSV, rebuilt Spanish sidecar, and coverage
manifest together. The normal reviewed mutation workflow publishes this bundle;
there is no separate after-publish localization workflow. Completion may send
one translation-debt notice per release using the shared helper. Runtime secret
and export permissions are managed by the protected ingestion IAM sync, which
the deployment workflow waits for. The sync first bootstraps the deployer's
metadata-read and IAM-policy permissions on the one Slack secret, then installs
the runtime grants. The deployer does not gain access to secret values through
this bootstrap.

Release uploads use no-clobber GCS generation preconditions. `latest/` uploads
replace only the observed generation. If release objects exist without a run
record, the job fails before touching `latest/`.

## Container

Build from the repo root:

```bash
docker build -f ingestion/eamlis_monthly/Dockerfile -t eamlis-monthly .
```
