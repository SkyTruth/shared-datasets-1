---
schema_version: 1
asset_slug: ims-sea-ice-extent
title: IMS Sea-Ice Extent
category: 200-imagery-derived
subcategory: 250-weather-climate
status: active
access_tier: public
owner: SkyTruth
update_cadence: daily
canonical_format: fgb
canonical_file: latest/ims-sea-ice-extent.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: NOAA/NSIDC IMS Daily Northern Hemisphere Snow and Ice Analysis G02156
license: Public U.S. government work; cite NSIDC G02156
citation: 'U.S. National Ice Center (2008). IMS Daily Northern Hemisphere Snow and Ice Analysis at 1 km, 4 km, and 24 km Resolutions,
  Version 1. Boulder, Colorado USA: National Snow and Ice Data Center. https://doi.org/10.7265/N52R3PMC. Accessed 2026-05-07.'
notes: Daily job publishes raw IMS class 3 as FGB plus PMTiles. PMTiles were rebuilt 2026-05-04 at maxzoom 8 from the 4000-meter
  source-resolution hint; pmtiles sha256 66bff572665dc444734b9c8ced0047ecbe672bee8b12afa307862a77a94c958d. Release history,
  source versions, row counts, and file hashes are recorded in the bucket release index and per-run records.
row_count: 2632
data_profile:
  field_count: 2
  identity_candidates: []
  notes: No unique ID candidate; DN/ice_date are class/time attributes
source_resolution_meters: 4000
files:
- path: latest/ims-sea-ice-extent.fgb
  format: fgb
  role: canonical
  purpose: Canonical vectorized class-3 extent
- path: latest/ims-sea-ice-extent.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same vector output
- path: releases/YYYY-MM-DD/ims-sea-ice-extent.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/ims-sea-ice-extent.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Daily run record
---

# IMS Sea-Ice Extent

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** daily
- **Canonical file:** `latest/ims-sea-ice-extent.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** NOAA/NSIDC IMS Daily Northern Hemisphere Snow and Ice Analysis G02156
- **License / terms:** Public U.S. government work; cite NSIDC G02156
- **Citation:** U.S. National Ice Center (2008). IMS Daily Northern Hemisphere Snow and Ice Analysis at 1 km, 4 km, and 24 km Resolutions, Version 1. Boulder, Colorado USA: National Snow and Ice Data Center. https://doi.org/10.7265/N52R3PMC. Accessed 2026-05-07.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains polygons vectorized from the 4 km IMS GeoTIFF raster class
`3`, which NSIDC describes as sea/lake ice. It is a raw class extraction and does
not remove inland or lake ice.

The release folder and `ice_date` field use the date encoded in the GeoTIFF
filename. NSIDC documents the GeoTIFF imagery as valid for the next day; the run
record preserves that documented valid date.

## When to use it

- Use this for a daily Northern Hemisphere ice-extent mask derived from IMS.
- Do not use this when lake ice must be removed or when an ocean-only sea-ice
  mask is required.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/ims-sea-ice-extent.fgb` | `fgb` | `canonical` | Canonical vectorized class-3 extent |
| `latest/ims-sea-ice-extent.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same vector output |
| `releases/YYYY-MM-DD/ims-sea-ice-extent.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/ims-sea-ice-extent.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Daily run record |
<!-- END GENERATED files-table -->

## Schema notes

The job derives a minimal schema from the source raster class and filename date.

The PMTiles artifact is generated from the same vectorized output. Auto maxzoom selection uses the stable `source_resolution_meters: 4000` hint, resolving to zooms 0 through 8.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `DN` | integer | IMS raster value. Published features are class `3`, described by NSIDC as sea/lake ice. |
| `ice_date` | string | Date encoded in the source GeoTIFF filename, formatted as `YYYY-MM-DD`. |

## Update notes

Updated by `python -m ingestion.sea_ice_daily.run`, deployed as the
`sea-ice-daily` Cloud Run Job and scheduled for `0 15 * * *` UTC.

The PMTiles artifact was rebuilt on 2026-05-04 from the canonical FGB using auto maxzoom selection. The 4000-meter source-resolution hint resolves to maxzoom 8. The rebuilt PMTiles SHA-256 is `66bff572665dc444734b9c8ced0047ecbe672bee8b12afa307862a77a94c958d`.

## Known caveats

IMS GeoTIFF filename dates are documented by NSIDC as creation dates, while the
images are valid for the next day. This asset intentionally uses the filename
date for release folders and `ice_date`, and stores the documented valid date in
the run record.
