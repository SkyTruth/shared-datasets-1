---
schema_version: 1
asset_slug: ucdp-ged-events
title: UCDP Georeferenced Event Dataset Global 26.1
category: 400-events-observations
subcategory: 440-field-observations
status: active
access_tier: public
owner: SkyTruth
update_cadence: annual
canonical_format: fgb
canonical_file: latest/ucdp-ged-events.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: UCDP Georeferenced Event Dataset Global version 26.1
source_url: https://ucdp.uu.se/downloads/ged/ged261-csv.zip
license: Creative Commons Attribution 4.0 International (CC BY 4.0); cite relevant UCDP publications
citation: 'Davies, Shawn; Pettersson, Therese; Oberg, Magnus. Organized violence 1989-2025, and violent political protests.
  Journal of Peace Research, 2026. https://doi.org/10.1093/jopres/xjag046; Sundberg, Ralph and Erik Melander. 2013. Introducing
  the UCDP Georeferenced Event Dataset. Journal of Peace Research 50(4): 523-532; Hogbladh, Stina. 2026. UCDP GED Codebook
  version 26.1. Department of Peace and Conflict Research, Uppsala University.'
notes: Manual first upload prepared from the UCDP GED Global 26.1 CSV archive extracted on 2026-03-30. The source CSV contains
  WKT point geometry, so the canonical shared-datasets artifact is FlatGeobuf plus PMTiles, not the source CSV. Release-oriented
  metadata artifacts include source-field feature_id values copied from UCDP id, geometry_hash values, properties_hash values,
  a metadata sidecar, release schema, and release manifest. PMTiles are lightweight metadata-lookup tiles with feature_id
  only. No localized metadata sidecars are generated for the initial upload. FGB sha256 afe0803a89d745ec64632c4f1434b0dc45ba255a0f0db213ca643d2df21546e1;
  PMTiles sha256 caea440c95eb8217ed00101ff72c03ff47616c22eb09460c6d8ed5e0ebd5e926; metadata sidecar sha256 b7cfb7b39ccfcd5cad0121ca06bd1a1294d1b71954f984eaa3e98918c4130414;
  schema sha256 1ec32c53deb6e1b20a62db1111a1c9605d6ed8f08827aebd42062e5e5927c2ad. PMTiles maxzoom 12 with all-point retention
  verified at zoom 0.
admission:
  intended_consumers:
  - SkyTruth conflict, risk, event, and exposure analysis workflows
  shared_rationale: Provides a reusable, citable, geospatially indexed global event-level organized-violence dataset in shared-datasets
    formats, avoiding repeated handling of the large source CSV archive across projects.
  steward: SkyTruth
  update_expectations: Manual annual refresh after UCDP publishes new GED global releases.
  estimated_published_size_gb: 1.1
  alternatives_considered: Project-specific scratch storage, repeated direct UCDP downloads by each consumer, and preserving
    the source CSV as the only shared artifact. Shared-datasets is preferred for reviewed reuse, canonical geospatial formats,
    feature identity, metadata sidecars, and catalog discovery.
  deprecation_policy: Keep dated releases readable; mark deprecated or superseded if UCDP changes terms, schema, scope, or
    a scheduled ingestion replaces the manual snapshot.
bounds:
- -117.9145
- -37.813611
- 155.896681
- 68.97917
geometry_type: Point
row_count: 417968
data_profile:
  field_count: 48
  identity_candidates:
  - field: id
    distinct_values: 417968
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: UCDP codebook identifies id as the persistent event identifier; all values are nonblank URL-safe decimal strings.
  - field: relid
    distinct_values: 417968
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique in this release, but not selected because the codebook says relid can change when an event is updated.
search_fields:
- field: conflict_name
  notes: Conflict label for filtering event records.
- field: dyad_name
  notes: Dyad label for filtering event records.
- field: side_a
  notes: Primary actor on side A.
- field: side_b
  notes: Primary actor on side B.
- field: where_coordinates
  notes: Source location label.
- field: adm_1
  notes: First-order administrative location when available.
- field: adm_2
  notes: Second-order administrative location when available.
- field: country
  notes: Country name.
- field: region
  notes: UCDP region grouping.
feature_identity:
  strategy: source_field
  source_fields:
  - id
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/ucdp-ged-events.metadata.ndjson.gz
  schema_file: latest/ucdp-ged-events.schema.json
  manifest_file: latest/ucdp-ged-events.manifest.json
  provenance_default: true
