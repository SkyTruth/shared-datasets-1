---
schema_version: 1
asset_slug: iho-world-seas
title: IHO World Seas
category: 100-geographic-reference
subcategory: 120-marine-boundaries
status: active
access_tier: public
owner: SkyTruth
update_cadence: manual
canonical_format: fgb
canonical_file: latest/iho-world-seas.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: Marine Regions World Seas IHO v3
license: See source terms
citation: Flanders Marine Institute (2018). IHO Sea Areas, version 3. Available online at https://www.marineregions.org/.
  https://doi.org/10.14284/323.
notes: Initial upload from iho-mr_World_Seas_IHO_v3.fgb; release 2026-04-29; sha256 1fb5a7988b686e1076fe0a21d75d5df32fa28dfcd100dbe3db3aaaf8c9493ba6;
  PMTiles sha256 0d0985cf36ad244215f80bf198dcc43eaef1767bdd9e580f07062391d273f51b; PMTiles rebuilt 2026-05-04 at maxzoom 12
  from sampled FGB geometry detail with local tile and browser QA. The 2026-06-05 reviewed metadata-contract release uses
  MRGID as the selected provider identifier, adds feature_id, ext_id, feature_hash, metadata/schema/manifest artifacts, and
  keeps the 2026-04-29 release readable and unchanged. No shared_datasets_group_id, shared_datasets_row_id, or localized metadata
  sidecars are generated. PMTiles are metadata-lookup tiles with feature_id and ext_id only. Release history, source generations,
  row counts, and hashes are recorded in the bucket release index and per-run record.
row_count: 101
data_profile:
  field_count: 13
  search_fields:
  - NAME
  - ID
  - MRGID
  identity_candidates:
  - field: ID
    distinct_values: 101
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique
  - field: MRGID
    distinct_values: 101
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique; selected provider ID for feature_id and ext_id in the 2026-06-05 metadata-contract release
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  feature_hash_column: feature_hash
  sidecar_file: latest/iho-world-seas.metadata.ndjson.gz
  schema_file: latest/iho-world-seas.schema.json
  manifest_file: latest/iho-world-seas.manifest.json
  provenance_default: true
files:
- path: latest/iho-world-seas.fgb
  format: fgb
  role: canonical
  purpose: Canonical World Seas polygon dataset with source fields plus feature_id, ext_id, and feature_hash
- path: latest/iho-world-seas.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map metadata-lookup tiles with feature_id and ext_id
- path: latest/iho-world-seas.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/iho-world-seas.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/iho-world-seas.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/YYYY-MM-DD/iho-world-seas.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/iho-world-seas.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: releases/YYYY-MM-DD/iho-world-seas.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/YYYY-MM-DD/iho-world-seas.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/YYYY-MM-DD/iho-world-seas.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Manual metadata-contract release run record
---

# IHO World Seas

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** manual
- **Canonical file:** `latest/iho-world-seas.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** Marine Regions World Seas IHO v3
- **License / terms:** See source terms
- **Citation:** Flanders Marine Institute (2018). IHO Sea Areas, version 3. Available online at https://www.marineregions.org/. https://doi.org/10.14284/323.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the Marine Regions World Seas IHO v3 polygon dataset. It is
a reusable named-seas reference layer for contextual mapping, source filtering,
and coarse marine-region grouping.

The canonical FlatGeobuf preserves the source geometry and attributes and adds
release-oriented feature metadata identity fields. The PMTiles artifact is
generated from the same source layer for web-map display and feature lookup.

## When to use it

- Use this for named sea and ocean areas in contextual maps or spatial joins.
- Use the FlatGeobuf file for analysis and the PMTiles file for display.
- Do not treat this as an authoritative legal maritime boundary dataset.
- Do not use the PMTiles artifact as the analytical source.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/iho-world-seas.fgb` | `fgb` | `canonical` | Canonical World Seas polygon dataset with source fields plus feature_id, ext_id, and feature_hash |
| `latest/iho-world-seas.pmtiles` | `pmtiles` | `companion` | Web map metadata-lookup tiles with feature_id and ext_id |
| `latest/iho-world-seas.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/iho-world-seas.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/iho-world-seas.manifest.json` | `json` | `metadata` | Release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/iho-world-seas.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/iho-world-seas.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/iho-world-seas.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/YYYY-MM-DD/iho-world-seas.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/YYYY-MM-DD/iho-world-seas.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Manual metadata-contract release run record |
<!-- END GENERATED files-table -->

## Schema notes

This is a direct format conversion from the Marine Regions source layer. Source
field names and values are preserved in the FlatGeobuf output. Metadata-contract
releases add `feature_id`, `ext_id`, and `feature_hash` fields to the canonical
FGB and metadata sidecar. PMTiles intentionally carry only `feature_id` and
`ext_id` properties; source attributes are served from the metadata sidecar/API.

## Properties / columns

Definitions are inherited from the Marine Regions World Seas IHO v3 source and
need source confirmation. Use the source documentation for authoritative field
definitions.

| Name | Type | Description |
|---|---|---|
| `NAME` | string | Source sea or ocean name. |
| `ID` | string | Source identifier for the named sea feature; exact code semantics need source confirmation. |
| `Longitude` | real | Source-provided representative longitude in decimal degrees. |
| `Latitude` | real | Source-provided representative latitude in decimal degrees. |
| `min_X` | real | Source-provided minimum longitude for the feature envelope. |
| `min_Y` | real | Source-provided minimum latitude for the feature envelope. |
| `max_X` | real | Source-provided maximum longitude for the feature envelope. |
| `max_Y` | real | Source-provided maximum latitude for the feature envelope. |
| `area` | integer64 | Source-provided area value; units need source confirmation. |
| `MRGID` | integer64 | Marine Regions Gazetteer identifier for the feature. |
| `feature_id` | string | Provider-backed feature ID derived from `MRGID`, formatted as `src:MRGID:{MRGID}`. |
| `ext_id` | string | External lookup ID; mirrors `MRGID` as a string. |
| `feature_hash` | string | SHA-256 content hash for the feature geometry and projected metadata properties. |

## Update notes

Manually converted from `iho-mr_World_Seas_IHO_v3.fgb` and published as a
2026-04-29 release.

PMTiles were rebuilt on 2026-05-04 from the published FGB using auto maxzoom
selection. The sampled FGB profile resolved to maxzoom 12 from representative
segment lengths. The rebuilt PMTiles SHA-256 is
`0d0985cf36ad244215f80bf198dcc43eaef1767bdd9e580f07062391d273f51b`.

The 2026-06-05 release repairs the asset to the release-oriented vector metadata
contract from the existing latest FGB generation `1777477236329598`, without
refetching or changing the 2026-04-29 release. The selected provider ID is
`MRGID`; `feature_id` is `src:MRGID:{MRGID}`, `ext_id` is the string form of
`MRGID`, and no group ID, row ID, translations, or localized metadata sidecars
are generated.

## Known caveats

Marine region names and extents are useful for contextual grouping, but they are
not a substitute for jurisdictional boundaries, EEZs, or legally authoritative
maritime limits.
