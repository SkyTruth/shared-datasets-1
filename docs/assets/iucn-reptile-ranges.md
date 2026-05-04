---
schema_version: 1
asset_slug: iucn-reptile-ranges
title: IUCN Red List Reptile Range Maps
category: 500-conservation-ecosystems
subcategory: 530-habitat-condition
status: active
access_tier: private
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/iucn-reptile-ranges.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: IUCN Red List of Threatened Species spatial data, reptiles group, Red List version 2025-2 / metadata v6.3
license: IUCN Red List Terms and Conditions of Use v3.1; non-commercial conservation, education, scientific analysis, and
  research use only; no reposting or redistribution without prior written permission from IUCN
license_flags:
- non-commercial-only
- no-redistribution-without-permission
notes: Initial private upload from local REPTILES.zip; release 2026-05-03; source zip sha256 68bf0ca138044a91dcfa301eda1ed9eb9c2b19f5b668c8e7ed1f281877ba4bbd;
  source shapefile parts REPTILES_PART1 and REPTILES_PART2 each contained 7026 features; combined output rows 14052; fgb sha256
  4c9020a8b138cdc5247c67ed71b29ef478df2c274aa6f05c1d76d1f49ce66bc2; pmtiles sha256 aabaf0360d767b892c020ccac85eb9ba0df1998280f9b2c40066e28b535db7eb;
  PMTiles generated with Tippecanoe maxzoom 6 and tile-simplify 0.01 for display; canonical FGB preserves source fields and
  adds source_part; raw zip not uploaded because zip is not an approved source/archive format and IUCN terms restrict reposting/redistribution
bounds:
- -179.999
- -54.059447
- 179.999
- 71.18811
geometry_type: MultiPolygon
row_count: 14052
pmtiles_maxzoom: 6
pmtiles_maxzoom_reason: Simplified display artifact for coarse global range-map preview; canonical FGB remains the analytical
  geometry.
files:
- path: latest/iucn-reptile-ranges.fgb
  format: fgb
  role: canonical
  purpose: Canonical FlatGeobuf reptile range polygons
- path: latest/iucn-reptile-ranges.pmtiles
  format: pmtiles
  role: companion
  purpose: Simplified web map tiles for catalog preview
- path: releases/YYYY-MM-DD/iucn-reptile-ranges.fgb
  format: fgb
  role: release
  purpose: Dated canonical FlatGeobuf release
- path: releases/YYYY-MM-DD/iucn-reptile-ranges.pmtiles
  format: pmtiles
  role: release
  purpose: Dated PMTiles release
---

# IUCN Red List Reptile Range Maps

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** private
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/iucn-reptile-ranges.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** IUCN Red List of Threatened Species spatial data, reptiles group, Red List version 2025-2 / metadata v6.3
- **License / terms:** IUCN Red List Terms and Conditions of Use v3.1; non-commercial conservation, education, scientific analysis, and research use only; no reposting or redistribution without prior written permission from IUCN
<!-- END GENERATED asset-summary -->

## What this is

This asset contains global reptile distribution polygons from The IUCN Red List of Threatened Species spatial data package. The source archive is `REPTILES.zip`, with `REPTILES_PART1.shp` and `REPTILES_PART2.shp` split from the source `REPTILES` feature class.

The two shapefile parts were combined into one WGS84 multipolygon FlatGeobuf. The canonical file preserves the source attributes and adds `source_part` to identify which source shapefile part each feature came from. PMTiles are included only as a simplified display artifact for catalog preview.

## When to use it

