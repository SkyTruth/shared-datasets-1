---
schema_version: 1
asset_slug: global-coral-reefs
title: Global Distribution of Coral Reefs
category: 500-conservation-ecosystems
subcategory: 530-habitat-condition
status: active
access_tier: private
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/global-coral-reefs.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
- latest/global-coral-reefs.metadata.ndjson.gz
- latest/global-coral-reefs.schema.json
- latest/global-coral-reefs.manifest.json
source: UNEP-WCMC, WorldFish Centre, WRI, and TNC Global Distribution of Coral Reefs v4.1
license: UNEP-WCMC General Data License (excluding WDPA); contextual internal use with citation only; no redistribution without
  permission
citation: 'UNEP-WCMC, WorldFish Centre, WRI, TNC (2021). Global distribution of coral reefs, compiled from multiple sources
  including the Millennium Coral Reef Mapping Project. Version 4.1, updated by UNEP-WCMC. Cambridge (UK): UN Environment Programme
  World Conservation Monitoring Centre. https://doi.org/10.34892/t2wk-5t34.'
notes: Converted local polygon and point shapefiles to FGB plus PMTiles with both layers; release 2026-04-29; polygon fgb
  sha256 387d9999983a2cf9916ce3d7d496c45319ed8196eccfe9b3d2e8622b82756869; point fgb sha256 a81fcf8264e397fe08cd2655ad580cf36da6fea6010e830b97947fb091bb8ccf;
  pmtiles sha256 2e33c2bbbf0942d0b692e663815177c47f87a008a5206451e0e293f8af82b7b6; metadata-contract release 2026-06-05 changes
  the active canonical contract from polygon-only to a mixed polygon+point FGB containing 17,504 polygon features and 925
  point features. The release adds generated feature_id values, geometry_hash values, properties_hash values, geometry_role,
  canonical metadata/schema/manifest artifacts, and metadata-lookup PMTiles with only feature_id. No localized metadata sidecar
  is generated for this release. fgb sha256 843a00eb56572d2b6b42ef21a67fe01899b212348c236fda14537f460d895c63; point fgb sha256
  c9c0e12cad445c29e08de4767866332bee09898600736e668aabf682649976c3; pmtiles sha256 def9354c709e14aaae1085f178bba37f753817d43926d1f12d557ddb717b8674,
  corrected 2026-06-06 with Tippecanoe drop-rate 1 after verifying all 925 point features decode at zoom 0; metadata sha256
  479f8defa534da8016e665f488293c5fd9628c148c1f634e501583e165f74f36; schema sha256 0c59dffa6d785fc3f7b6b1fd6a875b5712e135bf134b470e87f51e857e717547;
  corrective 2026-06-06 release uses generated decimal sequence feature_id values 1 through 18429 while preserving geometry_hash
  and properties_hash values. Corrective release artifact hashes before promotion finalization are fgb 474498fdc17bc83afe3c70dfb2475dacec32747f29e6139741b4c3d838e0d713;
  point fgb cfdff78a343cd9cb177ecfac8f1a265850408360b1750fac6cb92137b28e4c2d; pmtiles c6078a4bbb23bdd25ac2bb8283b7a5b1d383f36d4060cf3a54fcf1ffacdc6936;
  metadata 8d90c2f7d4de274d2f24f2e99ec1f4fa8db66edfdb321e3f9e948c289c4332dc; schema 319351fb6b07f5d74b886b097ed74252dd03e0635ef217062840bb0155454331.
  Finalized manifest checksum is recorded in the release run record after promotion.
row_count: 18429
data_profile:
  field_count: 22
  identity_candidates:
  - field: METADATA_I
    distinct_values: 79
    duplicate_value_count: 65
    duplicate_row_count: 17490
    status: non_unique
    notes: Metadata link, not row-unique
  notes: No documented source feature ID candidate; the metadata-contract release uses generated sequence feature_id values.
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/global-coral-reefs.metadata.ndjson.gz
  schema_file: latest/global-coral-reefs.schema.json
  manifest_file: latest/global-coral-reefs.manifest.json
  provenance_default: true
