---
schema_version: 1
asset_slug: wdpa-terrestrial
title: WDPA Terrestrial Protected and Conserved Areas
category: 100-geographic-reference
subcategory: 130-protected-areas
status: active
access_tier: public
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
source: UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
license: See Protected Planet WDPA terms
citation: 'UNEP-WCMC and IUCN (2026). Protected Planet: The World Database on Protected Areas (WDPA) and World Database on
  Other Effective Area-based Conservation Measures (WD-OECM) [Online], May 2026, Cambridge, UK: UNEP-WCMC and IUCN. Available
  at: www.protectedplanet.net.'
notes: Monthly job preserves source fields and publishes FGB plus PMTiles. Release history, source versions, row counts, and
  file hashes are recorded in the bucket release index and per-run records. The 2026-05-04 PMTiles were rebuilt at maxzoom
  12 from the published FGB profile; pmtiles sha256 02591002e5ef5c0e50829972de6ae037b41d8bcb5b8fa49ae5a0dd6a7f6c53d2
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
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** monthly
- **Canonical file:** `latest/wdpa-terrestrial.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
- **License / terms:** See Protected Planet WDPA terms
- **Citation:** UNEP-WCMC and IUCN (2026). Protected Planet: The World Database on Protected Areas (WDPA) and World Database on Other Effective Area-based Conservation Measures (WD-OECM) [Online], May 2026, Cambridge, UK: UNEP-WCMC and IUCN. Available at: www.protectedplanet.net.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the monthly WDPA/WDOECM source rows selected for the
terrestrial realm. It is a direct format conversion and split from the upstream
source. Fields are preserved from the source dataset.

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
| `SITE_ID` | integer | Source numeric site identifier. |
| `SITE_PID` | string | Source persistent site identifier. |
| `SITE_TYPE` | string | WDPA/WDOECM source site type. |
| `NAME_ENG` | string | Protected or conserved area name in English. |
| `NAME` | string | Protected or conserved area name as supplied by the source. |
| `DESIG` | string | Source designation text. |
| `DESIG_ENG` | string | Designation text in English. |
| `DESIG_TYPE` | string | Designation type or authority class. |
| `IUCN_CAT` | string | IUCN protected-area management category. |
| `INT_CRIT` | string | International criteria field, where supplied by the source. |
| `REALM` | string | Source realm value used for the marine/coastal versus terrestrial split. |
| `REP_M_AREA` | real | Source-reported marine area in square kilometers. |
| `REP_AREA` | real | Source-reported total area in square kilometers. |
| `NO_TAKE` | string | Source no-take status. |
| `NO_TK_AREA` | real | Source-reported no-take area in square kilometers. |
| `STATUS` | string | Source status for the protected or conserved area. |
| `STATUS_YR` | integer | Year associated with the source status. |
| `GOV_TYPE` | string | Governance type. |
| `GOVSUBTYPE` | string | Governance subtype. |
| `OWN_TYPE` | string | Ownership type. |
| `OWNSUBTYPE` | string | Ownership subtype. |
| `MANG_AUTH` | string | Management authority. |
| `MANG_PLAN` | string | Management-plan status or reference. |
| `VERIF` | string | Source verification status. |
| `METADATAID` | integer | Source metadata record identifier. |
| `PRNT_ISO3` | string | Parent country or territory ISO3 code. |
| `ISO3` | string | Country or territory ISO3 code associated with the feature. |
| `SUPP_INFO` | string | Supplementary information status or flag. |
| `CONS_OBJ` | string | Conservation-objective field from the source. |
| `INLND_WTRS` | string | Inland-waters classification or flag. |
| `OECM_ASMT` | string | OECM assessment status. |
| `GIS_M_AREA` | real | GIS-calculated marine area in square kilometers; null for point rows where not supplied. |
| `GIS_AREA` | real | GIS-calculated total area in square kilometers; null for point rows where not supplied. |

## Update notes

Updated by `python -m ingestion.wdpa_monthly.run`, deployed as the
`wdpa-monthly` Cloud Run Job and scheduled for `0 9 1-10 * *` UTC.

The 2026-05-04 PMTiles artifact was rebuilt from the published FGB using auto
maxzoom selection. The mixed point/vector FGB profile resolved to maxzoom 12
with point retention. The rebuilt PMTiles SHA-256 is
`02591002e5ef5c0e50829972de6ae037b41d8bcb5b8fa49ae5a0dd6a7f6c53d2`.

## Known caveats

The canonical FGB intentionally keeps mixed point and polygon geometries. Some
desktop GIS and map clients handle mixed-geometry layers less gracefully than
single-geometry layers.
