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
  fgb sha256 5e69cd50432794b6411a81d99faa1d1c74e9d778fbfd430e43e1c7adb4d9912a. Release 2026-06-05 was a v1 metadata-contract
  release built from the unchanged 2026-04-30 release FGB with v1 identity columns ext_id and feature_hash and 'gen:<24 hex
  chars>' feature_id handles; it remains readable legacy history. The 2026-06-10 corrective schema-contract revision rebuilds
  the release from the same unchanged 2026-04-30 source geometry under release feature identity v2 with generated monotonic
  decimal feature_id values assigned from geometry_hash+properties_hash, geometry_hash and properties_hash columns replacing
  ext_id and feature_hash, schema_version 2 metadata/schema/manifest artifacts, and metadata-lookup PMTiles rebuilt at maxzoom
  12 (maintainer-requested display fidelity upgrade from maxzoom 8) with only feature_id, via the repo-standard GeoJSONSeq
  to Tippecanoe MBTiles to PMTiles conversion path. Hashes for the 2026-06-10 candidates are fgb eadff2f0cb11f7daff481bd57fb541b8cdc2770b52369bc142ff28bd5582e036;
  pmtiles daaa24c56134df90de6ecf8d67149d7a3628e0c9ebba69faa813800b102f79cd; metadata 91479e53f4ad0acef7844ae7e2b819765bfc54b7a0ebb691d79076b1fcf3a62d;
  schema 2818348d1f84425f328c7c056d989b8234cc45cb531d01fbccf3b619e3b0999e; manifest 70e69ffd7259b8b5c1277c23bb75fc9d08094f6fbe9d54cf83096d01c44e4e7c.
  Release history, source generations, row counts, and hashes are recorded in the bucket release index and per-run records.
row_count: 11
data_profile:
  field_count: 6
  identity_candidates: []
  notes: No documented source feature ID candidate; the metadata-contract release uses generated feature_id sequence handles.
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
  purpose: Metadata-lookup tiles generated from the same source layer with only feature_id properties
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
10-meter ground resolution. The PMTiles maxzoom 12 display choice is a
maintainer-requested fidelity upgrade over the source-scale default of 8;
the underlying 1:10m geometry is still coarse at street or city zooms.

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
| `latest/natural-earth-10m-land.pmtiles` | `pmtiles` | `companion` | Metadata-lookup tiles generated from the same source layer with only feature_id properties |
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

The reviewed 2026-06-10 revision migrates the asset to release feature identity
v2, built from the unchanged 2026-04-30 release FGB. It assigns generated
monotonic decimal `feature_id` values (`1`..`11`) because the source has no
source feature ID; identity keys come from the pair of `geometry_hash` and
`properties_hash` content hashes, which are published as columns. The earlier
2026-06-05 release used the v1 identity contract (`ext_id` and `feature_hash`
columns with `gen:<24 hex chars>` feature IDs) and remains readable legacy
history; its IDs are not carried forward because they do not satisfy the v2
URL-safe `feature_id` rules. Natural Earth 10m Land has no schema-projectable
display-name field, so releases do not include localized metadata. One source
feature carries no attributes at all and publishes an empty properties record.

The PMTiles artifact was rebuilt for the 2026-06-10 revision at zooms 0 through
12 using the repo-standard path: export WGS84 GeoJSONSeq from the FGB, build
Tippecanoe MBTiles, and convert with `pmtiles convert`. The canonical
FlatGeobuf remains the analytical source and preserves the original source
geometry. The maxzoom 12 setting is a recorded maintainer override for display
fidelity; the stable `source_scale_denominator: 10000000` hint would otherwise
select maxzoom 8. PMTiles feature properties are intentionally limited to
`feature_id`.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `feature_id` | string | Public URL-safe lookup handle; generated monotonic decimal string assigned from the geometry/properties hash identity key. |
| `geometry_hash` | string | SHA-256 content hash computed from canonical feature geometry. |
| `properties_hash` | string | SHA-256 content hash computed from published non-geometry properties. |
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

The 2026-06-05 release was a v1 metadata-contract repair built from the
2026-04-30 release FGB generation `1777593092853652`. It published v1 identity
columns (`ext_id`, `feature_hash`) and `gen:<24 hex chars>` feature IDs with
schema_version 1 sidecar/schema/manifest artifacts.

The 2026-06-10 revision migrates the asset to release feature identity v2. It
starts from the same unchanged 2026-04-30 release FGB generation
`1777593092853652` and SHA-256
`5e69cd50432794b6411a81d99faa1d1c74e9d778fbfd430e43e1c7adb4d9912a`, assigns
generated monotonic decimal `feature_id` values from
`geometry_hash`+`properties_hash` identity keys, replaces `ext_id` and
`feature_hash` with `geometry_hash` and `properties_hash` columns (reviewed
schema compatibility waiver), and publishes schema_version 2 metadata, schema,
and manifest artifacts. The rebuilt FGB has 11 features, six non-geometry
fields, and zero invalid geometries. The PMTiles archive was rebuilt at
maxzoom 12 (maintainer-requested upgrade from maxzoom 8) and decodes to exactly
`feature_id` properties. A complete v1-to-v2 `feature_id` mapping for all 11
features is recorded in the revision publish PR. Older releases remain readable
legacy history and are not backfilled.

## Known caveats

Natural Earth notes that coastline accuracy is suspect for northern Russia and
southern Chile, and that some rank 5 land should be reclassified as rank 6. The
source also includes a `Null island` feature, which is retained for fidelity to
the source layer.