files:
- path: latest/global-coral-reefs.fgb
  format: fgb
  role: canonical
  purpose: Canonical mixed polygon and point FlatGeobuf layer with release metadata fields
- path: latest/global-coral-reefs-points.fgb
  format: fgb
  role: companion
  purpose: Companion point FlatGeobuf subset with matching feature_id, geometry_hash, and properties_hash values
- path: latest/global-coral-reefs.pmtiles
  format: pmtiles
  role: companion
  purpose: Metadata-lookup tiles generated from the mixed canonical FGB with only feature_id properties
- path: latest/global-coral-reefs.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/global-coral-reefs.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/global-coral-reefs.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/2026-06-05/global-coral-reefs.fgb
  format: fgb
  role: release
  purpose: Dated metadata-contract mixed polygon and point release
- path: releases/2026-06-05/global-coral-reefs-points.fgb
  format: fgb
  role: release
  purpose: Dated companion point subset release
- path: releases/2026-06-05/global-coral-reefs.pmtiles
  format: pmtiles
  role: release
  purpose: Dated metadata-lookup map-tile release
- path: releases/2026-06-05/global-coral-reefs.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Dated canonical feature metadata sidecar keyed by feature_id
- path: releases/2026-06-05/global-coral-reefs.schema.json
  format: json
  role: metadata
  purpose: Dated release feature metadata schema for field projection
- path: releases/2026-06-05/global-coral-reefs.manifest.json
  format: json
  role: metadata
  purpose: Dated release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: runs/2026-06-05.json
  format: json
  role: run-record
  purpose: Manual corrective metadata-contract release record
- path: releases/2026-06-06/global-coral-reefs.fgb
  format: fgb
  role: release
  purpose: Dated URL-safe feature_id corrective mixed polygon and point release
- path: releases/2026-06-06/global-coral-reefs-points.fgb
  format: fgb
  role: release
  purpose: Dated companion point subset with generated sequence feature_id values
- path: releases/2026-06-06/global-coral-reefs.pmtiles
  format: pmtiles
  role: release
  purpose: Dated metadata-lookup map-tile release with generated sequence feature_id values
- path: releases/2026-06-06/global-coral-reefs.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Dated canonical feature metadata sidecar keyed by feature_id with URL-safe feature_id values
- path: releases/2026-06-06/global-coral-reefs.schema.json
  format: json
  role: metadata
  purpose: Dated release feature metadata schema for field projection
- path: releases/2026-06-06/global-coral-reefs.manifest.json
  format: json
  role: metadata
  purpose: Dated release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: runs/2026-06-06.json
  format: json
  role: run-record
  purpose: Manual URL-safe feature_id corrective release record
---

# Global Distribution of Coral Reefs

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** private
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/global-coral-reefs.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** UNEP-WCMC, WorldFish Centre, WRI, and TNC Global Distribution of Coral Reefs v4.1
- **License / terms:** UNEP-WCMC General Data License (excluding WDPA); contextual internal use with citation only; no redistribution without permission
- **Citation:** UNEP-WCMC, WorldFish Centre, WRI, TNC (2021). Global distribution of coral reefs, compiled from multiple sources including the Millennium Coral Reef Mapping Project. Version 4.1, updated by UNEP-WCMC. Cambridge (UK): UN Environment Programme World Conservation Monitoring Centre. https://doi.org/10.34892/t2wk-5t34.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the UNEP-WCMC Global Distribution of Coral Reefs dataset, version 4.1. It maps warm-water coral reef distribution in tropical and subtropical regions and was compiled from multiple sources including the Millennium Coral Reef Mapping Project and the World Atlas of Coral Reefs.

The active canonical analytical file is now a mixed polygon and point FlatGeobuf. The source package includes both geometry types, and this release keeps both in the canonical file with a `geometry_role` discriminator. A companion point FGB is retained for consumers that already depend on a point-only file. The PMTiles file is a metadata-lookup artifact with geometry plus `feature_id` only.

