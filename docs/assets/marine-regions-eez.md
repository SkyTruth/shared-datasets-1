---
schema_version: 1
asset_slug: marine-regions-eez
title: Marine Regions EEZs and High Seas
category: 100-geographic-reference
subcategory: 120-marine-boundaries
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/marine-regions-eez.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: Marine Regions World EEZ v12 and World High Seas v2
license: Creative Commons Attribution 4.0 International, subject to Marine Regions attribution and redistribution guidance
citation: Flanders Marine Institute (2023). Maritime Boundaries Geodatabase, Maritime Boundaries and Exclusive Economic Zones
  (200NM), version 12. Available online at https://www.marineregions.org/. https://doi.org/10.14284/632. Flanders Marine Institute
  (2024). Maritime Boundaries Geodatabase, High Seas, version 2. Available online at https://www.marineregions.org/. https://doi.org/10.14284/696.
notes: Initial reviewed shared-datasets release from the supplied FlatGeobuf World_EEZ_v12_20231025-World_High_Seas_v2_20241010.fgb;
  release 2026-06-06; source sha256 15a039d989c7fd8ad41231f041ca428101d6bb5202c8039f6ef00c21a85413fc. The 2026-06-09 corrective
  schema-contract revision rebuilds the release with feature_id values copied from MRGID, geometry_hash values, properties_hash
  values, release metadata/schema/manifest schema_version 2 artifacts, metadata-translations rows keyed by raw feature_id,
  and localized GEONAME/name metadata sidecars for es, fr, id, pt, pt_br, and sw. PMTiles are v3 MVT metadata-lookup tiles
  at maxzoom 12 with feature_id only. Hashes for the 2026-06-09 candidates are fgb cce07effbf7eb74494d96de5a6c0e3b8af6908b580d73af177ef45e493e5151f;
  pmtiles de835691551e4c16af415579b0e2a2bfeb0d9095fedd410aa1210d6815fb9a51; metadata 4a83a16d3e3ab3105d3207d936d4e4e4b78c3e6d0876f5c182751053b4b57a1a;
  metadata-translations 114c82dad426ed568b1a2001731df06d1ea5e9058dde37de74719bd464c7c0d6; schema e36c73a22de54ee9b25bf7a856320def695deb1a08506313ab9d22a2489256ac;
  manifest 4bbceaf7b72c542beedd5a1ab6bd2b623ab3fdfc6c319e744552da82696a66fe. Firestore metadata serving is inactive for this
  release model; applications should read sidecars. Release history, source generations, row counts, and hashes are recorded
  in the bucket release index and per-run record.
admission:
  intended_consumers:
  - SkyTruth and Global Fishing Watch analysis workflows
  - Shared catalog map previews and reusable marine-boundary spatial lookups
  shared_rationale: Reusable global marine-boundary reference with a versioned shared copy so consumers do not maintain divergent
    project-local copies of EEZ, overlapping-claim, joint-regime, and high-seas boundaries.
  steward: SkyTruth
  update_expectations: Manual refreshes when Marine Regions publishes newer EEZ or High Seas releases needed by shared consumers.
  estimated_published_size_gb: 0.7
  alternatives_considered: Direct Marine Regions downloads by each consumer, project-local storage, and scratch-only staging.
    Shared-datasets is preferred for reviewed reuse, stable shared paths, documented metadata, and canonical geospatial formats.
  deprecation_policy: Keep dated releases readable; supersede with newer Marine Regions EEZ or High Seas releases; retire
    or make private if upstream terms change.
row_count: 286
data_profile:
  field_count: 39
  search_fields:
  - MRGID
  - GEONAME
  - TERRITORY1
  - SOVEREIGN1
  - ISO_SOV1
  - POL_TYPE
  identity_candidates:
  - field: MRGID
    distinct_values: 286
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique and nonblank in all 286 rows; selected source field ID for feature_id.
  - field: MRGID_EEZ
    distinct_values: 285
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: not_applicable
    notes: One blank value in the supplied FGB; not selected for stable feature identity.
