---
asset_slug: "ims-sea-ice-extent"
title: "IMS Sea-Ice Extent"
category: "200-imagery-derived"
subcategory: "250-weather-climate"
status: "active"
owner: "SkyTruth"
update_cadence: "daily"
canonical_format: "fgb"
last_updated: "2026-04-29"
source: "NOAA/NSIDC IMS Daily Northern Hemisphere Snow and Ice Analysis G02156"
license: "Public U.S. government work; cite NSIDC G02156"
---

# IMS Sea-Ice Extent

**Status:** active  
**Owner:** SkyTruth  
**Last updated:** 2026-04-29  
**Update cadence:** daily  
**Canonical file:** `latest/ims-sea-ice-extent.fgb`  
**Source:** NOAA/NSIDC IMS Daily Northern Hemisphere Snow and Ice Analysis G02156  
**License / terms:** Public U.S. government work; cite NSIDC G02156.

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

| File | Purpose |
|---|---|
| `latest/ims-sea-ice-extent.fgb` | Canonical vectorized class-3 extent |
| `latest/ims-sea-ice-extent.pmtiles` | Web map tiles generated from the same vector output |
| `releases/YYYY-MM-DD/ims-sea-ice-extent.fgb` | Dated canonical release |
| `releases/YYYY-MM-DD/ims-sea-ice-extent.pmtiles` | Dated map-tile release |
| `runs/YYYY-MM-DD.json` | Daily run record |

## Schema notes

The job derives a minimal schema from the source raster class and filename date.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `DN` | integer | IMS raster value. Published features are class `3`, described by NSIDC as sea/lake ice. |
| `ice_date` | string | Date encoded in the source GeoTIFF filename, formatted as `YYYY-MM-DD`. |

## Update notes

Updated by `python -m ingestion.sea_ice_daily.run`, deployed as the
`sea-ice-daily` Cloud Run Job and scheduled for `0 15 * * *` UTC.

## Known caveats

IMS GeoTIFF filename dates are documented by NSIDC as creation dates, while the
images are valid for the next day. This asset intentionally uses the filename
date for release folders and `ice_date`, and stores the documented valid date in
the run record.
