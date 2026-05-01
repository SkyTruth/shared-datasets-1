---
schema_version: 1
asset_slug: wdpa-terrestrial
title: WDPA Terrestrial Protected and Conserved Areas
category: 100-geographic-reference
subcategory: 130-protected-areas
status: active
owner: SkyTruth
update_cadence: monthly
canonical_format: fgb
canonical_file: latest/wdpa-terrestrial.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
last_updated: '2026-04-29'
source: UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
license: See Protected Planet WDPA terms
notes: Simplified monthly job preserves source fields and publishes FGB plus PMTiles
files:
- path: latest/wdpa-terrestrial.fgb
  format: fgb
  role: canonical
  purpose: Canonical mixed-geometry vector dataset
- path: latest/wdpa-terrestrial.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same monthly extract
- path: releases/YYYY-MM-DD/wdpa-terrestrial.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/wdpa-terrestrial.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Monthly run record
---

# WDPA Terrestrial Protected and Conserved Areas

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Owner:** SkyTruth
- **Last updated:** 2026-04-29
- **Update cadence:** monthly
- **Canonical file:** `latest/wdpa-terrestrial.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
- **License / terms:** See Protected Planet WDPA terms
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the monthly WDPA/WDOECM source rows whose `MARINE` value is
`0`. It is a direct format conversion and split from the upstream source. Fields
are preserved from the source dataset.

## When to use it

- Use this for reusable terrestrial protected-area and conserved-area boundaries or point records.
- Do not use this when a marine or coastal extract is required.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/wdpa-terrestrial.fgb` | `fgb` | `canonical` | Canonical mixed-geometry vector dataset |
| `latest/wdpa-terrestrial.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same monthly extract |
| `releases/YYYY-MM-DD/wdpa-terrestrial.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/wdpa-terrestrial.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Monthly run record |
<!-- END GENERATED files-table -->

## Schema notes

The simplified monthly job does not rename, add, or remove source fields. Refer
to the Protected Planet WDPA user manual and source metadata for authoritative
field definitions.

## Properties / columns

Definitions are inherited from the Protected Planet WDPA/WDOECM source and need
source confirmation for each monthly release. The job verifies that all selected
source layers have identical fields before publishing.

| Name | Type | Description |
|---|---|---|
| Source fields | varies | All fields are preserved from the Protected Planet WDPA/WDOECM source. Refer to the upstream user manual and source metadata for authoritative field names, types, and definitions for each monthly release. |

## Update notes

Updated by `python -m ingestion.wdpa_monthly.run`, deployed as the
`wdpa-monthly` Cloud Run Job and scheduled for `0 9 1 * *` UTC.

## Known caveats

The canonical FGB intentionally keeps mixed point and polygon geometries. Some
desktop GIS and map clients handle mixed-geometry layers less gracefully than
single-geometry layers.
