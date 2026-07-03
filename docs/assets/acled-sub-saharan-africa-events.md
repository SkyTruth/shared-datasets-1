---
schema_version: 1
asset_slug: acled-sub-saharan-africa-events
title: ACLED Sub-Saharan Africa Event-Level Conflict Data
category: 400-events-observations
subcategory: 440-field-observations
status: active
access_tier: private
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/acled-sub-saharan-africa-events.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: ACLED event-level data export for Sub-Saharan Africa
source_url: https://acleddata.com
license: ACLED Terms and Conditions; private internal shared-datasets use confirmed by SkyTruth authorization on 2026-05-08
citation: 'Raleigh, C., Linke, A., Hegre, H., & Karlsen, J. (2010). Introducing ACLED: An Armed Conflict Location and Event
  Dataset. Journal of Peace Research, 47(5), 651-660. ACLED (Armed Conflict Location & Event Data), Sub-Saharan Africa event-level
  export, accessed 2026-07-02, https://acleddata.com.'
license_flags:
- restricted-redistribution
- private-internal-use
- source-authorization-confirmed-2026-05-08
notes: Manual private snapshot from an ACLED event-level XLSX export for Sub-Saharan Africa; events 2025-06-30 through 2026-06-30;
  47937 point features; source XLSX sha256 76271676f26740fad075acc3c0f8d4144a8e41747ca47b296e1679360ca97a36; fgb sha256 5a889bc087d3502cc11d9c9042756d1f5ab118e380140c9ed48c5907099bfb77;
  pmtiles sha256 77122f5be6f44dde9530b0f8d7fd9b0020e86c5db071417e89161599d13663d0; metadata sidecar sha256 32f4d1aa45167f9040adef98cc4cedc0d2d5b25590cb7d4c6a4a5a3e5c91910d;
  schema sha256 ef2684e565b7137cfe0e894c8b7d3217777570d20322ed9c7040fc00956516fa. PMTiles maxzoom 12 metadata-lookup tiles
  with feature_id only, built with the repo-standard GeoJSONSeq to Tippecanoe MBTiles to pmtiles convert path. No localized
  metadata sidecars are generated for the initial upload.
admission:
  intended_consumers:
  - SkyTruth internal regional conflict, risk, and exposure analyses
  shared_rationale: Provides a reusable, curated private snapshot of ACLED event-level conflict and demonstration records
    for Sub-Saharan Africa in canonical geospatial formats with the release feature metadata contract, complementing the existing
    ACLED weekly Admin 1 aggregated assets and the ucdp-ged-events event-level asset.
  steward: SkyTruth
  update_expectations: Manual refreshes from authorized ACLED event-level exports when internal consumers need a newer snapshot.
  estimated_published_size_gb: 0.1
  alternatives_considered: Project-specific scratch storage, direct ACLED download by each consumer, and keeping the XLSX
    export only. Shared-datasets is preferred for reviewed private reuse, documented schema, feature identity, metadata sidecars,
    and catalog discovery.
  deprecation_policy: Keep dated releases readable; mark deprecated or superseded if ACLED changes terms, schema, or geography,
    or if a scheduled ingestion replaces this manual snapshot.
bounds:
- -25.0921
- -34.583
- 72.4234
- 34.7519
geometry_type: MultiPoint
row_count: 47937
data_profile:
  field_count: 34
  identity_candidates:
  - field: event_id_cnty
    distinct_values: 47937
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: ACLED's canonical per-country event identifier; every value is nonblank, unique, and URL-safe in this export.
search_fields:
- field: country
  notes: Country name for filtering event records.
- field: region
  notes: ACLED Sub-Saharan Africa regional grouping (Western, Eastern, Middle, Southern Africa).
- field: admin1
  notes: First-order administrative division.
- field: admin2
  notes: Second-order administrative division.
- field: location
  notes: Named event location.
- field: event_type
  notes: ACLED event type classification.
- field: sub_event_type
  notes: ACLED granular event classification.
- field: disorder_type
  notes: ACLED high-level disorder category.
- field: actor1
  notes: Primary named actor for the event.
- field: actor2
  notes: Second named actor when present.
feature_identity:
  column: feature_id
  strategy: source_field
  source_fields:
  - event_id_cnty
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/acled-sub-saharan-africa-events.metadata.ndjson.gz
  schema_file: latest/acled-sub-saharan-africa-events.schema.json
  manifest_file: latest/acled-sub-saharan-africa-events.manifest.json
  provenance_default: true
pmtiles_detail_hint: detailed
files:
- path: latest/acled-sub-saharan-africa-events.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 point dataset
- path: latest/acled-sub-saharan-africa-events.pmtiles
  format: pmtiles
  role: companion
  purpose: Metadata-lookup web map tiles generated from the same point features
