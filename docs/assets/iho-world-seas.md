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
notes: Initial upload from iho-mr_World_Seas_IHO_v3.fgb; release 2026-04-29. The 2026-06-05 metadata-contract release used
  identity v1 (feature_id 'src:MRGID:{n}', ext_id, feature_hash) and added machine-translated NAME metadata sidecars for es,
  fr, id, pt, pt_br, and sw. The 2026-06-09 release migrates the asset to the release feature identity v2 contract; feature_id
  is the bare MRGID string, geometry_hash and properties_hash replace feature_hash, and the ext_id and feature_hash columns
  are removed. Existing machine translations were carried forward re-keyed to the v2 feature_ids. PMTiles are metadata-lookup
  tiles with feature_id only. Prior releases remain readable and unchanged. Release history, source generations, row counts,
  and hashes are recorded in the bucket release index and per-run record.
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
    notes: Unique; selected source field ID for feature_id; v2 feature_id is the bare MRGID string as of release 2026-06-09
feature_identity:
  strategy: source_field
  source_fields:
  - MRGID
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/iho-world-seas.metadata.ndjson.gz
  schema_file: latest/iho-world-seas.schema.json
  manifest_file: latest/iho-world-seas.manifest.json
  provenance_default: true
files:
- path: latest/iho-world-seas.fgb
  format: fgb
  role: canonical
  purpose: Canonical World Seas polygon dataset with source fields plus feature_id, geometry_hash, and properties_hash
