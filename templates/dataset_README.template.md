---
schema_version: 1
asset_slug: "{asset-slug}"
title: "{Dataset title}"
category: "{top-level-category}"
subcategory: "{subcategory}"
status: "active" # active | deprecated | superseded | retired
# For deprecated, superseded, or retired assets, add lifecycle_reason,
# lifecycle_date, and consumer_guidance. Superseded assets also require
# successor_asset_slug.
access_tier: "public" # public | private
owner: "{person-or-team}"
update_cadence: "static" # static | manual | daily | weekly | monthly | ad hoc
canonical_format: "fgb" # fgb | cog | zarr | pmtiles | geojson | ndgeojson | csv
canonical_file: "latest/{asset-slug}.{ext}"
available_formats:
  - "fgb"
metadata_paths:
  - "README.md"
source: "{source-name-or-url}"
source_url: "{optional-source-homepage-or-api-url}"
license: "{license-or-terms-summary}"
citation: "{preferred citation for the original source publication}"
license_flags:
  - "{optional-discovery-flag}"
notes: "{short catalog note}"
admission:
  intended_consumers:
    - "{project-or-user}"
  shared_rationale: "{why this belongs in shared-datasets}"
  steward: "{person-or-team}"
  update_expectations: "{manual/static/scheduled expectation and freshness notes}"
  estimated_published_size_gb: "{number or unknown}"
  large_data_exception: "{required when estimated footprint is >= 10 GB}"
  alternatives_considered: "{project bucket, scratch, upstream access, etc.}"
  deprecation_policy: "{how consumers will be handled if stale or superseded}"
bounds: ["{min_lon}", "{min_lat}", "{max_lon}", "{max_lat}"] # optional WGS84 extent
geometry_type: "{optional Point | LineString | Polygon | MultiPolygon | mixed | none}"
row_count: "{optional integer row count}"
data_profile:
  field_count: "{required integer non-geometry column count when data_profile is present}"
  identity_candidates:
    - field: "{stable identifier field checked for uniqueness}"
      distinct_values: "{integer distinct non-empty values}"
      duplicate_value_count: "{integer count of repeated non-empty values}"
      duplicate_row_count: "{integer rows carrying repeated non-empty values}"
      status: "{unique | non_unique | unknown | not_applicable}"
      notes: "{short uniqueness note}"
  # If no credible identifier exists, use identity_candidates: [] and notes.
  notes: "{optional profile note such as No documented ext_id candidate}"
search_fields:
  - field: "{curated search/filter field such as NAME}"
    distinct_values: "{optional integer distinct non-empty values}"
    notes: "{optional reason this is useful for search/filtering}"
localized_names:
  storage: "localization_csv_v1"
  join_key: "ext_id"
  localization_file: "latest/{asset-slug}-localizations.csv"
  property_template: "name_{locale_code}"
  locale_code_format: "bcp47_field_safe"
  fallback_field: "name"
  translations:
    - locale_code: "{field-safe BCP 47 locale code such as en or pt_br}"
      field: "name_{locale_code}"
      review_state_field: "name_{locale_code}_review_state"
      label: "{optional human-readable language label}"
      review_state: "{source_provided | machine_translated | human_reviewed | mixed}"
feature_metadata:
  storage: "metadata_sidecar_v1"
  index_backend: "firestore"
  feature_id_column: "feature_id"
  feature_hash_column: "feature_hash"
  sidecar_file: "latest/{asset-slug}.metadata.ndjson.gz"
  schema_file: "latest/{asset-slug}.schema.json"
  manifest_file: "latest/{asset-slug}.manifest.json"
  provenance_default: true
generated_group_id:
  column: "shared_datasets_group_id"
  algorithm: "shared-datasets-group-id:v1"
  grouping_fields:
    - "{curator-selected grouping field}"
  token_length: "{base62 token length, minimum 8}"
  group_count: "{integer generated group count}"
  blank_group_count: "{optional integer blank/null groups assigned per feature}"
  stability: "{geometry-addressed stability note, including any identical-geometry ambiguity review}"
generated_row_id:
  column: "shared_datasets_row_id"
  algorithm: "shared-datasets-row-id:v1"
  token_length: "{base62 token length, minimum 8}"
  row_count: "{integer generated row ID count}"
  duplicate_geometry_digest_count: "{optional count of repeated geometry digests}"
  duplicate_geometry_row_count: "{optional rows carrying repeated geometry digests}"
  stability: "{row-address stability note; stable while canonical geometry and duplicate-geometry source order stay unchanged}"
  warning: "{required warning that this is not a provider/entity/group ID}"
