---
asset_slug: "gfw-fixed-infrastructure"
title: "Global Fishing Watch SAR Fixed Infrastructure"
category: "300-infrastructure-industrial"
subcategory: "330-offshore-platforms"
status: "active"
owner: "SkyTruth"
update_cadence: "manual"
canonical_format: "fgb"
last_updated: "2026-04-30"
source: "Global Fishing Watch Datasets API public-fixed-infrastructure-filtered:latest"
license: "Global Fishing Watch API non-commercial use only and subject to Global Fishing Watch Terms of Use"
---

# Global Fishing Watch SAR Fixed Infrastructure

- **Status:** active
- **Owner:** SkyTruth
- **Last updated:** 2026-04-30
- **Update cadence:** manual
- **Canonical file:** `latest/gfw-fixed-infrastructure.fgb`
- **Source:** [Global Fishing Watch Datasets API](https://globalfishingwatch.org/our-apis/documentation), dataset `public-fixed-infrastructure-filtered:latest`
- **License / terms:** Global Fishing Watch APIs are available for non-commercial purposes and are subject to Global Fishing Watch Terms of Use. Attribute Global Fishing Watch in downstream uses.

## What this is

This asset contains a 2026-04-30 snapshot of Global Fishing Watch SAR fixed offshore infrastructure points from the filtered Datasets API layer. The source documentation describes the layer as offshore infrastructure detected from satellite imagery and classified with deep learning, with labels for oil, wind, and unknown structures.

The filtered API layer matches the Global Fishing Watch public map rather than the noisier Data Download Portal export. According to the source documentation, it excludes noise-labeled detections, relabels Lake Maracaibo as oil, keeps structures detected for at least three months with predicted noise probability below 0.3, and removes additional noisy detections from selected Chile, Canada, and Norway regions.

## When to use it

- Use this as a reusable global point layer for offshore fixed infrastructure locations.
- Use `label` and `label_confidence` to distinguish likely oil, wind, and unknown structure classes.
- Use the FlatGeobuf file for analysis and the PMTiles file for web-map display.
- Do not use the PMTiles artifact as the analytical source.
- Do not use this for commercial purposes unless permitted by Global Fishing Watch terms.

## Files

| File | Purpose |
|---|---|
| `latest/gfw-fixed-infrastructure.fgb` | Canonical point dataset in WGS84 |
| `latest/gfw-fixed-infrastructure.pmtiles` | Web map tiles generated from the same point features |
| `releases/2026-04-30/gfw-fixed-infrastructure.fgb` | Dated canonical release |
| `releases/2026-04-30/gfw-fixed-infrastructure.pmtiles` | Dated map-tile release |
| `sources/gfw_infra_2026-04-30.csv` | Original local CSV export; noncanonical because it stores point geometry as `lon` and `lat` columns |

## Schema notes

Geometry is generated from the source `lon` and `lat` fields as WGS84 point geometry. Source fields are preserved as attributes in the FlatGeobuf output. The original CSV includes an unnamed leading index column, which is omitted from the geospatial outputs.

`structure_start_date` and `structure_end_date` are source-provided epoch timestamps in milliseconds. Empty source `structure_end_date` values are preserved as null values in the geospatial outputs.

The PMTiles artifact is generated with Tippecanoe from the same point features, with zooms 0 through 8. Lower zooms are generalized for display and should not be used as the analytical source.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `structure_id` | integer | Unique identifier for all detections of the same structure. |
| `lon` | real | Source longitude in decimal degrees. |
| `lat` | real | Source latitude in decimal degrees. |
| `label` | string | Predicted structure type. Source-documented values are `oil`, `wind`, and `unknown`. |
| `label_confidence` | string | Label confidence. Source-documented values are `high`, `medium`, and `low`. |
| `structure_start_date` | integer64 | First date the structure was detected, as epoch milliseconds. |
| `structure_end_date` | integer64 nullable | Last date the structure was detected, as epoch milliseconds; null where the source export has no end date. |

## Update notes

Manually converted from `/Users/jonathanraphael/Desktop/gfw_infra_2026-04-30` on 2026-04-30 using Python CSV processing, GDAL, Tippecanoe, and PMTiles tooling.

Output summary:

- Source rows: 57,681
- Published point features: 57,681
- Invalid coordinate rows omitted: 0
- Label counts: oil 31,970; wind 17,393; unknown 8,318
- Label confidence counts: high 46,686; medium 5,404; low 5,591
- Structure start date range: 2017-01-01 to 2025-12-01 UTC
- Structure end date range where present: 2017-01-01 to 2025-11-01 UTC
- Empty source `structure_end_date` rows: 23,442
- FGB SHA-256: `159af982d72f464091c06e68de6abe054a5c07ae05ff4731c8cb041979fb3447`
- PMTiles SHA-256: `85d5faf9d893b15dbc9abe30ecc548154da97a6f396dede2c6f1408137b0fbfd`
- Source CSV SHA-256: `07d8d7464c7c2d7410926d2a29c24eb2d2aa2993c2b576a138ce0c57111cf1a9`

## Known caveats

This is a manual snapshot of the filtered API/map layer, not a scheduled ingestion job. It should be refreshed manually or promoted to a scheduled ingestion job if consumers require current data.

The dataset is a model-derived offshore infrastructure layer. It should not be treated as an authoritative legal or permitting dataset for offshore platforms, wind farms, or industrial installations.