- path: latest/iho-world-seas.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map metadata-lookup tiles with feature_id
- path: latest/iho-world-seas.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/iho-world-seas.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Spanish metadata sidecar materialized from NAME translations
- path: latest/iho-world-seas.metadata.fr.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated French metadata sidecar materialized from NAME translations
- path: latest/iho-world-seas.metadata.id.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Indonesian metadata sidecar materialized from NAME translations
- path: latest/iho-world-seas.metadata.pt.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Portuguese metadata sidecar materialized from NAME translations
- path: latest/iho-world-seas.metadata.pt_br.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Brazilian Portuguese metadata sidecar materialized from NAME translations
- path: latest/iho-world-seas.metadata.sw.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Swahili metadata sidecar materialized from NAME translations
- path: latest/iho-world-seas.metadata-translations.csv
  format: csv
  role: metadata
  purpose: Editable translation source keyed by feature_id, field, locale, and source-value hash
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
- path: releases/YYYY-MM-DD/iho-world-seas.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Spanish metadata sidecar
- path: releases/YYYY-MM-DD/iho-world-seas.metadata.fr.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated French metadata sidecar
- path: releases/YYYY-MM-DD/iho-world-seas.metadata.id.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Indonesian metadata sidecar
- path: releases/YYYY-MM-DD/iho-world-seas.metadata.pt.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Portuguese metadata sidecar
- path: releases/YYYY-MM-DD/iho-world-seas.metadata.pt_br.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Brazilian Portuguese metadata sidecar
- path: releases/YYYY-MM-DD/iho-world-seas.metadata.sw.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Swahili metadata sidecar
- path: releases/YYYY-MM-DD/iho-world-seas.metadata-translations.csv
  format: csv
  role: release
  purpose: Dated editable translation source
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
- Use localized metadata sidecars when an application needs translated display names for `NAME`.
- Do not treat this as an authoritative legal maritime boundary dataset.
- Do not use the PMTiles artifact as the analytical source.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/iho-world-seas.fgb` | `fgb` | `canonical` | Canonical World Seas polygon dataset with source fields plus feature_id, geometry_hash, and properties_hash |
| `latest/iho-world-seas.pmtiles` | `pmtiles` | `companion` | Web map metadata-lookup tiles with feature_id |
| `latest/iho-world-seas.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/iho-world-seas.metadata.es.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Spanish metadata sidecar materialized from NAME translations |
| `latest/iho-world-seas.metadata.fr.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated French metadata sidecar materialized from NAME translations |
| `latest/iho-world-seas.metadata.id.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Indonesian metadata sidecar materialized from NAME translations |
| `latest/iho-world-seas.metadata.pt.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Portuguese metadata sidecar materialized from NAME translations |
| `latest/iho-world-seas.metadata.pt_br.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Brazilian Portuguese metadata sidecar materialized from NAME translations |
| `latest/iho-world-seas.metadata.sw.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Swahili metadata sidecar materialized from NAME translations |
| `latest/iho-world-seas.metadata-translations.csv` | `csv` | `metadata` | Editable translation source keyed by feature_id, field, locale, and source-value hash |
| `latest/iho-world-seas.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/iho-world-seas.manifest.json` | `json` | `metadata` | Release manifest tying source input, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/iho-world-seas.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/iho-world-seas.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/iho-world-seas.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/YYYY-MM-DD/iho-world-seas.metadata.es.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Spanish metadata sidecar |
| `releases/YYYY-MM-DD/iho-world-seas.metadata.fr.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated French metadata sidecar |
| `releases/YYYY-MM-DD/iho-world-seas.metadata.id.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Indonesian metadata sidecar |
| `releases/YYYY-MM-DD/iho-world-seas.metadata.pt.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Portuguese metadata sidecar |
| `releases/YYYY-MM-DD/iho-world-seas.metadata.pt_br.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Brazilian Portuguese metadata sidecar |
| `releases/YYYY-MM-DD/iho-world-seas.metadata.sw.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Swahili metadata sidecar |
| `releases/YYYY-MM-DD/iho-world-seas.metadata-translations.csv` | `csv` | `release` | Dated editable translation source |
| `releases/YYYY-MM-DD/iho-world-seas.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/YYYY-MM-DD/iho-world-seas.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Manual metadata-contract release run record |
<!-- END GENERATED files-table -->

## Schema notes

This is a direct format conversion from the Marine Regions source layer. Source
field names and values are preserved in the FlatGeobuf output. Metadata-contract
releases add `feature_id`, `geometry_hash`, and `properties_hash` fields to the canonical
FGB and metadata sidecar. PMTiles intentionally carry only the `feature_id`
property; source attributes are served from the metadata sidecar/API.

Identity v2 migration (2026-06-09): `feature_id` changed from the v1
`src:MRGID:{n}` form to the bare MRGID string (for example `src:MRGID:1904`
became `1904`). The v1 `ext_id` and `feature_hash` columns were removed;
`geometry_hash` and `properties_hash` replace `feature_hash`. Consumers that
stored v1 feature_ids can migrate by stripping the `src:MRGID:` prefix. The
`area` and `MRGID` columns are typed 32-bit integer in the v2 FGB (previously
integer64); all values are unchanged.

## Properties / columns

Definitions are inherited from the Marine Regions World Seas IHO v3 source and
need source confirmation. Use the source documentation for authoritative field
definitions.

| Name | Type | Description |
|---|---|---|
| `NAME` | string | Source sea or ocean name; translatable field in localized metadata sidecars. |
| `ID` | string | Source identifier for the named sea feature; exact code semantics need source confirmation. |
| `Longitude` | real | Source-provided representative longitude in decimal degrees. |
| `Latitude` | real | Source-provided representative latitude in decimal degrees. |
| `min_X` | real | Source-provided minimum longitude for the feature envelope. |
| `min_Y` | real | Source-provided minimum latitude for the feature envelope. |
| `max_X` | real | Source-provided maximum longitude for the feature envelope. |
| `max_Y` | real | Source-provided maximum latitude for the feature envelope. |
| `area` | integer | Source-provided area value; units need source confirmation. |
| `MRGID` | integer | Marine Regions Gazetteer identifier for the feature. |
| `feature_id` | string | Public URL-safe lookup handle; mirrors `MRGID` as a string. |
| `geometry_hash` | string | SHA-256 content hash computed from canonical feature geometry. |
| `properties_hash` | string | SHA-256 content hash computed from projected non-geometry metadata properties. |

## Update notes

Manually converted from `iho-mr_World_Seas_IHO_v3.fgb` and published as a
2026-04-29 release.

PMTiles were rebuilt on 2026-05-04 from the published FGB using auto maxzoom
selection. The sampled FGB profile resolved to maxzoom 12 from representative
segment lengths. The rebuilt PMTiles SHA-256 is
`0d0985cf36ad244215f80bf198dcc43eaef1767bdd9e580f07062391d273f51b`.

The 2026-06-05 release repaired the asset to the release-oriented vector metadata
contract (identity v1) from the existing latest FGB generation `1777477236329598`,
without refetching or changing the 2026-04-29 release.

The 2026-06-09 release migrates the asset to the release feature identity v2
contract from the existing latest FGB generation `1780805504653873`, again
without refetching the source. The selected source field ID remains `MRGID`,
and `feature_id` is now the bare string form of `MRGID` (v1 used
`src:MRGID:{n}`). The v1 `ext_id` and `feature_hash` columns were removed, and
`geometry_hash` plus `properties_hash` were added to the canonical FGB and
metadata sidecar. PMTiles were rebuilt as metadata-lookup tiles carrying only
`feature_id` (v1 tiles also carried `ext_id`), preserving the `iho_world_seas`
tile layer name and the `World_Seas_IHO_v3` FGB layer name. The 606 machine
translation rows for `NAME` (es, fr, id, pt, pt_br, sw) were carried forward
re-keyed to the v2 feature_ids; translated values are unchanged and remain
machine_translated pending human review.

## Localized Metadata

Machine translations for `NAME` are recorded in
`iho-world-seas.metadata-translations.csv` and materialized into localized
metadata sidecars for `es`, `fr`, `id`, `pt`, `pt_br`, and `sw`. Each localized
sidecar preserves all 101 feature records and carries one translated `NAME`
value per feature. Translation rows carry `review_state = machine_translated`
and have not been human reviewed. The 2026-06-09 identity v2 release carried
all 606 translation rows forward re-keyed to the new feature_ids; translated
values and source-value hashes are unchanged.

## Known caveats

Marine region names and extents are useful for contextual grouping, but they are
not a substitute for jurisdictional boundaries, EEZs, or legally authoritative
maritime limits.
