---
schema_version: 1
asset_slug: "{asset-slug}"
title: "{Dataset title}"
category: "{top-level-category}"
subcategory: "{subcategory}"
status: "active" # active | deprecated | retired | scratch
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
