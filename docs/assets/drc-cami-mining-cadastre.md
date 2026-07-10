---
schema_version: 1
asset_slug: drc-cami-mining-cadastre
title: DRC CAMI Mining Cadastre
category: 300-infrastructure-industrial
subcategory: 320-mining
status: active
access_tier: private
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/drc-cami-mining-cadastre.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
source: DRC Mining Cadastre Portal, maintained by Cadastre Minier (CAMI) and Spatial Dimension
source_url: https://drclicences.cami.cd/EN/
license: Rights reserved; portal disclaimer states republication, redistribution, sale, sublicense, modification, and similar
  reuse require prior written permission from CAMI and Spatial Dimension
citation: Cadastre Minier (CAMI) and Spatial Dimension. DRC Mining Cadastre Portal. https://drclicences.cami.cd/EN/. Accessed
  2026-06-29; portal data updated 2026-06-26.
license_flags:
- confirm-license
- permission-required
- restricted-redistribution
notes: Draft private upload package prepared from the public DRC Mining Cadastre Portal, whose page configuration reported
  DateUpdated as 26 June 2026 at 09:29 SAST. The canonical release excludes repeated aggregate ActiveLicenses layers and admin/geology
  context layers. Do not publish publicly until CAMI and Spatial Dimension permission or compatible terms are confirmed.
admission:
  intended_consumers:
  - SkyTruth analysts needing a reusable DRC mining cadastre licence/application polygon layer
  - Shared map/catalog users after license and access-tier review
  shared_rationale: Provides reusable DRC mining licence, application, artisanal exploitation zone, amodiation, restricted
    area, and agreement polygons in canonical FGB and PMTiles form, avoiding repeated portal scraping and preserving source
    provenance.
  alternatives_considered: Direct portal use avoids redistribution concerns but is less reproducible and requires live ArcGIS
    proxy access. Project-local storage would avoid catalog overhead but duplicate a dataset likely useful across mining and
    environmental workflows. Public shared-datasets publication is not appropriate without permission or terms review.
  steward: SkyTruth shared-datasets maintainers, pending confirmation of a named internal owner
  update_expectations: Manual refresh from the portal until source terms and refresh ownership are approved.
  estimated_published_size_gb: 1
  deprecation_policy: If CAMI or Spatial Dimension declines permission, terms become incompatible, or an authoritative open
    replacement appears, mark this asset deprecated or retired, keep any authorized historical releases readable only while
    allowed, and point consumers to the official portal or successor source.
bounds:
- 12.1916667
- -13.4333333
- 30.7666667
- 5.05
geometry_type: MultiPolygon
row_count: 5128
data_profile:
  field_count: 68
  identity_candidates:
  - field: source_record_key
    distinct_values: 5128
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Deterministic derived assignment key from source service, layer id, object id, guidShape, and Code; not an original
      CAMI identifier.
  - field: Code
    distinct_values: 4758
    duplicate_value_count: 348
    duplicate_row_count: 718
    status: non_unique
    notes: CAMI licence or application code-like field, but repeated across source layers and parts.
  - field: guidLicense
    distinct_values: 4739
    duplicate_value_count: 310
    duplicate_row_count: 621
    status: non_unique
    notes: Source GUID field, blank for some rows, non-unique across the merged table, and not URL-safe for feature_id.
  - field: guidShape
    distinct_values: 4777
    duplicate_value_count: 348
    duplicate_row_count: 699
    status: non_unique
    notes: Source shape GUID field, non-unique across the merged table and not URL-safe for feature_id.
  - field: ESRI_OID
    distinct_values: 4603
    duplicate_value_count: 430
    duplicate_row_count: 955
    status: non_unique
    notes: Object ID is only layer-local and repeats across source services and layers.
  notes: No original CAMI field was globally unique, nonblank, and URL-safe across the curated merged table. The draft release
    uses generated decimal feature_id values assigned from source_record_key.
search_fields:
- Code
- Type
- Status
- Region
- Parties
- MapRef
- Commodities
- source_layer_name
feature_identity:
  column: feature_id
  strategy: generated_sequence_source_fields
  source_fields:
  - source_record_key
  hash_algorithm: sha256
  canonicalization_version: release-feature-model-v2
  generated_id_type: monotonic_integer_string
  assignment_key:
  - source_record_key
  previous_release: null
  next_generated_id: '5129'
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/drc-cami-mining-cadastre.metadata.ndjson.gz
  schema_file: latest/drc-cami-mining-cadastre.schema.json
  manifest_file: latest/drc-cami-mining-cadastre.manifest.json
  provenance_default: true
pmtiles_maxzoom: 12
pmtiles_maxzoom_reason: pmtiles_detail_hint=detailed maps to zoom 12
pmtiles_detail_hint: detailed
files:
- path: latest/drc-cami-mining-cadastre.fgb
  format: fgb
  role: canonical
  purpose: Canonical WGS84 polygon dataset with full published attributes and release feature metadata columns
