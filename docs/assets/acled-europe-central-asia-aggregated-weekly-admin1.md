---
schema_version: 1
asset_slug: acled-europe-central-asia-aggregated-weekly-admin1
title: ACLED Europe and Central Asia Weekly Admin 1 Aggregated Events
category: 400-events-observations
subcategory: 430-alerts-notices
status: active
access_tier: private
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/acled-europe-central-asia-aggregated-weekly-admin1.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: ACLED aggregated data for Europe and Central Asia
source_url: https://acleddata.com/aggregated/aggregated-data-europe-and-central-asia
license: ACLED Terms and Conditions; private internal shared-datasets use confirmed by SkyTruth authorization on 2026-05-08
citation: ACLED (Armed Conflict Location & Event Data), aggregated data for Europe and Central Asia, accessed 2026-05-08,
  up to week of 2026-04-25, https://acleddata.com/aggregated/aggregated-data-europe-and-central-asia.
license_flags:
- restricted-redistribution
- private-internal-use
- source-authorization-confirmed-2026-05-08
notes: Manual private snapshot from ACLED weekly aggregated Europe and Central Asia XLSX export; release 2026-04-25; 120245
  point features; fgb sha256 44eb1a9f3fc0e20d41dd3c1918b32907c1c3b066dd178a8d5ec59f2c45b66f47; pmtiles sha256 b4ddd762e69578c87dcd9e97c5916b2383755fa85e6dc38f7726fd968c88f7d2;
  PMTiles maxzoom 12 with all-point retention verified at zoom 0; future rebuilds must use the repo-standard GeoJSONSeq to
  Tippecanoe MBTiles to PMTiles conversion path.
admission:
  intended_consumers:
  - SkyTruth internal regional conflict, risk, and exposure analyses
  shared_rationale: Provides a reusable, curated private snapshot of ACLED weekly Admin 1 conflict and demonstration aggregates
    for Europe and Central Asia, avoiding repeated local spreadsheet handling across internal projects.
  steward: SkyTruth
  update_expectations: Manual refreshes from ACLED weekly aggregated exports when internal consumers need a newer snapshot.
  estimated_published_size_gb: 0.08
  alternatives_considered: Project-specific scratch storage, direct ACLED download by each consumer, and keeping the XLSX
    only. Shared-datasets is preferred for reviewed private reuse, documented schema, and canonical geospatial formats.
  deprecation_policy: Keep dated releases readable; mark deprecated or superseded if ACLED changes terms, schema, geography,
    or a scheduled ingestion replaces this manual snapshot.
bounds:
- -51.0801
- 28.2931
- 169.514
- 78.8154
geometry_type: MultiPoint
row_count: 120245
pmtiles_detail_hint: detailed
files:
- path: latest/acled-europe-central-asia-aggregated-weekly-admin1.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 Admin 1 centroid point dataset
- path: latest/acled-europe-central-asia-aggregated-weekly-admin1.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same point features
- path: releases/2026-04-25/acled-europe-central-asia-aggregated-weekly-admin1.fgb
  format: fgb
  role: release
  purpose: Dated canonical release for the source export up to week of 2026-04-25
- path: releases/2026-04-25/acled-europe-central-asia-aggregated-weekly-admin1.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release for the source export up to week of 2026-04-25
---

# ACLED Europe and Central Asia Weekly Admin 1 Aggregated Events

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** private
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/acled-europe-central-asia-aggregated-weekly-admin1.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** ACLED aggregated data for Europe and Central Asia
- **License / terms:** ACLED Terms and Conditions; private internal shared-datasets use confirmed by SkyTruth authorization on 2026-05-08
- **Citation:** ACLED (Armed Conflict Location & Event Data), aggregated data for Europe and Central Asia, accessed 2026-05-08, up to week of 2026-04-25, https://acleddata.com/aggregated/aggregated-data-europe-and-central-asia.
<!-- END GENERATED asset-summary -->

## What this is

This private asset is a geospatial conversion of ACLED's Europe and Central Asia aggregated data export. It contains weekly event, fatality, and population exposure counts grouped by ACLED region, country or territory, first-order administrative division, disorder type, event type, and sub-event type.

The source workbook is named `Europe-Central-Asia_aggregated_data_up_to_week_of-2026-04-25.xlsx`. It covers weekly records from 2017-12-30 through 2026-04-25. Geometry is generated from ACLED's administrative centroid longitude and latitude fields so consumers can map the aggregated rows while retaining the original centroid coordinate attributes.

## When to use it

