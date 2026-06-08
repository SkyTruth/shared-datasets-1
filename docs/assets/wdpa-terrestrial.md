---
schema_version: 1
asset_slug: wdpa-terrestrial
title: WDPA Terrestrial Protected and Conserved Areas
category: 100-geographic-reference
subcategory: 130-protected-areas
status: active
access_tier: public
owner: SkyTruth
update_cadence: monthly
canonical_format: fgb
canonical_file: latest/wdpa-terrestrial.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
license: See Protected Planet WDPA terms
citation: 'UNEP-WCMC and IUCN (2026). Protected Planet: The World Database on Protected Areas (WDPA) and World Database on
  Other Effective Area-based Conservation Measures (WD-OECM) [Online], June 2026, Cambridge, UK: UNEP-WCMC and IUCN. Available
  at: www.protectedplanet.net.'
notes: Monthly job preserves source fields and publishes FGB plus PMTiles. The 2026-06-05 reviewed metadata-contract release
  adds source-field feature_id values from SITE_PID, geometry_hash values, properties_hash values, canonical metadata/schema/manifest
  artifacts, and an initial Spanish NAME_ENG metadata sidecar generated from the metadata-translations CSV. PMTiles are lightweight
  metadata-lookup tiles with feature_id only. The release preserves 2,661 upstream invalid geometries from the current FGB;
  no geometry repair was applied. Release history, source versions, row counts, and file hashes are recorded in the bucket
  release index and per-run records.
row_count: 304572
data_profile:
  field_count: 33
  identity_candidates:
  - field: SITE_ID
    distinct_values: 303285
    duplicate_value_count: 566
    duplicate_row_count: 1853
    status: non_unique
    notes: Not unique
  - field: SITE_PID
    distinct_values: 304572
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique
feature_identity:
  strategy: source_field
  source_fields:
  - SITE_PID
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/wdpa-terrestrial.metadata.ndjson.gz
  schema_file: latest/wdpa-terrestrial.schema.json
  manifest_file: latest/wdpa-terrestrial.manifest.json
  provenance_default: true
files:
- path: latest/wdpa-terrestrial.fgb
  format: fgb
  role: canonical
  purpose: Canonical mixed-geometry vector dataset
- path: latest/wdpa-terrestrial.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same monthly extract
- path: latest/wdpa-terrestrial.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/wdpa-terrestrial.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Spanish metadata sidecar materialized from NAME_ENG translations
- path: latest/wdpa-terrestrial.metadata-translations.csv
  format: csv
  role: metadata
  purpose: Editable Spanish translation source keyed by feature_id, field, locale, and source-value hash
- path: latest/wdpa-terrestrial.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/wdpa-terrestrial.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/YYYY-MM-DD/wdpa-terrestrial.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/wdpa-terrestrial.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Spanish metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata-translations.csv
  format: csv
  role: release
  purpose: Dated editable Spanish translation source
- path: releases/YYYY-MM-DD/wdpa-terrestrial.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/YYYY-MM-DD/wdpa-terrestrial.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Monthly run record
---

# WDPA Terrestrial Protected and Conserved Areas

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** monthly
- **Canonical file:** `latest/wdpa-terrestrial.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
- **License / terms:** See Protected Planet WDPA terms
- **Citation:** UNEP-WCMC and IUCN (2026). Protected Planet: The World Database on Protected Areas (WDPA) and World Database on Other Effective Area-based Conservation Measures (WD-OECM) [Online], June 2026, Cambridge, UK: UNEP-WCMC and IUCN. Available at: www.protectedplanet.net.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the monthly WDPA/WDOECM source rows selected for the
terrestrial realm. It is a direct format conversion and split from the upstream
source. Fields are preserved from the source dataset.

## When to use it

- Use this for reusable terrestrial protected-area and conserved-area boundaries or point records.
- Do not use this when a marine or coastal extract is required.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/wdpa-terrestrial.fgb` | `fgb` | `canonical` | Canonical mixed-geometry vector dataset |
| `latest/wdpa-terrestrial.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same monthly extract |
| `latest/wdpa-terrestrial.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/wdpa-terrestrial.metadata.es.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Spanish metadata sidecar materialized from NAME_ENG translations |
| `latest/wdpa-terrestrial.metadata-translations.csv` | `csv` | `metadata` | Editable Spanish translation source keyed by feature_id, field, locale, and source-value hash |
| `latest/wdpa-terrestrial.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/wdpa-terrestrial.manifest.json` | `json` | `metadata` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/wdpa-terrestrial.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/wdpa-terrestrial.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata.es.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Spanish metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata-translations.csv` | `csv` | `release` | Dated editable Spanish translation source |
| `releases/YYYY-MM-DD/wdpa-terrestrial.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/YYYY-MM-DD/wdpa-terrestrial.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Monthly run record |
<!-- END GENERATED files-table -->

