---
schema_version: 1
asset_slug: "{asset-slug}"
title: "{Dataset title}"
category: "{top-level-category}"
subcategory: "{subcategory}"
status: "active"
# For deprecated, superseded, or retired assets, add lifecycle_reason,
# lifecycle_date, and consumer_guidance. Superseded assets also require
# successor_asset_slug.
access_tier: "public"
owner: "{person-or-team}"
update_cadence: "manual"
canonical_format: "{format}"
canonical_file: "latest/{asset-slug}.{ext}"
available_formats:
  - "{format}"
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
geometry_type: "{optional geometry type}"
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
  notes: "{optional profile note such as No documented feature_id candidate}"
search_fields:
  - field: "{curated search/filter field such as NAME}"
    distinct_values: "{optional integer distinct non-empty values}"
    notes: "{optional reason this is useful for search/filtering}"
localized_names:
  storage: "localization_csv_v1"
  join_key: "feature_id"
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
  geometry_hash_column: "geometry_hash"
  properties_hash_column: "properties_hash"
  sidecar_file: "latest/{asset-slug}.metadata.ndjson.gz"
  schema_file: "latest/{asset-slug}.schema.json"
  manifest_file: "latest/{asset-slug}.manifest.json"
  provenance_default: true
feature_identity:
  column: "feature_id"
  strategy: "{source_field | generated_sequence_source_fields | generated_sequence_content_hash}"
  source_fields:
    - "{source field used directly when strategy is source_field}"
  hash_algorithm: "sha256"
  canonicalization_version: "release-feature-model-v1"
  generated_id_type: "monotonic_integer_string"
  assignment_key:
    - "{source field name, or geometry_hash}"
    - "{optional second source field name, or properties_hash}"
  previous_release: "{optional YYYY-MM-DD release used for ID carry-forward}"
  next_generated_id: "{next unused generated decimal ID}"
  ambiguity_report: "{optional path to generated partial-hash match report}"
source_resolution_meters: "{optional source resolution for PMTiles auto maxzoom}"
source_scale_denominator: "{optional source scale denominator for PMTiles auto maxzoom}"
pmtiles_maxzoom: "{optional explicit PMTiles maxzoom}"
pmtiles_maxzoom_reason: "{required if pmtiles_maxzoom is set}"
pmtiles_detail_hint: "{optional coarse | medium | detailed}"
files:
  - path: "latest/{asset-slug}.{ext}"
    format: "{format}"
    role: "canonical"
    purpose: "Canonical file"
- path: "latest/{asset-slug}-localizations.csv"
  format: "csv"
  role: "localization"
  purpose: "Feature display-name localizations keyed by feature_id for metadata/API use"
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

One paragraph.

## Files

<!-- BEGIN GENERATED files-table -->
Run `uv run python scripts/catalog_docs.py generate` after filling in the frontmatter `files` list.
<!-- END GENERATED files-table -->

## Schema notes

Short notes on fields or usage.
Populate `row_count` and `data_profile` in frontmatter from the canonical
artifact after conversion. For identifier candidates, count distinct and
duplicate values over non-empty values in the canonical artifact.
If `feature_identity` is present, `feature_id` must be a native
property/column in the canonical file and metadata sidecar.
If the asset publishes localized display metadata, keep the editable translation
source in `latest/{asset-slug}.metadata-translations.csv`, keyed by
`feature_id`, `field`, `locale`, and `source_value_hash`, and declare it in
`feature_metadata.translations_csv`. Resolve display labels through the
metadata API or locale sidecar; do not put `name` or declared
`name_${locale_code}` fields in PMTiles feature properties. The canonical FGB
must keep unique nonblank URL-safe `feature_id` values matching
`^[A-Za-z0-9]{1,64}$`.
For vector assets, keep `feature_id`, `geometry_hash`, and `properties_hash` in
the canonical FGB, keep only `feature_id` in PMTiles, publish the metadata
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
| `{field_name}` | `{type}` | `{short meaning or "Needs source confirmation."}` |

## Update notes

Manual.
