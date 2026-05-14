---
schema_version: 1
asset_slug: cerulean-s1-envelope
title: Cerulean S1 Envelope
category: 200-imagery-derived
subcategory: 210-satellite-indexes
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/cerulean-s1-envelope.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: SkyTruth internal derived Cerulean Sentinel-1 envelope WKT extract
license: SkyTruth internal use; upstream source and redistribution terms need confirmation
citation: SkyTruth (2026). Cerulean S1 Envelope. Internal derived Sentinel-1 envelope extract; upstream source citation needs
  confirmation.
notes: Named as a Cerulean envelope to avoid implying complete Sentinel-1 footprint coverage; the legacy remote prefix sentinel-1-footprints
  is a deprecated pre-rename location and is intentionally not an active catalog slug; release 2026-05-01; source features
  1; fgb sha256 4fd635807aa544d8a0019f54ff663a639816cc7b2726d7a935fb7d8780924b11; pmtiles sha256 33f080e73a6ea2f5dc78b7174abbaf61c6d3c52165615f69ff1d8510ac225e6d;
  PMTiles rebuilt 2026-05-04 with Tippecanoe zooms 0-6, no simplification, and synthetic source_layer property for catalog
  inspection; canonical FGB preserves the source WKT geometry as an envelope only
row_count: 1
data_profile:
  field_count: 0
  identity_candidates: []
  notes: No attribute fields
pmtiles_detail_hint: coarse
files:
- path: latest/cerulean-s1-envelope.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 multipolygon envelope dataset
- path: latest/cerulean-s1-envelope.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same envelope geometry
- path: releases/2026-05-01/cerulean-s1-envelope.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/cerulean-s1-envelope.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile releases
- path: runs/2026-05-01.json
  format: json
  role: run-record
  purpose: Manual publish record
---

# Cerulean S1 Envelope

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/cerulean-s1-envelope.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** SkyTruth internal derived Cerulean Sentinel-1 envelope WKT extract
- **License / terms:** SkyTruth internal use; upstream source and redistribution terms need confirmation
- **Citation:** SkyTruth (2026). Cerulean S1 Envelope. Internal derived Sentinel-1 envelope extract; upstream source citation needs confirmation.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains a SkyTruth internal derived Cerulean Sentinel-1 analysis
envelope from the local source file `s1 footprint wkt.csv`. The source CSV has
one `wkt` column and one valid WGS84 `MultiPolygon` feature.

This is an envelope or coverage mask used for Cerulean context, not a complete
Sentinel-1 scene-footprint catalog. The canonical FlatGeobuf preserves the
source geometry as a single multipolygon feature. The PMTiles artifact is
generated from the same geometry for web-map display only.

## When to use it

- Use this as a broad Cerulean Sentinel-1 analysis envelope for map display,
  coarse filtering, and context.
- Use the FlatGeobuf file for analytical workflows.
- Use the PMTiles file for web-map display.
- Do not use this as a scene-level Sentinel-1 catalog, acquisition metadata
  table, repeat-intensity surface, polarization inventory, orbit list, or
  public redistribution source without confirming the upstream source and
  license terms.
- Do not use the PMTiles artifact as the analytical source.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/cerulean-s1-envelope.fgb` | `fgb` | `canonical` | Canonical WGS84 multipolygon envelope dataset |
| `latest/cerulean-s1-envelope.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same envelope geometry |
| `releases/2026-05-01/cerulean-s1-envelope.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/cerulean-s1-envelope.pmtiles` | `pmtiles` | `release` | Dated map-tile releases |
| `runs/2026-05-01.json` | `json` | `run-record` | Manual publish record |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 multipolygon geometry. The source CSV contains one valid
`MultiPolygon` with 64 polygon parts and bounds `(-179.366759, -79.420040) -
(179.536787, 89.565949)`. The published FlatGeobuf has one feature in layer
`cerulean_s1_envelope`.

The source CSV is not published as a canonical format because shared-datasets
CSV assets must not contain geometry columns. The PMTiles artifact is generated
with Tippecanoe 2.79.0 from a temporary GeoJSON tiling input, with zooms 0
through 6 and no display simplification.

The remote prefix `200-imagery-derived/210-satellite-indexes/sentinel-1-footprints/`
was an initial name for this dataset before the framing was corrected. It is
treated as a deprecated legacy prefix for audit purposes, not as a separate
catalog asset and not as the canonical publishing location.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `geometry` | MultiPolygon | Cerulean Sentinel-1 analysis envelope geometry in WGS84. |

The canonical FGB publishes no non-geometry properties. PMTiles display tiles add
a compact synthetic `source_layer` property so the catalog inspector can identify
decoded features.

## Update notes

Manually converted from `/Users/jonathanraphael/Downloads/s1 footprint wkt.csv`
on 2026-05-01 using `scripts/vector_asset.py`. The asset name emphasizes that
the geometry is an envelope and should not be interpreted as complete
Sentinel-1 footprint coverage.

Output summary:

- Source rows: 1
- Published features: 1
- CRS: EPSG:4326
- Toolchain: GDAL 3.6.2; Tippecanoe 2.79.0; PMTiles CLI unavailable locally
- FGB SHA-256: `4fd635807aa544d8a0019f54ff663a639816cc7b2726d7a935fb7d8780924b11`
- PMTiles maxzoom: 6
- PMTiles zoom 0 decoded feature properties: `source_layer`
- PMTiles SHA-256: `33f080e73a6ea2f5dc78b7174abbaf61c6d3c52165615f69ff1d8510ac225e6d`

## Known caveats

The input file did not include source metadata, acquisition fields, timestamps,
repeat intensity, polarization, orbit direction, relative orbit, platform, or
license text. Treat this as an internal derived Cerulean envelope until the
upstream source, generation method, and redistribution terms are confirmed.
