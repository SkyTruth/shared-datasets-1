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
source: Natural Earth 1:10,000,000 physical land polygons v5.1.1
license: Public domain per Natural Earth Terms of Use
citation: Made with Natural Earth. Free vector and raster map data at naturalearthdata.com; land polygons version 5.1.1.
notes: Initial upload from local Natural Earth ne_10m_land shapefile version 5.1.1; release 2026-04-30; source features 11;
  fgb sha256 5e69cd50432794b6411a81d99faa1d1c74e9d778fbfd430e43e1c7adb4d9912a; pmtiles sha256 52793c9fd15c17777a271cb3f984d8a3ffee8acb7c25a8aa04b8809d458901be;
  PMTiles rebuilt 2026-05-04 with zooms 0-8, no pre-tiling simplification, no line simplification, and no tiny-polygon reduction
  at maximum zoom for higher-zoom display fidelity; future rebuilds must use the repo-standard GeoJSONSeq to Tippecanoe MBTiles
  to PMTiles conversion path; canonical FGB preserves source geometry and fields
row_count: 11
data_profile:
  field_count: 3
  identity_candidates: []
  notes: No documented ext_id candidate
source_scale_denominator: 10000000
files:
- path: latest/natural-earth-10m-land.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 multipolygon dataset
- path: latest/natural-earth-10m-land.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same source layer
- path: releases/2026-04-30/natural-earth-10m-land.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/2026-04-30/natural-earth-10m-land.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
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
| `latest/natural-earth-10m-land.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same source layer |
| `releases/2026-04-30/natural-earth-10m-land.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/2026-04-30/natural-earth-10m-land.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 multipolygon geometry. The source shapefile reports 11 polygon
features with extent `(-180, -90) - (180, 83.634101)`. The published FlatGeobuf
promotes geometries to multipolygon and keeps the source fields unchanged.

The PMTiles artifact was rebuilt on 2026-05-04 with zooms 0 through 8, no
pre-tiling simplification, no line simplification, and no tiny-polygon reduction
at maximum zoom. Future rebuilds must export WGS84 GeoJSONSeq from the FGB,
build Tippecanoe MBTiles, and convert with `pmtiles convert`. The canonical
FlatGeobuf remains the analytical source and preserves the original source
geometry. The maxzoom
comes from the stable `source_scale_denominator: 10000000` hint, not from a
meter-resolution interpretation of the Natural Earth `10m` label.

## Properties / columns

| Name | Type | Description |
|---|---|---|
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

## Known caveats

Natural Earth notes that coastline accuracy is suspect for northern Russia and
southern Chile, and that some rank 5 land should be reclassified as rank 6. The
source also includes a `Null island` feature, which is retained for fidelity to
the source layer.
