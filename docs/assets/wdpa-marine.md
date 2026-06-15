---
schema_version: 1
asset_slug: wdpa-marine
title: WDPA Marine Protected and Conserved Areas
category: 100-geographic-reference
subcategory: 130-protected-areas
status: active
access_tier: public
owner: SkyTruth
update_cadence: monthly
canonical_format: fgb
canonical_file: latest/wdpa-marine.fgb
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
notes: Monthly job preserves source fields and publishes FGB plus PMTiles. The 2026-06-09 feature_id contract repair release
  uses generated decimal feature_id values carried forward from prior releases when the SITE_PID-backed identity key matches,
  geometry_hash values, properties_hash values, canonical metadata/schema/manifest artifacts, metadata-translations CSV, and
  localized metadata sidecars for es, fr, id, pt, pt_br, and sw. PMTiles are lightweight metadata-lookup tiles with feature_id
  only. The release preserves 331 upstream invalid geometries from the Jun2026 source FGB; no geometry repair was applied.
  A 2026-06-15 translation-only update completes NAME_ENG rows for the six localized metadata sidecars. Release history, source
  versions, row counts, and file hashes are recorded in the bucket release index and per-run records.
row_count: 17657
data_profile:
  field_count: 33
  identity_candidates:
  - field: SITE_ID
    distinct_values: 17343
    duplicate_value_count: 152
    duplicate_row_count: 466
    status: non_unique
    notes: Not unique
  - field: SITE_PID
    distinct_values: 17657
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique
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
  sidecar_file: latest/wdpa-marine.metadata.ndjson.gz
  schema_file: latest/wdpa-marine.schema.json
  manifest_file: latest/wdpa-marine.manifest.json
  provenance_default: true
files:
- path: latest/wdpa-marine.fgb
  format: fgb
  role: canonical
  purpose: Canonical mixed-geometry vector dataset
- path: latest/wdpa-marine.pmtiles
  format: pmtiles
  role: companion
  purpose: Web map tiles generated from the same monthly extract
- path: latest/wdpa-marine.metadata.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Canonical feature metadata sidecar keyed by feature_id
- path: latest/wdpa-marine.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Spanish metadata sidecar materialized from approved translation rows
- path: latest/wdpa-marine.metadata.fr.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated French metadata sidecar materialized from approved translation rows
- path: latest/wdpa-marine.metadata.id.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Indonesian metadata sidecar materialized from approved translation rows
- path: latest/wdpa-marine.metadata.pt.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Portuguese metadata sidecar materialized from approved translation rows
- path: latest/wdpa-marine.metadata.pt_br.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Brazilian Portuguese metadata sidecar materialized from approved translation rows
- path: latest/wdpa-marine.metadata.sw.ndjson.gz
  format: ndjson_gzip
  role: metadata
  purpose: Generated Swahili metadata sidecar materialized from approved translation rows
- path: latest/wdpa-marine.metadata-translations.csv
  format: csv
  role: metadata
  purpose: Editable translation source keyed by feature_id, field, locale, and source-value hash
- path: latest/wdpa-marine.schema.json
  format: json
  role: metadata
  purpose: Release feature metadata schema for field projection
- path: latest/wdpa-marine.manifest.json
  format: json
  role: metadata
  purpose: Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy
- path: releases/YYYY-MM-DD/wdpa-marine.fgb
  format: fgb
  role: release
  purpose: Dated canonical release
- path: releases/YYYY-MM-DD/wdpa-marine.pmtiles
  format: pmtiles
  role: release
  purpose: Dated map-tile release
- path: releases/YYYY-MM-DD/wdpa-marine.metadata.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated canonical metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-marine.metadata.es.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Spanish metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-marine.metadata.fr.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated French metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-marine.metadata.id.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Indonesian metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-marine.metadata.pt.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Portuguese metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-marine.metadata.pt_br.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Brazilian Portuguese metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-marine.metadata.sw.ndjson.gz
  format: ndjson_gzip
  role: release
  purpose: Dated generated Swahili metadata sidecar
- path: releases/YYYY-MM-DD/wdpa-marine.metadata-translations.csv
  format: csv
  role: release
  purpose: Dated editable translation source
- path: releases/YYYY-MM-DD/wdpa-marine.schema.json
  format: json
  role: release
  purpose: Dated release feature schema
- path: releases/YYYY-MM-DD/wdpa-marine.manifest.json
  format: json
  role: release
  purpose: Dated release manifest with artifact checksums and index-load policy
- path: runs/YYYY-MM-DD.json
  format: json
  role: run-record
  purpose: Monthly run record
---

# WDPA Marine Protected and Conserved Areas

<!-- BEGIN GENERATED asset-summary -->
- **Status:** active
- **Access tier:** public
- **Owner:** SkyTruth
- **Update cadence:** monthly
- **Canonical file:** `latest/wdpa-marine.fgb`
- **Available formats:** `fgb`, `pmtiles`
- **Source:** UNEP-WCMC and IUCN Protected Planet WDPA/WDOECM
- **License / terms:** See Protected Planet WDPA terms
- **Citation:** UNEP-WCMC and IUCN (2026). Protected Planet: The World Database on Protected Areas (WDPA) and World Database on Other Effective Area-based Conservation Measures (WD-OECM) [Online], June 2026, Cambridge, UK: UNEP-WCMC and IUCN. Available at: www.protectedplanet.net.
<!-- END GENERATED asset-summary -->

