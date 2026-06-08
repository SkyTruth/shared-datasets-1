---
schema_version: 1
asset_slug: petrodata
title: PETRODATA Petroleum Fields
category: 300-infrastructure-industrial
subcategory: 310-energy
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/petrodata.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: PRIO PETRODATA v1.2
license: No explicit license found on the PRIO dataset page; cite Lujala, Rod, and Thieme 2007 and follow source terms
citation: 'Lujala, Paivi, Jan Ketil Rod, and Nadja Thieme (2007). Fighting over Oil: Introducing a New Dataset. Conflict Management
  and Peace Science 24(3): 239-256. https://doi.org/10.1080/07388940701468526.'
notes: Combined local onshore and offshore shapefiles into one FGB plus PMTiles with source_layer; release 2026-04-29; fgb
  sha256 d77f5e4bdb9d231a9058e70c03648092a613c5009889d5f57e0ae05969950296; pmtiles sha256 798ea67f06e20c7912b441cf0a6b3eb5ceee9063d9f164c9e57daacd737741a7;
  corrective release 2026-05-06 repaired geometry with GDAL -makevalid while preserving duplicate source PRIMKEY rows; fgb
  sha256 b2a19482a325dc7eae3f91f1ededcf586531dcee04272239c4ca210d23fa358b; pmtiles sha256 e3fb9bf85f023e316438ba1ef3a8ca78182b541d37b23eeede69346272e1af22;
  corrective release 2026-06-05 adds generated feature_id values, geometry_hash values, properties_hash values, canonical
  metadata/schema/manifest artifacts, a machine-translated Spanish NAME metadata sidecar, and metadata-lookup PMTiles with
  only feature_id; fgb sha256 e2ac998e9b82a017667d24cfc8096ac29e658d07605863fe764106e214b016bd; pmtiles sha256 b25cd901930fedb1fe8a5ac2ac517ddf80f89739eeccad504db57f4568f54ffe;
  metadata sha256 703d29b548a1941d6a63dde4754be3140909f430df12295ed2852af424b9a42d; metadata.es sha256 34bd03d5d67a4670835219fa1a9b1d31c635e84da2b0ef34d90db7f06e1736e5;
  metadata-translations sha256 762d0eb6d2063690f7091ab8bb28a5283965e2ee4240a1b20b1260ec90615265; schema sha256 18b8182a5ffae1758647cb874933a7c0788f0e6cb7c7e4b0ebb09c24679b2739;
  manifest sha256 0e4b58d68693e17e612ddabb352e127aa9b755693dd61e553da65e06d56189b6; PMTiles maxzoom 9 preserved from the existing
  published PETRODATA PMTiles because this remediation changes metadata lookup fields rather than source scale
geometry_type: MultiPolygon
row_count: 1273
data_profile:
  field_count: 24
  identity_candidates:
  - field: PRIMKEY
    distinct_values: 1270
    duplicate_value_count: 3
    duplicate_row_count: 6
    status: non_unique
    notes: 'Duplicate source rows preserved: AL001PET, TU006PET, TU009PET'
  - field: source_layer + PRIMKEY
    distinct_values: 1270
    duplicate_value_count: 3
    duplicate_row_count: 6
    status: non_unique
    notes: Composite still duplicates the three preserved source key pairs, so it is not used as a source feature ID.
  - field: feature_id
    distinct_values: 1273
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Generated per-feature ID approved for the 2026-06-05 metadata-contract release after source field IDs were ruled
      out.
feature_identity:
  strategy: generated_sequence_content_hash
  source_fields: []
  generated_id_type: monotonic_integer_string
  assignment_key:
  - geometry_hash
  - properties_hash
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/petrodata.metadata.ndjson.gz
  schema_file: latest/petrodata.schema.json
  manifest_file: latest/petrodata.manifest.json
  provenance_default: true
files:
- path: latest/petrodata.fgb
  format: fgb
  role: canonical
  purpose: Canonical combined vector dataset
- path: latest/petrodata.pmtiles
  format: pmtiles
  role: companion
  purpose: Metadata-lookup tiles generated from the same combined polygons with only feature_id properties
- path: latest/petrodata.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/petrodata.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Spanish metadata sidecar materialized from NAME translations
- path: latest/petrodata.metadata-translations.csv
  format: csv
  role: metadata
  purpose: Editable Spanish translation source keyed by feature_id, field, locale, and source-value hash
