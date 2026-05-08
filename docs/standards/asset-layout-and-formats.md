# Asset Layout And Formats

Use this document for shared dataset object layout, naming, approved formats,
and asset README requirements.

## Approved Formats

Only these canonical or supported formats are approved by default:

| Format identifier | Extension/path | Use |
|---|---|---|
| `fgb` | `.fgb` | Canonical geographic vector data |
| `cog` | `.tif` | Canonical raster data as Cloud Optimized GeoTIFF |
| `zarr` | `.zarr/` prefix | Canonical multidimensional/chunked array products |
| `pmtiles` | `.pmtiles` | Web map tiles and visualization artifacts |
| `geojson` | `.geojson` | Small previews, small interchange files, debugging |
| `ndgeojson` | `.ndgeojson` | Streamable vector interchange/debugging |
| `csv` | `.csv` | Non-geometry tables only |

Rules:

- CSV must not contain geometry columns such as WKT, WKB, GeoJSON geometry
  blobs, latitude/longitude pairs intended as geometry, or encoded geometries
  unless clearly documented as noncanonical source/debug content.
- `.fgb` is the preferred canonical vector format.
- `.pmtiles` is a serving/display artifact, not the canonical analytical source.
- Shared vector `.pmtiles` display artifacts should use the repo vector
  helper's auto maxzoom policy: generate the canonical FGB first, profile the
  FGB, then choose maxzoom from source scale/resolution metadata and measured
  geometry detail. The policy biases toward detailed presentation and caps at
  zoom 12 by default. Lower than zoom 8 requires source/profile evidence or a
  documented override.
- `.geojson` should stay small enough to inspect or transfer easily.
- PNG, JPEG, and WebP files are allowed only under `previews/` or as tile
  encodings inside `.pmtiles`.
- NetCDF, GRIB, HDF, raw non-COG GeoTIFF, and similar source rasters are allowed
  only under `source/`, `sources/`, or `archive/` by documented README
  exception.
- Analyst-friendly source exports such as `.xlsx` are not canonical dataset
  formats. For a publish request, convert them into an approved canonical format
  and stage the original source export under `_scratch/pending-publishes/` as
  review evidence unless a documented source/archive exception and path
  validation explicitly allow promotion.
- Adding a new canonical format requires explicit approval and updates to this
  doc, templates, catalog schema/validation, and review guidance.

## Raster Rules

Cloud Optimized GeoTIFF is the preferred canonical raster format. Publish COGs
as `.tif`, not raw GeoTIFFs.

COGs must be:

- internally tiled,
- internally overviewed,
- georeferenced,
- self-contained with no required `.aux.xml`, `.ovr`, `.tfw`, or similar
  sidecars.

COG defaults are:

- object content type: `image/tiff; application=geotiff; profile=cloud-optimized`
- `BIGTIFF=IF_SAFER`
- 512 pixel blocks
- internal overviews
- lossless compression

Use nearest-neighbor overviews for categorical/class/mask rasters. Use average
or documented continuous resampling for measured continuous grids.

Zarr is approved only for true multidimensional, time-series, variable-rich, or
chunked array products where COG would be a poor access pattern.

## Default Asset Layout

```text
{category}/{subcategory}/{asset-slug}/
  README.md
  latest/
    {asset-slug}.{ext}
    manifest.json        # only for multi-object assets such as Zarr
  releases/
    YYYY-MM-DD/
      {asset-slug}.{ext}
      {asset-slug}.zarr/ # only for Zarr and other approved prefix formats
  previews/
    {asset-slug}-preview.png
  runs/
    YYYY-MM-DD.json
```

Minimum valid asset:

```text
{category}/{subcategory}/{asset-slug}/
  README.md
  latest/
    {asset-slug}.{ext}
```

Use `releases/YYYY-MM-DD/` when the asset is cron-updated, used by more than one
major project, difficult to recreate, expensive to recreate, or needed for
reproducible downstream model/analysis snapshots.

Use `runs/YYYY-MM-DD.json` when a scheduled job generated/refreshed the asset, a
failed run needs documentation, or a backfill occurred.

Single-object assets, including COGs, may use standard `latest/` and
`releases/YYYY-MM-DD/` file copies.

Multi-object assets, including Zarr, must write immutable data under
`releases/YYYY-MM-DD/{asset-slug}.zarr/` and update only
`latest/manifest.json`. Do not mirror thousands of mutable Zarr chunk objects
under `latest/`.

Required Zarr latest manifest shape:

```json
{
  "asset_slug": "example-asset",
  "canonical_format": "zarr",
  "updated": "YYYY-MM-DD",
  "release_path": "gs://skytruth-shared-datasets-1/category/subcategory/example-asset/releases/YYYY-MM-DD/example-asset.zarr/"
}
```

## Naming

Use lowercase kebab-case for asset slugs:

```text
wdpa
offshore-platforms
natural-earth-admin0
iso-country-codes
cerulean-slick-labels
```

Avoid names like:

```text
WDPA_latest_FINAL
JonaUpload
UseThisOne
platforms_v2_FINAL_really
```

File naming:

```text
{asset-slug}.{ext}
{asset-slug}-{layer}.{ext}
```

Avoid dates in filenames when the date is already encoded in
`releases/YYYY-MM-DD/`.

## README Requirements

Every asset must have a `README.md`. In this repo, `docs/assets/{asset-slug}.md`
is the local source used to generate catalog metadata and upload-ready bucket
README content.

Required fields:

- Title.
- Status.
- Owner.
- Update cadence.
- Canonical file.
- Source.
- License / terms.
- Citation for the original source publication or authoritative dataset
  release.
- Short explanation of what the asset is.
- File table.
- Schema notes or field notes.
- Property/column table with names, types, and short explanations where these
  can be derived from the source data or source documentation.
- Raster metadata table for canonical COG or Zarr assets, including CRS,
  resolution, dimensions, band semantics, dtype, nodata, units, scale/offset,
  and sampling where applicable.
- Update notes.

If property explanations are unknown, still list names/types and say definitions
need source confirmation.

Use `templates/dataset_README.template.md` for important assets and
`templates/dataset_README.minimal.template.md` for small/simple assets.