- Use this for private internal analysis of ACLED weekly Admin 1 event, fatality, and exposure aggregates in Europe and Central Asia.
- Use the `fgb` file as the analytical source and the `pmtiles` file for map preview or lightweight visual inspection.
- Use `week`, `country`, `admin1`, `disorder_type`, `event_type`, and `sub_event_type` as the primary grouping fields.
- Do not use this as raw event-level ACLED data; rows are already aggregated by week, Admin 1, and event classification.
- Do not redistribute this dataset or make it public unless ACLED terms and SkyTruth authorization explicitly allow that use.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/acled-europe-central-asia-aggregated-weekly-admin1.fgb` | `fgb` | `canonical` | Canonical WGS84 Admin 1 centroid point dataset |
| `latest/acled-europe-central-asia-aggregated-weekly-admin1.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same point features |
| `releases/2026-04-25/acled-europe-central-asia-aggregated-weekly-admin1.fgb` | `fgb` | `release` | Dated canonical release for the source export up to week of 2026-04-25 |
| `releases/2026-04-25/acled-europe-central-asia-aggregated-weekly-admin1.pmtiles` | `pmtiles` | `release` | Dated map-tile release for the source export up to week of 2026-04-25 |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 point geometry derived from `centroid_longitude` and `centroid_latitude`. These coordinates are administrative centroids for mapping aggregated Admin 1 records, not precise event locations.

The source workbook had one visible sheet with 120,245 data rows and 13 source columns. During conversion, source field names were normalized to lowercase snake case. A constant `source` column and companion source metadata columns were added to satisfy ACLED attribution expectations for generated data files.

`population_exposure` is ACLED's best estimate of population exposed to events based on proximity. ACLED's aggregated data guidance says this value should not be summed for analysis.

The PMTiles artifact is derived from the same point features, with zooms 0 through 12 and zoom 0 retention verified against the published point count. Future rebuilds must export WGS84 GeoJSONSeq from the FGB, build Tippecanoe MBTiles, and convert with `pmtiles convert`. The canonical FGB remains the analytical source.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `week` | datetime | Date of the Saturday marking the start of the aggregated week, covering Saturday through Friday. |
| `region` | string | ACLED broad geographic grouping. This export includes Europe and Caucasus and Central Asia regional groupings. |
| `country` | string | Country or territory. |
| `admin1` | string | First-order administrative division. |
| `event_type` | string | ACLED event type, such as Battles, Explosions/Remote violence, Protests, Riots, Strategic developments, or Violence against civilians. |
| `sub_event_type` | string | ACLED's more granular event classification. |
| `events` | integer | Total number of discrete events recorded for the week, Admin 1, and sub-event type. |
| `fatalities` | integer | Sum of reported fatalities across the events represented by the row. |
| `population_exposure` | integer | ACLED best estimate of population exposed to the events, based on proximity. Do not sum this field for analysis. |
| `disorder_type` | string | ACLED high-level disorder category: Political violence, Demonstrations, Strategic developments, or combined categories. |
| `admin1_id` | integer | Source administrative unit identifier from ACLED's aggregated export. |
| `centroid_latitude` | real | ACLED Admin 1 centroid latitude in decimal degrees. |
| `centroid_longitude` | real | ACLED Admin 1 centroid longitude in decimal degrees. |
| `source` | string | Per-feature source attribution: ACLED (Armed Conflict Location & Event Data), https://acleddata.com. |
| `source_url` | string | ACLED source export URL used for this snapshot. |
| `source_accessed_date` | datetime | Date this source export was supplied and processed for shared-datasets. |
| `source_version` | string | Source freshness note from the file name: up to week of 2026-04-25. |

## Update notes

Manually converted on 2026-05-08 from the supplied ACLED XLSX export after SkyTruth confirmed private shared-datasets upload authorization for the ACLED data. The source export URL was provided as `https://acleddata.com/aggregated/aggregated-data-europe-and-central-asia`.

Output summary:

- Source XLSX SHA-256: `411b8ac12cc4139e7e53bea096c7e5da9ffc748fd27745e0f15896b893430ccb`
- Source data rows: 120,245
- Published point features: 120,245
- Week range: 2017-12-30 to 2026-04-25
- Countries and territories: 61
- Total aggregated events: 646,638
- Total reported fatalities: 274,001
- Extent: -51.080100, 28.293100 to 169.514000, 78.815400
- FGB SHA-256: `44eb1a9f3fc0e20d41dd3c1918b32907c1c3b066dd178a8d5ec59f2c45b66f47`
- PMTiles SHA-256: `b4ddd762e69578c87dcd9e97c5916b2383755fa85e6dc38f7726fd968c88f7d2`
- PMTiles zoom 0 decoded point features: 120,245
- PMTiles validation used zoom 0 decode feature-count and property checks. Future rebuilds must export WGS84 GeoJSONSeq from the FGB, build Tippecanoe MBTiles, and convert with `pmtiles convert`.

## Known caveats

This is a private licensed-data snapshot. Do not make the asset public, redistribute it outside authorized SkyTruth use, or expose it through public catalog views without a separate terms review.

ACLED's aggregated rows are simplified snapshots of global trends in political violence and demonstrations. They are not raw event-level records and should not be interpreted as precise event locations because the geometry is an Admin 1 centroid.

ACLED updates aggregated exports weekly. This manual shared-datasets asset will become stale unless refreshed from a newer authorized export.