- path: latest/acled-sub-saharan-africa-events.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/acled-sub-saharan-africa-events.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/acled-sub-saharan-africa-events.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/2026-06-30/acled-sub-saharan-africa-events.fgb
  format: fgb
  role: release
  purpose: Dated canonical release for the ACLED export covering events through 2026-06-30
- path: releases/2026-06-30/acled-sub-saharan-africa-events.pmtiles
  format: pmtiles
  role: release
  purpose: Dated metadata-lookup map-tile release
- path: releases/2026-06-30/acled-sub-saharan-africa-events.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/2026-06-30/acled-sub-saharan-africa-events.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/2026-06-30/acled-sub-saharan-africa-events.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
---

# ACLED Sub-Saharan Africa Event-Level Conflict Data

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** private
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/acled-sub-saharan-africa-events.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** ACLED event-level data export for Sub-Saharan Africa
- **License / terms:** ACLED Terms and Conditions; private internal shared-datasets use confirmed by SkyTruth authorization on 2026-05-08
- **Citation:** Raleigh, C., Linke, A., Hegre, H., & Karlsen, J. (2010). Introducing ACLED: An Armed Conflict Location and Event Dataset. Journal of Peace Research, 47(5), 651-660. ACLED (Armed Conflict Location & Event Data), Sub-Saharan Africa event-level export, accessed 2026-07-02, https://acleddata.com.
<!-- END GENERATED asset-summary -->

## What this is

This private asset is a geospatial conversion of an ACLED (Armed Conflict Location & Event Data) event-level
export for Sub-Saharan Africa. It contains individual political violence, demonstration, and strategic
development events from 2025-06-30 through 2026-06-30 across 51 countries and territories in Western, Eastern,
Middle, and Southern Africa.

The source workbook is named `ACLED_Dataset_Sub-SaharanAfrica.xlsx`. Geometry is generated from ACLED's event
`longitude` and `latitude` fields as WGS84 points, and the original coordinate attributes are preserved.

## When to use it

- Use this for private internal event-level analysis of ACLED conflict and demonstration records in
  Sub-Saharan Africa.
- Use the FlatGeobuf file as the analytical source and the PMTiles file for catalog map preview or lightweight
  visual inspection.
- Use `event_id_cnty` or the published `feature_id` (copied from it) for stable event-level lookup.
- Use `geo_precision` and `time_precision` when interpreting location and date uncertainty.
- Do not redistribute this dataset or make it public unless ACLED terms and SkyTruth authorization explicitly
  allow that use.
