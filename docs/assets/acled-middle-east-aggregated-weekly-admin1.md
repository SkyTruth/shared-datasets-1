---
schema_version: 1
asset_slug: acled-middle-east-aggregated-weekly-admin1
title: ACLED Middle East Weekly Admin 1 Aggregated Events
category: 400-events-observations
subcategory: 430-alerts-notices
status: active
access_tier: private
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/acled-middle-east-aggregated-weekly-admin1.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: ACLED aggregated data for Middle East
source_url: https://acleddata.com/aggregated/aggregated-data-middle-east
license: ACLED Terms and Conditions; private internal shared-datasets use confirmed by SkyTruth authorization on 2026-05-08
citation: ACLED (Armed Conflict Location & Event Data), aggregated data for Middle East, accessed 2026-05-12, up to week of
  2026-05-02, https://acleddata.com/aggregated/aggregated-data-middle-east.
license_flags:
- restricted-redistribution
- private-internal-use
- source-authorization-confirmed-2026-05-08
notes: Manual private snapshot from ACLED weekly aggregated Middle East XLSX export; release 2026-05-02; 147190 point features;
  fgb sha256 b26a3d53c1d0dbe4f3f16715002b7b3751bafc4321c7b435170e581c96ebac79; pmtiles sha256 ab55ed31434042157036006e9524d30327f100e226361d654d0bc23a8f131464;
  PMTiles maxzoom 12 with Tippecanoe no feature limit/no tile size limit/drop-rate 1 so zoom 0 retains all 147190 points.
admission:
  intended_consumers:
  - SkyTruth internal regional conflict, risk, and exposure analyses
  shared_rationale: Provides a reusable, curated private snapshot of ACLED weekly Admin 1 conflict and demonstration aggregates
    for the Middle East, avoiding repeated local spreadsheet handling across internal projects.
  steward: SkyTruth
  update_expectations: Manual refreshes from ACLED weekly aggregated exports when internal consumers need a newer snapshot.
  estimated_published_size_gb: 0.17
  alternatives_considered: Project-specific scratch storage, direct ACLED download by each consumer, and keeping the XLSX
    only. Shared-datasets is preferred for reviewed private reuse, documented schema, and canonical geospatial formats.
  deprecation_policy: Keep dated releases readable; mark deprecated or superseded if ACLED changes terms, schema, geography,
    or a scheduled ingestion replaces this manual snapshot.
bounds:
- 26.6957
- 4.769
- 62.9761
- 41.7313
geometry_type: MultiPoint
row_count: 147190
pmtiles_detail_hint: detailed
files:
- path: latest/acled-middle-east-aggregated-weekly-admin1.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 Admin 1 centroid point dataset
- path: latest/acled-middle-east-aggregated-weekly-admin1.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same point features
- path: releases/2026-05-02/acled-middle-east-aggregated-weekly-admin1.fgb
  format: fgb
  role: release
  purpose: Dated canonical release for the source export up to week of 2026-05-02
- path: releases/2026-05-02/acled-middle-east-aggregated-weekly-admin1.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release for the source export up to week of 2026-05-02
---

# ACLED Middle East Weekly Admin 1 Aggregated Events

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** private
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/acled-middle-east-aggregated-weekly-admin1.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** ACLED aggregated data for Middle East
- **License / terms:** ACLED Terms and Conditions; private internal shared-datasets use confirmed by SkyTruth authorization on 2026-05-08
- **Citation:** ACLED (Armed Conflict Location & Event Data), aggregated data for Middle East, accessed 2026-05-12, up to week of 2026-05-02, https://acleddata.com/aggregated/aggregated-data-middle-east.
<!-- END GENERATED asset-summary -->

## What this is

This private asset is a geospatial conversion of ACLED's Middle East aggregated data export. It contains weekly event, fatality, and population exposure counts grouped by ACLED region, country or territory, first-order administrative division, disorder type, event type, and sub-event type.

The source workbook is named `Middle-East_aggregated_data_up_to_week_of-2026-05-02_0.xlsx`. It covers weekly records from 2014-12-27 through 2026-05-02. Geometry is generated from ACLED's administrative centroid longitude and latitude fields so consumers can map the aggregated rows while retaining the original centroid coordinate attributes.