feature_identity:
  strategy: source_field
  source_fields:
  - MRGID
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: inactive_firestore_serving
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/marine-regions-eez.metadata.ndjson.gz
  schema_file: latest/marine-regions-eez.schema.json
  manifest_file: latest/marine-regions-eez.manifest.json
  provenance_default: true
files:
- path: latest/marine-regions-eez.fgb
  format: fgb
  role: canonical
  purpose: Canonical EEZ and high seas polygon dataset with source fields plus feature_id, geometry_hash, and properties_hash
- path: latest/marine-regions-eez.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map metadata-lookup tiles with feature_id
- path: latest/marine-regions-eez.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/marine-regions-eez.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Spanish metadata sidecar materialized from GEONAME translations
- path: latest/marine-regions-eez.metadata.fr.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated French metadata sidecar materialized from GEONAME translations
- path: latest/marine-regions-eez.metadata.id.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Indonesian metadata sidecar materialized from GEONAME translations
- path: latest/marine-regions-eez.metadata.pt.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Portuguese metadata sidecar materialized from GEONAME translations
- path: latest/marine-regions-eez.metadata.pt_br.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Brazilian Portuguese metadata sidecar materialized from GEONAME translations
- path: latest/marine-regions-eez.metadata.sw.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Swahili metadata sidecar materialized from GEONAME translations
- path: latest/marine-regions-eez.metadata-translations.csv
  format: csv
  role: metadata
  purpose: Editable translation source keyed by feature_id, field, locale, and source-value hash
- path: latest/marine-regions-eez.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/marine-regions-eez.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/YYYY-MM-DD/marine-regions-eez.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/marine-regions-eez.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: releases/YYYY-MM-DD/marine-regions-eez.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/YYYY-MM-DD/marine-regions-eez.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Spanish metadata sidecar
- path: releases/YYYY-MM-DD/marine-regions-eez.metadata.fr.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated French metadata sidecar
- path: releases/YYYY-MM-DD/marine-regions-eez.metadata.id.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Indonesian metadata sidecar
- path: releases/YYYY-MM-DD/marine-regions-eez.metadata.pt.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Portuguese metadata sidecar
- path: releases/YYYY-MM-DD/marine-regions-eez.metadata.pt_br.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Brazilian Portuguese metadata sidecar
- path: releases/YYYY-MM-DD/marine-regions-eez.metadata.sw.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Swahili metadata sidecar
- path: releases/YYYY-MM-DD/marine-regions-eez.metadata-translations.csv
  format: csv
  role: release
  purpose: Dated editable translation source
- path: releases/YYYY-MM-DD/marine-regions-eez.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/YYYY-MM-DD/marine-regions-eez.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Manual first-release run record
---

# Marine Regions EEZs and High Seas

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/marine-regions-eez.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** Marine Regions World EEZ v12 and World High Seas v2
- **License / terms:** Creative Commons Attribution 4.0 International, subject to Marine Regions attribution and redistribution guidance
- **Citation:** Flanders Marine Institute (2023). Maritime Boundaries Geodatabase, Maritime Boundaries and Exclusive Economic Zones (200NM), version 12. Available online at https://www.marineregions.org/. https://doi.org/10.14284/632. Flanders Marine Institute (2024). Maritime Boundaries Geodatabase, High Seas, version 2. Available online at https://www.marineregions.org/. https://doi.org/10.14284/696.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains a global Marine Regions maritime-boundary reference layer
combining the World EEZ v12 release dated 2023-10-25 and the World High Seas v2
release dated 2024-10-10. It is published as a shared reference layer for
SkyTruth workflows that need versioned EEZ, overlapping-claim, joint-regime, and
high-seas polygons.

The initial shared-datasets release uses the supplied combined FlatGeobuf as
source material. It preserves the source geometries and attributes, adds the
shared-datasets feature metadata contract, and builds PMTiles for catalog and
map lookup use.

## When to use it

