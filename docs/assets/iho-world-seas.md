---
schema_version: 1
asset_slug: iho-world-seas
title: IHO World Seas
category: 100-geographic-reference
subcategory: 120-marine-boundaries
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/iho-world-seas.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: Marine Regions World Seas IHO v3
license: See source terms
citation: Flanders Marine Institute (2018). IHO Sea Areas, version 3. Available online at https://www.marineregions.org/.
  https://doi.org/10.14284/323.
notes: Initial upload from iho-mr_World_Seas_IHO_v3.fgb; release 2026-04-29; sha256 1fb5a7988b686e1076fe0a21d75d5df32fa28dfcd100dbe3db3aaaf8c9493ba6;
  PMTiles sha256 0d0985cf36ad244215f80bf198dcc43eaef1767bdd9e580f07062391d273f51b; PMTiles rebuilt 2026-05-04 at maxzoom 12
  from sampled FGB geometry detail with local tile and browser QA
row_count: 101
data_profile:
  field_count: 10
  identity_candidates:
  - field: ID
    distinct_values: 101
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique
  - field: MRGID
    distinct_values: 101
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique
files:
- path: latest/iho-world-seas.fgb
  format: fgb
  role: canonical
  purpose: Canonical World Seas polygon dataset
- path: latest/iho-world-seas.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same source layer
- path: releases/2026-04-29/iho-world-seas.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/2026-04-29/iho-world-seas.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
---

# IHO World Seas

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/iho-world-seas.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** Marine Regions World Seas IHO v3
- **License / terms:** See source terms
- **Citation:** Flanders Marine Institute (2018). IHO Sea Areas, version 3. Available online at https://www.marineregions.org/. https://doi.org/10.14284/323.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the Marine Regions World Seas IHO v3 polygon dataset. It is
a reusable named-seas reference layer for contextual mapping, source filtering,
and coarse marine-region grouping.

The canonical FlatGeobuf preserves the source geometry and attributes. The
PMTiles artifact is generated from the same source layer for web-map display.

## When to use it

- Use this for named sea and ocean areas in contextual maps or spatial joins.
- Use the FlatGeobuf file for analysis and the PMTiles file for display.
- Do not treat this as an authoritative legal maritime boundary dataset.
- Do not use the PMTiles artifact as the analytical source.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/iho-world-seas.fgb` | `fgb` | `canonical` | Canonical World Seas polygon dataset |
| `latest/iho-world-seas.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same source layer |
| `releases/2026-04-29/iho-world-seas.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/2026-04-29/iho-world-seas.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
<!-- END GENERATED files-table -->

## Schema notes

This is a direct format conversion from the Marine Regions source layer. Source
field names and values are preserved in the FlatGeobuf output.

## Properties / columns

Definitions are inherited from the Marine Regions World Seas IHO v3 source and
need source confirmation. Use the source documentation for authoritative field
definitions.

| Name | Type | Description |
|---|---|---|
| `NAME` | string | Source sea or ocean name. |
| `ID` | string | Source identifier for the named sea feature; exact code semantics need source confirmation. |
| `Longitude` | real | Source-provided representative longitude in decimal degrees. |
| `Latitude` | real | Source-provided representative latitude in decimal degrees. |
| `min_X` | real | Source-provided minimum longitude for the feature envelope. |
| `min_Y` | real | Source-provided minimum latitude for the feature envelope. |
| `max_X` | real | Source-provided maximum longitude for the feature envelope. |
| `max_Y` | real | Source-provided maximum latitude for the feature envelope. |
| `area` | integer64 | Source-provided area value; units need source confirmation. |
| `MRGID` | integer64 | Marine Regions Gazetteer identifier for the feature. |

## Update notes

Manually converted from `iho-mr_World_Seas_IHO_v3.fgb` and published as a
2026-04-29 release.

PMTiles were rebuilt on 2026-05-04 from the published FGB using auto maxzoom
selection. The sampled FGB profile resolved to maxzoom 12 from representative
segment lengths. The rebuilt PMTiles SHA-256 is
`0d0985cf36ad244215f80bf198dcc43eaef1767bdd9e580f07062391d273f51b`.

## Known caveats

Marine region names and extents are useful for contextual grouping, but they are
not a substitute for jurisdictional boundaries, EEZs, or legally authoritative
maritime limits.
