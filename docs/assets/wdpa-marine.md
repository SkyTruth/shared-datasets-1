---
asset_slug: "wdpa-marine"
title: "WDPA Marine Protected and Conserved Areas"
category: "100-geographic-reference"
subcategory: "130-protected-areas"
status: "active"
owner: "SkyTruth"
update_cadence: "monthly"
canonical_format: "fgb"
last_updated: "2026-04-29"
source: "UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM"
license: "See Protected Planet WDPA terms"
---

# WDPA Marine Protected and Conserved Areas

**Status:** active  
**Owner:** SkyTruth  
**Last updated:** 2026-04-29  
**Update cadence:** monthly  
**Canonical file:** `latest/wdpa-marine.fgb`  
**Source:** UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM  
**License / terms:** See Protected Planet WDPA terms.

## What this is

This asset contains the monthly WDPA/WDOECM source rows whose `MARINE` value is
`1` or `2`. It is a direct format conversion and split from the upstream source.
Fields are preserved from the source dataset.

## When to use it

- Use this for reusable marine protected-area and conserved-area boundaries or point records.
- Do not use this when a terrestrial-only extract is required.

## Files

| File | Purpose |
|---|---|
| `latest/wdpa-marine.fgb` | Canonical mixed-geometry vector dataset |
| `latest/wdpa-marine.pmtiles` | Web map tiles generated from the same monthly extract |
| `releases/YYYY-MM-DD/wdpa-marine.fgb` | Dated canonical release |
| `releases/YYYY-MM-DD/wdpa-marine.pmtiles` | Dated map-tile release |
| `runs/YYYY-MM-DD.json` | Monthly run record |

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