source_resolution_meters: "{optional source resolution for PMTiles auto maxzoom}"
source_scale_denominator: "{optional source scale denominator for PMTiles auto maxzoom}"
pmtiles_maxzoom: "{optional explicit PMTiles maxzoom}"
pmtiles_maxzoom_reason: "{required if pmtiles_maxzoom is set}"
pmtiles_detail_hint: "{optional coarse | medium | detailed}"
files:
  - path: "latest/{asset-slug}.{ext}"
    format: "fgb"
    role: "canonical"
    purpose: "Canonical dataset"
- path: "latest/{asset-slug}-localizations.csv"
  format: "csv"
  role: "localization"
  purpose: "Feature display-name localizations keyed by ext_id for metadata/API use"
  - path: "latest/{asset-slug}.metadata.ndjson.gz"
    format: "ndjson_gzip"
    role: "metadata"
    purpose: "Canonical feature metadata sidecar keyed by feature_id"
  - path: "latest/{asset-slug}.schema.json"
    format: "json"
    role: "metadata"
    purpose: "Release feature schema"
  - path: "latest/{asset-slug}.manifest.json"
    format: "json"
    role: "metadata"
    purpose: "Release manifest"
---

# {Dataset title}

<!-- BEGIN GENERATED asset-summary -->
Run `uv run python scripts/catalog_docs.py generate` after filling in the frontmatter.
<!-- END GENERATED asset-summary -->

## What this is

Briefly describe the dataset in one or two paragraphs.

## When to use it

- Use this for ...
- Do not use this for ...

## Files

<!-- BEGIN GENERATED files-table -->
Run `uv run python scripts/catalog_docs.py generate` after filling in the frontmatter `files` list.
<!-- END GENERATED files-table -->

## Schema notes

Describe important fields, join keys, units, coordinate assumptions, and any known quirks.
Populate `row_count` and `data_profile` in frontmatter from the canonical
artifact after conversion. For identifier candidates, count distinct and
duplicate values over non-empty values in the canonical artifact.
If `generated_group_id` is present, `shared_datasets_group_id` must be a native
property/column in the canonical file and metadata sidecar.
If `generated_row_id` is present, `shared_datasets_row_id` must be a native
property/column in the canonical file and metadata sidecar, and must be
documented as a last-resort row address rather than a provider/entity/group ID.
If the asset publishes localized display names, keep the consumer-facing
localization source in `latest/{asset-slug}-localizations.csv`, keyed by
`ext_id`, and declare it in `localized_names`. Resolve display labels through
the metadata API or localization sidecar; do not put `name` or declared
`name_${locale_code}` fields in PMTiles feature properties. The canonical FGB
must keep unique nonblank `ext_id` values but does not need native localized
name columns.
For vector assets, keep `feature_id` and `feature_hash` in the canonical FGB,
keep `feature_id` and `ext_id` in PMTiles, publish the metadata
sidecar/schema/manifest files, and declare them in `feature_metadata`.

## Raster metadata

Required for canonical COG or Zarr assets. Delete this section for non-raster assets.

| Field | Value |
|---|---|
| CRS | `{EPSG code or WKT summary}` |
| Pixel size / resolution | `{x/y pixel size or nominal resolution}` |
| Dimensions | `{width x height x bands, or Zarr dimensions/chunks}` |
| Band semantics | `{band names/classes/variables}` |
| Data type / nodata | `{dtype and nodata value}` |
| Units / scale / offset | `{units, scale, and offset}` |
| Sampling | `area | point | unknown` |
| Validation | `COG valid; internal overviews; no sidecars | Zarr manifest points at immutable release` |

## Properties / columns

| Name | Type | Description |
|---|---|---|
| `{field_name}` | `{type}` | `{short meaning, units, or source-definition note}` |

If descriptions are not available from the source data or source documentation, list the names/types anyway and mark descriptions as needing source confirmation.

## Update notes

Describe how this dataset is updated. If cron-managed, link to the repo job, scheduler, or script.

## Known caveats

List limitations, source caveats, license caveats, and quality concerns.
