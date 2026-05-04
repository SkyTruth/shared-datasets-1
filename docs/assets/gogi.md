---
schema_version: 1
asset_slug: gogi
title: Global Oil & Gas Features Database
category: 300-infrastructure-industrial
subcategory: 310-energy
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/gogi-pipelines.fgb
available_formats:
- fgb
- csv
- pmtiles
metadata_paths:
- README.md
source: NETL EDX Global Oil & Gas Features Database gogi_v10_3_1shp.zip
source_url: https://edx.netl.doe.gov/dataset/global-oil-gas-features-database
license: Creative Commons Attribution and Open Data Commons Attribution License; attribution required
license_flags:
- attribution-required
notes: Initial upload from gogi_v10_3_1shp.zip; release 2026-05-02; source zip sha256 2621f210dde27f068ea2987e0cf0135b20c2641e94e6b4e3692ac9ee4263ad07;
  17 source shapefile layers converted to WGS84 FGB plus 2 combined CSV catalog tables; retained 1,623,469 vector features
  after omitting 394 well points that could not be reprojected; Well.dbf is truncated in the source shapefile resource, so
  wells are published as geometry only; pmtiles sha256 9fe9a4705b06ae08327e59b769b06ffe16d1d7f6932b0b318bbe92055be8ab31; PMTiles
  rebuilt 2026-05-04 at maxzoom 12 as multi-layer display tiles with compact feature properties, Tippecanoe v2.79.0, and low-zoom
  point retention
geometry_type: mixed
row_count: 1623469
files:
- path: latest/gogi-pipelines.fgb
  format: fgb
  role: canonical
  purpose: Catalog anchor and canonical WGS84 pipeline line layer
- path: latest/gogi-active-wells.fgb
  format: fgb
  role: companion
  purpose: WGS84 active wells polygon layer
- path: latest/gogi-basins.fgb
  format: fgb
  role: companion
  purpose: WGS84 basins polygon layer
- path: latest/gogi-fields.fgb
  format: fgb
  role: companion
  purpose: WGS84 fields polygon layer
- path: latest/gogi-lng.fgb
  format: fgb
  role: companion
  purpose: WGS84 LNG point layer
- path: latest/gogi-mines.fgb
  format: fgb
  role: companion
  purpose: WGS84 mines point layer
- path: latest/gogi-platforms-and-well-pads.fgb
  format: fgb
  role: companion
  purpose: WGS84 platforms and well pads point layer
- path: latest/gogi-ports.fgb
  format: fgb
  role: companion
  purpose: WGS84 ports point layer
- path: latest/gogi-power-plants.fgb
  format: fgb
  role: companion
  purpose: WGS84 power plants point layer
- path: latest/gogi-processing-plants.fgb
  format: fgb
  role: companion
  purpose: WGS84 processing plants point layer
- path: latest/gogi-railways.fgb
  format: fgb
  role: companion
  purpose: WGS84 railways line layer
- path: latest/gogi-refineries.fgb
  format: fgb
  role: companion
  purpose: WGS84 refineries point layer
- path: latest/gogi-stations.fgb
  format: fgb
  role: companion
  purpose: WGS84 stations point layer
- path: latest/gogi-storage.fgb
  format: fgb
  role: companion
  purpose: WGS84 storage point layer
- path: latest/gogi-underground-storage.fgb
  format: fgb
  role: companion
  purpose: WGS84 underground storage point layer
- path: latest/gogi-well-geometry.fgb
  format: fgb
  role: companion
  purpose: WGS84 well point geometry only; source DBF is truncated
- path: latest/gogi-wells-vector-grid.fgb
  format: fgb
  role: companion
  purpose: WGS84 wells vector grid polygon layer
- path: latest/gogi-data-catalog.csv
  format: csv
  role: companion
  purpose: Combined non-geometry regional source catalog tables with source_table
- path: latest/gogi-data-catalog-validation.csv
  format: csv
  role: companion
  purpose: Combined non-geometry validation catalog table with source_table
- path: latest/gogi.pmtiles
  format: pmtiles
  role: companion
  purpose: Multi-layer web map tiles with compact feature properties for catalog preview
