---
schema_version: 1
asset_slug: wdpa-terrestrial
title: WDPA Terrestrial Protected and Conserved Areas
category: 100-geographic-reference
subcategory: 130-protected-areas
status: active
access_tier: public
owner: SkyTruth
update_cadence: monthly
canonical_format: fgb
canonical_file: latest/wdpa-terrestrial.fgb
available_formats:
- fgb
- pmtiles
metadata_paths:
- README.md
- runs/YYYY-MM-DD.json
source: UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
license: See Protected Planet WDPA terms
citation: 'UNEP-WCMC and IUCN (2026). Protected Planet: The World Database on Protected Areas (WDPA) and World Database on
  Other Effective Area-based Conservation Measures (WD-OECM) [Online], June 2026, Cambridge, UK: UNEP-WCMC and IUCN. Available
  at: www.protectedplanet.net.'
notes: Monthly job preserves source fields and publishes FGB plus PMTiles. The 2026-06-05 metadata-contract release used identity
  v1 (composite src:SITE_PID feature_id and ext_id values, feature_hash) and added canonical metadata/schema/manifest artifacts
  plus an initial Spanish NAME_ENG metadata sidecar. The 2026-06-09 release migrates the asset to the release feature identity
  v2 contract; feature_id values are generated decimal sequence handles keyed by SITE_PID, geometry_hash and properties_hash
  replace feature_hash, and the v1 id, ext_id, and feature_hash columns are removed. PMTiles are lightweight metadata-lookup
  tiles with feature_id only, built at maxzoom 12. The 2026-06-09 release was later expanded with a complete translation set
  for 17 descriptive fields in es, fr, id, pt, pt_br, and sw, including localized metadata sidecars for each locale. WDPA
  NAME is the original/local protected-area name; NAME_ENG is a misleading legacy source field retained for source-schema
  compatibility, but in localized sidecars it should be read as the active-locale display name, effectively name_localized.
  The release preserves 2,661 upstream invalid geometries from the Jun2026 source FGB; no geometry repair was applied. Release
  history, source versions, row counts, and file hashes are recorded in the bucket release index and per-run records.
row_count: 304572
data_profile:
  field_count: 33
  identity_candidates:
  - field: SITE_ID
    distinct_values: 303285
    duplicate_value_count: 566
    duplicate_row_count: 1853
    status: non_unique
    notes: Not unique
  - field: SITE_PID
    distinct_values: 304572
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique and non-blank, but 2,142 values are not URL-safe (underscore parcel suffixes such as 305092_1), so SITE_PID
      is the identity assignment key rather than the published feature_id
feature_identity:
  strategy: generated_sequence_source_fields
  source_fields:
  - SITE_PID
  generated_id_type: monotonic_integer_string
  assignment_key:
  - SITE_PID
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/wdpa-terrestrial.metadata.ndjson.gz
  schema_file: latest/wdpa-terrestrial.schema.json
  manifest_file: latest/wdpa-terrestrial.manifest.json
  provenance_default: true
files:
- path: latest/wdpa-terrestrial.fgb
  format: fgb
  role: canonical
  purpose: Canonical mixed-geometry vector dataset
- path: latest/wdpa-terrestrial.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same monthly extract
- path: latest/wdpa-terrestrial.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/wdpa-terrestrial.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Spanish metadata sidecar materialized from approved translation rows
- path: latest/wdpa-terrestrial.metadata.fr.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated French metadata sidecar materialized from approved translation rows
- path: latest/wdpa-terrestrial.metadata.id.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Indonesian metadata sidecar materialized from approved translation rows
- path: latest/wdpa-terrestrial.metadata.pt.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Portuguese metadata sidecar materialized from approved translation rows
- path: latest/wdpa-terrestrial.metadata.pt_br.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Brazilian Portuguese metadata sidecar materialized from approved translation rows
- path: latest/wdpa-terrestrial.metadata.sw.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Swahili metadata sidecar materialized from approved translation rows
- path: latest/wdpa-terrestrial.metadata-translations.csv
  format: csv
  role: metadata
  purpose: Editable translation source keyed by feature_id, field, locale, and source-value hash
