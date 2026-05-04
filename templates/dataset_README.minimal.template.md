---
schema_version: 1
asset_slug: "{asset-slug}"
title: "{Dataset title}"
category: "{top-level-category}"
subcategory: "{subcategory}"
status: "active"
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
license_flags:
  - "{optional-discovery-flag}"
notes: "{short catalog note}"
bounds: ["{min_lon}", "{min_lat}", "{max_lon}", "{max_lat}"] # optional WGS84 extent
geometry_type: "{optional geometry type}"
row_count: "{optional integer row count}"
files:
  - path: "latest/{asset-slug}.{ext}"
    format: "{format}"
    role: "canonical"
    purpose: "Canonical file"
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
