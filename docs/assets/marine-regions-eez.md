---
schema_version: 1
asset_slug: marine-regions-eez
title: Marine Regions Exclusive Economic Zones
category: 100-geographic-reference
subcategory: 120-marine-boundaries
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/marine-regions-eez.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
last_updated: '2026-04-29'
source: Marine Regions EEZ v12
license: See Marine Regions terms
notes: Initial upload from eez-mr_eez_v12.fgb; release 2026-04-29; sha256 b4f1d04cff66a75a4176734a02e2af994b7c55490b7b42cdcf7ba2d5c431f6b7;
  PMTiles sha256 34345c1e24ef99fe5c9be0b8a4f5b38b26a5e4b7411c6735002133e17105fea2
files:
- path: latest/marine-regions-eez.fgb
  format: fgb
  role: canonical
  purpose: Canonical EEZ polygon dataset
- path: latest/marine-regions-eez.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same source layer
- path: releases/2026-04-29/marine-regions-eez.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/2026-04-29/marine-regions-eez.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
---

# Marine Regions Exclusive Economic Zones

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Last updated:** 2026-04-29
- **Update cadence:** manual
- **Canonical file:** `latest/marine-regions-eez.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** Marine Regions EEZ v12
- **License / terms:** See Marine Regions terms
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the Marine Regions Exclusive Economic Zones v12 polygon
dataset. It is a reusable marine boundary layer for spatial filtering,
contextual mapping, and coarse jurisdictional attribution.

The canonical FlatGeobuf preserves the source geometry and attributes. The
PMTiles artifact is generated from the same source layer for web-map display.

## When to use it

- Use this for reusable EEZ boundaries in analysis and contextual maps.
- Use the FlatGeobuf file for analysis and the PMTiles file for display.
- Do not treat this as legal advice or as a replacement for source authority.
- Do not use the PMTiles artifact as the analytical source.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/marine-regions-eez.fgb` | `fgb` | `canonical` | Canonical EEZ polygon dataset |
| `latest/marine-regions-eez.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same source layer |
| `releases/2026-04-29/marine-regions-eez.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/2026-04-29/marine-regions-eez.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
<!-- END GENERATED files-table -->

## Schema notes

This is a direct format conversion from the Marine Regions EEZ v12 source layer.
Source field names and values are preserved in the FlatGeobuf output.

## Properties / columns

Definitions are inherited from the Marine Regions EEZ v12 source and need source
confirmation. Use the source documentation for authoritative field definitions.

## Update notes

Manually converted from `eez-mr_eez_v12.fgb` and published as a 2026-04-29
release.

## Known caveats

EEZ geometries and attributes should be interpreted according to Marine Regions
source terms and documentation. Use source authority for legal or operational
decisions.