- path: latest/wdpa-terrestrial.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/wdpa-terrestrial.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/YYYY-MM-DD/wdpa-terrestrial.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/wdpa-terrestrial.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Spanish metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata.fr.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated French metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata.id.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Indonesian metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata.pt.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Portuguese metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata.pt_br.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Brazilian Portuguese metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata.sw.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Swahili metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-terrestrial.metadata-translations.csv
  format: csv
  role: release
  purpose: Dated editable translation source
- path: releases/YYYY-MM-DD/wdpa-terrestrial.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/YYYY-MM-DD/wdpa-terrestrial.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Monthly run record
---

# WDPA Terrestrial Protected and Conserved Areas

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** monthly
- **Canonical file:** `latest/wdpa-terrestrial.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
- **License / terms:** See Protected Planet WDPA terms
- **Citation:** UNEP-WCMC and IUCN (2026). Protected Planet: The World Database on Protected Areas (WDPA) and World Database on Other Effective Area-based Conservation Measures (WD-OECM) [Online], June 2026, Cambridge, UK: UNEP-WCMC and IUCN. Available at: www.protectedplanet.net.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the monthly WDPA/WDOECM source rows selected for the
terrestrial realm. It is a direct format conversion and split from the upstream
source. Fields are preserved from the source dataset.

## When to use it

- Use this for reusable terrestrial protected-area and conserved-area boundaries or point records.
- Use localized metadata sidecars when an application needs translated WDPA descriptive fields.
- Do not use this when a marine or coastal extract is required.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/wdpa-terrestrial.fgb` | `fgb` | `canonical` | Canonical mixed-geometry vector dataset |
| `latest/wdpa-terrestrial.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same monthly extract |
| `latest/wdpa-terrestrial.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/wdpa-terrestrial.metadata.es.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Spanish metadata sidecar materialized from approved translation rows |
| `latest/wdpa-terrestrial.metadata.fr.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated French metadata sidecar materialized from approved translation rows |
| `latest/wdpa-terrestrial.metadata.id.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Indonesian metadata sidecar materialized from approved translation rows |
| `latest/wdpa-terrestrial.metadata.pt.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Portuguese metadata sidecar materialized from approved translation rows |
| `latest/wdpa-terrestrial.metadata.pt_br.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Brazilian Portuguese metadata sidecar materialized from approved translation rows |
| `latest/wdpa-terrestrial.metadata.sw.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Swahili metadata sidecar materialized from approved translation rows |
| `latest/wdpa-terrestrial.metadata-translations.csv` | `csv` | `metadata` | Editable translation source keyed by feature_id, field, locale, and source-value hash |
| `latest/wdpa-terrestrial.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/wdpa-terrestrial.manifest.json` | `json` | `metadata` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/wdpa-terrestrial.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/wdpa-terrestrial.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata.es.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Spanish metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata.fr.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated French metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata.id.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Indonesian metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata.pt.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Portuguese metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata.pt_br.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Brazilian Portuguese metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata.sw.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Swahili metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-terrestrial.metadata-translations.csv` | `csv` | `release` | Dated editable translation source |
| `releases/YYYY-MM-DD/wdpa-terrestrial.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/YYYY-MM-DD/wdpa-terrestrial.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Monthly run record |
<!-- END GENERATED files-table -->

## Schema notes

The simplified monthly job does not rename, add, or remove source fields. Refer
to the Protected Planet WDPA user manual and source metadata for authoritative
field definitions.

Identity v2 migration (2026-06-09): `feature_id` changed from the v1 composite
`src:SITE_PID:{pid}` form to generated decimal sequence handles assigned in
source row order and keyed by `SITE_PID`. The v1 `id`, `ext_id`, and
`feature_hash` columns were removed; `geometry_hash` and `properties_hash`
replace `feature_hash`. Consumers that stored v1 feature_id or ext_id handles
should re-resolve features through `SITE_PID` in the metadata sidecar. Source
fields, row count, and geometry are unchanged from the Jun2026 source release.

Name-field semantics are important for localized consumers. `NAME` is the
protected or conserved area name as supplied by WDPA, generally in the original
or local language/script of the protected area. `NAME_ENG` is the upstream
English-name source field in the canonical FGB and canonical metadata sidecar.
Localized metadata sidecars preserve the source schema, so translated display
names are still stored in `NAME_ENG`. In localized sidecars, `NAME_ENG` is a
misleading legacy name; consumers should treat it as the active-locale display
name, effectively `name_localized`, not as an English-only value.

## Properties / columns

Definitions are inherited from the Protected Planet WDPA/WDOECM source and need
source confirmation for each monthly release. The job verifies that all selected
source layers have identical fields before publishing. Metadata-contract
releases add `feature_id`, `geometry_hash`, and `properties_hash` fields.

| Name | Type | Description |
|---|---|---|
| `SITE_ID` | integer | Source numeric site identifier. |
| `SITE_PID` | string | Source persistent site identifier. |
| `SITE_TYPE` | string | WDPA/WDOECM source site type. |
| `NAME_ENG` | string | Legacy upstream English-name field. In localized metadata sidecars, this field name is misleading: it contains the active-locale display name and is effectively `name_localized`. |
| `NAME` | string | Protected or conserved area name as supplied by the source, generally in the original or local language/script of the protected area. |
| `DESIG` | string | Source designation text. |
| `DESIG_ENG` | string | Designation text in English. |
| `DESIG_TYPE` | string | Designation type or authority class. |
| `IUCN_CAT` | string | IUCN protected-area management category. |
| `INT_CRIT` | string | International criteria field, where supplied by the source. |
| `REALM` | string | Source realm value used for the marine/coastal versus terrestrial split. |
| `REP_M_AREA` | real | Source-reported marine area in square kilometers. |
| `REP_AREA` | real | Source-reported total area in square kilometers. |
| `NO_TAKE` | string | Source no-take status. |
| `NO_TK_AREA` | real | Source-reported no-take area in square kilometers. |
| `STATUS` | string | Source status for the protected or conserved area. |
| `STATUS_YR` | integer | Year associated with the source status. |
| `GOV_TYPE` | string | Governance type. |
| `GOVSUBTYPE` | string | Governance subtype. |
| `OWN_TYPE` | string | Ownership type. |
| `OWNSUBTYPE` | string | Ownership subtype. |
| `MANG_AUTH` | string | Management authority. |
| `MANG_PLAN` | string | Management-plan status or reference. |
| `VERIF` | string | Source verification status. |
| `METADATAID` | integer | Source metadata record identifier. |
| `PRNT_ISO3` | string | Parent country or territory ISO3 code. |
| `ISO3` | string | Country or territory ISO3 code associated with the feature. |
| `SUPP_INFO` | string | Supplementary information status or flag. |
| `CONS_OBJ` | string | Conservation-objective field from the source. |
| `INLND_WTRS` | string | Inland-waters classification or flag. |
| `OECM_ASMT` | string | OECM assessment status. |
| `GIS_M_AREA` | real | GIS-calculated marine area in square kilometers; null for point rows where not supplied. |
| `GIS_AREA` | real | GIS-calculated total area in square kilometers; null for point rows where not supplied. |
| `feature_id` | string | Public URL-safe lookup handle. Generated decimal sequence handle assigned from the `SITE_PID` identity key; `SITE_PID` itself is unique but not URL-safe (2,142 underscore parcel suffixes), so it is not published as the handle. |
| `geometry_hash` | string | SHA-256 content hash computed from canonical feature geometry. |
| `properties_hash` | string | SHA-256 content hash computed from projected non-geometry metadata properties. |

## Update notes

Updated by `python -m ingestion.wdpa_monthly.run`, deployed as the
`wdpa-monthly` Cloud Run Job and scheduled for `0 9 1-10 * *` UTC.

A 2026-06-05 metadata-contract release was staged from the unchanged June 2026
latest FGB so consumers can use feature metadata sidecars. That release used
identity v1: composite `src:SITE_PID:{pid}` `feature_id` and `ext_id` values,
`feature_hash` checksums, a canonical metadata sidecar, release schema,
manifest, metadata-translations CSV, and a generated Spanish localized sidecar
for `NAME_ENG`.

The 2026-06-09 release migrates the asset to the release feature identity v2
contract, rebuilt from the unchanged Jun2026 source data in
`releases/2026-06-01/wdpa-terrestrial.fgb` (generation `1780393820549219`)
without refetching the source. `feature_id` values are generated decimal
sequence handles keyed by `SITE_PID` (`SITE_PID` is unique but 2,142 values
carry non-URL-safe underscore parcel suffixes). The v1 `id`, `ext_id`, and
`feature_hash` columns were removed, and `geometry_hash` plus `properties_hash`
were added to the canonical FGB and metadata sidecar. PMTiles were rebuilt as
metadata-lookup tiles carrying only `feature_id` at maxzoom 12, preserving the
`wdpa_terrestrial` tile and FGB layer names; the monthly job builds WDPA PMTiles
at maxzoom 12.

The same 2026-06-09 release was expanded with a complete machine-generated
translation set mirroring wdpa-marine: `NAME_ENG`, `DESIG_ENG`, `DESIG_TYPE`,
`GOV_TYPE`, `OWN_TYPE`, `NO_TAKE`, `STATUS`, `IUCN_CAT`, `VERIF`, `OECM_ASMT`,
`DESIG`, `MANG_PLAN`, `CONS_OBJ`, `SUPP_INFO`, `INLND_WTRS`, `GOVSUBTYPE`, and
`OWNSUBTYPE` in `es`, `fr`, `id`, `pt`, `pt_br`, and `sw`. The expanded
metadata-translations CSV contains 31,066,344 rows (304,572 features x 17
fields x 6 locales; full coverage, no remaining `needs_review` placeholder
rows). Translation provenance is recorded per row in `notes`: values whose
source text already appeared in the wdpa-marine translation CSV were seeded
from those marine rows (`seeded_from=wdpa-marine`); `NAME_ENG` and the
remaining unique values were translated by a maintainer-supplied translation
service and imported from per-language spreadsheets
(`imported_from=name_eng-translations-zip`,
`imported_from=pending-translations-zip`); 7,107 `NAME_ENG` es values and
6,095 `DESIG_ENG` unique values were machine translated with
deep-translator/Google (`provider=google`). `pt` and `pt_br` rows carry
identical values, matching the wdpa-marine convention. The `NAME_ENG`
translation rows are localized display-name values for the active sidecar
locale; in localized sidecars the field name is a misleading legacy
source-schema label and would be better understood as `name_localized`.
Localization validation
applied 5,177,724 rows per locale with no stale or orphaned translations.
Expanded artifact SHA-256 values are metadata-translations CSV
`0c71507f9e9244d2fcdb3be633fc2e78b391195b9cf37a26bb98bff5128bab86`,
metadata.es `6ab50a002dfdc61f3fb96a362559bf23872d78e1dbd4e19b62a80c5a5aac2777`,
metadata.fr `d0b6ff4fac764d4c7f5c7cd34c483143051d18a4b0a60fa9b1951952764b3dd3`,
metadata.id `bb1d3617725497f0a2692bd22c40f7baa7dd34b2bf15d885a2b1e1f55580b068`,
metadata.pt `a35c990d26e30d8c428ebe10385b3203e8b64ab846c5416f06968659e9301578`,
metadata.pt_br `a35c990d26e30d8c428ebe10385b3203e8b64ab846c5416f06968659e9301578`,
and metadata.sw `0f175da54cc0f64da606930dc081c106570d1196faf1c72bb43969cc0de45b69`.
All translated values carry `review_state = machine_translated` pending human
review.

## Known caveats

The canonical FGB intentionally keeps mixed point and polygon geometries. Some
desktop GIS and map clients handle mixed-geometry layers less gracefully than
single-geometry layers.

The 2026-06-05 and 2026-06-09 releases preserve 2,661 invalid geometries
already present in the June 2026 source FGB. Geometry repair was intentionally
out of scope for these contract releases because it would change canonical
feature geometry, geometry hashes, and properties hashes.
