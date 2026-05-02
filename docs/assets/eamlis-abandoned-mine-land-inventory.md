---
schema_version: 1
asset_slug: eamlis-abandoned-mine-land-inventory
title: OSMRE e-AMLIS Abandoned Mine Land Inventory
category: 300-infrastructure-industrial
subcategory: 320-mining
status: active
access_tier: public
owner: SkyTruth
update_cadence: monthly, skipped when unchanged
canonical_format: fgb
canonical_file: latest/eamlis-abandoned-mine-land-inventory.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
last_updated: '2026-05-01'
source: U.S. Department of the Interior OSMRE e-AMLIS
license: Creative Commons Attribution per EDX listing; cite OSMRE e-AMLIS
notes: Scheduled refresh release 2026-05-01; source rows 63110; unique AMLIS_KEY 24427; fgb sha256 556cabc1d073f4d165d1ad13a5539cbd095951096a17cb9f61520a7f8d1f2e41;
  pmtiles sha256 ab09dd5deebc579f68b84bc7860538b458f8730faba7c31b7474cbf502640792; PMTiles generated with Tippecanoe no feature
  limit/no tile size limit/drop-rate 1 for low-zoom point fidelity; stale initial GeoJSON remains only under source/provenance
  paths and is not advertised as an active data-plane format
files:
- path: latest/eamlis-abandoned-mine-land-inventory.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 point dataset
- path: latest/eamlis-abandoned-mine-land-inventory.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same point features
- path: releases/YYYY-MM-DD/eamlis-abandoned-mine-land-inventory.fgb
  format: fgb
  role: release
  purpose: Dated canonical releases
- path: releases/YYYY-MM-DD/eamlis-abandoned-mine-land-inventory.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile releases
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Scheduled ingestion run records
- path: sources/eamlis-data-accessed-2025-10-24-4326.geojson
  format: geojson
  role: source
  purpose: Initial source GeoJSON supplied for the first upload; noncanonical because it is large and less efficient for analysis
---

