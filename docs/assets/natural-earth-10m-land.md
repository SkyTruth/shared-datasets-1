---
schema_version: 1
asset_slug: natural-earth-10m-land
title: Natural Earth 10m Land
category: 100-geographic-reference
subcategory: 110-boundaries
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/natural-earth-10m-land.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: Natural Earth 1:10,000,000 physical land polygons v5.1.1
license: Public domain per Natural Earth Terms of Use
citation: Made with Natural Earth. Free vector and raster map data at naturalearthdata.com; land polygons version 5.1.1.
notes: Initial upload from local Natural Earth ne_10m_land shapefile version 5.1.1; release 2026-04-30; source features 11;
  fgb sha256 5e69cd50432794b6411a81d99faa1d1c74e9d778fbfd430e43e1c7adb4d9912a; pmtiles sha256 52793c9fd15c17777a271cb3f984d8a3ffee8acb7c25a8aa04b8809d458901be;
  PMTiles rebuilt 2026-05-04 with zooms 0-8, no pre-tiling simplification, no line simplification, and no tiny-polygon reduction
  at maximum zoom for higher-zoom display fidelity; future rebuilds must use the repo-standard GeoJSONSeq to Tippecanoe MBTiles
  to PMTiles conversion path; canonical FGB preserves source geometry and fields; metadata-contract release 2026-06-05 was
  built from the unchanged 2026-04-30 release FGB and adds generated geometry-digest feature_id values, ext_id mirroring feature_id,
  feature_hash values, canonical metadata/schema/manifest artifacts, and metadata-lookup PMTiles with only feature_id and
  ext_id; fgb sha256 fba2554abe0e0d55eb79095538782b6691200950400ad258f418b84286461032; pmtiles sha256 45bc4178050c3704efe75381477ab2b829c389488e2573685b72c087d6c5e28b;
  metadata sha256 2111661d1b0b43247e8d47771a79b3d49541f1f1c4d40f269fe14ef31dd4f030; schema sha256 05c1c4ab87d2fef7195fe140ece38371acb41340dee2779360b221dadb816a48;
  manifest sha256 1c0bb60bba5c5432274f0c68d54ed5bb374e95518488b5559267444c636f6ce4
row_count: 11
data_profile:
  field_count: 6
  identity_candidates: []
  notes: No documented source feature ID candidate; metadata-contract releases use generated geometry-digest feature IDs and
    ext_id mirrors feature_id.
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  feature_hash_column: feature_hash
  sidecar_file: latest/natural-earth-10m-land.metadata.ndjson.gz
  schema_file: latest/natural-earth-10m-land.schema.json
  manifest_file: latest/natural-earth-10m-land.manifest.json
  provenance_default: true
source_scale_denominator: 10000000
files:
- path: latest/natural-earth-10m-land.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 multipolygon dataset
- path: latest/natural-earth-10m-land.pmtiles
  format: pmtiles
  role: companion
  purpose: Metadata-lookup tiles generated from the same source layer with only feature_id and ext_id properties
- path: latest/natural-earth-10m-land.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/natural-earth-10m-land.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/natural-earth-10m-land.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/YYYY-MM-DD/natural-earth-10m-land.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/natural-earth-10m-land.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: releases/YYYY-MM-DD/natural-earth-10m-land.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Dated canonical feature metadata sidecar keyed by feature_id
- path: releases/YYYY-MM-DD/natural-earth-10m-land.schema.json
  format: json
  role: metadata
  purpose: Dated release feature metadata schema for field projection
- path: releases/YYYY-MM-DD/natural-earth-10m-land.manifest.json
  format: json
  role: metadata
  purpose: Dated release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Manual release run record
---

# Natural Earth 10m Land

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/natural-earth-10m-land.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** Natural Earth 1:10,000,000 physical land polygons v5.1.1
- **License / terms:** Public domain per Natural Earth Terms of Use
- **Citation:** Made with Natural Earth. Free vector and raster map data at naturalearthdata.com; land polygons version 5.1.1.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the Natural Earth 1:10m land polygon layer, including major
islands. Natural Earth describes the layer as derived from the 10m coastline, with
continental polygons split into smaller contiguous pieces to improve processing
performance in some software.

In Natural Earth naming, `10m` means 1:10,000,000 map scale; it is not
10-meter ground resolution. The PMTiles maxzoom 8 display choice follows that
source scale and is expected to look coarse at street or city zooms.

The canonical file preserves the source attributes and promotes polygon geometry
to multipolygon for consistent analytical handling. The PMTiles artifact is for
web-map display only.

## When to use it

- Use this as a lightweight global land polygon reference layer for cartography,
  contextual maps, clipping, and coarse land/water display.
