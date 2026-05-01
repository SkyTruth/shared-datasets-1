---
asset_slug: "natural-earth-10m-land"
title: "Natural Earth 10m Land"
category: "100-geographic-reference"
subcategory: "110-boundaries"
status: "active"
owner: "SkyTruth"
update_cadence: "manual"
canonical_format: "fgb"
last_updated: "2026-04-30"
source: "Natural Earth 1:10m physical land polygons v5.1.1"
license: "Public domain per Natural Earth Terms of Use"
---

# Natural Earth 10m Land

**Status:** active  
**Owner:** SkyTruth  
**Last updated:** 2026-04-30  
**Update cadence:** manual  
**Canonical file:** `latest/natural-earth-10m-land.fgb`  
**Source:** Natural Earth 1:10m physical `ne_10m_land`, version 5.1.1  
**License / terms:** Public domain per Natural Earth Terms of Use.

## What this is

This asset contains the Natural Earth 1:10m land polygon layer, including major
islands. Natural Earth describes the layer as derived from the 10m coastline, with
continental polygons split into smaller contiguous pieces to improve processing
performance in some software.

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

| File | Purpose |
|---|---|
| `latest/natural-earth-10m-land.fgb` | Canonical WGS84 multipolygon dataset |
| `latest/natural-earth-10m-land.pmtiles` | Web map tiles generated from the same source layer |
| `releases/2026-04-30/natural-earth-10m-land.fgb` | Dated canonical release |
| `releases/2026-04-30/natural-earth-10m-land.pmtiles` | Dated map-tile release |

## Schema notes

Geometry is WGS84 multipolygon geometry. The source shapefile reports 11 polygon
features with extent `(-180, -90) - (180, 83.634101)`. The published FlatGeobuf
promotes geometries to multipolygon and keeps the source fields unchanged.

The PMTiles artifact was generated with Tippecanoe 2.79.0 from a temporary
GeoJSON tiling input, with zooms 0 through 6 and `--tile-simplify 0.01`. That
simplification applies only to display tiles; the canonical FlatGeobuf remains
unsimplified.

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
- Toolchain: GDAL 3.6.2; Tippecanoe 2.79.0; PMTiles CLI 1.30.2 for validation
- FGB SHA-256: `5e69cd50432794b6411a81d99faa1d1c74e9d778fbfd430e43e1c7adb4d9912a`
- PMTiles SHA-256: `e395193e936fca420fcdc4139dcfedf3a4945ed5f7128a5194b16fdc5b936fcf`

## Known caveats

Natural Earth notes that coastline accuracy is suspect for northern Russia and
southern Chile, and that some rank 5 land should be reclassified as rank 6. The
source also includes a `Null island` feature, which is retained for fidelity to
the source layer.
