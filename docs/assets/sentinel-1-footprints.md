---
schema_version: 1
asset_slug: sentinel-1-footprints
title: Sentinel-1 Footprints
category: 200-imagery-derived
subcategory: 210-satellite-indexes
status: active
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/sentinel-1-footprints.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
last_updated: '2026-05-01'
source: SkyTruth internal derived Sentinel-1 footprint WKT extract
license: SkyTruth internal use; upstream source and redistribution terms need confirmation
notes: Initial upload from local s1 footprint wkt.csv; release 2026-05-01; source features 1; fgb sha256 bbef2a6f3adc8c0e5b189be0d09163712cdbaa62c46b4963656683211ec2ba26;
  pmtiles sha256 a29c9062b1728f4690b64857ebd8c2816ccc8731797cf3f86e4eec4add04e902; PMTiles generated with Tippecanoe zooms
  0-8 and no simplification; canonical FGB preserves the source WKT geometry
files:
- path: latest/sentinel-1-footprints.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 multipolygon footprint dataset
- path: latest/sentinel-1-footprints.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same footprint geometry
- path: releases/2026-05-01/sentinel-1-footprints.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/2026-05-01/sentinel-1-footprints.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: runs/2026-05-01.json
  format: json
  role: run-record
  purpose: Manual publish record
---

# Sentinel-1 Footprints

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Owner:** SkyTruth
- **Last updated:** 2026-05-01
- **Update cadence:** manual
- **Canonical file:** `latest/sentinel-1-footprints.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** SkyTruth internal derived Sentinel-1 footprint WKT extract
- **License / terms:** SkyTruth internal use; upstream source and redistribution terms need confirmation
<!-- END GENERATED asset-summary -->

## What this is

This asset contains a SkyTruth internal derived Sentinel-1 footprint geometry
from the local source file `s1 footprint wkt.csv`. The source CSV has one `wkt`
column and one valid WGS84 `MultiPolygon` feature.

The canonical FlatGeobuf preserves the source geometry as a single multipolygon
feature. The PMTiles artifact is generated from the same geometry for web-map
display only.

## When to use it

- Use this as a broad Sentinel-1 footprint coverage polygon for map display,
  coarse filtering, and context.
- Use the FlatGeobuf file for analytical workflows.
- Use the PMTiles file for web-map display.
- Do not use this as a scene-level Sentinel-1 catalog, acquisition metadata
  table, orbit list, or public redistribution source without confirming the
  upstream source and license terms.
- Do not use the PMTiles artifact as the analytical source.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/sentinel-1-footprints.fgb` | `fgb` | `canonical` | Canonical WGS84 multipolygon footprint dataset |
| `latest/sentinel-1-footprints.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same footprint geometry |
| `releases/2026-05-01/sentinel-1-footprints.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/2026-05-01/sentinel-1-footprints.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `runs/2026-05-01.json` | `json` | `run-record` | Manual publish record |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 multipolygon geometry. The source CSV contains one valid
`MultiPolygon` with 64 polygon parts and bounds `(-179.366759, -79.420040) -
(179.536787, 89.565949)`. The published FlatGeobuf has one feature in layer
`sentinel_1_footprints`.

The source CSV is not published as a canonical format because shared-datasets
CSV assets must not contain geometry columns. The PMTiles artifact was generated
with Tippecanoe 2.79.0 from a temporary GeoJSON tiling input, with zooms 0
through 8 and no display simplification.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `geometry` | MultiPolygon | Sentinel-1 footprint geometry in WGS84. |

No non-geometry properties are published.

## Update notes

Manually converted from `/Users/jonathanraphael/Downloads/s1 footprint wkt.csv`
on 2026-05-01 using `scripts/vector_asset.py`.

Output summary:

- Source rows: 1
- Published features: 1
- CRS: EPSG:4326
- Toolchain: GDAL 3.6.2; Tippecanoe 2.79.0; PMTiles CLI unavailable locally
- FGB SHA-256: `bbef2a6f3adc8c0e5b189be0d09163712cdbaa62c46b4963656683211ec2ba26`
- PMTiles SHA-256: `a29c9062b1728f4690b64857ebd8c2816ccc8731797cf3f86e4eec4add04e902`

## Known caveats

The input file did not include source metadata, acquisition fields, timestamps,
or license text. Treat this as an internal derived footprint geometry until the
upstream source, generation method, and redistribution terms are confirmed.