- path: releases/2026-05-02/gogi-pipelines.fgb
  format: fgb
  role: release
  purpose: Dated pipeline line layer release
- path: releases/2026-05-02/gogi-active-wells.fgb
  format: fgb
  role: release
  purpose: Dated active wells polygon layer release
- path: releases/2026-05-02/gogi-basins.fgb
  format: fgb
  role: release
  purpose: Dated basins polygon layer release
- path: releases/2026-05-02/gogi-fields.fgb
  format: fgb
  role: release
  purpose: Dated fields polygon layer release
- path: releases/2026-05-02/gogi-lng.fgb
  format: fgb
  role: release
  purpose: Dated LNG point layer release
- path: releases/2026-05-02/gogi-mines.fgb
  format: fgb
  role: release
  purpose: Dated mines point layer release
- path: releases/2026-05-02/gogi-platforms-and-well-pads.fgb
  format: fgb
  role: release
  purpose: Dated platforms and well pads point layer release
- path: releases/2026-05-02/gogi-ports.fgb
  format: fgb
  role: release
  purpose: Dated ports point layer release
- path: releases/2026-05-02/gogi-power-plants.fgb
  format: fgb
  role: release
  purpose: Dated power plants point layer release
- path: releases/2026-05-02/gogi-processing-plants.fgb
  format: fgb
  role: release
  purpose: Dated processing plants point layer release
- path: releases/2026-05-02/gogi-railways.fgb
  format: fgb
  role: release
  purpose: Dated railways line layer release
- path: releases/2026-05-02/gogi-refineries.fgb
  format: fgb
  role: release
  purpose: Dated refineries point layer release
- path: releases/2026-05-02/gogi-stations.fgb
  format: fgb
  role: release
  purpose: Dated stations point layer release
- path: releases/2026-05-02/gogi-storage.fgb
  format: fgb
  role: release
  purpose: Dated storage point layer release
- path: releases/2026-05-02/gogi-underground-storage.fgb
  format: fgb
  role: release
  purpose: Dated underground storage point layer release
- path: releases/2026-05-02/gogi-well-geometry.fgb
  format: fgb
  role: release
  purpose: Dated well point geometry-only release
- path: releases/2026-05-02/gogi-wells-vector-grid.fgb
  format: fgb
  role: release
  purpose: Dated wells vector grid polygon layer release
- path: releases/2026-05-02/gogi-data-catalog.csv
  format: csv
  role: release
  purpose: Dated combined regional source catalog table release
- path: releases/2026-05-02/gogi-data-catalog-validation.csv
  format: csv
  role: release
  purpose: Dated validation catalog table release
- path: releases/2026-05-02/gogi.pmtiles
  format: pmtiles
  role: release
  purpose: Dated multi-layer web map tile release with compact feature properties
---