## What this is

This asset contains the monthly WDPA/WDOECM source rows selected for the marine
or coastal realm. It is a direct format conversion and split from the upstream
source. Fields are preserved from the source dataset.

## When to use it

- Use this for reusable marine protected-area and conserved-area boundaries or point records.
- Use localized metadata sidecars when an application needs translated WDPA descriptive fields.
- Do not use this when a terrestrial-only extract is required.

## Files

<!-- BEGIN GENERATED files-table -->
| File | Format | Role | Purpose |
|---|---|---|---|
| `latest/wdpa-marine.fgb` | `fgb` | `canonical` | Canonical mixed-geometry vector dataset |
| `latest/wdpa-marine.pmtiles` | `pmtiles` | `companion` | Web map tiles generated from the same monthly extract |
| `latest/wdpa-marine.metadata.ndjson.gz` | `ndjson_gzip` | `metadata` | Canonical feature metadata sidecar keyed by feature_id |
| `latest/wdpa-marine.metadata.es.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Spanish metadata sidecar materialized from approved translation rows |
| `latest/wdpa-marine.metadata.fr.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated French metadata sidecar materialized from approved translation rows |
| `latest/wdpa-marine.metadata.id.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Indonesian metadata sidecar materialized from approved translation rows |
| `latest/wdpa-marine.metadata.pt.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Portuguese metadata sidecar materialized from approved translation rows |
| `latest/wdpa-marine.metadata.pt_br.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Brazilian Portuguese metadata sidecar materialized from approved translation rows |
| `latest/wdpa-marine.metadata.sw.ndjson.gz` | `ndjson_gzip` | `metadata` | Generated Swahili metadata sidecar materialized from approved translation rows |
| `latest/wdpa-marine.metadata-translations.csv` | `csv` | `metadata` | Editable translation source keyed by feature_id, field, locale, and source-value hash |
| `latest/wdpa-marine.schema.json` | `json` | `metadata` | Release feature metadata schema for field projection |
| `latest/wdpa-marine.manifest.json` | `json` | `metadata` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index-load policy |
| `releases/YYYY-MM-DD/wdpa-marine.fgb` | `fgb` | `release` | Dated canonical release |
| `releases/YYYY-MM-DD/wdpa-marine.pmtiles` | `pmtiles` | `release` | Dated map-tile release |
| `releases/YYYY-MM-DD/wdpa-marine.metadata.ndjson.gz` | `ndjson_gzip` | `release` | Dated canonical metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-marine.metadata.es.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Spanish metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-marine.metadata.fr.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated French metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-marine.metadata.id.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Indonesian metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-marine.metadata.pt.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Portuguese metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-marine.metadata.pt_br.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Brazilian Portuguese metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-marine.metadata.sw.ndjson.gz` | `ndjson_gzip` | `release` | Dated generated Swahili metadata sidecar |
| `releases/YYYY-MM-DD/wdpa-marine.metadata-translations.csv` | `csv` | `release` | Dated editable translation source |
| `releases/YYYY-MM-DD/wdpa-marine.schema.json` | `json` | `release` | Dated release feature schema |
| `releases/YYYY-MM-DD/wdpa-marine.manifest.json` | `json` | `release` | Dated release manifest with artifact checksums and index-load policy |
| `runs/YYYY-MM-DD.json` | `json` | `run-record` | Monthly run record |
<!-- END GENERATED files-table -->

## Schema notes

The simplified monthly job does not rename, add, or remove source fields. Refer
to the Protected Planet WDPA user manual and source metadata for authoritative
field definitions.

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
| `NAME_ENG` | string | Protected or conserved area name in English. |
| `NAME` | string | Protected or conserved area name as supplied by the source. |
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
| `feature_id` | string | Public URL-safe lookup handle. The current 2026-06-09 repair release uses generated decimal sequence handles because some `SITE_PID` values contain non-alphanumeric characters; scheduled refreshes carry prior generated handles forward when the SITE_PID-backed identity key matches. |
| `geometry_hash` | string | SHA-256 content hash computed from canonical feature geometry. |
| `properties_hash` | string | SHA-256 content hash computed from projected non-geometry metadata properties. |

## Update notes

Updated by `python -m ingestion.wdpa_monthly.run`, deployed as the
`wdpa-monthly` Cloud Run Job and scheduled for `0 9 1-10 * *` UTC.

The 2026-05-04 PMTiles artifact was rebuilt from the published FGB using auto
maxzoom selection. The mixed point/vector FGB profile resolved to maxzoom 12
with point retention. The rebuilt PMTiles SHA-256 is
`963e851bf7f0952a9eee321074d77bd071bc935e74692932569a98fa4801ed8e`.

