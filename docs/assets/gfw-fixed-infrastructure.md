---
schema_version: 1
asset_slug: gfw-fixed-infrastructure
title: Global Fishing Watch SAR Fixed Infrastructure
category: 300-infrastructure-industrial
subcategory: 330-offshore-platforms
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/gfw-fixed-infrastructure.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: Global Fishing Watch Datasets API public-fixed-infrastructure-filtered:latest
license: Global Fishing Watch API non-commercial use only and subject to Global Fishing Watch Terms of Use
citation: 'Global Fishing Watch (2026). Fixed infrastructure detections from Sentinel-1 and Sentinel-2, public-fixed-infrastructure-filtered:latest;
  derived from Paolo, F.S. et al. (2024). Satellite mapping reveals extensive industrial activity at sea. Nature 625: 85-91.
  https://doi.org/10.1038/s41586-023-06825-8.'
notes: Initial upload from gfw_infra_2026-04-30; release 2026-04-30; source rows 57681; fgb sha256 159af982d72f464091c06e68de6abe054a5c07ae05ff4731c8cb041979fb3447;
  source csv sha256 07d8d7464c7c2d7410926d2a29c24eb2d2aa2993c2b576a138ce0c57111cf1a9. The 2026-06-10 release migrates the
  asset to the release feature identity v2 contract with no upstream data change, rebuilt from the unchanged 2026-04-30 release
  FGB; feature_id is copied from the unique URL-safe structure_id source field, geometry_hash and properties_hash columns
  are added, geometry is promoted from point to multipoint by the repo-standard vector build, and canonical metadata sidecar,
  schema, and manifest artifacts are published at schema_version 2. PMTiles are lightweight metadata-lookup tiles with feature_id
  only, built at maxzoom 12 with all-point retention verified at zoom 0 for all 57681 points; full attributes are served from
  the FGB and metadata sidecar. Releases dated before 2026-06-10 contain only FGB and full-property PMTiles. Rebuilds must
  use the repo-standard GeoJSONSeq to Tippecanoe MBTiles to PMTiles conversion path. 2026-06-10 hashes are fgb sha256 b6bf5f12650f77ab50724f8c09a145642ec29400a6891323fa105f4b8edfe2a4;
  pmtiles sha256 f877b80f3a1bb835c7749a4563162a15f9b4f2dffcfcdbb760ab8601f4aab1db; metadata sha256 2510a2524e855d86562d0ca821c358871fdf6dd437196294df5b73d38078df8b;
  schema sha256 1fb57139a5ce0ac3fbc35d9c9f13bbea9691827ec1d3a2468d1c398d2b77a1e7; manifest sha256 b2beab3e0ff85f8a7a79af40399b7c9b0cedc0c1d88607c11508d75d854ed1bc
  after same-release manifest-generation repair
row_count: 57681
data_profile:
  field_count: 10
  identity_candidates:
  - field: structure_id
    distinct_values: 57681
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique, non-blank, and URL-safe; all values are decimal integer strings matching the feature_id rules
search_fields:
- label
- label_confidence
feature_identity:
  strategy: source_field
  source_fields:
  - structure_id
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/gfw-fixed-infrastructure.metadata.ndjson.gz
  schema_file: latest/gfw-fixed-infrastructure.schema.json
  manifest_file: latest/gfw-fixed-infrastructure.manifest.json
  provenance_default: true
files:
- path: latest/gfw-fixed-infrastructure.fgb
  format: fgb
  role: canonical
  purpose: Canonical point dataset in WGS84
- path: latest/gfw-fixed-infrastructure.pmtiles
  format: pmtiles
  role: companion
  purpose: Metadata-lookup web map tiles with feature_id only
- path: latest/gfw-fixed-infrastructure.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/gfw-fixed-infrastructure.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/gfw-fixed-infrastructure.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/YYYY-MM-DD/gfw-fixed-infrastructure.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/gfw-fixed-infrastructure.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: releases/YYYY-MM-DD/gfw-fixed-infrastructure.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/YYYY-MM-DD/gfw-fixed-infrastructure.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/YYYY-MM-DD/gfw-fixed-infrastructure.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Per-release run record
- path: sources/gfw_infra_2026-04-30.csv
  format: csv
  role: source
  purpose: Original local CSV export; noncanonical because it stores point geometry as `lon` and `lat` columns