## When to use it

- Use this as a contextual map layer for coral reef extent and location.
- Filter `geometry_role = 'polygon'` if a workflow expects the legacy polygon-only canonical geometry contract.
- Cite the source when displaying or using the layer.
- Do not treat this as an externally redistributable dataset unless UNEP-WCMC permission is confirmed.
- Do not use polygon area totals without checking for overlaps and source caveats.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/global-coral-reefs.fgb` | `fgb` | `canonical` | Canonical mixed polygon and point FlatGeobuf layer with release metadata fields |
| `latest/global-coral-reefs-points.fgb` | `fgb` | `companion` | Companion point FlatGeobuf subset with matching feature_id, geometry_hash, and properties_hash values |
| `latest/global-coral-reefs.pmtiles` | `pmtiles` | `companion` | Metadata-lookup tiles generated from the mixed canonical FGB with only feature_id properties |
| `latest/global-coral-reefs.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/global-coral-reefs.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/global-coral-reefs.manifest.json` | `json` | `metadata` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/2026-06-05/global-coral-reefs.fgb` | `fgb` | `release` | Dated metadata-contract mixed polygon and point release |
| `releases/2026-06-05/global-coral-reefs-points.fgb` | `fgb` | `release` | Dated companion point subset release |
| `releases/2026-06-05/global-coral-reefs.pmtiles` | `pmtiles` | `release` | Dated metadata-lookup map-tile release |
| `releases/2026-06-05/global-coral-reefs.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Dated canonical feature metadata sidecar keyed by feature_id |
| `releases/2026-06-05/global-coral-reefs.schema.json` | `json` | `metadata` | Dated release feature metadata schema for field projection |
| `releases/2026-06-05/global-coral-reefs.manifest.json` | `json` | `metadata` | Dated release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `runs/2026-06-05.json` | `json` | `run-record` | Manual corrective metadata-contract release record |
| `releases/2026-06-06/global-coral-reefs.fgb` | `fgb` | `release` | Dated URL-safe feature_id corrective mixed polygon and point release |
| `releases/2026-06-06/global-coral-reefs-points.fgb` | `fgb` | `release` | Dated companion point subset with generated sequence feature_id values |
| `releases/2026-06-06/global-coral-reefs.pmtiles` | `pmtiles` | `release` | Dated metadata-lookup map-tile release with generated sequence feature_id values |
| `releases/2026-06-06/global-coral-reefs.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Dated canonical feature metadata sidecar keyed by feature_id with URL-safe feature_id values |
| `releases/2026-06-06/global-coral-reefs.schema.json` | `json` | `metadata` | Dated release feature metadata schema for field projection |
| `releases/2026-06-06/global-coral-reefs.manifest.json` | `json` | `metadata` | Dated release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `runs/2026-06-06.json` | `json` | `run-record` | Manual URL-safe feature_id corrective release record |
<!-- END GENERATED files-table -->

## Schema notes

This is a format conversion from the local source shapefiles `WCMC008_CoralReef2018_Py_v4_1.shp` and `WCMC008_CoralReef2018_Pt_v4_1.shp` to FlatGeobuf and PMTiles. Source field names and values are preserved in the canonical FGB and metadata sidecar. The active canonical FGB contains both polygon and point features and adds `geometry_role` with values `polygon` and `point`.

The local source package contains 17,504 polygon features and 925 point features. The reviewed 2026-06-05 metadata-contract release publishes all 18,429 features. This is an incompatible geometry-contract change for consumers that assumed `latest/global-coral-reefs.fgb` was polygon-only; those consumers should filter `geometry_role = 'polygon'` or pin an older dated release.

No source feature ID is documented. `METADATA_I` is a low-cardinality metadata
grouping value, not a unique feature identifier. The active metadata-contract
release therefore uses generated decimal sequence `feature_id` handles `1`
through `18429`, each matching `^[A-Za-z0-9]{1,64}$`. `geometry_hash` is
computed from canonical feature geometry, and `properties_hash` is computed from
published non-geometry properties. No localized metadata sidecar is generated
for this release.