- Use this for internal conservation, education, scientific analysis, and research workflows that need reptile range context.
- Use the `citation` field and IUCN dataset citation guidance when deriving maps, figures, analyses, or publications from these data.
- Do not use this for commercial or revenue-generating work without prior written permission from IUCN.
- Do not make this asset public, grant broad download access, repost it, or redistribute it without confirming IUCN permission.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/iucn-reptile-ranges.fgb` | `fgb` | `canonical` | Canonical FlatGeobuf reptile range polygons |
| `latest/iucn-reptile-ranges.pmtiles` | `pmtiles` | `companion` | Simplified web map tiles for catalog preview |
| `releases/YYYY-MM-DD/iucn-reptile-ranges.fgb` | `fgb` | `release` | Dated canonical FlatGeobuf release |
| `releases/YYYY-MM-DD/iucn-reptile-ranges.pmtiles` | `pmtiles` | `release` | Dated PMTiles release |
<!-- END GENERATED files-table -->

## Schema notes

The source package contains two polygon shapefiles with the same schema and feature count. Each part contains 7,026 features. The published canonical FlatGeobuf appends both parts into one layer with 14,052 multipolygon features.

Source geometry is WGS84 longitude/latitude. The source metadata warns that `SHAPE_Area` is calculated in unprojected decimal-degree units and is not suitable for meaningful area calculations. Project geometries to an appropriate projected CRS before area analysis.

Numeric code definitions for fields such as `presence`, `origin`, `seasonal`, and `generalisd` should be read from the current IUCN Red List spatial data mapping standards before analysis.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `id_no` | integer | IUCN taxon identifier. |
| `sci_name` | string | Scientific name. |
| `presence` | integer | IUCN presence code; confirm code definitions from IUCN mapping standards before analysis. |
| `origin` | integer | IUCN origin code; confirm code definitions from IUCN mapping standards before analysis. |
| `seasonal` | integer | IUCN seasonal code; confirm code definitions from IUCN mapping standards before analysis. |
| `compiler` | string | Person or organization credited for compiling the range map. |
| `yrcompiled` | integer | Year the range map was compiled. |
| `citation` | string | Citation component for the species map. |
| `subspecies` | string | Subspecies name or qualifier, when present. |
| `subpop` | string | Subpopulation label, when present. |
| `source` | string | Source reference or supporting source note, when present. |
| `island` | string | Island label, when present. |
| `tax_comm` | string | Taxonomic comment, when present. |
| `dist_comm` | string | Distribution comment, when present. |
| `generalisd` | integer | IUCN generalized-range flag/code; confirm code definitions from IUCN mapping standards before analysis. |
| `legend` | string | Human-readable distribution status legend. |
| `kingdom` | string | Taxonomic kingdom. |
| `phylum` | string | Taxonomic phylum. |
| `class` | string | Taxonomic class. |
| `order_` | string | Taxonomic order. |
| `family` | string | Taxonomic family. |
| `genus` | string | Taxonomic genus. |
| `category` | string | IUCN Red List category code. |
| `marine` | string | Whether the taxon is marked marine in the source attributes. |
| `terrestria` | string | Whether the taxon is marked terrestrial in the source attributes. |
| `freshwater` | string | Whether the taxon is marked freshwater in the source attributes. |
| `SHAPE_Leng` | real | Source GIS length field in unprojected coordinate units. |
| `SHAPE_Area` | real | Source GIS area field in unprojected decimal-degree units; not suitable for area totals. |
| `source_part` | string | Source shapefile part: `REPTILES_PART1` or `REPTILES_PART2`. |

## Update notes

Manually converted from local `REPTILES.zip` on 2026-05-03.

Source package details:

- Metadata PDF: `METADATA_for_Digital_Distribution_Maps_of_The_IUCN_Red_List_of_Threatened_Species_v6_3.pdf`
- Terms PDF: `IUCN_Red_List_Terms_and_Conditions_of_Use_v3_1.pdf`
- Source metadata update: December 2022, version 6.3
- Source shapefile creation timestamp: 2025-10-01
- Source archive SHA-256: `68bf0ca138044a91dcfa301eda1ed9eb9c2b19f5b668c8e7ed1f281877ba4bbd`

Conversion details:

- `REPTILES_PART1.shp`: 7,026 polygon features
- `REPTILES_PART2.shp`: 7,026 polygon features
- Combined canonical output: 14,052 multipolygon features
- Temporary combined source was generated with GDAL `ogr2ogr`, `-makevalid`, and a constant `source_part` field.
- Canonical FlatGeobuf was built with GDAL 3.6.2.
- PMTiles were generated with Tippecanoe 2.79.0, `--maximum-zoom 6`, and `--tile-simplify 0.01`.
- PMTiles CLI was not available locally; validation used the repo vector helper plus `tippecanoe-decode`.

## Known caveats

IUCN Red List terms restrict commercial use, reposting, sublicensing, reselling, and redistribution without prior written permission from IUCN. This asset is private and intended for internal workflows that comply with IUCN terms.

The source package is split into two shapefiles with repeated schema and matching feature counts. Downstream users should preserve `source_part` if they need to trace features back to the original shapefile split.

PMTiles are simplified for display and should not be used as analytical geometry. Use the canonical FlatGeobuf for analysis.
