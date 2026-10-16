---
schema_version: 1
asset_slug: mpatlas-zone-assessments
title: MPAtlas Marine Protected Area Zone Assessments
category: 100-geographic-reference
subcategory: 130-protected-areas
status: active
access_tier: private
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/mpatlas-zone-assessments.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: 'Marine Conservation Institute, MPAtlas public API v4: https://guide.mpatlas.org/api/public/v4/zone/geojson/'
source_url: https://marine-conservation.org/mpatlas/download/
license: MPAtlas Data Use Terms (https://marine-conservation.org/data-use/); non-commercial use; redistribution covered by
  existing SkyTruth permission as confirmed by maintainer on 2026-10-16.
citation: 'Marine Conservation Institute and Partners (2026). MPAtlas. Glen Ellen, CA. https://mpatlas.org/ [Accessed 16/10/2026].
  UNEP-WCMC and IUCN, Protected Planet: The World Database on Protected Areas (WDPA) [via MPAtlas, 10-2026], Cambridge, UK.'
license_flags:
- noncommercial
- redistribution-permission-confirmed
notes: 'Snapshot retrieved 2026-10-16: 1,857 available zone geometries from 1,882 source records; 25 missing geometries excluded.
  Includes 1,841 polygon zones and 16 point locations. Eighteen source geometries repaired with GDAL MakeValid. feature_id
  is the source zone_id as text; source assessment attributes retained.'
admission:
  intended_consumers:
  - SkyTruth 30x30 marine protection analysis (existing MPAtlas API consumer)
  shared_rationale: Reusable assessed marine protected-area zone geometries and original assessment attributes, with a stable
    dated snapshot and compact catalog display artifacts.
  source_license_citation_status: Official MPAtlas API and attribution verified; maintainer confirmed existing SkyTruth permission
    covers shared distribution on 2026-10-16. Access remains private; public access has not been authorized.
  steward: Jonathan Raphael (@jonaraphael), SkyTruth
  update_expectations: Manual snapshots from the current MPAtlas API; refresh on curator request.
  estimated_published_size_gb: '0.4254'
  alternatives_considered: Direct API access requires each consumer to fetch and normalize a mutable 286 MiB GeoJSON. WDPA
    has broader boundary coverage but does not substitute for MPAtlas zone assessments. Project-local storage remains an option
    for non-reusable derivatives.
  deprecation_policy: Retain citable snapshots and document upstream retirement or replacement; deprecate with consumer guidance
    rather than deleting releases.
bounds:
- -180.0
- -85.4118872
- 180.0
- 86.453711112
geometry_type: mixed
row_count: 1857
data_profile:
  field_count: 48
  identity_candidates:
  - field: zone_id
    distinct_values: 1857
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Selected stable zone identity.
  - field: assessment_id
    distinct_values: 1857
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: 0 empty rows; assessment_id identifies the assessment, while site/WDPA IDs can repeat across zones.
  - field: site_id
    distinct_values: 1305
    duplicate_value_count: 221
    duplicate_row_count: 773
    status: non_unique
    notes: 0 empty rows; assessment_id identifies the assessment, while site/WDPA IDs can repeat across zones.
  - field: wdpa_id
    distinct_values: 1125
    duplicate_value_count: 169
    duplicate_row_count: 574
    status: non_unique
    notes: 327 empty rows; assessment_id identifies the assessment, while site/WDPA IDs can repeat across zones.
  notes: Exact full-column profile over all 1,857 records with available geometry and 45 upstream properties. Three release
    identity/hash columns are added.
search_fields:
- field: site_name
  distinct_values: 1246
  notes: Source value retained for name search or filtering.
- field: zone_name
  distinct_values: 1706
  notes: Source value retained for name search or filtering.
- field: country
  distinct_values: 101
  notes: Source value retained for name search or filtering.
- field: sovereign
  distinct_values: 84
  notes: Source value retained for name search or filtering.
- field: mpaguide_protection_level
  distinct_values: 6
  notes: Source value retained for name search or filtering.
- field: assessment_establishment_stage
  distinct_values: 4
  notes: Source value retained for name search or filtering.
feature_identity:
  strategy: source_field
  source_fields:
  - zone_id
feature_metadata:
  storage: metadata_sidecar_v1
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/mpatlas-zone-assessments.metadata.ndjson.gz
  schema_file: latest/mpatlas-zone-assessments.schema.json
  manifest_file: latest/mpatlas-zone-assessments.manifest.json
  provenance_default: true
translation_locales: []
translation_fields: []
files:
- path: latest/mpatlas-zone-assessments.fgb
  format: fgb
  role: canonical
  purpose: Canonical zone geometries and source properties in WGS84
- path: latest/mpatlas-zone-assessments.pmtiles
  format: pmtiles
  role: companion
  purpose: Display tiles with geometry and feature_id only
- path: latest/mpatlas-zone-assessments.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Feature metadata sidecar keyed by feature_id
- path: latest/mpatlas-zone-assessments.schema.json
  format: json
  role: metadata
  purpose: Release field schema
- path: latest/mpatlas-zone-assessments.manifest.json
  format: json
  role: metadata
  purpose: Source, artifact hashes, identity policy, and validation evidence
- path: releases/YYYY-MM-DD/mpatlas-zone-assessments.fgb
  format: fgb
  role: release
  purpose: Canonical zone geometries and source properties in WGS84
- path: releases/YYYY-MM-DD/mpatlas-zone-assessments.pmtiles
  format: pmtiles
  role: release
  purpose: Display tiles with geometry and feature_id only
- path: releases/YYYY-MM-DD/mpatlas-zone-assessments.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Feature metadata sidecar keyed by feature_id
- path: releases/YYYY-MM-DD/mpatlas-zone-assessments.schema.json
  format: json
  role: metadata
  purpose: Release field schema
- path: releases/YYYY-MM-DD/mpatlas-zone-assessments.manifest.json
  format: json
  role: metadata
  purpose: Source, artifact hashes, identity policy, and validation evidence
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Protected publication run record
---

# MPAtlas Marine Protected Area Zone Assessments

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** private
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/mpatlas-zone-assessments.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** Marine Conservation Institute, MPAtlas public API v4: https://guide.mpatlas.org/api/public/v4/zone/geojson/
- **License / terms:** MPAtlas Data Use Terms (https://marine-conservation.org/data-use/); non-commercial use; redistribution covered by existing SkyTruth permission as confirmed by maintainer on 2026-10-16.
- **Citation:** Marine Conservation Institute and Partners (2026). MPAtlas. Glen Ellen, CA. https://mpatlas.org/ [Accessed 16/10/2026]. UNEP-WCMC and IUCN, Protected Planet: The World Database on Protected Areas (WDPA) [via MPAtlas, 10-2026], Cambridge, UK.
<!-- END GENERATED asset-summary -->

## What this is

A dated snapshot of the [MPAtlas public zone geometry API](https://guide.mpatlas.org/api/public/v4/zone/geojson/), retrieved on 2026-10-16. MPAtlas, maintained by Marine Conservation Institute and partners, adds science-based assessments of marine protected areas and zones. It is not a complete inventory of all global marine protected areas. See the [official download description](https://marine-conservation.org/mpatlas/download/) and [methods](https://mpatlas.org/methods/).

## When to use it

Use the FlatGeobuf file for zone-level spatial analysis and the PMTiles file for display. The existing SkyTruth 30x30 pipeline consumes this upstream API. This shared asset preserves upstream v4 field names; consumers of its historical normalized field names must map them explicitly. In particular, `establishment_stage` is site-level and `assessment_establishment_stage` is zone-effective.

Retain `zone_id` when joining releases. `assessment_id` can identify a different assessment when the same zone is reassessed. Unknown protection does not imply that a site is unprotected. The 16 point locations represent locations supplied by MPAtlas, not boundary polygons, and cannot be used for area calculations.

## Source, access and attribution

The [MPAtlas Data Use Terms](https://marine-conservation.org/data-use/) restrict commercial use and redistribution. On 2026-10-16 the maintainer confirmed that existing SkyTruth permission covers shared distribution. This asset remains private; this record does not grant public access or permission beyond the applicable agreement. Preserve both MPAtlas and underlying WDPA attribution, together with any relevant assessment-author and affiliated-report citations.

Citation: Marine Conservation Institute and Partners (2026). MPAtlas. Glen Ellen, CA. https://mpatlas.org/ [Accessed 16/10/2026]. UNEP-WCMC and IUCN, Protected Planet: The World Database on Protected Areas (WDPA) [via MPAtlas, 10-2026], Cambridge, UK.

The API did not expose a version identifier in the downloaded response. The release date identifies retrieval of the current API snapshot. The downloaded GeoJSON SHA-256 is `2a0808def01d491c9d7facce6d57e9209b8762e3f0586e3d48ef05a8a406f37d` (299,562,824 bytes).

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/mpatlas-zone-assessments.fgb` | `fgb` | `canonical` | Canonical zone geometries and source properties in WGS84 |
| `latest/mpatlas-zone-assessments.pmtiles` | `pmtiles` | `companion` | Display tiles with geometry and feature_id only |
| `latest/mpatlas-zone-assessments.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Feature metadata sidecar keyed by feature_id |
| `latest/mpatlas-zone-assessments.schema.json` | `json` | `metadata` | Release field schema |
| `latest/mpatlas-zone-assessments.manifest.json` | `json` | `metadata` | Source, artifact hashes, identity policy, and validation evidence |
| `releases/YYYY-MM-DD/mpatlas-zone-assessments.fgb` | `fgb` | `release` | Canonical zone geometries and source properties in WGS84 |
| `releases/YYYY-MM-DD/mpatlas-zone-assessments.pmtiles` | `pmtiles` | `release` | Display tiles with geometry and feature_id only |
| `releases/YYYY-MM-DD/mpatlas-zone-assessments.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Feature metadata sidecar keyed by feature_id |
| `releases/YYYY-MM-DD/mpatlas-zone-assessments.schema.json` | `json` | `metadata` | Release field schema |
| `releases/YYYY-MM-DD/mpatlas-zone-assessments.manifest.json` | `json` | `metadata` | Source, artifact hashes, identity policy, and validation evidence |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Protected publication run record |
<!-- END GENERATED files-table -->

## Geometry and preparation

The upstream response has 1,882 rows and 45 properties. This geometry asset contains the 1,857 rows with geometry; 25 null-geometry records are excluded. GDAL MakeValid repairs 18 source geometries with self-intersections. Polygon and point records are promoted to MultiPolygon and MultiPoint respectively. The result contains 1,841 MultiPolygons and 16 MultiPoints, all nonempty and valid, in EPSG:4326. Canonical geometry is not simplified. PMTiles display geometry may be simplified by the tile builder.

Null-geometry source zone IDs: 6976, 7015, 6995, 6969, 6051, 7083, 7088, 7018, 7225, 7224, 6054, 3047, 6973, 6996, 3040, 6946, 6959, 7090, 6966, 7320, 6927, 6972, 7065, 7226, 7013.

Repaired source zone IDs: 6707, 3989, 6844, 6510, 7058, 7228, 6984, 762, 6754, 7418, 6978, 7348, 6854, 6951, 322, 7362, 6101, 7217.

Original property values are preserved. Arrays and objects in `affiliated_reports`, `assessment_projects`, and `classes` are serialized as canonical JSON text, so all 45 original property names remain available in the FlatGeobuf and metadata sidecar. Nulls, booleans, identifiers, and timestamp text retain their source meanings. Source text may contain HTML; consumers must escape or sanitize it before display.

## Feature identity and profile

Exact full-data statistics cover all 1,857 published geometries. There are 45 source properties and 48 canonical non-geometry columns after adding `feature_id`, `geometry_hash`, and `properties_hash`. `feature_id` copies the unique, non-null alphanumeric representation of `zone_id`. No generated sequence is needed. Hashes use the repository release feature model and the stored canonical geometry/properties.

| Field | Type | Distinct / rows | Empty | Domination | Skew | Top example | Decision / concern |
|---|---|---:|---:|---:|---:|---|---|
| `zone_id` | integer | 1857 / 1,857 | 0.0% | 0.05% | 1.0 | 7521 | Selected source identity |
| `assessment_id` | integer | 1857 / 1,857 | 0.0% | 0.05% | 1.0 | 105114 | Unique assessment identity, not preferred zone identity |
| `site_id` | integer | 1305 / 1,857 | 0.0% | 1.51% | 19.68 | 478 | Repeated across zones |
| `wdpa_id` | integer | 1125 / 1,857 | 17.61% | 1.51% | 20.59 | 555571016 | Missing and repeated |
| `site_name` | string | 1246 / 1,857 | 0.0% | 1.51% | 18.79 | iSimangaliso | Search; repeated names |
| `zone_name` | string | 1706 / 1,857 | 0.0% | 1.62% | 27.56 | Default Zone | Search; repeated names |
| `country` | string | 101 / 1,857 | 0.05% | 15.94% | 16.11 | USA | Filter; one empty row |
| `mpaguide_protection_level` | string | 6 / 1,857 | 0.0% | 39.63% | 2.38 | unknown | Filter; unknown is a source category |
| `assessment_establishment_stage` | string | 4 / 1,857 | 0.0% | 46.15% | 1.85 | actively managed | Filter; repeated categories |

Domination is the most frequent value count divided by all profiled rows. Skew is its count divided by the expected count under a uniform distribution of nonempty distinct values. Generated monotonic decimal IDs were considered as a fallback and are unnecessary because `zone_id` is suitable.

## Schema notes

The metadata schema lists the 45 source properties. The canonical FGB adds three release identity/hash columns. JSON-valued source properties use canonical JSON strings. Source dates and timestamps remain text, and missing values remain null.

## Properties / columns

| Field | Published type | Meaning |
|---|---|---|
| `affiliated_reports` | string | Canonical JSON text preserving the upstream report array. |
| `assessment_author` | string | Source assessment author attribution. |
| `assessment_country` | string | Country code recorded for the assessment. |
| `assessment_created_date` | string | Original source assessment creation timestamp as text. |
| `assessment_date` | string | Source date of the assessment. |
| `assessment_designated_date` | string | Source assessment-level designation date. |
| `assessment_establishment_stage` | string | Zone-effective establishment stage used by the assessment; retain separately from site-level establishment_stage. |
| `assessment_id` | integer | Identifier of the published zone assessment in this snapshot. |
| `assessment_implemented_date` | string | Source assessment-level implementation date. |
| `assessment_modified_date` | string | Original source assessment modification timestamp as text. |
| `assessment_projects` | string | Canonical JSON text preserving the upstream project array. |
| `assessment_proposed_date` | string | Source assessment-level proposed date. |
| `assessment_purpose` | string | Source assessment purpose. |
| `assessment_sovereign` | string | Sovereign code recorded for the assessment. |
| `classes` | string | Canonical JSON text preserving activity assessments, including mining, dredging/dumping, anchoring, infrastructure, aquaculture, fishing/extraction and nonextractive activities. |
| `country` | string | Source country code or code list; one row is empty. |
| `designated_date` | string | Source site-level designation date; text or null. |
| `establishment_citation` | string | Source citations supporting establishment information. |
| `establishment_info` | string | Source establishment explanation; may contain HTML. |
| `establishment_stage` | string | Site-level establishment stage; distinct from the zone-effective assessment stage. |
| `formally_recognized` | boolean | Source boolean indicating formal recognition; null is retained. |
| `geom_url` | string | Source per-zone geometry endpoint. |
| `implemented_date` | string | Source site-level implementation date; text or null. |
| `mpaguide_protection_level` | string | Source MPA Guide protection classification, including unknown; not an identity field. |
| `pa_type` | string | Source protected-area type classification. |
| `pa_type_citation` | string | Source citation supporting protected-area classification; may contain HTML. |
| `pa_type_info` | string | Source explanatory text for the protected-area type; may contain HTML. |
| `proposed_date` | string | Source site-level proposed date; text or null. |
| `regulations_in_force` | boolean | Source boolean indicating regulations are in force; null is retained. |
| `site_designation` | string | Source protected-area designation. |
| `site_id` | integer | MPAtlas parent protected-area site identifier; multiple zones can share a site. |
| `site_name` | string | Source protected-area site name. |
| `sovereign` | string | Source sovereign code or code list. |
| `status` | string | Source assessment publication status. |
| `unprotected_water_column` | boolean | Source boolean indicating an unprotected water column; null is retained. |
| `unprotected_water_column_citation` | string | Source citation supporting water-column information. |
| `unprotected_water_column_info` | string | Source explanation of water-column protection. |
| `wdpa_id` | integer | Upstream WDPA identifier; nullable and not unique across zones. |
| `wdpa_pid` | string | Upstream WDPA parcel identifier; nullable. |
| `zone_area` | number | Upstream zone area value; do not substitute it for measured geometry area. Unit definition requires source confirmation. |
| `zone_created_date` | string | Original source zone creation timestamp as text. |
| `zone_full_name` | string | Source display label combining site, designation, zone and assessment status. |
| `zone_id` | integer | MPAtlas zone identifier; copied as text into feature_id. |
| `zone_modified_date` | string | Original source zone modification timestamp as text. |
| `zone_name` | string | Source zone name. |
| `feature_id` | string | Source zone_id copied as an alphanumeric lookup handle. |
| `geometry_hash` | string | SHA-256 identity of stored canonical geometry. |
| `properties_hash` | string | SHA-256 identity of canonical source properties. |

## Update notes

The maintainer explicitly chose no translations for the initial release on 2026-10-16. Original source-language metadata is retained. Translations may be added in a later reviewed metadata revision.

Refresh manually from the official API on curator request; this intake does not create a scheduled ingestion job. Recheck terms, permission, schema, missing geometries, source identity uniqueness, and source version before each refresh. Publish a new dated release before changing latest. Retain citable releases, document upstream retirement or replacement, and provide consumer guidance before deprecation.

## Build and validation evidence

The initial candidates total approximately 213 MB per release, or 426 MB for the dated release plus latest copies. PMTiles uses automatically selected maximum zoom 12 because the dataset includes point locations. Its zoom-0 representative tile decodes to 824 features and contains only `feature_id` properties. Archive magic, `pmtiles verify`, metadata inspection, geometry validity, all 1,857 geometry/property hashes, source-ID uniqueness, and sidecar/schema consistency passed local checks. Destination generations will be bound by protected publication; local validation does not establish publication success.

| Artifact | Bytes | SHA-256 |
|---|---:|---|
| `mpatlas-zone-assessments.metadata.ndjson.gz` | 1,191,409 | `b1f8394371a17545c88b8c71c176ce5c218a2c0873bc32c6b2274ff412b4ae23` |
| `mpatlas-zone-assessments.pmtiles` | 48,269,928 | `9c0d18aebf3484177406ddadce9443b715761b87d7cb682e0d20190d8808b89a` |
| `mpatlas-zone-assessments.fgb` | 163,206,024 | `2edc9bfcd7e9515beb3e30cf869dcec87ecdc233a84fce1289b47566631d1d1f` |
| `mpatlas-zone-assessments.schema.json` | 5,608 | `a3012f7c1fa61519b35919ead23952dfbd6c6f6312ad7c95bf78c5493ffa1135` |
| `mpatlas-zone-assessments.manifest.json` | 14,884 | `b5458a331a01e20743d1ff79f021bded78176c736e91d4d34752c977dbbf1b06` |

| Tool | Resolved path | Reported version |
|---|---|---|
| ogr2ogr | `/usr/local/Cellar/gdal/3.13.1_1/bin/ogr2ogr` | GDAL 3.13.1 "Iowa City", released 2026/06/01 |
| ogrinfo | `/usr/local/Cellar/gdal/3.13.1_1/bin/ogrinfo` | GDAL 3.13.1 "Iowa City", released 2026/06/01 |
| tippecanoe | `/usr/local/Cellar/tippecanoe/2.79.0/bin/tippecanoe` | tippecanoe v2.79.0 |
| tippecanoe-decode | `/usr/local/Cellar/tippecanoe/2.79.0/bin/tippecanoe-decode` | Installed with Tippecanoe 2.79.0; decoder has no version flag |
| pmtiles | `/usr/local/Cellar/pmtiles/1.30.3/bin/pmtiles` | Homebrew package 1.30.3; binary reports pmtiles dev, commit none, built at unknown |

The PMTiles executable reports a development version; its binary checksum is retained with the local build evidence and release manifest. Source and generated artifact checksums identify the exact bytes. The protected publisher finalizes the manifest with observed destination generations, so its published checksum will differ from the prepared candidate checksum above.