The PMTiles artifact is a lightweight metadata-lookup archive. Decoded feature properties are intentionally limited to `feature_id`; full attributes remain in the canonical FGB and metadata sidecar.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `feature_id` | string | Public URL-safe lookup handle. Active `latest/` values are unique generated decimal sequence IDs. |
| `geometry_hash` | string | SHA-256 content hash computed from canonical feature geometry. |
| `properties_hash` | string | SHA-256 content hash computed from published non-geometry properties. |
| `geometry_role` | string | Published geometry discriminator with values `polygon` and `point`. |
| `LAYER_NAME` | string | Source layer or feature grouping code. |
| `METADATA_I` | real | Metadata ID linking records to the source metadata table. |
| `ORIG_NAME` | string | Original feature name as provided by the data provider. |
| `FAMILY` | string | Scientific family name, when available. |
| `GENUS` | string | Scientific genus name, when available. |
| `SPECIES` | string | Scientific species name, when available. |
| `DATA_TYPE` | string | Whether data were obtained by remote sensing, field survey, or another method. |
| `START_DATE` | string | Start date of data collection or survey, when available. |
| `END_DATE` | string | End date of data collection or survey, when available. |
| `DATE_TYPE` | string | Code describing date accuracy for `START_DATE` and `END_DATE`. |
| `VERIF` | string | Government or expert verification status. |
| `NAME` | string | English feature name as provided by the data provider. |
| `LOC_DEF` | string | Local definition of the feature as provided by the data provider. |
| `SURVEY_MET` | string | Data gathering approach. |
| `GIS_AREA_K` | real | GIS-calculated area in square kilometers. |
| `Shape_Leng` | real | Source polygon length field. Polygon layer only. |
| `Shape_Area` | real | Source polygon area field. Polygon layer only. |
| `REP_AREA_K` | string | Reported area in square kilometers, when available. |

## Update notes

Manually converted from `/Users/jonathanraphael/Desktop/14_001_WCMC008_CoralReefs2018_v4_1` on 2026-04-29 using GDAL and PMTiles tooling.

PMTiles were rebuilt on 2026-05-04 at maxzoom 12 from the published polygon and point FGB layers. The rebuild keeps the `global_coral_reefs` and `global_coral_reefs_points` tile layers, preserves source properties, and retains all 925 point features at zoom 0.

The corrective 2026-06-05 metadata-contract release was built from the existing prod polygon and point FGBs to avoid rehydrating the source shapefile package. The mixed canonical FGB contains 17,504 polygon features and 925 point features. The point companion is a subset with matching `feature_id`, `geometry_hash`, and `properties_hash` values. PMTiles were rebuilt from the mixed FGB as a single metadata-lookup layer with only `feature_id` properties.

The corrective 2026-06-06 feature_id release was built from the 2026-06-05 metadata-contract latest artifacts. It preserves `geometry_hash` and `properties_hash` values, uses generated decimal sequence handles, and republishes the mixed FGB, point companion FGB, PMTiles lookup archive, metadata sidecar, schema, manifest, and run record through a reviewed publish plan.

Requested citation:

UNEP-WCMC, WorldFish Centre, WRI, TNC (2021). Global distribution of coral reefs, compiled from multiple sources including the Millennium Coral Reef Mapping Project. Version 4.1, updated by UNEP-WCMC. Cambridge (UK): UN Environment Programme World Conservation Monitoring Centre. Data DOI: https://doi.org/10.34892/t2wk-5t34

## Known Caveats

The source metadata notes that the dataset was compiled from multiple sources with varying scale and quality, has not undergone external review, may include overlapping polygons, and may require dissolve operations before surface-area calculations.

The source license restricts commercial use, sublicensing, and redistribution. This asset is intended for internal contextual display with citation, not third-party data distribution.
