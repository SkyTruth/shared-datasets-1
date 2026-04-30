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

If that fingerprint matches the latest successful run record, the job writes a
skipped run record and does not download the source. If the fingerprint changed,
the job downloads the filtered layer with paginated ArcGIS queries, converts it
to FlatGeobuf, validates row count and geometry type, and compares the generated
FGB SHA-256 to the latest successful run. Matching output hashes are also
skipped.

When changed, the job writes:

```text
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/releases/YYYY-MM-DD/eamlis-abandoned-mine-land-inventory.fgb
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/latest/eamlis-abandoned-mine-land-inventory.fgb
300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/runs/YYYY-MM-DD.json
```

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