# OSMRE e-AMLIS Abandoned Mine Land Inventory

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Last updated:** 2026-05-01
- **Update cadence:** monthly, skipped when unchanged
- **Canonical file:** `latest/eamlis-abandoned-mine-land-inventory.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** U.S. Department of the Interior OSMRE e-AMLIS
- **License / terms:** Creative Commons Attribution per EDX listing; cite OSMRE e-AMLIS
<!-- END GENERATED asset-summary -->

## What this is

This asset is a point snapshot of the Office of Surface Mining Reclamation and Enforcement enhanced Abandoned Mine Land Inventory System, or e-AMLIS. OSMRE describes e-AMLIS as an inventory of land and water impacted by legacy coal mining operations, including location, type, extent, and direct reclamation construction cost information for identified abandoned mine land problems.

The initial 2026-04-30 bucket release was converted from a supplied GeoJSON file named `eAMLIS_data_accessed_2025-10-24_4326.geojson`. Scheduled refreshes use the public ArcGIS hosted feature layer behind the e-AMLIS application, filtered with `LAT_DEG > 0` to match the public current map layer.

## When to use it

- Use this as a reusable national point layer for OSMRE abandoned mine land problem features.
- Use `AMLIS_KEY` to group multiple problem-type records that share a problem area.
- Use the FlatGeobuf file for analytical work and the PMTiles file for web-map display.
- Do not treat this as a complete inventory of every abandoned mine, active mine, post-1982 mine, responsible operator, or underground mine footprint.
- Do not use the source GeoJSON as the analytical default; it is preserved for provenance.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/eamlis-abandoned-mine-land-inventory.fgb` | `fgb` | `canonical` | Canonical WGS84 point dataset |
| `latest/eamlis-abandoned-mine-land-inventory.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same point features |
| `releases/YYYY-MM-DD/eamlis-abandoned-mine-land-inventory.fgb` | `fgb` | `release` | Dated canonical releases |
| `releases/YYYY-MM-DD/eamlis-abandoned-mine-land-inventory.pmtiles` | `pmtiles` | `release` | Dated map-tile releases |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Scheduled ingestion run records |
| `sources/eamlis-data-accessed-2025-10-24-4326.geojson` | `geojson` | `source` | Initial source GeoJSON supplied for the first upload; noncanonical because it is large and less efficient for analysis |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 point geometry derived from the public ArcGIS hosted feature layer. The public service also exposes `EST_LATITUDE` and `EST_LONGITUDE` display fields that may be less precise than the feature geometry; use the geometry column for geospatial analysis.

Multiple records can share the same `AMLIS_KEY` and point geometry because rows represent individual abandoned mine land problem types within a problem area. The current 2026-05-01 scheduled release contains 63,110 features, 24,427 unique `AMLIS_KEY` values, 55 `STATE_KEY` values, 43 unique `PROB_TY_CD` values, and no null geometries. Scheduled run records document the current public source count for each refresh attempt.

Scheduled refreshes preserve the public ArcGIS hosted layer fields. ArcGIS date fields are normalized to ISO `YYYY-MM-DD` values during ingestion; unit and cost fields are numeric where the hosted layer exposes numeric types.

The PMTiles artifact is generated with Tippecanoe from the same point features, with zooms 0 through 8. It uses `--no-feature-limit`, `--no-tile-size-limit`, and `--drop-rate=1` so low-zoom tiles retain dense point content for visual inspection. The canonical FGB remains the analytical source.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `AMLIS_KEY` | string | OSMRE problem area identifier, generally a state or Tribal abbreviation followed by six digits. |
| `STATE_KEY` | string | Source state or Tribal account abbreviation. |
| `PA_NUMBER` | integer | Problem area number. |
| `PA_NAME` | string | Problem area name assigned by a state or Tribe. |
| `PU_NUMBER` | string | Planning unit number. |
| `PU_NAME` | string | Planning unit name. |
| `UPDATE_CODE` | string | Source update code; definitions need source confirmation. |
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
| `HUC_CODE` | string | Hydrologic unit code generated from the source problem-area location. |
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
| `POPULATION` | integer | Census population or risk population value from the source. |
| `SQUARE_MILES` | real | Source area value in square miles. |
| `DATE_PREPARED` | date | Source prepared date for the problem area description. |
| `DATE_REVISED` | date | Source revised date for the problem area description or problem-type record. |
| `CERTIFIED_STATE` | integer | Certified-state indicator; definitions need source confirmation. |
| `PROBLEM_KEY` | string | Source problem-feature key associated with the problem area and problem type. |
| `NUMBER` | integer | Source problem-feature number. |
| `PRIORITY` | string | AML priority or category code. Numeric and nonnumeric codes are preserved as source text. |
| `PROB_TY_CD` | string | Abbreviated abandoned mine land problem type code. |
| `PROB_TY_NAME` | string | Abandoned mine land problem type name. |
| `PROGRAM` | string | Source program or funding-source code. |
| `FUND_SRC` | integer | Funding-source code; definitions need source confirmation. |
| `UNFD_UNITS` | real | Unfunded units for the problem-type feature, in source English units. |
| `UNFD_METERS` | real | Unfunded metric conversion for the problem-type feature. |
| `UNFD_COST` | real | Estimated direct construction cost for future reclamation. |
| `UNFD_GPRA` | real | Unfunded GPRA acre-equivalent conversion. |
| `FUND_UNITS` | real | Funded units for the problem-type feature, in source English units. |
| `FUND_METERS` | real | Funded metric conversion for the problem-type feature. |
| `FUND_COST` | real | Actual direct construction cost obligated for reclamation. |
| `FUND_GPRA` | real | Funded GPRA acre-equivalent conversion. |
| `COMP_UNITS` | real | Completed units for the problem-type feature, in source English units. |
| `COMP_METERS` | real | Completed metric conversion for the problem-type feature. |
| `COMP_COST` | real | Final direct construction cost expended for reclamation. |
| `COMP_GPRA` | real | Completed GPRA acre-equivalent conversion. |
| `TOTAL_UNITS` | real | Source total units for the problem-type feature. |
| `TOTAL_COST` | real | Source total direct construction cost for the problem-type feature. |
| `RECLAMATION` | string | Reclamation status text. |
| `STATUS0` | string | Problem status text used by the public e-AMLIS application. |
| `OBJECTID` | integer | Source object identifier from the hosted feature layer. |

## Update notes

The current release was generated by the monthly Cloud Run job on 2026-05-01 from the public ArcGIS hosted feature layer, then PMTiles were added with Tippecanoe.

Output summary:

- Source features: 63,110
- Published FGB features: 63,110
- PMTiles zoom 0 decoded point features: 63,110
- Unique `AMLIS_KEY` values: 24,427
- Null geometries: 0
- Extent: -161.234444, 28.503843 to -71.249444, 70.500000
- FGB SHA-256: `556cabc1d073f4d165d1ad13a5539cbd095951096a17cb9f61520a7f8d1f2e41`
- PMTiles SHA-256: `ab09dd5deebc579f68b84bc7860538b458f8730faba7c31b7474cbf502640792`
- Toolchain: GDAL 3.6.2, Tippecanoe 2.79.0; PMTiles CLI unavailable locally, so archive validation used successful Tippecanoe generation plus `tippecanoe-decode` feature-count checks.

Monthly scheduled ingestion was added after the initial manual upload. The job checks the public ArcGIS layer metadata and source statistics first, skips unchanged source fingerprints without downloading features, and also skips publication when a changed source fingerprint generates the same FGB SHA-256 as the latest successful run.

## Known caveats

OSMRE says e-AMLIS is dynamic and is modified as new problems are identified and existing problems are reclaimed. Scheduled refreshes publish only when the public source changes.

OSMRE notes that the inventory does not include all land and water damaged by past mining, does not include responsible mine or company information, and tracks only direct construction costs for identified AML features.

Field descriptions are based on the OSMRE e-AMLIS data dictionary where fields could be matched. Some export-specific fields and coded values still need source confirmation before being used for formal reporting.