pmtiles_detail_hint: detailed
files:
- path: latest/ucdp-ged-events.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 point dataset
- path: latest/ucdp-ged-events.pmtiles
  format: pmtiles
  role: companion
  purpose: Metadata-lookup web map tiles generated from the same point features
- path: latest/ucdp-ged-events.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/ucdp-ged-events.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/ucdp-ged-events.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/2026-03-30/ucdp-ged-events.fgb
  format: fgb
  role: release
  purpose: Dated canonical release for UCDP GED Global 26.1 extracted on 2026-03-30
- path: releases/2026-03-30/ucdp-ged-events.pmtiles
  format: pmtiles
  role: release
  purpose: Dated metadata-lookup map-tile release
- path: releases/2026-03-30/ucdp-ged-events.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/2026-03-30/ucdp-ged-events.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/2026-03-30/ucdp-ged-events.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Manual publish run records
---

# UCDP Georeferenced Event Dataset Global 26.1

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** annual
- **Canonical file:** `latest/ucdp-ged-events.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** UCDP Georeferenced Event Dataset Global version 26.1
- **License / terms:** Creative Commons Attribution 4.0 International (CC BY 4.0); cite relevant UCDP publications
- **Citation:** Davies, Shawn; Pettersson, Therese; Oberg, Magnus. Organized violence 1989-2025, and violent political protests. Journal of Peace Research, 2026. https://doi.org/10.1093/jopres/xjag046; Sundberg, Ralph and Erik Melander. 2013. Introducing the UCDP Georeferenced Event Dataset. Journal of Peace Research 50(4): 523-532; Hogbladh, Stina. 2026. UCDP GED Codebook version 26.1. Department of Peace and Conflict Research, Uppsala University.
<!-- END GENERATED asset-summary -->

## What this is

This asset is a geospatial conversion of UCDP Georeferenced Event Dataset Global version 26.1. It contains
event-level organized-violence records from January 1, 1989 through December 31, 2025, including state-based
conflict, non-state conflict, and one-sided violence events.

The source archive is `ged261-csv.zip`, containing `GEDEvent_v26_1.csv`. The source CSV includes WKT point
geometry in `geom_wkt`; the shared-datasets canonical artifact stores that geometry natively as WGS84 point
features and omits the raw WKT column from published properties.

## When to use it

- Use this for global event-level analysis of UCDP organized violence records.
- Use the FlatGeobuf file as the analytical source and the PMTiles file for catalog map preview or lightweight
  visual inspection.
- Use `id` or published `feature_id` for stable event-level lookup across the release.
- Use `relid` only as a release-local reference; UCDP documents that `relid` can change when an event is updated.
- Do not treat event coordinates as exact locations in every case; use `where_prec`, `date_prec`, and
  `event_clarity` when interpreting uncertainty.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/ucdp-ged-events.fgb` | `fgb` | `canonical` | Canonical WGS84 point dataset |
| `latest/ucdp-ged-events.pmtiles` | `pmtiles` | `companion` | Metadata-lookup web map tiles generated from the same point features |
| `latest/ucdp-ged-events.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/ucdp-ged-events.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/ucdp-ged-events.manifest.json` | `json` | `metadata` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/2026-03-30/ucdp-ged-events.fgb` | `fgb` | `release` | Dated canonical release for UCDP GED Global 26.1 extracted on 2026-03-30 |
| `releases/2026-03-30/ucdp-ged-events.pmtiles` | `pmtiles` | `release` | Dated metadata-lookup map-tile release |
| `releases/2026-03-30/ucdp-ged-events.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/2026-03-30/ucdp-ged-events.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/2026-03-30/ucdp-ged-events.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Manual publish run records |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 point geometry parsed from source `geom_wkt`. The source coordinate fields `latitude` and
`longitude` are preserved as attributes for traceability, but the native geometry column is the canonical
geospatial representation.

The source CSV had 417,968 data rows and 49 source columns. The published FGB contains 417,968 point features
and 51 non-geometry properties: 48 source properties after omitting `geom_wkt`, plus `feature_id`,
`geometry_hash`, and `properties_hash`.

`feature_id` is copied from UCDP `id` because the codebook identifies `id` as the persistent event identifier
and the local full-row profile found every value to be unique, nonblank, and URL-safe. `geometry_hash` is
computed from canonical GeoJSON geometry. `properties_hash` is computed from published non-geometry source
properties, excluding `feature_id`, `geometry_hash`, and `properties_hash`.