- Use the FlatGeobuf file for analytical workflows.
- Use the PMTiles file for web-map display.
- Do not use this as a legal boundary, shoreline engineering dataset, cadastral
  dataset, or high-precision coastline source.
- Do not use the PMTiles artifact as the analytical source.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/natural-earth-10m-land.fgb` | `fgb` | `canonical` | Canonical WGS84 multipolygon dataset |
| `latest/natural-earth-10m-land.pmtiles` | `pmtiles` | `companion` | Metadata-lookup tiles generated from the same source layer with only feature_id and ext_id properties |
| `latest/natural-earth-10m-land.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/natural-earth-10m-land.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/natural-earth-10m-land.manifest.json` | `json` | `metadata` | Release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/natural-earth-10m-land.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/natural-earth-10m-land.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/natural-earth-10m-land.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Dated canonical feature metadata sidecar keyed by feature_id |
| `releases/YYYY-MM-DD/natural-earth-10m-land.schema.json` | `json` | `metadata` | Dated release feature metadata schema for field projection |
| `releases/YYYY-MM-DD/natural-earth-10m-land.manifest.json` | `json` | `metadata` | Dated release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Manual release run record |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 multipolygon geometry. The source shapefile reports 11 polygon
features with extent `(-180, -90) - (180, 83.634101)`. The published FlatGeobuf
promotes geometries to multipolygon and keeps the source fields plus release
metadata fields.

The reviewed 2026-06-05 metadata-contract release was built from the unchanged
2026-04-30 release FGB. It adds generated per-feature `feature_id` values because
the source has no provider feature ID. `ext_id` is set equal to `feature_id`; no
`shared_datasets_group_id` or `shared_datasets_row_id` is published.
`feature_hash` is computed from normalized geometry plus projected metadata
properties. Natural Earth 10m Land has no schema-projectable display-name field,
so this release does not include localized metadata.

The PMTiles artifact was rebuilt on 2026-05-04 with zooms 0 through 8, no
pre-tiling simplification, no line simplification, and no tiny-polygon reduction
at maximum zoom. Future rebuilds must export WGS84 GeoJSONSeq from the FGB,
build Tippecanoe MBTiles, and convert with `pmtiles convert`. The canonical
FlatGeobuf remains the analytical source and preserves the original source
geometry. The maxzoom comes from the stable `source_scale_denominator: 10000000`
hint, not from a meter-resolution interpretation of the Natural Earth `10m`
label. PMTiles feature properties are intentionally limited to `feature_id` and
`ext_id`.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `ext_id` | string | External lookup ID. For metadata-contract releases this mirrors the generated `feature_id`. |
| `feature_hash` | string | SHA-256 content hash computed from normalized geometry plus projected metadata properties. |
| `feature_id` | string | Generated stable feature ID derived from the feature geometry digest. |
| `featurecla` | string | Natural Earth feature class. Values in this source include `Land`, `Null island`, and one null source value. |
| `scalerank` | integer | Natural Earth display rank for scale-dependent cartography; lower values are generally more prominent. |
| `min_zoom` | real | Natural Earth minimum zoom guidance for display. |

## Update notes

Manually converted from the local Natural Earth source shapefile
`ne_10m_land.shp` on 2026-04-30 using `scripts/vector_asset.py`.

Output summary:

- Source version: 5.1.1
- Published features: 11
- CRS: EPSG:4326
- PMTiles validation used `tippecanoe-decode`; future rebuilds must export WGS84 GeoJSONSeq from the FGB, build Tippecanoe MBTiles, and convert with `pmtiles convert`
- FGB SHA-256: `5e69cd50432794b6411a81d99faa1d1c74e9d778fbfd430e43e1c7adb4d9912a`
- PMTiles SHA-256: `52793c9fd15c17777a271cb3f984d8a3ffee8acb7c25a8aa04b8809d458901be`

The 2026-06-05 metadata-contract release starts from the 2026-04-30 release FGB
generation `1777593092853652` and SHA-256
`5e69cd50432794b6411a81d99faa1d1c74e9d778fbfd430e43e1c7adb4d9912a`. It
generates unique geometry-digest `feature_id` values, sets `ext_id` equal to
`feature_id`, and publishes `feature_hash` values plus canonical metadata,
schema, and manifest artifacts. The rebuilt FGB has 11 features, six
non-geometry fields, and zero invalid geometries. The rebuilt PMTiles archive
keeps maxzoom 8 and decodes to exactly `feature_id` and `ext_id` properties.
Older releases remain readable legacy pre-metadata-contract history and are not
backfilled.

## Known caveats

Natural Earth notes that coastline accuracy is suspect for northern Russia and
southern Chile, and that some rank 5 land should be reclassified as rank 6. The
source also includes a `Null island` feature, which is retained for fidelity to
the source layer.