- path: latest/petrodata.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/petrodata.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/YYYY-MM-DD/petrodata.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/petrodata.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: releases/YYYY-MM-DD/petrodata.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/YYYY-MM-DD/petrodata.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Spanish metadata sidecar
- path: releases/YYYY-MM-DD/petrodata.metadata-translations.csv
  format: csv
  role: release
  purpose: Dated editable Spanish translation source
- path: releases/YYYY-MM-DD/petrodata.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/YYYY-MM-DD/petrodata.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
---

# PETRODATA Petroleum Fields

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/petrodata.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** PRIO PETRODATA v1.2
- **License / terms:** No explicit license found on the PRIO dataset page; cite Lujala, Rod, and Thieme 2007 and follow source terms
- **Citation:** Lujala, Paivi, Jan Ketil Rod, and Nadja Thieme (2007). Fighting over Oil: Introducing a New Dataset. Conflict Management and Peace Science 24(3): 239-256. https://doi.org/10.1080/07388940701468526.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains PRIO PETRODATA v1.2, a global dataset of oil and gas field/deposit polygons. PETRODATA was compiled for research on relationships between hydrocarbon resources and armed civil conflict, and covers 1946-2003.

The source dataset represents generalized petroleum field locations as polygons, with centroid latitude/longitude attributes and descriptive fields for reserve type, discovery year, production year, and source information. This published asset combines the source onshore and offshore shapefiles into one canonical layer and adds `source_layer` to distinguish the original file.

## When to use it

