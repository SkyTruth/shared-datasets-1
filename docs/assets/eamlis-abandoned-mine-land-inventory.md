---
asset_slug: "eamlis-abandoned-mine-land-inventory"
title: "OSMRE e-AMLIS Abandoned Mine Land Inventory"
category: "300-infrastructure-industrial"
subcategory: "320-mining"
status: "active"
owner: "SkyTruth"
update_cadence: "manual"
canonical_format: "fgb"
last_updated: "2026-04-30"
source: "U.S. Department of the Interior OSMRE e-AMLIS"
license: "Creative Commons Attribution per EDX listing; cite OSMRE e-AMLIS"
---

# OSMRE e-AMLIS Abandoned Mine Land Inventory

- **Status:** active
- **Owner:** SkyTruth
- **Last updated:** 2026-04-30
- **Update cadence:** manual
- **Canonical file:** `latest/eamlis-abandoned-mine-land-inventory.fgb`
- **Source:** [OSMRE Abandoned Mine Land Inventory System](https://www.osmre.gov/programs/e-amlis)
- **Source field reference:** [OSMRE e-AMLIS Data Dictionary](https://www.osmre.gov/programs/e-amlis-data-dictionary)
- **License / terms:** [EDX lists the e-AMLIS dataset](https://edx.netl.doe.gov/dataset/abandoned-mine-land-inventory-system-e-amlis) under Creative Commons Attribution. Cite the U.S. Department of the Interior Office of Surface Mining Reclamation and Enforcement e-AMLIS source.

## What this is

This asset is a point snapshot of the Office of Surface Mining Reclamation and Enforcement enhanced Abandoned Mine Land Inventory System, or e-AMLIS. OSMRE describes e-AMLIS as an inventory of land and water impacted by legacy coal mining operations, including location, type, extent, and direct reclamation construction cost information for identified abandoned mine land problems.

The published source GeoJSON was supplied as a local file named `eAMLIS_data_accessed_2025-10-24_4326.geojson`. The embedded GeoJSON collection name says `eAMLIS_data_accessed_2024-10-24_4326`; this README preserves that date ambiguity and treats the 2026-04-30 bucket release date as the shared-datasets publication snapshot date.

## When to use it

- Use this as a reusable national point layer for OSMRE abandoned mine land problem features.
- Use `AMLIS_KEY` to group multiple problem-type records that share a problem area.
- Use the FlatGeobuf file for analytical work.
- Do not treat this as a complete inventory of every abandoned mine, active mine, post-1982 mine, responsible operator, or underground mine footprint.
- Do not use the source GeoJSON as the analytical default; it is preserved for provenance.

## Files

| File | Purpose |
|---|---|
| `latest/eamlis-abandoned-mine-land-inventory.fgb` | Canonical WGS84 point dataset |
| `releases/2026-04-30/eamlis-abandoned-mine-land-inventory.fgb` | Dated canonical release |
| `sources/eamlis-data-accessed-2025-10-24-4326.geojson` | Source GeoJSON supplied for this upload; noncanonical because it is large and less efficient for analysis |

## Schema notes

Geometry is WGS84 point geometry derived from the source GeoJSON coordinates. The source also includes `x` and `y` attributes that appear to be projected Web Mercator coordinate values from the export; use the geometry column for geospatial analysis.

Multiple records can share the same `AMLIS_KEY` and point geometry because rows represent individual abandoned mine land problem types within a problem area. The snapshot contains 62,220 features, 24,062 unique `AMLIS_KEY` values, 56 `STATE_KEY` values, 44 unique `PROB_TY_CD` values, and no null geometries.

The source stores many unit and cost fields as strings with thousands separators and fixed decimal formatting. They are preserved as strings in the canonical FlatGeobuf to avoid silent numeric coercion.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `OBJECTID` | integer | Source object identifier from the exported layer. |
| `AMLIS_KEY` | string | OSMRE problem area identifier, generally a state or Tribal abbreviation followed by six digits. |
| `STATE_KEY` | string | Source state or Tribal account abbreviation. |
| `PA_NUMBER` | string | Problem area number. |
| `PA_NAME` | string | Problem area name assigned by a state or Tribe. |
| `PU_NUMBER` | string | Planning unit number. |
| `PU_NAME` | string | Planning unit name. |
| `EST_LATITUDE` | real | Source latitude value in decimal degrees. |
| `EST_LONGITUDE` | real | Source longitude value in decimal degrees. |
| `LAT_DEG` | integer | Latitude degree component from the source coordinate fields. |
| `LAT_MIN` | integer | Latitude minute component from the source coordinate fields. |
| `LON_DEG` | integer | Longitude degree component from the source coordinate fields. |
| `LON_MIN` | integer | Longitude minute component from the source coordinate fields. |
| `COUNTY` | string | County name generated from the source problem-area location. |
| `FIPS_CODE` | string | Federal Information Processing Standards geography code from the source. |
| `CONG_DIST` | integer | Congressional district generated from the source problem-area location. |
| `QUAD_NAME` | string | USGS quadrangle name generated from the source problem-area location. |
| `HUC_CODE` | integer | Hydrologic unit code generated from the source problem-area location. |
| `WATERSHED` | string | Watershed name generated from the source problem-area location. |
| `MINE_TYPE` | string | Mining type code. The OSMRE data dictionary describes values for surface, underground, or both; additional codes in this export need source confirmation. |
| `ORE_TYPES` | string | Ore type for non-coal reclamation sites, where supplied. |
| `OWNER_PRIVATE` | integer | Percent private ownership for the problem area. |
| `OWNER_STATE` | integer | Percent state ownership for the problem area. |
| `OWNER_INDIAN` | integer | Percent Tribal ownership for the problem area. |
| `OWNER_BLM` | integer | Percent Bureau of Land Management ownership for the problem area. |
| `OWNER_FOREST` | integer | Percent Forest Service ownership for the problem area. |
| `OWNER_NATIONAL` | integer | Percent National Park Service ownership for the problem area. |
| `OWNER_OTHER` | integer | Percent other federal ownership for the problem area. |
| `POPULATION` | string | Census population or risk population value from the source export, preserved as text. |
| `DATE_PREPARED` | string | Source prepared date for the problem area description. |
| `DATE_REVISED` | string | Source revised date for the problem area description or problem-type record. |
| `PRIORITY` | string | AML priority or category code. Numeric and nonnumeric codes are preserved as source text. |
| `PROB_TY_CD` | string | Abbreviated abandoned mine land problem type code. |
| `PROB_TY_NAME` | string | Abandoned mine land problem type name. |
| `PROGRAM` | string | Source program or funding-source code. |
| `UNFD_UNITS` | string | Unfunded units for the problem-type feature, in source English units. |
| `UNFD_METERS` | string | Unfunded metric conversion for the problem-type feature. |
| `UNFD_COST` | string | Estimated direct construction cost for future reclamation. |
| `UNFD_GPRA` | string | Unfunded GPRA acre-equivalent conversion. |
| `FUND_UNITS` | string | Funded units for the problem-type feature, in source English units. |
| `FUND_METERS` | string | Funded metric conversion for the problem-type feature. |
| `FUND_COST` | string | Actual direct construction cost obligated for reclamation. |
| `FUND_GPRA` | string | Funded GPRA acre-equivalent conversion. |
| `COMP_UNITS` | string | Completed units for the problem-type feature, in source English units. |
| `COMP_METERS` | string | Completed metric conversion for the problem-type feature. |
| `COMP_COST` | string | Final direct construction cost expended for reclamation. |
| `COMP_GPRA` | string | Completed GPRA acre-equivalent conversion. |
| `TOTAL_UNITS` | string | Source total units for the problem-type feature. |
| `TOTAL_COST` | string | Source total direct construction cost for the problem-type feature. |
| `x` | real | Source export coordinate value, likely Web Mercator x. Use geometry instead. |
| `y` | real | Source export coordinate value, likely Web Mercator y. Use geometry instead. |

## Update notes

Manually converted from the supplied GeoJSON source to FlatGeobuf on 2026-04-30 using GDAL.

Output summary:

- Source GeoJSON features: 62,220
- Published FGB features: 62,220
- Unique `AMLIS_KEY` values: 24,062
- Null geometries: 0
- Extent: -161.234444, 28.503843 to -71.249444, 70.500000
- FGB SHA-256: `0da1ecfd89d5d981350dfb76416044659421e4af827dc9a980a2fc0c34696a01`
- Source GeoJSON SHA-256: `0d039c14ac175926fc4000b9b3728c6bcf8e6021c6724119cba4e9d76b306643`

## Known caveats

OSMRE says e-AMLIS is dynamic and is modified as new problems are identified and existing problems are reclaimed. This upload is a static manual snapshot, not a scheduled ingestion job.

OSMRE notes that the inventory does not include all land and water damaged by past mining, does not include responsible mine or company information, and tracks only direct construction costs for identified AML features.

Field descriptions are based on the OSMRE e-AMLIS data dictionary where fields could be matched. Some export-specific fields and coded values still need source confirmation before being used for formal reporting.