- For weekly Admin 1 aggregates instead of raw events, see the `acled-europe-central-asia-aggregated-weekly-admin1`
  and `acled-middle-east-aggregated-weekly-admin1` assets.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/acled-sub-saharan-africa-events.fgb` | `fgb` | `canonical` | Canonical WGS84 point dataset |
| `latest/acled-sub-saharan-africa-events.pmtiles` | `pmtiles` | `companion` | Metadata-lookup web map tiles generated from the same point features |
| `latest/acled-sub-saharan-africa-events.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/acled-sub-saharan-africa-events.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/acled-sub-saharan-africa-events.manifest.json` | `json` | `metadata` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/2026-06-30/acled-sub-saharan-africa-events.fgb` | `fgb` | `release` | Dated canonical release for the ACLED export covering events through 2026-06-30 |
| `releases/2026-06-30/acled-sub-saharan-africa-events.pmtiles` | `pmtiles` | `release` | Dated metadata-lookup map-tile release |
| `releases/2026-06-30/acled-sub-saharan-africa-events.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/2026-06-30/acled-sub-saharan-africa-events.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/2026-06-30/acled-sub-saharan-africa-events.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
<!-- END GENERATED files-table -->

## Schema notes

Geometry is WGS84 point geometry derived from the source `longitude` and `latitude` columns (stored as
MultiPoint in the FGB layer). The source coordinate fields are preserved as attributes for traceability.

The source workbook had one visible sheet with 47,937 data rows and 31 source columns. The published FGB
contains 47,937 point features and 34 non-geometry properties: 31 source properties plus `feature_id`,
`geometry_hash`, and `properties_hash`. `event_date` is stored as a DateTime field in the FGB; the metadata
sidecar preserves it as an ISO `YYYY-MM-DD` string.

`feature_id` is copied from ACLED `event_id_cnty` because it is ACLED's canonical per-country event identifier
and the local full-row profile found every value to be unique, nonblank, and URL-safe. `geometry_hash` is
computed from canonical GeoJSON geometry. `properties_hash` is computed from published non-geometry source
properties, excluding `feature_id`, `geometry_hash`, and `properties_hash`.

The PMTiles artifact is a lightweight metadata-lookup tile archive with only `feature_id` properties. Full
attributes are preserved in the canonical FGB and metadata sidecar.

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `event_id_cnty` | string | ACLED unique event identifier with country prefix; copied to `feature_id`. |
| `event_date` | datetime | Date the event occurred (date precision applies; see `time_precision`). |
| `year` | integer | Calendar year of the event. |
| `time_precision` | integer | ACLED date precision code (1 = exact day, higher = less precise). |
| `disorder_type` | string | ACLED high-level disorder category: Political violence, Demonstrations, Strategic developments, or combined categories. |
| `event_type` | string | ACLED event type: Battles, Explosions/Remote violence, Protests, Riots, Strategic developments, or Violence against civilians. |
| `sub_event_type` | string | ACLED's more granular event classification. |
| `actor1` | string | Named primary actor involved in the event. |
| `assoc_actor_1` | string | Actors associated with or identifying `actor1`, when reported. |
| `inter1` | string | ACLED actor-type label for `actor1`, such as State forces or Rioters. |
| `actor2` | string | Second named actor when present. |
| `assoc_actor_2` | string | Actors associated with or identifying `actor2`, when reported. |
| `inter2` | string | ACLED actor-type label for `actor2`. |
| `interaction` | string | Interaction label combining the actor types involved. |
| `civilian_targeting` | string | Civilian targeting flag when the event targeted civilians; blank otherwise. |
| `iso` | integer | Numeric ISO 3166-1 country code. |
| `region` | string | ACLED regional grouping: Western, Eastern, Middle, or Southern Africa. |
| `country` | string | Country or territory. |
| `admin1` | string | First-order administrative division. |
| `admin2` | string | Second-order administrative division when available. |
| `admin3` | string | Third-order administrative division when available. |
| `location` | string | Named location where the event occurred. |
| `latitude` | real | Event latitude in decimal degrees. |
| `longitude` | real | Event longitude in decimal degrees. |
| `geo_precision` | integer | ACLED geographic precision code (1 = exact location, higher = less precise). |
| `source` | string | Sources reporting the event. |
| `source_scale` | string | Geographic scale of the reporting sources. |
| `notes` | string | Short event description written by ACLED researchers. |
| `fatalities` | integer | Best available estimate of reported fatalities for the event. |
| `tags` | string | Additional structured event tags, when present. |
| `timestamp` | integer | Unix epoch timestamp of ACLED's last update to the event record. |
| `feature_id` | string | URL-safe lookup handle copied from source `event_id_cnty`. |
| `geometry_hash` | string | SHA-256 hash of canonical feature geometry. |
| `properties_hash` | string | SHA-256 hash of published non-geometry source properties. |

## Update notes

Manually converted on 2026-07-02 from the supplied ACLED event-level XLSX export. This is the first upload of
this asset; the maintainer chose to generate no metadata translations for the initial upload.

Output summary:

- Source XLSX SHA-256: `76271676f26740fad075acc3c0f8d4144a8e41747ca47b296e1679360ca97a36`
- Source data rows: 47,937
- Published point features: 47,937
- Event date range: 2025-06-30 to 2026-06-30
- Countries and territories: 51
- Extent: -25.092100, -34.583000 to 72.423400, 34.751900
- FGB SHA-256: `5a889bc087d3502cc11d9c9042756d1f5ab118e380140c9ed48c5907099bfb77`
- PMTiles SHA-256: `77122f5be6f44dde9530b0f8d7fd9b0020e86c5db071417e89161599d13663d0`
- Metadata sidecar SHA-256: `32f4d1aa45167f9040adef98cc4cedc0d2d5b25590cb7d4c6a4a5a3e5c91910d`
- Schema SHA-256: `ef2684e565b7137cfe0e894c8b7d3217777570d20322ed9c7040fc00956516fa`
- PMTiles validation: magic bytes, `pmtiles verify`, `pmtiles show` inspection, and zoom-0 tile decode with
  `feature_id` property checks via the release vector contract validator.

## Known caveats

This is a private licensed-data snapshot. Do not make the asset public, redistribute it outside authorized
SkyTruth use, or expose it through public catalog views without a separate terms review.

ACLED event locations and dates carry source uncertainty. Use `geo_precision` and `time_precision` when
interpreting precision, and treat `fatalities` as ACLED's best reported estimate rather than a confirmed count.

ACLED updates its event data continuously. This manual snapshot covers events through 2026-06-30 and will
become stale unless refreshed from a newer authorized export.