- path: latest/drc-cami-mining-cadastre.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same polygon features with feature_id only
- path: latest/drc-cami-mining-cadastre.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/drc-cami-mining-cadastre.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/drc-cami-mining-cadastre.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/YYYY-MM-DD/drc-cami-mining-cadastre.fgb
  format: fgb
  role: release
  purpose: Dated canonical releases
- path: releases/YYYY-MM-DD/drc-cami-mining-cadastre.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile releases
- path: releases/YYYY-MM-DD/drc-cami-mining-cadastre.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/YYYY-MM-DD/drc-cami-mining-cadastre.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/YYYY-MM-DD/drc-cami-mining-cadastre.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
---

# DRC CAMI Mining Cadastre

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** private
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/drc-cami-mining-cadastre.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** DRC Mining Cadastre Portal, maintained by Cadastre Minier (CAMI) and Spatial Dimension
- **License / terms:** Rights reserved; portal disclaimer states republication, redistribution, sale, sublicense, modification, and similar reuse require prior written permission from CAMI and Spatial Dimension
- **Citation:** Cadastre Minier (CAMI) and Spatial Dimension. DRC Mining Cadastre Portal. https://drclicences.cami.cd/EN/. Accessed 2026-06-29; portal data updated 2026-06-26.
<!-- END GENERATED asset-summary -->

## What this is

This draft asset packages polygon features from the DRC Mining Cadastre Portal maintained by Cadastre Minier (CAMI) and Spatial Dimension. It covers DRC mining cadastre licence and application records, including active exploration and exploitation permits, artisanal exploitation zones, registered amodiations, geological research zones, restricted areas, and agreements.

The local source download used the portal session proxy to page ArcGIS FeatureServer layers as GeoJSON. The curated canonical table excludes repeated aggregate `ActiveLicenses` layers and excludes admin/geology map context layers, leaving 5,128 granular cadastre features. The portal page configuration reported the source data as updated on 2026-06-26 at 09:29 SAST.

## When to use it

- Use this as a draft private polygon package for DRC mining cadastre licences, applications, and related cadastre zones.
- Use the FGB for analysis and PMTiles for catalog or web-map display.
- Use `feature_id` for row lookup in this release, and use `source_record_key` for source-record lineage.
- Use `Code`, `Type`, `Status`, `Region`, `Parties`, `MapRef`, and `Commodities` for filtering and search.
- Do not treat this as approved for public redistribution until CAMI and Spatial Dimension permission or compatible terms are confirmed.
- Do not treat this as a legal title opinion, authoritative status certificate, or complete replacement for the official CAMI portal.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/drc-cami-mining-cadastre.fgb` | `fgb` | `canonical` | Canonical WGS84 polygon dataset with full published attributes and release feature metadata columns |
| `latest/drc-cami-mining-cadastre.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same polygon features with feature_id only |
| `latest/drc-cami-mining-cadastre.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/drc-cami-mining-cadastre.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/drc-cami-mining-cadastre.manifest.json` | `json` | `metadata` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/drc-cami-mining-cadastre.fgb` | `fgb` | `release` | Dated canonical releases |
| `releases/YYYY-MM-DD/drc-cami-mining-cadastre.pmtiles` | `pmtiles` | `release` | Dated map-tile releases |
| `releases/YYYY-MM-DD/drc-cami-mining-cadastre.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/YYYY-MM-DD/drc-cami-mining-cadastre.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/YYYY-MM-DD/drc-cami-mining-cadastre.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
<!-- END GENERATED files-table -->

## Schema notes

The canonical FGB contains 5,128 MultiPolygon features in EPSG:4326. The release feature model adds `feature_id`, `geometry_hash`, and `properties_hash`. No original CAMI field was globally unique, nonblank, and URL-safe across the curated merged table. The draft package assigns generated decimal `feature_id` values from a deterministic `source_record_key` derived from source service, layer id, object id, `guidShape`, and `Code`.

The source layer names are preserved in `source_service`, `source_layer_id`, and `source_layer_name`. The repeated aggregate `ActiveLicenses` layers are omitted to avoid triple-counting features already present in granular layers. The admin and geology services are omitted because they are portal context layers, not mining-cadastre licence records.

No localized metadata sidecars were autogenerated for the first upload package.

## Properties / columns