---

# Global Fishing Watch SAR Fixed Infrastructure

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/gfw-fixed-infrastructure.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** Global Fishing Watch Datasets API public-fixed-infrastructure-filtered:latest
- **License / terms:** Global Fishing Watch API non-commercial use only and subject to Global Fishing Watch Terms of Use
- **Citation:** Global Fishing Watch (2026). Fixed infrastructure detections from Sentinel-1 and Sentinel-2, public-fixed-infrastructure-filtered:latest; derived from Paolo, F.S. et al. (2024). Satellite mapping reveals extensive industrial activity at sea. Nature 625: 85-91. https://doi.org/10.1038/s41586-023-06825-8.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains a 2026-04-30 snapshot of Global Fishing Watch SAR fixed offshore infrastructure points from the filtered Datasets API layer. The source documentation describes the layer as offshore infrastructure detected from satellite imagery and classified with deep learning, with labels for oil, wind, and unknown structures.

The filtered API layer matches the Global Fishing Watch public map rather than the noisier Data Download Portal export. According to the source documentation, it excludes noise-labeled detections, relabels Lake Maracaibo as oil, keeps structures detected for at least three months with predicted noise probability below 0.3, and removes additional noisy detections from selected Chile, Canada, and Norway regions.

## When to use it

- Use this as a reusable global point layer for offshore fixed infrastructure locations.
- Use `label` and `label_confidence` to distinguish likely oil, wind, and unknown structure classes.
- Use the FlatGeobuf file for analysis; full attributes also ship in the metadata sidecar keyed by `feature_id`.
- Use the PMTiles file for web-map display and `feature_id` lookup only; since the 2026-06-10 release the tiles carry no other properties. Resolve `label` and other attributes through the metadata sidecar or the FGB.
- Do not use the PMTiles artifact as the analytical source.
- Do not use this for commercial purposes unless permitted by Global Fishing Watch terms.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/gfw-fixed-infrastructure.fgb` | `fgb` | `canonical` | Canonical point dataset in WGS84 |
| `latest/gfw-fixed-infrastructure.pmtiles` | `pmtiles` | `companion` | Metadata-lookup web map tiles with feature_id only |
| `latest/gfw-fixed-infrastructure.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/gfw-fixed-infrastructure.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/gfw-fixed-infrastructure.manifest.json` | `json` | `metadata` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/gfw-fixed-infrastructure.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/gfw-fixed-infrastructure.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/gfw-fixed-infrastructure.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/YYYY-MM-DD/gfw-fixed-infrastructure.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/YYYY-MM-DD/gfw-fixed-infrastructure.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Per-release run record |
| `sources/gfw_infra_2026-04-30.csv` | `csv` | `source` | Original local CSV export; noncanonical because it stores point geometry as `lon` and `lat` columns |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is generated from the source `lon` and `lat` fields as WGS84 point geometry. Since the 2026-06-10 release the repo-standard vector build promotes geometries to multipoint for consistent analytical handling; every feature still holds exactly one point. Source fields are preserved as attributes in the FlatGeobuf output. The original CSV includes an unnamed leading index column, which is omitted from the geospatial outputs.

`structure_start_date` and `structure_end_date` are source-provided epoch timestamps in milliseconds. Empty source `structure_end_date` values are preserved as null values in the geospatial outputs.

The 2026-06-10 release migrates the asset to release feature identity v2. `feature_id` is copied directly from the unique, non-blank, URL-safe `structure_id` source field, so feature identity is stable across future snapshots that keep `structure_id`. `geometry_hash` and `properties_hash` are SHA-256 content hashes published for duplicate detection and refresh-time identity checks. The canonical metadata sidecar repeats every feature's attributes keyed by `feature_id`, the schema file lists projectable fields, and the manifest ties source inputs, artifact checksums, identity policy, and validation together.

The PMTiles artifact is a lightweight metadata-lookup layer with zooms 0 through 12 and zoom 0 retention verified against the published point count. Its features carry only `feature_id`; display labels and all other attributes must be resolved from the metadata sidecar or the FGB. Releases dated before 2026-06-10 contain full-property PMTiles instead. Future rebuilds must export WGS84 GeoJSONSeq from the FGB, build Tippecanoe MBTiles, and convert with `pmtiles convert`. The canonical FGB remains the analytical source.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `feature_id` | string | Public URL-safe lookup handle; copied directly from the unique `structure_id` source field. |
| `geometry_hash` | string | SHA-256 content hash computed from canonical feature geometry. |
| `properties_hash` | string | SHA-256 content hash computed from published non-geometry source properties. |
| `structure_id` | integer | Unique identifier for all detections of the same structure. |
| `lon` | real | Source longitude in decimal degrees. |
| `lat` | real | Source latitude in decimal degrees. |
| `label` | string | Predicted structure type. Source-documented values are `oil`, `wind`, and `unknown`. |
| `label_confidence` | string | Label confidence. Source-documented values are `high`, `medium`, and `low`. |
| `structure_start_date` | integer64 | First date the structure was detected, as epoch milliseconds. |
| `structure_end_date` | integer64 nullable | Last date the structure was detected, as epoch milliseconds; null where the source export has no end date. |

## Update notes

Manually converted from `/Users/jonathanraphael/Desktop/gfw_infra_2026-04-30` on 2026-04-30 using Python CSV processing, GDAL, and PMTiles tooling. The 2026-04-30 release FGB is sha256 `159af982d72f464091c06e68de6abe054a5c07ae05ff4731c8cb041979fb3447`; the source CSV is sha256 `07d8d7464c7c2d7410926d2a29c24eb2d2aa2993c2b576a138ce0c57111cf1a9`.

The 2026-06-10 release migrates the asset to release feature identity v2 with no upstream data change. It rebuilds from the unchanged 2026-04-30 release FGB generation `1777524695665551`, copies `feature_id` from `structure_id`, adds `geometry_hash` and `properties_hash` columns, promotes geometry from point to multipoint via the repo-standard vector build, and publishes schema_version 2 metadata sidecar, schema, and manifest artifacts. The PMTiles archive was rebuilt as feature_id-only metadata-lookup tiles at auto-selected maxzoom 12, and zoom 0 decodes to all 57,681 published features. Hashes recomputed from the published FGB match the stored identity columns and sidecar for all features.

Output summary for the 2026-06-10 release:

- Published features: 57,681 (unchanged)
- Label counts: oil 31,970; wind 17,393; unknown 8,318
- Label confidence counts: high 46,686; medium 5,404; low 5,591
- Structure start date range: 2017-01-01 to 2025-12-01 UTC
- Structure end date range where present: 2017-01-01 to 2025-11-01 UTC
- Empty source `structure_end_date` rows: 23,442
- FGB SHA-256: `b6bf5f12650f77ab50724f8c09a145642ec29400a6891323fa105f4b8edfe2a4`
- PMTiles SHA-256: `f877b80f3a1bb835c7749a4563162a15f9b4f2dffcfcdbb760ab8601f4aab1db`
- Metadata sidecar SHA-256: `2510a2524e855d86562d0ca821c358871fdf6dd437196294df5b73d38078df8b`
- Schema SHA-256: `1fb57139a5ce0ac3fbc35d9c9f13bbea9691827ec1d3a2468d1c398d2b77a1e7`
- Manifest SHA-256: `b2beab3e0ff85f8a7a79af40399b7c9b0cedc0c1d88607c11508d75d854ed1bc` after same-release manifest-generation repair
- PMTiles zoom 0 decoded features: 57,681 with only the `feature_id` property
- No localized metadata: the asset has no schema-projectable display-name field; `label` and `label_confidence` are enum codes (recorded no-translation decision)

## Known caveats

This is a manual snapshot of the filtered API/map layer, not a scheduled ingestion job. It should be refreshed manually or promoted to a scheduled ingestion job if consumers require current data.

The dataset is a model-derived offshore infrastructure layer. It should not be treated as an authoritative legal or permitting dataset for offshore platforms, wind farms, or industrial installations.

Consumers styling web maps directly from PMTiles properties (for example coloring by `label`) must migrate to the metadata sidecar or FGB attributes: since the 2026-06-10 release the tiles carry only `feature_id`. The full-property tiles remain available under `releases/2026-04-30/`.
