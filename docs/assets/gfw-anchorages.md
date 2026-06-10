---
schema_version: 1
asset_slug: gfw-anchorages
title: Global Fishing Watch Anchorages
category: 600-maritime-ocean
subcategory: 640-ocean-activity
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/gfw-anchorages.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: Global Fishing Watch Anchorages Version 2
license: Copyright Global Fishing Watch; non-commercial use only under CC BY-NC 4.0 and subject to Global Fishing Watch Terms
  of Use
citation: Global Fishing Watch (2026). Global Anchorages dataset, Version 2. https://globalfishingwatch.org/datasets-and-code-anchorages/.
notes: Initial upload from named_anchorages_v2_pipe_v3_202601.csv; release 2026-02-02; source rows 166497; published rows
  166496; omitted invalid lon row s2id 8efe7543. The 2026-06-10 release migrates the asset to the release feature identity
  v2 contract with no upstream data change; feature_id is copied from the unique URL-safe s2id source field, geometry_hash
  and properties_hash columns are added, and canonical metadata sidecar, schema, and manifest artifacts are published. PMTiles
  are lightweight metadata-lookup tiles with feature_id only, built at maxzoom 12 with all-point retention verified at zoom
  0 for all 166496 points; full attributes are served from the FGB and metadata sidecar. Releases dated before 2026-06-10
  contain only FGB and full-property PMTiles. Rebuilds must use the repo-standard GeoJSONSeq to Tippecanoe MBTiles to PMTiles
  conversion path.
row_count: 166496
data_profile:
  field_count: 13
  identity_candidates:
  - field: s2id
    distinct_values: 166496
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique, non-blank, and URL-safe; all values are 8-character lowercase hex strings matching the feature_id rules
search_fields:
- label
- sublabel
- iso3
feature_identity:
  strategy: source_field
  source_fields:
  - s2id
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/gfw-anchorages.metadata.ndjson.gz
  schema_file: latest/gfw-anchorages.schema.json
  manifest_file: latest/gfw-anchorages.manifest.json
  provenance_default: true
files:
- path: latest/gfw-anchorages.fgb
  format: fgb
  role: canonical
  purpose: Canonical point dataset in WGS84
- path: latest/gfw-anchorages.pmtiles
  format: pmtiles
  role: companion
  purpose: Metadata-lookup web map tiles with feature_id only
- path: latest/gfw-anchorages.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/gfw-anchorages.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/gfw-anchorages.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/YYYY-MM-DD/gfw-anchorages.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/gfw-anchorages.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: releases/YYYY-MM-DD/gfw-anchorages.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/YYYY-MM-DD/gfw-anchorages.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/YYYY-MM-DD/gfw-anchorages.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Per-release run record
---

# Global Fishing Watch Anchorages

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/gfw-anchorages.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** Global Fishing Watch Anchorages Version 2
- **License / terms:** Copyright Global Fishing Watch; non-commercial use only under CC BY-NC 4.0 and subject to Global Fishing Watch Terms of Use
- **Citation:** Global Fishing Watch (2026). Global Anchorages dataset, Version 2. https://globalfishingwatch.org/datasets-and-code-anchorages/.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the Global Fishing Watch Anchorages dataset, a global point database of anchorage locations where AIS-broadcasting vessels congregate. Global Fishing Watch identifies anchorages using S2 level 14 cells and stationary vessel behavior, then assigns labels and groups nearby anchorages into broader ports where applicable.

The source CSV contains 166,497 rows. This shared asset publishes 166,496 valid point features after omitting one source row with an invalid longitude value.

## When to use it