## When to use it

- Use this for private internal analysis of ACLED weekly Admin 1 event, fatality, and exposure aggregates in the Middle East.
- Use the `fgb` file as the analytical source and the `pmtiles` file for map preview or lightweight visual inspection.
- Use `week`, `country`, `admin1`, `disorder_type`, `event_type`, and `sub_event_type` as the primary grouping fields.
- Do not use this as raw event-level ACLED data; rows are already aggregated by week, Admin 1, and event classification.
- Do not redistribute this dataset or make it public unless ACLED terms and SkyTruth authorization explicitly allow that use.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/acled-middle-east-aggregated-weekly-admin1.fgb` | `fgb` | `canonical` | Canonical WGS84 Admin 1 centroid point dataset |
| `latest/acled-middle-east-aggregated-weekly-admin1.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same point features |
| `releases/2026-05-02/acled-middle-east-aggregated-weekly-admin1.fgb` | `fgb` | `release` | Dated canonical release for the source export up to week of 2026-05-02 |
| `releases/2026-05-02/acled-middle-east-aggregated-weekly-admin1.pmtiles` | `pmtiles` | `release` | Dated map-tile release for the source export up to week of 2026-05-02 |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 point geometry derived from `centroid_longitude` and `centroid_latitude`. These coordinates are administrative centroids for mapping aggregated Admin 1 records, not precise event locations.

The source workbook had one visible sheet with 147,190 data rows and 13 source columns. During conversion, source field names were normalized to lowercase snake case, and source `ID` was stored as `admin1_id` for consistency with the existing ACLED aggregate asset. A constant `source` column and companion source metadata columns were added to satisfy ACLED attribution expectations for generated data files.

`population_exposure` is ACLED's best estimate of population exposed to events based on proximity. ACLED's aggregated data guidance says this value should not be summed for analysis. The source workbook has 30,723 rows with blank `population_exposure`.

The PMTiles artifact is generated with Tippecanoe from the same point features, with zooms 0 through 12. It uses `--no-feature-limit`, `--no-tile-size-limit`, and `--drop-rate=1` so low-zoom tiles retain dense point content for visual inspection. The canonical FGB remains the analytical source.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `week` | datetime | Date of the Saturday marking the start of the aggregated week, covering Saturday through Friday. |
| `region` | string | ACLED broad geographic grouping. This export includes the Middle East regional grouping. |
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
| `source_version` | string | Source freshness note from the file name: up to week of 2026-05-02. |

## Update notes

Manually converted on 2026-05-12 from the supplied ACLED XLSX export after SkyTruth confirmed private shared-datasets upload authorization for the ACLED data. The source export URL was inferred from ACLED's aggregated Middle East data page.

Output summary:

- Source XLSX SHA-256: `1d1262c48cd42ba84ed83d77b50571fc3a4d5113c052169905aaa09cc36255f5`
- Source data rows: 147,190
- Published point features: 147,190
- Week range: 2014-12-27 to 2026-05-02
- Countries and territories: 15
- Total aggregated events: 602,174
- Total reported fatalities: 550,542
- Rows with blank `population_exposure`: 30,723
- Extent: 26.695700, 4.769000 to 62.976100, 41.731300
- FGB SHA-256: `b26a3d53c1d0dbe4f3f16715002b7b3751bafc4321c7b435170e581c96ebac79`
- PMTiles SHA-256: `ab55ed31434042157036006e9524d30327f100e226361d654d0bc23a8f131464`
- PMTiles zoom 0 decoded point features: 147,190
- Toolchain: GDAL 3.6.2 and Tippecanoe 2.79.0. PMTiles CLI was unavailable locally; validation used successful Tippecanoe PMTiles generation plus decoded zoom 0 feature-count and property checks.

## Known caveats

This is a private licensed-data snapshot. Do not make the asset public, redistribute it outside authorized SkyTruth use, or expose it through public catalog views without a separate terms review.

ACLED's aggregated rows are simplified snapshots of global trends in political violence and demonstrations. They are not raw event-level records and should not be interpreted as precise event locations because the geometry is an Admin 1 centroid.

ACLED updates aggregated exports weekly. This manual shared-datasets asset will become stale unless refreshed from a newer authorized export.
