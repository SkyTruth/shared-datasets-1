---
schema_version: 1
asset_slug: petrodata
title: PETRODATA Petroleum Fields
category: 300-infrastructure-industrial
subcategory: 310-energy
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/petrodata.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: PRIO PETRODATA v1.2
license: No explicit license found on the PRIO dataset page; cite Lujala, Rod, and Thieme 2007 and follow source terms
notes: Combined local onshore and offshore shapefiles into one FGB plus PMTiles with source_layer; release 2026-04-29; fgb
  sha256 d77f5e4bdb9d231a9058e70c03648092a613c5009889d5f57e0ae05969950296; pmtiles sha256 798ea67f06e20c7912b441cf0a6b3eb5ceee9063d9f164c9e57daacd737741a7;
  PMTiles rebuilt 2026-05-04 at maxzoom 9 from sampled FGB geometry detail
files:
- path: latest/petrodata.fgb
  format: fgb
  role: canonical
  purpose: Canonical combined vector dataset
- path: latest/petrodata.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same combined polygons
- path: releases/YYYY-MM-DD/petrodata.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/petrodata.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
---

# PETRODATA Petroleum Fields

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/petrodata.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** PRIO PETRODATA v1.2
- **License / terms:** No explicit license found on the PRIO dataset page; cite Lujala, Rod, and Thieme 2007 and follow source terms
<!-- END GENERATED asset-summary -->

## What this is

This asset contains PRIO PETRODATA v1.2, a global dataset of oil and gas field/deposit polygons. PETRODATA was compiled for research on relationships between hydrocarbon resources and armed civil conflict, and covers 1946-2003.

The source dataset represents generalized petroleum field locations as polygons, with centroid latitude/longitude attributes and descriptive fields for reserve type, discovery year, production year, and source information. This published asset combines the source onshore and offshore shapefiles into one canonical layer and adds `source_layer` to distinguish the original file.

## When to use it

- Use this for reusable global oil and gas field or deposit polygons.
- Use `source_layer = 'onshore'` for terrestrial records and `source_layer = 'offshore'` for marine records.
- Do not use this as a current operating-infrastructure inventory or as precise lease/facility boundaries.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/petrodata.fgb` | `fgb` | `canonical` | Canonical combined vector dataset |
| `latest/petrodata.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same combined polygons |
| `releases/YYYY-MM-DD/petrodata.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/petrodata.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
<!-- END GENERATED files-table -->

## Schema notes

This is a format conversion from `Petrodata_Onshore_V1.2.shp` and `Petrodata_offshore_V1.2.shp` to FlatGeobuf and PMTiles. Source field names and values are preserved, and `source_layer` was added with values `onshore` and `offshore`. Geometry was promoted to multipolygon during conversion for consistent output typing.

The local v1.2 files contain 1,273 total features: 891 onshore and 382 offshore. The PRIO codebook describes PETRODATA's variables and notes that polygons may represent one or several fields, with polygon size determined by source point distribution rather than the number of fields inside.

The PMTiles artifact is generated from the same combined polygons, with zooms 0 through 9. Auto maxzoom selection used sampled FGB geometry detail rather than a fixed fallback.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `source_layer` | string | Source split: `onshore` or `offshore`. Added during conversion. |
| `PRIMKEY` | string | Unique polygon identifier. Onshore keys combine FIPS country code, site number, and `PET`; offshore keys combine `OF`, a running number, and `PET`. |
| `COUNTRY` | string | Country assigned to the polygon. |
| `FIPSCODE` | string | FIPS country code. |
| `COWCODE` | integer | Correlates of War country code; `-9999` where no code exists. |
| `CONTCODE` | integer | PRIO/Uppsala-style continent code. |
| `SITENUM` | integer | Site number assigned within country. |
| `NAME` | string | Region, basin, or location name. |
| `LAT` | real | Latitude of the polygon centroid in decimal degrees. |
| `LONG` | real | Longitude of the polygon centroid in decimal degrees. |
| `RES` | string | Resource code. PETRODATA uses `PET` for petroleum. |
| `RESINFO` | string | Hydrocarbon type: `oil`, `gas`, `oil and gas`, or missing marker. |
| `LOCSOURCE` | string | Reference for location information. |
| `FIELDINFO` | integer | Production status code: `1` known production, `2` confirmed discovery with no known production, `3` unknown production status, `4` under exploration/no formal discovery. |
| `DISC` | integer | First discovery year in the polygon; `1945` for pre-1946 discoveries and `-9999` for missing values. |
| `DISCPRES` | integer | Discovery-year precision code. |
| `PROD` | integer | First production year in the polygon; `1945` for pre-1946 production and `-9999` for missing values. |
| `PRODPRES` | integer | Production-year precision code. |
| `OTHERINFO` | string | Additional polygon notes. |
| `SOURCEINFO` | string | References for descriptive variables. |
| `VERSION` | real | Source dataset version. |

## Update notes

Manually converted from `/Users/jonathanraphael/Desktop/Petrodata v12 Data (1)` on 2026-04-29 using GDAL, Tippecanoe, and PMTiles tooling.

The PMTiles artifact was rebuilt on 2026-05-04 from the canonical FGB using auto maxzoom selection. The sampled FGB profile resolved to maxzoom 9 from representative segment lengths and feature dimensions. The rebuilt PMTiles SHA-256 is `798ea67f06e20c7912b441cf0a6b3eb5ceee9063d9f164c9e57daacd737741a7`.

## Known caveats

This dataset is historical and covers 1946-2003. It is a generalized geological/resource dataset and should not be treated as current infrastructure, facility, lease, or operational status data. Offshore country assignment can be uncertain because the source notes that offshore boundaries are often fuzzy.
