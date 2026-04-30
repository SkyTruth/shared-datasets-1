# {Dataset title}

**Status:** active  
**Owner:** {person-or-team}  
**Last updated:** YYYY-MM-DD  
**Update cadence:** static | manual | daily | weekly | monthly | ad hoc  
**Canonical file:** `latest/{asset-slug}.{ext}`  
**Source:** {source name or URL}  
**License / terms:** {short note}

## What this is

One paragraph.

## Files

| File | Purpose |
|---|---|
| `latest/{asset-slug}.{ext}` | Canonical file |

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