- Use this for reusable global oil and gas field or deposit polygons.
- Use `source_layer = 'onshore'` for terrestrial records and `source_layer = 'offshore'` for marine records.
- Do not use this as a current operating-infrastructure inventory or as precise lease/facility boundaries.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/petrodata.fgb` | `fgb` | `canonical` | Canonical combined vector dataset |
| `latest/petrodata.pmtiles` | `pmtiles` | `companion` | Metadata-lookup tiles generated from the same combined polygons with only feature_id properties |
| `latest/petrodata.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/petrodata.metadata.es.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Spanish metadata sidecar materialized from NAME translations |
| `latest/petrodata.metadata-translations.csv` | `csv` | `metadata` | Editable Spanish translation source keyed by feature_id, field, locale, and source-value hash |
| `latest/petrodata.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/petrodata.manifest.json` | `json` | `metadata` | Release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/petrodata.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/petrodata.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/petrodata.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/YYYY-MM-DD/petrodata.metadata.es.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Spanish metadata sidecar |
| `releases/YYYY-MM-DD/petrodata.metadata-translations.csv` | `csv` | `release` | Dated editable Spanish translation source |
| `releases/YYYY-MM-DD/petrodata.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/YYYY-MM-DD/petrodata.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
<!-- END GENERATED files-table -->

## Schema notes

This is a format conversion from `Petrodata_Onshore_V1.2.shp` and `Petrodata_offshore_V1.2.shp` to FlatGeobuf and PMTiles. Source field names and values are preserved, and `source_layer` was added with values `onshore` and `offshore`. Geometry was promoted to multipolygon during conversion for consistent output typing.

The local v1.2 files contain 1,273 source features: 891 onshore and 382 offshore. The canonical 2026-05-06 release keeps all 1,273 source features, repairs one invalid geometry, and preserves the three duplicate `PRIMKEY` pairs present in the source. The PRIO codebook describes PETRODATA's variables and notes that polygons may represent one or several fields, with polygon size determined by source point distribution rather than the number of fields inside.

The reviewed 2026-06-05 metadata-contract release keeps all 1,273 features and adds generated per-feature `feature_id` values because neither `PRIMKEY` nor `source_layer + PRIMKEY` is unique. Its published `feature_id` values are generated decimal sequence handles; no `feature_id` or `feature_id` is published. `properties_hash` is computed from normalized geometry plus projected metadata properties. The canonical FGB and metadata sidecar preserve full source attributes.

The PMTiles artifact is generated from the same combined polygons, with zooms 0 through 9. The 2026-06-05 PMTiles archive preserves maxzoom 9 from the existing published PETRODATA tiles because this corrective release changes metadata lookup fields rather than source scale. PMTiles feature properties are intentionally limited to `feature_id`.

The Spanish metadata sidecar is materialized from `petrodata.metadata-translations.csv` for locale `es` and field `NAME`. Translation rows are machine-translated and carry `review_state = machine_translated`; they have not been human reviewed.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `source_layer` | string | Source split: `onshore` or `offshore`. Added during conversion. |
| `PRIMKEY` | string | Source polygon identifier. Onshore keys combine FIPS country code, site number, and `PET`; offshore keys combine `OF`, a running number, and `PET`. Three duplicate onshore key pairs are preserved from the source. |
| `COUNTRY` | string | Country assigned to the polygon. |
| `FIPSCODE` | string | FIPS country code. |
| `COWCODE` | integer | Correlates of War country code; `-9999` where no code exists. |
| `CONTCODE` | integer | PRIO/Uppsala-style continent code. |
| `SITENUM` | integer | Site number assigned within country. |
| `NAME` | string | Region, basin, or location name. |
| `LAT` | real | Latitude of the polygon centroid in decimal degrees. |
| `LONG` | real | Longitude of the polygon centroid in decimal degrees. |
| `RES` | string | Resource code. PETRODATA uses `PET` for petroleum. |
| `RESINFO` | string | Hydrocarbon type: `oil`, `gas`, `oil and gas`, or missing marker. |
| `LOCSOURCE` | string | Reference for location information. |
| `FIELDINFO` | integer | Production status code: `1` known production, `2` confirmed discovery with no known production, `3` unknown production status, `4` under exploration/no formal discovery. |
| `DISC` | integer | First discovery year in the polygon; `1945` for pre-1946 discoveries and `-9999` for missing values. |
| `DISCPRES` | integer | Discovery-year precision code. |
| `PROD` | integer | First production year in the polygon; `1945` for pre-1946 production and `-9999` for missing values. |
| `PRODPRES` | integer | Production-year precision code. |
| `OTHERINFO` | string | Additional polygon notes. |
| `SOURCEINFO` | string | References for descriptive variables. |
| `VERSION` | real | Source dataset version. |
| `feature_id` | string | Public URL-safe lookup handle used by PMTiles lookup tiles and metadata sidecars. |
| `geometry_hash` | string | SHA-256 content hash computed from canonical feature geometry. |
| `properties_hash` | string | SHA-256 content hash computed from projected non-geometry metadata properties. |

## Update notes

Manually converted from `/Users/jonathanraphael/Desktop/Petrodata v12 Data (1)` on 2026-04-29 using GDAL and PMTiles tooling.

The PMTiles artifact was rebuilt on 2026-05-04 from the canonical FGB using auto maxzoom selection. The sampled FGB profile resolved to maxzoom 9 from representative segment lengths and feature dimensions. The rebuilt PMTiles SHA-256 is `798ea67f06e20c7912b441cf0a6b3eb5ceee9063d9f164c9e57daacd737741a7`.

The corrective 2026-05-06 release repairs one invalid Bangladesh polygon with GDAL `-makevalid` while preserving all source rows, including the duplicate `AL001PET`, `TU006PET`, and `TU009PET` pairs. The rebuilt FGB has 1,273 features, 1,270 distinct `PRIMKEY` values, and zero invalid geometries. The rebuilt FGB SHA-256 is `b2a19482a325dc7eae3f91f1ededcf586531dcee04272239c4ca210d23fa358b`; the rebuilt PMTiles SHA-256 is `e3fb9bf85f023e316438ba1ef3a8ca78182b541d37b23eeede69346272e1af22`.

The corrective 2026-06-05 release adds the release-oriented metadata contract.
It starts from `latest/petrodata.fgb` generation `1778081494369379`, generates
unique `feature_id` values after confirming `PRIMKEY` and
`source_layer + PRIMKEY` are non-unique, and publishes `geometry_hash` and
`properties_hash` values plus canonical metadata, Spanish metadata, schema, and
manifest artifacts. The PMTiles archive keeps maxzoom 9 and decodes to exactly
`feature_id` properties. Older releases remain readable
pre-metadata-contract history and are not backfilled.

## Known caveats

This dataset is historical and covers 1946-2003. It is a generalized geological/resource dataset and should not be treated as current infrastructure, facility, lease, or operational status data. Offshore country assignment can be uncertain because the source notes that offshore boundaries are often fuzzy.