# Global Oil & Gas Features Database

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/gogi-pipelines.fgb`
- **Available formats:** `fgb`, `csv`, `pmtiles`
- **Source:** NETL EDX Global Oil & Gas Features Database gogi_v10_3_1shp.zip
- **License / terms:** Creative Commons Attribution and Open Data Commons Attribution License; attribution required
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the National Energy Technology Laboratory Energy Data eXchange Global Oil & Gas Features Database, commonly called GOGI. The source describes GOGI as a global inventory of publicly available oil and gas infrastructure datasets refined into a single open database. The EDX submission provides DOI `10.18141/1427300` and cites Sabbatino et al. 2017.

The published shared-datasets asset converts the `gogi_v10_3_1shp.zip` shapefile resource into approved analysis and display formats. Each source shapefile layer is published as a separate WGS84 FlatGeobuf file, the source data catalog CSV tables are combined into two non-geometry companion CSV files, and a single multi-layer PMTiles artifact supports catalog map preview.

## When to use it

- Use this for broad global context on oil and gas infrastructure, including pipelines, wells, fields, basins, LNG facilities, refineries, storage, ports, railways, processing plants, stations, platforms, and related source catalog tables.
- Use the layer-specific FGB files rather than assuming one file contains the full source package.
- Do not use this as a current authoritative facility, permitting, legal boundary, or operational-status dataset.
- Do not rely on well attributes from this shapefile resource; the source `Well.dbf` is truncated, so only well geometry is published here.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/gogi-pipelines.fgb` | `fgb` | `canonical` | Catalog anchor and canonical WGS84 pipeline line layer |
| `latest/gogi-active-wells.fgb` | `fgb` | `companion` | WGS84 active wells polygon layer |
| `latest/gogi-basins.fgb` | `fgb` | `companion` | WGS84 basins polygon layer |
| `latest/gogi-fields.fgb` | `fgb` | `companion` | WGS84 fields polygon layer |
| `latest/gogi-lng.fgb` | `fgb` | `companion` | WGS84 LNG point layer |
| `latest/gogi-mines.fgb` | `fgb` | `companion` | WGS84 mines point layer |
| `latest/gogi-platforms-and-well-pads.fgb` | `fgb` | `companion` | WGS84 platforms and well pads point layer |
| `latest/gogi-ports.fgb` | `fgb` | `companion` | WGS84 ports point layer |
| `latest/gogi-power-plants.fgb` | `fgb` | `companion` | WGS84 power plants point layer |
| `latest/gogi-processing-plants.fgb` | `fgb` | `companion` | WGS84 processing plants point layer |
| `latest/gogi-railways.fgb` | `fgb` | `companion` | WGS84 railways line layer |
| `latest/gogi-refineries.fgb` | `fgb` | `companion` | WGS84 refineries point layer |
| `latest/gogi-stations.fgb` | `fgb` | `companion` | WGS84 stations point layer |
| `latest/gogi-storage.fgb` | `fgb` | `companion` | WGS84 storage point layer |
| `latest/gogi-underground-storage.fgb` | `fgb` | `companion` | WGS84 underground storage point layer |
| `latest/gogi-well-geometry.fgb` | `fgb` | `companion` | WGS84 well point geometry only; source DBF is truncated |
| `latest/gogi-wells-vector-grid.fgb` | `fgb` | `companion` | WGS84 wells vector grid polygon layer |
| `latest/gogi-data-catalog.csv` | `csv` | `companion` | Combined non-geometry regional source catalog tables with source_table |
| `latest/gogi-data-catalog-validation.csv` | `csv` | `companion` | Combined non-geometry validation catalog table with source_table |
| `latest/gogi.pmtiles` | `pmtiles` | `companion` | Multi-layer web map tiles with compact feature properties for catalog preview |
| `releases/2026-05-02/gogi-pipelines.fgb` | `fgb` | `release` | Dated pipeline line layer release |
| `releases/2026-05-02/gogi-active-wells.fgb` | `fgb` | `release` | Dated active wells polygon layer release |
| `releases/2026-05-02/gogi-basins.fgb` | `fgb` | `release` | Dated basins polygon layer release |
| `releases/2026-05-02/gogi-fields.fgb` | `fgb` | `release` | Dated fields polygon layer release |
| `releases/2026-05-02/gogi-lng.fgb` | `fgb` | `release` | Dated LNG point layer release |
| `releases/2026-05-02/gogi-mines.fgb` | `fgb` | `release` | Dated mines point layer release |
| `releases/2026-05-02/gogi-platforms-and-well-pads.fgb` | `fgb` | `release` | Dated platforms and well pads point layer release |
| `releases/2026-05-02/gogi-ports.fgb` | `fgb` | `release` | Dated ports point layer release |
| `releases/2026-05-02/gogi-power-plants.fgb` | `fgb` | `release` | Dated power plants point layer release |
| `releases/2026-05-02/gogi-processing-plants.fgb` | `fgb` | `release` | Dated processing plants point layer release |
| `releases/2026-05-02/gogi-railways.fgb` | `fgb` | `release` | Dated railways line layer release |
| `releases/2026-05-02/gogi-refineries.fgb` | `fgb` | `release` | Dated refineries point layer release |
| `releases/2026-05-02/gogi-stations.fgb` | `fgb` | `release` | Dated stations point layer release |
| `releases/2026-05-02/gogi-storage.fgb` | `fgb` | `release` | Dated storage point layer release |
| `releases/2026-05-02/gogi-underground-storage.fgb` | `fgb` | `release` | Dated underground storage point layer release |
| `releases/2026-05-02/gogi-well-geometry.fgb` | `fgb` | `release` | Dated well point geometry-only release |
| `releases/2026-05-02/gogi-wells-vector-grid.fgb` | `fgb` | `release` | Dated wells vector grid polygon layer release |
| `releases/2026-05-02/gogi-data-catalog.csv` | `csv` | `release` | Dated combined regional source catalog table release |
| `releases/2026-05-02/gogi-data-catalog-validation.csv` | `csv` | `release` | Dated validation catalog table release |
| `releases/2026-05-02/gogi.pmtiles` | `pmtiles` | `release` | Dated multi-layer web map tile release with compact feature properties |
<!-- END GENERATED files-table -->