- Use this for reusable global marine-boundary lookups, spatial joins, and map previews.
- Use `feature_id` for stable shared-datasets feature addressing.
- Use `feature_id` or `MRGID` when joining back to Marine Regions records.
- Use localized metadata sidecars when an application needs translated display names for `GEONAME`.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/marine-regions-eez.fgb` | `fgb` | `canonical` | Canonical EEZ and high seas polygon dataset with source fields plus feature_id, geometry_hash, and properties_hash |
| `latest/marine-regions-eez.pmtiles` | `pmtiles` | `companion` | Web map metadata-lookup tiles with feature_id |
| `latest/marine-regions-eez.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/marine-regions-eez.metadata.es.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Spanish metadata sidecar materialized from GEONAME translations |
| `latest/marine-regions-eez.metadata.fr.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated French metadata sidecar materialized from GEONAME translations |
| `latest/marine-regions-eez.metadata.id.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Indonesian metadata sidecar materialized from GEONAME translations |
| `latest/marine-regions-eez.metadata.pt.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Portuguese metadata sidecar materialized from GEONAME translations |
| `latest/marine-regions-eez.metadata.pt_br.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Brazilian Portuguese metadata sidecar materialized from GEONAME translations |
| `latest/marine-regions-eez.metadata.sw.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Swahili metadata sidecar materialized from GEONAME translations |
| `latest/marine-regions-eez.metadata-translations.csv` | `csv` | `metadata` | Editable translation source keyed by feature_id, field, locale, and source-value hash |
| `latest/marine-regions-eez.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/marine-regions-eez.manifest.json` | `json` | `metadata` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/marine-regions-eez.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/marine-regions-eez.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/marine-regions-eez.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/YYYY-MM-DD/marine-regions-eez.metadata.es.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Spanish metadata sidecar |
| `releases/YYYY-MM-DD/marine-regions-eez.metadata.fr.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated French metadata sidecar |
| `releases/YYYY-MM-DD/marine-regions-eez.metadata.id.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Indonesian metadata sidecar |
| `releases/YYYY-MM-DD/marine-regions-eez.metadata.pt.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Portuguese metadata sidecar |
| `releases/YYYY-MM-DD/marine-regions-eez.metadata.pt_br.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Brazilian Portuguese metadata sidecar |
| `releases/YYYY-MM-DD/marine-regions-eez.metadata.sw.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Swahili metadata sidecar |
| `releases/YYYY-MM-DD/marine-regions-eez.metadata-translations.csv` | `csv` | `release` | Dated editable translation source |
| `releases/YYYY-MM-DD/marine-regions-eez.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/YYYY-MM-DD/marine-regions-eez.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Manual first-release run record |
<!-- END GENERATED files-table -->

## Schema notes

The canonical FlatGeobuf has 286 MultiPolygon features in EPSG:4326. It includes
the source fields plus generated `feature_id`, `geometry_hash`, and `properties_hash`
columns. The PMTiles are metadata-lookup tiles and intentionally carry only
`feature_id`; applications should resolve labels and attributes
through the metadata sidecar. Firestore metadata serving is inactive for this
release model, and the release manifest records an `inactive_firestore_serving`
index policy.

## Properties / columns

| Field | Notes |
|---|---|
| `id` | Internal vector-helper identifier; use `feature_id` or `MRGID` for stable joins |
| `MRGID` | Marine Regions identifier; selected source field ID and source for `feature_id` |
| `GEONAME` | Source boundary display name; translatable field in localized metadata sidecars |
| `MRGID_TER1`, `MRGID_TER2`, `MRGID_TER3` | Marine Regions territory identifiers for claimant territories |
| `POL_TYPE` | Source polygon type such as `200NM`, `Overlapping claim`, or `Joint regime` |
| `MRGID_SOV1`, `MRGID_SOV2`, `MRGID_SOV3` | Marine Regions sovereign identifiers |
| `TERRITORY1`, `TERRITORY2`, `TERRITORY3` | Source territory names |
| `ISO_TER1`, `ISO_TER2`, `ISO_TER3` | ISO territory codes where supplied |
| `SOVEREIGN1`, `SOVEREIGN2`, `SOVEREIGN3` | Source sovereign names |
| `X_1`, `Y_1` | Source representative coordinates |
| `MRGID_EEZ` | Source EEZ identifier; not selected for feature identity because one source row is blank |
| `AREA_KM2` | Source area in square kilometers |
| `ISO_SOV1`, `ISO_SOV2`, `ISO_SOV3` | ISO sovereign codes where supplied |
| `UN_SOV1`, `UN_SOV2`, `UN_SOV3` | United Nations sovereign numeric codes where supplied |
| `UN_TER1`, `UN_TER2`, `UN_TER3` | United Nations territory numeric codes where supplied |
| `name` | Source helper display name |
| `source` | Source component label |
| `layer` | Source layer label |
| `path` | Source path label |
| `feature_id` | URL-safe lookup handle copied from `MRGID` |
| `geometry_hash` | Stable hash of canonical feature geometry |
| `properties_hash` | Stable hash of published non-geometry properties |

## Feature Identity Decisions

| Field | Type | Rows | Nonblank | Distinct | Top value count | Domination | Skew | Decision | Concerns |
|---|---:|---:|---:|---:|---:|---:|---:|---|---|
| `MRGID` | Integer | 286 | 286 | 286 | 1 | 0.35% | 1.00 | Selected source field ID | None |
| `MRGID_EEZ` | Integer | 286 | 285 | 285 | 1 | 0.35% | 1.00 | Rejected ID candidate | One blank value |
| `GEONAME` | String | 286 | 285 | 285 | 1 | 0.35% | 1.00 | Search field | One blank value; display/search field |
| `TERRITORY1` | String | 286 | 285 | 254 | 3 | 1.05% | 1.00 | Search field | One blank value; not unique |
| `SOVEREIGN1` | String | 286 | 285 | 157 | 23 | 8.04% | 1.28 | Search field | One blank value; not unique |
| `ISO_SOV1` | String | 286 | 285 | 157 | 23 | 8.04% | 1.35 | Search field | One blank value; not unique |
| `POL_TYPE` | String | 286 | 285 | 3 | 229 | 80.07% | 6.54 | Search/filter field | One blank value; not unique |

`MRGID` is unique, nonblank, and source-maintained in the supplied source, so
the release uses the string form of `MRGID` directly as `feature_id`.

## Localized Metadata

`GEONAME` translations were imported from
`cerulean-cloud/docs/aoi_name_translations.csv` into
`marine-regions-eez.metadata-translations.csv`, then keyed to raw `feature_id`
values and materialized into localized metadata sidecars for `es`, `fr`, `id`,
`pt`, `pt_br`, and `sw`.

The Cerulean source contains 282 EEZ translation rows. The supplied feature set
matched 280 of those MRGIDs. Two Cerulean MRGIDs, `8489` and `33176`, were not
present in the supplied FGB, and six supplied feature MRGIDs, `64430`, `64431`,
`64440`, `64446`, `64459`, and `64460`, had no Cerulean translation row. The
High Seas feature, `63203`, has localized `name` values in all six locales
because the canonical metadata record stores its display label in `name` rather
than `GEONAME`. The 2026-06-09 translation source has 1,680 rows; each localized
sidecar applies 280 translations, has no stale, orphaned, or missing-field rows,
preserves all 286 feature records, and falls back to canonical names for the six
features without localized values.

## Source and Terms

The source datasets are published by the Flanders Marine Institute through
Marine Regions. Cite both Marine Regions DOI records listed above when using
this asset. Marine Regions remains the authoritative source for current
maritime-boundary releases; check the upstream site before treating this shared
copy as current beyond the release date.

Marine Regions asks users to avoid republishing downloaded products as an
independent download service. This shared-datasets copy exists to support
SkyTruth shared data consumers and should preserve upstream attribution,
citation, and version information wherever it is reused.

## Update notes

Refresh this asset manually when Marine Regions publishes newer EEZ or high
seas releases needed by shared consumers. New releases should keep older dated
releases readable, recompute feature metadata and PMTiles, regenerate localized
sidecars from the current translation source or a reviewed replacement, and
record any source-schema or identity changes in this document.