A 2026-06-09 feature_id contract repair release was staged from the unchanged June
2026 source FGB so consumers can use feature metadata sidecars with URL-safe
lookup handles. The release adds generated decimal `feature_id` values tied to
SITE_PID-backed identity keys, `geometry_hash` values, `properties_hash`
checksums, a canonical metadata sidecar, release schema, manifest,
metadata-translations CSV, and generated localized sidecars for `es`, `fr`,
`id`, `pt`, `pt_br`, and `sw`. The PMTiles release is a lightweight lookup
archive with only `feature_id` properties generated with Tippecanoe and
converted to PMTiles v3.
The repaired artifact SHA-256 values are FGB
`c93c482f715e2e061cb8bdb745cfadc6462f4c8ae6db391df95c4ed39ba1bcb9`,
PMTiles `f94f28fa6fd3d93d2c0abc8bf39a026b253b0554279906e5f5cd6e163ed253ee`,
metadata sidecar `7bae601c9d5643fcbcbc456e453c805c92b123e0d8dad51bf4a8bba8f0d5bbb9`,
schema `a198467d5b866a960cbf417826980aebd9d8f104594d266e07db7b39f9a660ec`,
manifest `d4c5bf52e7011340076b35ba6ed8f2029f7d2d35762f85b18bf3f4fb2a97780f`,
and metadata-translations CSV
`05eba30d4e53176fb4acaaf139826b8dc1bd49c67a232cb19c8be4bff6545a6d`.

The same 2026-06-09 release was expanded with machine-generated first-pass
translations for `DESIG_ENG`, `DESIG_TYPE`, `GOV_TYPE`, `OWN_TYPE`, `NO_TAKE`,
`STATUS`, `VERIF`, `OECM_ASMT`, `DESIG`, `GOVSUBTYPE`, `OWNSUBTYPE`,
`MANG_PLAN`, `CONS_OBJ`, `SUPP_INFO`, `INLND_WTRS`, and `IUCN_CAT` in `es`,
`fr`, `id`, `pt`, `pt_br`, and `sw`. The expanded translation CSV contains
1,712,729 rows including the previous Spanish `NAME_ENG` rows. Localization
validation applied 300,169 Spanish rows and 282,512 rows for each other locale,
with no stale or orphaned translations. Expanded artifact SHA-256 values are
metadata-translations CSV
`df1fbe53b8f05592e7a024bc23603a9b0f16e5c12eb0560532f465c466649fc6`,
metadata.es `0a75924aeeaf4cca3eed091d1edae1a7206a2d85f643945cfa4165918b30682f`,
metadata.fr `c4c82edc9da75c1011f89eeef21dc8eec78d0d3cb8bfa9191788b778ae986ee4`,
metadata.id `5ede90b63e4c29dec7eb906a51669d9ba7c86a1dffd039bfc637b799298a2885`,
metadata.pt `fe062371384d625670cf91340328df5ba38e458efb33fc9116cd5ec0580fbf94`,
metadata.pt_br `fe062371384d625670cf91340328df5ba38e458efb33fc9116cd5ec0580fbf94`,
and metadata.sw `1259f97f12d07524fd227a7db0327f6cd768bca475b7a741f4448fdc965451e9`.
The PMTiles companion was rebuilt at maxzoom 12 with Tippecanoe v2.79.0 and
validated with `pmtiles verify`, `pmtiles show`, and decoded z0/z12 tile
property checks.

A 2026-06-15 translation-only follow-up completes `NAME_ENG` rows for `es`,
`fr`, `id`, `pt`, `pt_br`, and `sw` using Google Translate document output
keyed back to the current source-value hashes. The provided `pt-br` document
output was applied to both `pt` and `pt_br`. The updated metadata-translations
CSV contains 1,801,014 rows, with 17,657 `NAME_ENG` rows per locale.
Localization validation applied 300,169 rows for each locale, with no stale,
orphaned, missing-field, or untranslated features. Translation-only artifact
SHA-256 values are metadata-translations CSV
`fb35204e7b28a9f441aa086aac180fccbe23d926a5fb9ec7c4ee8c4569f2d7f9`,
metadata.es `83d56835a4ffc36961098b4b6f8ee58ebd2782f8d5f551c52e91246a30fa1252`,
metadata.fr `70d0b42fd962f82d66f93c43dd5fd43217023ac4e334df22bdcdebb12ada0088`,
metadata.id `61e9c6677060c084e284a972fe1c79decd1fbf76a9800a69be8f955085e2ba84`,
metadata.pt `79f989ef8c79ecd5e138b6fd7f5874367b15941ec54aeebb821c73c09a8e32c4`,
metadata.pt_br `79f989ef8c79ecd5e138b6fd7f5874367b15941ec54aeebb821c73c09a8e32c4`,
and metadata.sw `249ae7007fe8cc931882f31cca075d91981c67624e2bcaa1423379b2b300ef59`.

## Known caveats

The canonical FGB intentionally keeps mixed point and polygon geometries. Some
desktop GIS and map clients handle mixed-geometry layers less gracefully than
single-geometry layers.

The 2026-06-09 feature_id contract repair release preserves 331 invalid geometries
already present in the June 2026 source FGB. Geometry repair was intentionally
out of scope for this contract repair because it would change canonical feature
geometry, geometry hashes, and properties hashes.
