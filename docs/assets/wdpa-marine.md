---
schema_version: 1
asset_slug: wdpa-marine
title: WDPA Marine Protected and Conserved Areas
category: 100-geographic-reference
subcategory: 130-protected-areas
status: active
access_tier: public
owner: SkyTruth
update_cadence: monthly
canonical_format: fgb
canonical_file: latest/wdpa-marine.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
license: See Protected Planet WDPA terms
notes: Monthly job preserves source fields and publishes FGB plus PMTiles. Release history, source versions, row counts, and
  file hashes are recorded in the bucket release index and per-run records. The 2026-05-04 PMTiles were rebuilt at maxzoom
  12 from the published FGB profile; pmtiles sha256 963e851bf7f0952a9eee321074d77bd071bc935e74692932569a98fa4801ed8e
files:
- path: latest/wdpa-marine.fgb
  format: fgb
  role: canonical
  purpose: Canonical mixed-geometry vector dataset
- path: latest/wdpa-marine.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same monthly extract
- path: releases/YYYY-MM-DD/wdpa-marine.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/wdpa-marine.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Monthly run record
---

# WDPA Marine Protected and Conserved Areas

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** monthly
- **Canonical file:** `latest/wdpa-marine.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
- **License / terms:** See Protected Planet WDPA terms
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the monthly WDPA/WDOECM source rows whose `MARINE` value is
`1` or `2`. It is a direct format conversion and split from the upstream source.
Fields are preserved from the source dataset.

## When to use it

- Use this for reusable marine protected-area and conserved-area boundaries or point records.
- Do not use this when a terrestrial-only extract is required.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/wdpa-marine.fgb` | `fgb` | `canonical` | Canonical mixed-geometry vector dataset |
| `latest/wdpa-marine.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same monthly extract |
| `releases/YYYY-MM-DD/wdpa-marine.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/wdpa-marine.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
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
`wdpa-monthly` Cloud Run Job and scheduled for `0 9 1-10 * *` UTC.

The 2026-05-04 PMTiles artifact was rebuilt from the published FGB using auto
maxzoom selection. The mixed point/vector FGB profile resolved to maxzoom 12
with point retention. The rebuilt PMTiles SHA-256 is
`963e851bf7f0952a9eee321074d77bd071bc935e74692932569a98fa4801ed8e`.

## Known caveats

The canonical FGB intentionally keeps mixed point and polygon geometries. Some
desktop GIS and map clients handle mixed-geometry layers less gracefully than
single-geometry layers.