| Field | Type | Notes |
|---|---|---|
| `AccCode` | String | Source access or accounting code field; definition needs source confirmation. |
| `ApplNo` | String | Source application number field; repeated and not suitable as row identity. |
| `Archived` | String | Source archived flag. |
| `AreaUnit` | String | Unit for source reported area. |
| `AreaValue` | String | Source reported area value. |
| `CalculatedAreaUnit` | String | Unit for source calculated area. |
| `CalculatedAreaValue` | String | Source calculated area value. |
| `Code` | String | CAMI licence or application code-like field; useful for search but not row-unique in the merged release. |
| `Comments` | String | Source comments or status notes. |
| `Commodities` | String | Commodity names associated with the licence or application. |
| `CommoditiesCd` | String | Commodity codes associated with the licence or application. |
| `CreationDate` | Integer | Source ArcGIS creation timestamp in milliseconds since Unix epoch when present. |
| `Creator` | String | Source ArcGIS creator field when present. |
| `DteApplied` | Integer | Application date as source ArcGIS timestamp in milliseconds since Unix epoch. |
| `DteEnd` | Integer | Source end date as ArcGIS timestamp in milliseconds since Unix epoch when present. |
| `DteExpires` | Integer | Expiry date as source ArcGIS timestamp in milliseconds since Unix epoch. |
| `DteGranted` | Integer | Grant date as source ArcGIS timestamp in milliseconds since Unix epoch. |
| `DtePegged` | Integer | Source pegged date as ArcGIS timestamp in milliseconds since Unix epoch when present. |
| `DteRenewal` | String | Source renewal date or value; mixed source typing, definition needs source confirmation. |
| `DteSigned` | Integer | Source signed date as ArcGIS timestamp in milliseconds since Unix epoch when present. |
| `DteStart` | Integer | Source start date as ArcGIS timestamp in milliseconds since Unix epoch when present. |
| `ESRI_OID` | Integer | Source ArcGIS object ID; layer-local and not unique across the merged release. |
| `EditDate` | Integer | Source ArcGIS edit timestamp in milliseconds since Unix epoch when present. |
| `Editor` | String | Source ArcGIS editor field when present. |
| `ExteriorShapePartCount` | Integer | Source count of exterior shape parts. |
| `GlobalID` | String | Source ArcGIS global ID when present. |
| `Group1` | String | Source grouping field; definition needs source confirmation. |
| `Group2` | String | Source grouping field; definition needs source confirmation. |
| `Group3` | String | Source grouping field; definition needs source confirmation. |
| `Group4` | String | Source grouping field; definition needs source confirmation. |
| `Group5` | String | Source grouping field; definition needs source confirmation. |
| `Interest` | String | Source interest field; definition needs source confirmation. |
| `Jurisdic` | String | Source jurisdiction field. |
| `LengthValue` | String | Source length value field when present. |
| `MapRef` | String | Source map reference string, often country, province, territory, and map sheet. |
| `Name` | String | Source name field when present. |
| `OldCode` | String | Source previous code field when present. |
| `OrigStore` | String | Source origin/store field; definition needs source confirmation. |
| `Part` | String | Source shape part label. |
| `Parties` | String | Listed parties and ownership percentages from the source portal. |
| `Region` | String | Source region or province-like field. |
| `Renewal` | Integer | Source renewal count or flag; definition needs source confirmation. |
| `RespOffice` | String | Source responsible office code. |
| `Shape__Area` | String | Source ArcGIS shape area attribute; canonical geometry is stored in FGB geometry. |
| `Shape__Length` | Number | Source ArcGIS shape length attribute; canonical geometry is stored in FGB geometry. |
| `Status` | String | Source licence/application status label. |
| `StatusGrp` | String | Source status group label. |
| `SubType` | String | Source subtype label when present. |
| `SubTypeAbb` | String | Source subtype abbreviation when present. |
| `Summary` | String | Source summary field when present. |
| `Type` | String | Source licence/application type label. |
| `TypeAbb` | String | Source type abbreviation when present. |
| `TypeCode` | String | Source type code when present. |
| `TypeGroup` | String | Source type group label. |
| `feature_id` | String | Generated decimal row identifier for this release. |
| `geometry_hash` | String | SHA-256 canonical geometry hash generated by the shared-datasets release feature model. |
| `guidAgreement` | String | Source agreement GUID when present. |
| `guidLicense` | String | Source licence GUID when present; not row-unique in the merged release. |
| `guidLicenseType` | String | Source licence type GUID when present. |
| `guidPart` | String | Source part GUID. |
| `guidShape` | String | Source shape GUID; not row-unique in the merged release. |
| `guidStatus` | String | Source status GUID when present. |
| `properties_hash` | String | SHA-256 canonical property hash generated by the shared-datasets release feature model. |
| `source_layer_id` | Integer | ArcGIS FeatureServer layer id used for the source feature. |
| `source_layer_name` | String | Human-readable source layer or feature type name. |
| `source_oid` | String | Source object ID preserved as text for lineage. |
| `source_record_key` | String | Deterministic derived assignment key from source service, layer id, object id, guidShape, and Code. |
| `source_service` | String | ArcGIS FeatureServer service name used for the source feature. |

## Update notes

This is a manual draft upload package. To refresh it, reopen the CAMI portal to establish a session, page the ArcGIS FeatureServer layers through `Proxy.aspx`, rebuild the curated source while excluding repeated aggregate `ActiveLicenses` layers, rerun the release feature model, and rebuild FGB, PMTiles, metadata sidecar, schema, and manifest artifacts.

## Known caveats

- The portal disclaimer reserves rights and states that republication and redistribution require prior written permission from CAMI and Spatial Dimension. Treat public publication as blocked until permission or compatible terms are confirmed.
- Source attribute definitions were inferred from field names and portal display templates; many fields need source confirmation.
- Date fields are source ArcGIS epoch-millisecond values, not normalized ISO date strings.
- The source portal is intended for transparency and stakeholder communication; it is not a substitute for legal title due diligence.
- The draft excludes admin and geology context layers. It also excludes aggregate `ActiveLicenses` layers to avoid duplicate features.
