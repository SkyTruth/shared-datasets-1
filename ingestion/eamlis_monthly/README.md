# Monthly e-AMLIS Job

This job publishes the existing `eamlis-abandoned-mine-land-inventory` asset from
the public OSMRE e-AMLIS ArcGIS hosted feature layer:

```text
https://services.arcgis.com/Vsy5ieu7PwNdunLd/arcgis/rest/services/eAMLISExternalView/FeatureServer/0
```

The source filter is `LAT_DEG > 0`, matching the public app's current map layer.

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

The job first reads ArcGIS layer metadata and source statistics, then builds a
source fingerprint from:

- service item ID
- `editingInfo.dataLastEditDate`
- `editingInfo.schemaLastEditDate`
- filtered feature count
- max `DATE_REVISED`
- source field schema hash

If that fingerprint matches the latest successful run record, the job checks the
latest release metadata contract. A valid unchanged release writes a skipped run
record and does not download the source. If the source changed, or if the latest
successful release lacks the current metadata contract, the job downloads the
filtered layer with paginated ArcGIS queries and rebuilds the release.

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

Spanish metadata is maintained as a derived localization layer outside the core
job output. When a repair release changes `feature_id` shape, migrate
`eamlis-abandoned-mine-land-inventory.metadata-translations.csv` keys from
legacy `src:OBJECTID:<value>` to plain `<value>`, then materialize
`eamlis-abandoned-mine-land-inventory.metadata.es.ndjson.gz` from the new
canonical sidecar and schema with `scripts/feature_metadata_localization.py`.
Promote the migrated CSV and generated Spanish sidecar through the reviewed
dataset mutation workflow, or rely on the feature metadata localization
materialization workflow after a reviewed translation-source publish plan.

Release uploads use no-clobber GCS generation preconditions. `latest/` uploads
replace only the observed generation. If release objects exist without a run
record, the job fails before touching `latest/`.

## Container

Build from the repo root:

```bash
docker build -f ingestion/eamlis_monthly/Dockerfile -t eamlis-monthly .
```

## Cost Controls and Teardown

Immediate stop:

```bash
gcloud scheduler jobs pause eamlis-monthly \
  --location=us-central1 \
  --project=shared-datasets-1
```

Pausing the scheduler stops future automatic monthly runs without deleting the
Cloud Run Job, service accounts, IAM, Terraform state, or published GCS data.
