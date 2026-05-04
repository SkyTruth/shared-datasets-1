---
schema_version: 1
asset_slug: global-coral-reefs
title: Global Distribution of Coral Reefs
category: 500-conservation-ecosystems
subcategory: 530-habitat-condition
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/global-coral-reefs.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: UNEP-WCMC, WorldFish Centre, WRI, and TNC Global Distribution of Coral Reefs v4.1
license: UNEP-WCMC General Data License (excluding WDPA); contextual internal use with citation only; no redistribution without
  permission
notes: Converted local polygon and point shapefiles to FGB plus PMTiles with both layers; release 2026-04-29; polygon fgb
  sha256 387d9999983a2cf9916ce3d7d496c45319ed8196eccfe9b3d2e8622b82756869; point fgb sha256 a81fcf8264e397fe08cd2655ad580cf36da6fea6010e830b97947fb091bb8ccf;
  pmtiles sha256 02321121660212e551732b658c603be1f3754f1b54abd2056fd11353bf670612
files:
- path: latest/global-coral-reefs.fgb
  format: fgb
  role: canonical
  purpose: Canonical polygon FlatGeobuf layer
- path: latest/global-coral-reefs-points.fgb
  format: fgb
  role: companion
  purpose: Companion point FlatGeobuf layer from the source package
- path: latest/global-coral-reefs.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles with polygon and point layers
- path: releases/YYYY-MM-DD/global-coral-reefs.fgb
  format: fgb
  role: release
  purpose: Dated canonical polygon release
- path: releases/YYYY-MM-DD/global-coral-reefs-points.fgb
  format: fgb
  role: release
  purpose: Dated companion point release
- path: releases/YYYY-MM-DD/global-coral-reefs.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
---

# Global Distribution of Coral Reefs

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/global-coral-reefs.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** UNEP-WCMC, WorldFish Centre, WRI, and TNC Global Distribution of Coral Reefs v4.1
- **License / terms:** UNEP-WCMC General Data License (excluding WDPA); contextual internal use with citation only; no redistribution without permission
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the UNEP-WCMC Global Distribution of Coral Reefs dataset, version 4.1. It maps warm-water coral reef distribution in tropical and subtropical regions and was compiled from multiple sources including the Millennium Coral Reef Mapping Project and the World Atlas of Coral Reefs.

The canonical analytical file is the polygon layer. A companion point layer is retained because the source package includes both geometry types. The PMTiles file contains both layers for contextual map display.

## When to use it

- Use this as a contextual map layer for coral reef extent and location.
- Cite the source when displaying or using the layer.
- Do not treat this as an externally redistributable dataset unless UNEP-WCMC permission is confirmed.
- Do not use polygon area totals without checking for overlaps and source caveats.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/global-coral-reefs.fgb` | `fgb` | `canonical` | Canonical polygon FlatGeobuf layer |
| `latest/global-coral-reefs-points.fgb` | `fgb` | `companion` | Companion point FlatGeobuf layer from the source package |
| `latest/global-coral-reefs.pmtiles` | `pmtiles` | `companion` | Web map tiles with polygon and point layers |
| `releases/YYYY-MM-DD/global-coral-reefs.fgb` | `fgb` | `release` | Dated canonical polygon release |
| `releases/YYYY-MM-DD/global-coral-reefs-points.fgb` | `fgb` | `release` | Dated companion point release |
| `releases/YYYY-MM-DD/global-coral-reefs.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
<!-- END GENERATED files-table -->

## Schema notes

This is a format conversion from the local source shapefiles `WCMC008_CoralReef2018_Py_v4_1.shp` and `WCMC008_CoralReef2018_Pt_v4_1.shp` to FlatGeobuf and PMTiles. Source field names and values are preserved. The polygon output was promoted to multipolygon geometry and repaired with GDAL `-makevalid`.

The local source package contains 17,504 polygon features and 925 point features. The source metadata states that the dataset was collected from 1954-2009, version 4.1 was released in March 2021, and corrections are made on an ad hoc basis.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `LAYER_NAME` | string | Source layer or feature grouping code. |
| `METADATA_I` | real | Metadata ID linking records to the source metadata table. |
| `ORIG_NAME` | string | Original feature name as provided by the data provider. |
| `FAMILY` | string | Scientific family name, when available. |
| `GENUS` | string | Scientific genus name, when available. |
| `SPECIES` | string | Scientific species name, when available. |
| `DATA_TYPE` | string | Whether data were obtained by remote sensing, field survey, or another method. |
| `START_DATE` | string | Start date of data collection or survey, when available. |
| `END_DATE` | string | End date of data collection or survey, when available. |
| `DATE_TYPE` | string | Code describing date accuracy for `START_DATE` and `END_DATE`. |
| `VERIF` | string | Government or expert verification status. |
| `NAME` | string | English feature name as provided by the data provider. |
| `LOC_DEF` | string | Local definition of the feature as provided by the data provider. |
| `SURVEY_MET` | string | Data gathering approach. |
| `GIS_AREA_K` | real | GIS-calculated area in square kilometers. |
| `Shape_Leng` | real | Source polygon length field. Polygon layer only. |
| `Shape_Area` | real | Source polygon area field. Polygon layer only. |
| `REP_AREA_K` | string | Reported area in square kilometers, when available. |

## Update notes

Manually converted from `/Users/jonathanraphael/Desktop/14_001_WCMC008_CoralReefs2018_v4_1` on 2026-04-29 using GDAL, Tippecanoe, and PMTiles tooling.

Requested citation:

UNEP-WCMC, WorldFish Centre, WRI, TNC (2021). Global distribution of coral reefs, compiled from multiple sources including the Millennium Coral Reef Mapping Project. Version 4.1, updated by UNEP-WCMC. Cambridge (UK): UN Environment Programme World Conservation Monitoring Centre. Data DOI: https://doi.org/10.34892/t2wk-5t34

## Known Caveats

The source metadata notes that the dataset was compiled from multiple sources with varying scale and quality, has not undergone external review, may include overlapping polygons, and may require dissolve operations before surface-area calculations.

The source license restricts commercial use, sublicensing, and redistribution. This asset is intended for internal contextual display with citation, not third-party data distribution.