- Use this as a reusable global reference layer for anchorage locations and broader port labels.
- Use `label` for the broader port grouping and `sublabel` for more detailed anchorage labels where available.
- Do not use this as a legal port boundary dataset, berth-level authority, port visit event table, or voyage table.
- Do not use this for commercial purposes unless allowed by Global Fishing Watch terms.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/gfw-anchorages.fgb` | `fgb` | `canonical` | Canonical point dataset in WGS84 |
| `latest/gfw-anchorages.pmtiles` | `pmtiles` | `companion` | Metadata-lookup web map tiles with feature_id only |
| `latest/gfw-anchorages.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/gfw-anchorages.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/gfw-anchorages.manifest.json` | `json` | `metadata` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/gfw-anchorages.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/gfw-anchorages.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/gfw-anchorages.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/YYYY-MM-DD/gfw-anchorages.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/YYYY-MM-DD/gfw-anchorages.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Per-release run record |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is generated from the source `lon` and `lat` fields as WGS84 point geometry. Source columns are preserved as attributes. `distance_from_shore_m` and `drift_radius` are numeric where provided; `dock` is preserved as the source `true`, `false`, or empty string value.

One source row is omitted from the geospatial outputs because its longitude is outside valid EPSG:4326 bounds: `s2id=8efe7543`, `lat=11.84060637`, `lon=1001`, `label=POINTE NOIRE`.

Since the 2026-06-10 release the asset follows the release feature identity v2 contract. The canonical FGB carries three identity columns in addition to the source columns: `feature_id` (copied from the unique URL-safe `s2id`), `geometry_hash`, and `properties_hash`. Full attributes per `feature_id` are also published in the canonical metadata sidecar, with field projection described by the release schema file. The FGB geometry type is MultiPoint (single-point members) from the repo-standard `PROMOTE_TO_MULTI` build; coordinates are unchanged.

The PMTiles artifact is a lightweight metadata-lookup tileset: geometry plus `feature_id` only, zooms 0 through 12, with zoom 0 retention verified against the published point count. Display labels and other attributes must be resolved from the metadata sidecar or the FGB, not from tile properties. Rebuilds must export WGS84 GeoJSONSeq from the FGB, build Tippecanoe MBTiles, and convert with `pmtiles convert`. The canonical FGB remains the analytical source.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `s2id` | string | Unique anchorage identifier from the source S2 cell. |
| `lat` | real | Source latitude in decimal degrees. |
| `lon` | real | Source longitude in decimal degrees. |
| `label` | string | Broader port label. |
| `sublabel` | string | Detailed anchorage label where available. |
| `label_source` | string | Source of the anchorage label, such as AIS top destination, manual anchorage overrides, World Port Index, GeoNames, or regional datasets. |
| `iso3` | string | ISO3 code of the EEZ containing the anchorage. |
| `distance_from_shore_m` | real | Source-provided distance from shore in meters where available. |
| `drift_radius` | real | Source-provided average drift radius where available. |
| `dock` | string | Source-provided dock flag: `true`, `false`, or empty. |
| `feature_id` | string | Stable unique release feature identifier, copied from `s2id`. |
| `geometry_hash` | string | Content fingerprint over the canonical geometry (`sha256:` prefixed). |
| `properties_hash` | string | Content fingerprint over the published non-geometry source properties (`sha256:` prefixed). |

## Update notes

Manually converted from `named_anchorages_v2_pipe_v3_202601.csv` on 2026-04-30 using GDAL and PMTiles tooling. Source release date is tracked as 2026-02-02 based on the Global Fishing Watch Data Download Portal last update date.

The PMTiles artifact was rebuilt on 2026-05-04 from the canonical FGB using auto maxzoom selection. The point-only FGB profile resolves to maxzoom 12. The rebuilt zoom 0 tile decodes to all 166,496 published points.

The 2026-06-10 release migrated the asset to the release feature identity v2 contract with no upstream data change: it was rebuilt from the canonical 2026-02-02 FGB with `feature_id` copied from `s2id`, `geometry_hash` and `properties_hash` added, and canonical metadata sidecar, schema, and manifest artifacts published. The PMTiles companion changed from full-property tiles to feature_id-only metadata-lookup tiles (all 166,496 points retained at zoom 0; verified with `pmtiles verify` and a decoded zoom 0 tile). Consumers that previously read `label` or other attributes from tile properties must switch to the metadata sidecar. No locale metadata translations are autogenerated for this asset; `label` and `sublabel` are proper-noun port and anchorage names. Build tools: GDAL 3.6.2, Tippecanoe v2.79.0, go-pmtiles.

Output summary for the 2026-06-10 release:

- Published point features: 166,496 (unchanged from 2026-02-02)
- FGB SHA-256: `9e80a45803485bd37556e551bae421af1a10351a782a82a59d2edc8daf306c0d`
- PMTiles SHA-256: `21ad09260aa881db7adc4b8a54130137b487762535a04c8f9ef78933cef582b5`
- Metadata sidecar SHA-256: `49bf6f4fc8cd97d0cb0fdfaea4f6fd77c8843bec1a4fac1cc6e5fc4bcbf6c502`
- Schema SHA-256: `c258cbbfcac700397d7e969f0b662dcc4c06d1d3f81c07352f0f4c2eb57241ab`

Original 2026-02-02 release summary:

- Source rows: 166,497
- Published point features: 166,496
- Omitted invalid coordinate rows: 1
- FGB SHA-256: `9698918d2fea828ae8bbe00feab3c76364b26e6153d73c357880087957b09351`
- PMTiles SHA-256: `54d8a622cf6f426aa78dc9cbffae89a57212f5c428e6fd2218e414718f8e8cdd`

## Known caveats

Global Fishing Watch updates the source dataset periodically with new anchorage locations, updated names, and AIS data pipeline changes. This asset is a manual shared-datasets snapshot, not a scheduled ingestion job.

The dataset represents anchorage locations inferred or curated by Global Fishing Watch. It should not be treated as authoritative legal infrastructure boundaries or complete port operations metadata.
