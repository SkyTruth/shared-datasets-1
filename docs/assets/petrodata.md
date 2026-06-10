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
  the 2026-06-05 metadata-contract release (PR 48) merged but its promotion failed the schema preflight and published nothing,
  so it was superseded without backfill; release 2026-06-10 introduces the feature identity v2 metadata contract with generated
  decimal feature_id values, geometry_hash values, properties_hash values, canonical metadata/schema/manifest artifacts, a
  machine-translated Spanish NAME metadata sidecar, and metadata-lookup PMTiles with only feature_id; fgb sha256 6e248d38aa190c9098b4328d6346f0486043dd9f4e6cf71f973a756921a18daf;
  pmtiles sha256 453e5b8b589f64961f94437795c9c35011018467dd80a04325c418c2ea593b91; metadata sha256 89a6f09bb09439a53b429e8fe31e7226bc91ce208d48402807c9890c84462c0e;
  metadata.es sha256 b5ed4daa56d1b9be879e123d1c31dc7fc6380ac907853f38e31317c07cdd3d74; metadata-translations sha256 a7867a02aea52cea05b757dc4dfa74353ba0cd26b0f2585e2816d649fe9ba674;
  schema sha256 8405208e3be1c45b8a220976c8e81d799b9b15c6d46a2def965eeac5f1015414; manifest sha256 ba7c51a2a8cf01c8bf75eff69a15d6625be8a0953e17a257a5f779f7e38d7da5;
  PMTiles maxzoom 9 resolved by auto maxzoom from FGB sampled geometry detail, matching the previously published PETRODATA
  maxzoom
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
    notes: Generated decimal per-feature ID approved for the 2026-06-10 feature identity v2 release after source field IDs
      were ruled out.
search_fields:
- NAME
- COUNTRY
- source_layer
- RESINFO
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

The reviewed 2026-06-10 feature identity v2 release keeps all 1,273 features and adds generated per-feature `feature_id` values because neither `PRIMKEY` nor `source_layer + PRIMKEY` is unique. Its published `feature_id` values are generated monotonic decimal sequence handles (`1` through `1273`); no legacy `ext_id` or `feature_hash` columns are published. `geometry_hash` is a SHA-256 hash of the canonical feature geometry and `properties_hash` is a SHA-256 hash of the projected non-geometry properties; the pair is the generated-ID assignment key and is unique across all 1,273 features. The canonical FGB and metadata sidecar preserve full source attributes.

The PMTiles artifact is generated from the same combined polygons, with zooms 0 through 9. Auto maxzoom selection resolved maxzoom 9 from sampled FGB geometry detail, matching the previously published PETRODATA tiles. PMTiles feature properties are intentionally limited to `feature_id`.

The Spanish metadata sidecar is materialized from `petrodata.metadata-translations.csv` for locale `es` and field `NAME`. Translation rows were machine-translated for the staged 2026-06-05 release and re-keyed to the 2026-06-10 `feature_id` values by source-value hash; they carry `review_state = machine_translated` and have not been human reviewed.

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

A 2026-06-05 metadata-contract release was prepared and merged through PR 48,
but its `Approved dataset mutation` promotion failed the schema-compatibility
preflight (five integer fields were narrowed from `Integer64` to `Integer`
without a waiver) and published nothing. That staged release also implemented
the superseded feature identity v1 contract (`gen:`-prefixed `feature_id`
handles, `ext_id`, `feature_hash`) and was not retried.

The 2026-06-10 release introduces the release feature identity v2 metadata
contract. It is built from `releases/2026-05-06/petrodata.fgb` generation
`1778081462517258` (byte-identical to `latest/petrodata.fgb` at build time),
preserves all source field types including the five `Integer64` widths,
generates unique decimal `feature_id` values `1` through `1273` after
confirming `PRIMKEY` and `source_layer + PRIMKEY` are non-unique, and
publishes `geometry_hash` and `properties_hash` values plus canonical
metadata, Spanish metadata, translation-source, schema, and manifest
artifacts at sidecar/schema/manifest `schema_version` 2. No prior canonical
`feature_id` contract was ever published, so no identity mapping carryover
applies. The PMTiles archive resolves to maxzoom 9 via auto selection and
decodes to exactly `feature_id` properties. Older releases remain readable
pre-metadata-contract history and are not backfilled.

## Known caveats

This dataset is historical and covers 1946-2003. It is a generalized geological/resource dataset and should not be treated as current infrastructure, facility, lease, or operational status data. Offshore country assignment can be uncertain because the source notes that offshore boundaries are often fuzzy.