## Schema notes

The simplified monthly job does not rename, add, or remove source fields. Refer
to the Protected Planet WDPA user manual and source metadata for authoritative
field definitions.

## Properties / columns

Definitions are inherited from the Protected Planet WDPA/WDOECM source and need
source confirmation for each monthly release. The job verifies that all selected
source layers have identical fields before publishing. Metadata-contract
releases add `feature_id`, `geometry_hash`, and `properties_hash` fields.

| Name | Type | Description |
|---|---|---|
| `SITE_ID` | integer | Source numeric site identifier. |
| `SITE_PID` | string | Source persistent site identifier. |
| `SITE_TYPE` | string | WDPA/WDOECM source site type. |
| `NAME_ENG` | string | Protected or conserved area name in English. |
| `NAME` | string | Protected or conserved area name as supplied by the source. |
| `DESIG` | string | Source designation text. |
| `DESIG_ENG` | string | Designation text in English. |
| `DESIG_TYPE` | string | Designation type or authority class. |
| `IUCN_CAT` | string | IUCN protected-area management category. |
| `INT_CRIT` | string | International criteria field, where supplied by the source. |
| `REALM` | string | Source realm value used for the marine/coastal versus terrestrial split. |
| `REP_M_AREA` | real | Source-reported marine area in square kilometers. |
| `REP_AREA` | real | Source-reported total area in square kilometers. |
| `NO_TAKE` | string | Source no-take status. |
| `NO_TK_AREA` | real | Source-reported no-take area in square kilometers. |
| `STATUS` | string | Source status for the protected or conserved area. |
| `STATUS_YR` | integer | Year associated with the source status. |
| `GOV_TYPE` | string | Governance type. |
| `GOVSUBTYPE` | string | Governance subtype. |
| `OWN_TYPE` | string | Ownership type. |
| `OWNSUBTYPE` | string | Ownership subtype. |
| `MANG_AUTH` | string | Management authority. |
| `MANG_PLAN` | string | Management-plan status or reference. |
| `VERIF` | string | Source verification status. |
| `METADATAID` | integer | Source metadata record identifier. |
| `PRNT_ISO3` | string | Parent country or territory ISO3 code. |
| `ISO3` | string | Country or territory ISO3 code associated with the feature. |
| `SUPP_INFO` | string | Supplementary information status or flag. |
| `CONS_OBJ` | string | Conservation-objective field from the source. |
| `INLND_WTRS` | string | Inland-waters classification or flag. |
| `OECM_ASMT` | string | OECM assessment status. |
| `GIS_M_AREA` | real | GIS-calculated marine area in square kilometers; null for point rows where not supplied. |
| `GIS_AREA` | real | GIS-calculated total area in square kilometers; null for point rows where not supplied. |
| `feature_id` | string | Public URL-safe lookup handle. Releases use URL-safe `SITE_PID` values only when every value is unique, nonblank, and alphanumeric; otherwise they use generated decimal sequence handles. |
| `geometry_hash` | string | SHA-256 content hash computed from canonical feature geometry. |
| `properties_hash` | string | SHA-256 content hash computed from projected non-geometry metadata properties. |

## Update notes

Updated by `python -m ingestion.wdpa_monthly.run`, deployed as the
`wdpa-monthly` Cloud Run Job and scheduled for `0 9 1-10 * *` UTC.

A 2026-06-05 metadata-contract release was staged from the unchanged June 2026
latest FGB so consumers can use feature metadata sidecars and Firestore index
loads. The release adds SITE_PID-backed `feature_id` values, `properties_hash`
checksums, a canonical metadata sidecar, release schema, manifest,
metadata-translations CSV, and generated Spanish localized sidecar for
`NAME_ENG`. The PMTiles release is a lightweight lookup archive with only
`feature_id` properties.

## Known caveats

The canonical FGB intentionally keeps mixed point and polygon geometries. Some
desktop GIS and map clients handle mixed-geometry layers less gracefully than
single-geometry layers.

The 2026-06-05 metadata-contract release preserves 2,661 invalid geometries
already present in the June 2026 source FGB. Geometry repair was intentionally
out of scope for this contract repair because it would change canonical feature
geometry, geometry hashes, and properties hashes.