## Schema notes

The source shapefiles use the ESRI World Eckert IV projected coordinate system. Published FGB layers were reprojected to WGS84 (`EPSG:4326`) for shared consumption. Layer names were normalized to lowercase `gogi_*` names, while source field names and values are otherwise preserved for all layers except `gogi-well-geometry.fgb`.

The `Well.dbf` file in `gogi_v10_3_1shp.zip` is truncated: its DBF header advertises 736,476 rows with a 3,826-byte record length, which would require 2,817,758,137 bytes, but the resource contains a 2,147,485,024-byte DBF. GDAL cannot read attributes past the truncation point. To avoid publishing partial well attributes, `gogi-well-geometry.fgb` was generated from the shapefile geometry and index files without the DBF. It retains 736,082 WGS84 well points; 394 source well geometries could not be reprojected from World Eckert IV to WGS84 and were omitted.

The source `Data_Catalog_*.csv` tables were combined into `gogi-data-catalog.csv` with a `source_table` column. `Data_Catalog_Validation.csv` was published separately as `gogi-data-catalog-validation.csv`, also with `source_table`.

The `gogi.pmtiles` artifact is a multi-layer display artifact built from the 17 published FGB vector layers. It preserves named vector layers for map preview and includes a compact set of source properties for feature inspection. Analytical use should still rely on the FGB and CSV files. The `well_geometry` tile layer has only `source_layer` and `property_note` properties because the source `Well.dbf` is truncated and well attributes are intentionally not published. PMTiles were rebuilt on 2026-05-04 at maxzoom 12, with decoded zoom 0 checks confirming full point-layer retention including all 736,082 well geometry points.

Layer feature counts:

| Layer file | Geometry | Features / rows |
|---|---:|---:|
| `gogi-active-wells.fgb` | multipolygon | 6,990 |
| `gogi-basins.fgb` | multipolygon | 1,046 |
| `gogi-fields.fgb` | multipolygon | 25,236 |
| `gogi-lng.fgb` | point | 329 |
| `gogi-mines.fgb` | point | 51,602 |
| `gogi-pipelines.fgb` | multiline | 411,521 |
| `gogi-platforms-and-well-pads.fgb` | point | 9,845 |
| `gogi-ports.fgb` | point | 3,702 |
| `gogi-power-plants.fgb` | point | 14,097 |
| `gogi-processing-plants.fgb` | point | 1,922 |
| `gogi-railways.fgb` | multiline | 280,734 |
| `gogi-refineries.fgb` | point | 2,272 |
| `gogi-stations.fgb` | point | 13,876 |
| `gogi-storage.fgb` | point | 26,103 |
| `gogi-underground-storage.fgb` | point | 3,731 |
| `gogi-well-geometry.fgb` | point | 736,082 |
| `gogi-wells-vector-grid.fgb` | multipolygon | 34,381 |
| `gogi-data-catalog.csv` | non-geometry table | 609 |
| `gogi-data-catalog-validation.csv` | non-geometry table | 122 |

## Properties / columns

Layer-specific field presence varies. Common vector fields include:

| Name | Type | Description |
|---|---|---|
| `source_layer` | string | Synthetic PMTiles property identifying the display layer. |
| `property_note` | string | Synthetic PMTiles note on `well_geometry` explaining that source well attributes are not published because `Well.dbf` is truncated. |
| `MD_Country` | string | Source country or country grouping. |
| `MD_Source_` | string / real | Source metadata field; exact source definition needs source confirmation. |
| `MD_Source` | string | Source metadata text or citation. |
| `Onshore_Of` | string | Onshore/offshore classification where populated. |
| `Capacity` | string | Capacity text where populated. |
| `Operator` | string | Operator name where populated. |
| `Installati` | string | Installation date or installation text where populated. |
| `Facility_N` | string | Facility name where populated. |
| `Status` | string | Source status text where populated. |
| `Type` | string | Source feature type. |
| `Commodity` | string | Commodity text where populated. |
| `MD_Fkey` | string / integer | Source metadata foreign key or feature key. |
| `Spat_Ranks` | string | Spatial quality rank text. |
| `Temp_Ranks` | string | Temporal quality rank text. |
| `Sour_Ranks` | string | Source quality rank text. |
| `MD_Region` | string | Source region grouping. |
| `MD_Source1` | real | Numeric source quality value. |
| `MD_Tempora` | real | Numeric temporal quality value. |
| `MD_Spatial` | real | Numeric spatial quality value. |
| `NumberSour` | real / integer | Number of sources or source-count field. |
| `Shape_Leng` | real | Source GIS length field where present. |
| `Shape_Area` | real | Source GIS area field where present. |
| `Diameter` | string | Pipeline diameter text where populated. |
| `Throughput` | string | Pipeline throughput text where populated. |
| `ORIG_FID` | integer64 | Original feature identifier where present. |
| `Source_Qua` | real / string | Source quality field where present. |
| `Temporal_Q` | real / string | Temporal quality field where present. |
| `Spatial_Qu` | real / string | Spatial quality field where present. |
| `Count` | integer / real | Count field in active wells and wells vector grid layers. |
| `Id` | integer64 | Source identifier in the wells vector grid layer. |

Additional basin fields include `REG_TL_NAM`, `MAX_FILLKM`, `SUB_REGIME`, `BASIN_ID`, `SUBREGCODE`, `REG_TL_ABV`, `OWNER`, `AVAILABLE`, `SUBREG_GRP`, and `Shape_Le_1`. The two CSV catalog tables preserve source table headers and add `source_table` to identify the original source CSV.

## Update notes

Manually converted from `/Users/jonathanraphael/Downloads/gogi_v10_3_1shp.zip` on 2026-05-02.

Toolchain:

- GDAL: `GDAL 3.6.2, released 2023/01/02`
- `ogr2ogr`: `/Users/jonathanraphael/miniforge3/bin/ogr2ogr`
- Tippecanoe: `tippecanoe v2.79.0` at `/usr/local/bin/tippecanoe`
- Source zip SHA-256: `2621f210dde27f068ea2987e0cf0135b20c2641e94e6b4e3692ac9ee4263ad07`
- PMTiles SHA-256: `6a1a2184598925e6225306b5ec103929035d1c5eeef9882b9a4740740ced7f0c`

The PMTiles artifact was rebuilt on 2026-05-02 from GeoJSONSeq intermediates derived from the 17 WGS84 FGB layers. Tippecanoe used named layers, minzoom 0, maxzoom 8, `--no-feature-limit`, `--no-tile-size-limit`, `--drop-rate=1`, `--maximum-string-attribute-length=256`, and `--no-tile-stats`; the standalone `pmtiles` CLI was unavailable locally. The tile build keeps compact feature properties for the catalog inspector, while `well_geometry` carries only `source_layer` and a `property_note` because source well attributes are unavailable. `tippecanoe-decode` confirmed all point layers retain full zoom 0 published counts.

The source EDX page lists Creative Commons Attribution and Open Data Commons Attribution License terms. The source page was last updated by EDX on 2025-01-24 and the local source zip was downloaded on 2026-05-02.

## Known caveats

GOGI was compiled from many public sources with varying spatial and temporal quality. Use the quality/source fields and catalog tables before treating any layer as complete or current.

The source shapefile resource contains a truncated `Well.dbf`, so this publication intentionally avoids partial well attributes. Use `gogi-well-geometry.fgb` only for well point locations retained after reprojection.

Some source geometries fall outside the valid inverse projection domain for World Eckert IV to WGS84 conversion. Reprojection omitted 394 well points; the wells vector grid converted with projection-domain warnings.
