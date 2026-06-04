---
schema_version: 1
asset_slug: gfw-anchorages
title: Global Fishing Watch Anchorages
category: 600-maritime-ocean
subcategory: 640-ocean-activity
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/gfw-anchorages.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: Global Fishing Watch Anchorages Version 2
license: Copyright Global Fishing Watch; non-commercial use only under CC BY-NC 4.0 and subject to Global Fishing Watch Terms
  of Use
citation: Global Fishing Watch (2026). Global Anchorages dataset, Version 2. https://globalfishingwatch.org/datasets-and-code-anchorages/.
notes: Initial upload from named_anchorages_v2_pipe_v3_202601.csv; release 2026-02-02; source rows 166497; published rows
  166496; omitted invalid lon row s2id 8efe7543; fgb sha256 9698918d2fea828ae8bbe00feab3c76364b26e6153d73c357880087957b09351;
  pmtiles sha256 54d8a622cf6f426aa78dc9cbffae89a57212f5c428e6fd2218e414718f8e8cdd; PMTiles rebuilt 2026-05-04 at maxzoom 12
  with all-point retention verified at zoom 0 for all 166496 points; future rebuilds must use the repo-standard GDAL MBTiles
  to PMTiles conversion path
row_count: 166496
data_profile:
  field_count: 10
  identity_candidates:
  - field: s2id
    distinct_values: 166496
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique
files:
- path: latest/gfw-anchorages.fgb
  format: fgb
  role: canonical
  purpose: Canonical point dataset in WGS84
- path: latest/gfw-anchorages.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same point dataset
- path: releases/2026-02-02/gfw-anchorages.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/2026-02-02/gfw-anchorages.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
---

# Global Fishing Watch Anchorages

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/gfw-anchorages.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** Global Fishing Watch Anchorages Version 2
- **License / terms:** Copyright Global Fishing Watch; non-commercial use only under CC BY-NC 4.0 and subject to Global Fishing Watch Terms of Use
- **Citation:** Global Fishing Watch (2026). Global Anchorages dataset, Version 2. https://globalfishingwatch.org/datasets-and-code-anchorages/.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the Global Fishing Watch Anchorages dataset, a global point database of anchorage locations where AIS-broadcasting vessels congregate. Global Fishing Watch identifies anchorages using S2 level 14 cells and stationary vessel behavior, then assigns labels and groups nearby anchorages into broader ports where applicable.

The source CSV contains 166,497 rows. This shared asset publishes 166,496 valid point features after omitting one source row with an invalid longitude value.

## When to use it

- Use this as a reusable global reference layer for anchorage locations and broader port labels.
- Use `label` for the broader port grouping and `sublabel` for more detailed anchorage labels where available.
- Do not use this as a legal port boundary dataset, berth-level authority, port visit event table, or voyage table.
- Do not use this for commercial purposes unless allowed by Global Fishing Watch terms.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/gfw-anchorages.fgb` | `fgb` | `canonical` | Canonical point dataset in WGS84 |
| `latest/gfw-anchorages.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same point dataset |
| `releases/2026-02-02/gfw-anchorages.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/2026-02-02/gfw-anchorages.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is generated from the source `lon` and `lat` fields as WGS84 point geometry. Source columns are preserved as attributes. `distance_from_shore_m` and `drift_radius` are numeric where provided; `dock` is preserved as the source `true`, `false`, or empty string value.

One source row is omitted from the geospatial outputs because its longitude is outside valid EPSG:4326 bounds: `s2id=8efe7543`, `lat=11.84060637`, `lon=1001`, `label=POINTE NOIRE`.

The PMTiles artifact is derived from the same filtered point features, with zooms 0 through 12 and zoom 0 retention verified against the published point count. Future rebuilds must use GDAL MBTiles output converted with `pmtiles convert`. The canonical FGB remains the analytical source.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `s2id` | string | Unique anchorage identifier from the source S2 cell. |
| `lat` | real | Source latitude in decimal degrees. |
| `lon` | real | Source longitude in decimal degrees. |
| `label` | string | Broader port label. |
| `sublabel` | string | Detailed anchorage label where available. |
| `label_source` | string | Source of the anchorage label, such as AIS top destination, manual anchorage overrides, World Port Index, GeoNames, or regional datasets. |
| `iso3` | string | ISO3 code of the EEZ containing the anchorage. |
| `distance_from_shore_m` | real | Source-provided distance from shore in meters where available. |
| `drift_radius` | real | Source-provided average drift radius where available. |
| `dock` | string | Source-provided dock flag: `true`, `false`, or empty. |

## Update notes

Manually converted from `named_anchorages_v2_pipe_v3_202601.csv` on 2026-04-30 using GDAL and PMTiles tooling. Source release date is tracked as 2026-02-02 based on the Global Fishing Watch Data Download Portal last update date.

The PMTiles artifact was rebuilt on 2026-05-04 from the canonical FGB using auto maxzoom selection. The point-only FGB profile resolves to maxzoom 12. The rebuilt zoom 0 tile decodes to all 166,496 published points. Future rebuilds must use GDAL MBTiles output converted with `pmtiles convert`.

Output summary:

- Source rows: 166,497
- Published point features: 166,496
- Omitted invalid coordinate rows: 1
- FGB SHA-256: `9698918d2fea828ae8bbe00feab3c76364b26e6153d73c357880087957b09351`
- PMTiles SHA-256: `54d8a622cf6f426aa78dc9cbffae89a57212f5c428e6fd2218e414718f8e8cdd`

## Known caveats

Global Fishing Watch updates the source dataset periodically with new anchorage locations, updated names, and AIS data pipeline changes. This asset is a manual shared-datasets snapshot, not a scheduled ingestion job.

The dataset represents anchorage locations inferred or curated by Global Fishing Watch. It should not be treated as authoritative legal infrastructure boundaries or complete port operations metadata.