The PMTiles artifact is a lightweight metadata-lookup tile archive with only `feature_id` properties. Full
attributes are preserved in the canonical FGB and metadata sidecar.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `id` | integer | UCDP persistent event identifier; copied to `feature_id` as a string. |
| `relid` | string | UCDP release/event identifier; can change when an event is updated. |
| `year` | integer | Calendar year of the event. |
| `active_year` | boolean | Whether the conflict or dyad was active in the event year. |
| `code_status` | string | UCDP coding status for the event record. |
| `type_of_violence` | integer | UCDP violence type code: state-based, non-state, or one-sided violence. |
| `conflict_dset_id` | integer | Legacy UCDP conflict dataset identifier. |
| `conflict_new_id` | integer | Current UCDP conflict identifier. |
| `conflict_name` | string | UCDP conflict name. |
| `dyad_dset_id` | integer | Legacy UCDP dyad dataset identifier. |
| `dyad_new_id` | integer | Current UCDP dyad identifier. |
| `dyad_name` | string | UCDP dyad name. |
| `side_a_dset_id` | integer | Legacy UCDP identifier for side A. |
| `side_a_new_id` | integer | Current UCDP identifier for side A. |
| `side_a` | string | Actor or actor grouping on side A. |
| `side_b_dset_id` | integer | Legacy UCDP identifier for side B. |
| `side_b_new_id` | integer | Current UCDP identifier for side B. |
| `side_b` | string | Actor, actor grouping, or civilian side B label. |
| `number_of_sources` | integer | Number of source items recorded for the event. |
| `source_article` | string | Source article or source summary text supplied by UCDP. |
| `source_office` | string | Source office or source outlet metadata. |
| `source_date` | string | Source publication date metadata as supplied by UCDP. |
| `source_headline` | string | Source headline metadata. |
| `source_original` | string | Original source reference text. |
| `where_prec` | integer | UCDP geographic precision code. |
| `where_coordinates` | string | Source location name used for geocoding. |
| `where_description` | string | Text description of the event location. |
| `adm_1` | string | First-order administrative unit name when available. |
| `adm_2` | string | Second-order administrative unit name when available. |
| `latitude` | real | Source latitude in decimal degrees. |
| `longitude` | real | Source longitude in decimal degrees. |
| `priogrid_gid` | integer | PRIO-GRID cell identifier. |
| `country` | string | Country name. |
| `country_id` | integer | UCDP country identifier. |
| `region` | string | UCDP region grouping. |
| `event_clarity` | integer | UCDP event clarity code. |
| `date_prec` | integer | UCDP date precision code. |
| `date_start` | date | Event start date. |
| `date_end` | date | Event end date. |
| `deaths_a` | integer | Best estimate of deaths attributed to side A. |
| `deaths_b` | integer | Best estimate of deaths attributed to side B. |
| `deaths_civilians` | integer | Best estimate of civilian deaths. |
| `deaths_unknown` | integer | Best estimate of deaths with unknown side or status. |
| `best` | integer | UCDP best estimate of total deaths for the event. |
| `high` | integer | UCDP high estimate of total deaths for the event. |
| `low` | integer | UCDP low estimate of total deaths for the event. |
| `gwnoa` | integer | Gleditsch and Ward country code for side A when applicable. |
| `gwnob` | integer | Gleditsch and Ward country code for side B when applicable. |
| `feature_id` | string | URL-safe lookup handle copied from source `id`. |
| `geometry_hash` | string | SHA-256 hash of canonical feature geometry. |
| `properties_hash` | string | SHA-256 hash of published non-geometry source properties. |

## Update notes

This is a manual annual snapshot prepared from UCDP GED Global version 26.1. The source archive was extracted
by UCDP on 2026-03-30. Future updates should create a new dated release after UCDP publishes a new GED global
version, rerun the feature identity profile, and preserve `id` as `feature_id` if the codebook guidance and
uniqueness checks remain valid.

## Known caveats

UCDP event locations and dates carry source uncertainty. Use `where_prec`, `date_prec`, and `event_clarity`
when interpreting precision. Fatality fields are estimates and should be interpreted according to the UCDP GED
codebook. The source CSV archive is not published as a canonical shared-datasets format because the approved
geospatial contract is FlatGeobuf plus PMTiles and release metadata artifacts.
