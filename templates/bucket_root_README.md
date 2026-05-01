# SkyTruth Shared Datasets Bucket

This bucket stores reusable shared dataset files for SkyTruth projects.

The control-plane repository for this bucket is:

https://github.com/SkyTruth/shared-datasets-1

Use that repository for source-of-truth documentation, issue discussion,
infrastructure changes, ingestion jobs, upload tooling, and pull requests.

## Start Here

- Catalog: `_catalog/shared-datasets-catalog.csv`
- Dataset documentation: each asset folder has its own `README.md`
- Stable current files: each asset exposes current files under `latest/`
- Versioned snapshots: important or scheduled assets use `releases/YYYY-MM-DD/`
- Run records: scheduled jobs write lightweight records under `runs/`

The `latest/` paths are convenient current pointers. Use `releases/YYYY-MM-DD/`
when reproducibility or a fixed snapshot matters.

## Bucket Taxonomy

Assets are organized by what the dataset is, not by the project that first used
it.

| Prefix | Meaning |
|---|---|
| `_catalog/` | Catalog files and shared indexes. |
| `_templates/` | Bucket-side templates. |
| `_scratch/` | Temporary or exploratory objects that are not canonical data. |
| `_deprecated/` | Deprecated assets kept for compatibility or auditability. |
| `000-system/` | System files, validation outputs, and shared operational assets. |
| `100-geographic-reference/` | Boundaries, marine regions, protected areas, grids, and gazetteers. |
| `200-imagery-derived/` | Remote-sensing and imagery-derived products. |
| `300-infrastructure-industrial/` | Facilities, industrial assets, permits, leases, and infrastructure. |
| `400-events-observations/` | Events, detections, observations, alerts, and scraped feeds. |
| `500-conservation-ecosystems/` | Conservation, land cover, habitats, ecosystems, disturbance, and recovery. |
| `600-maritime-ocean/` | Vessels, AIS-derived products, fishing, and ocean activity. |
| `700-non-geographic-reference/` | Non-spatial lookup tables, crosswalks, organizations, operators, units, and codes. |
| `800-derived-ml-products/` | Reusable labels, features, predictions, benchmarks, and model-ready data. |

Standard asset layout:

```text
{category}/{subcategory}/{asset-slug}/
  README.md
  latest/
    {asset-slug}.{ext}
  releases/
    YYYY-MM-DD/
      {asset-slug}.{ext}
  previews/
    {asset-slug}-preview.png
  runs/
    YYYY-MM-DD.json
```

## Approved Formats

Canonical data should use one of the repository-approved formats:

- `.fgb` for canonical vector data.
- Cloud Optimized GeoTIFF `.tif` for canonical raster data.
- `.zarr/` for multidimensional or chunked array products.
- `.pmtiles` for map tiles and visualization artifacts.
- `.geojson` or `.ndgeojson` for small previews, interchange, or debugging.
- `.csv` for non-geometry tables only.

PNG, JPEG, and WebP files belong under `previews/`. Raw source rasters such as
NetCDF, GRIB, HDF, or non-COG GeoTIFFs belong only under documented
`source/`, `sources/`, or `archive/` exceptions.

## For Maintainers

Use the repository tooling for bucket writes. Do not blindly overwrite existing
objects.

Recommended workflow:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py validate-path gs://skytruth-shared-datasets-1/path/to/object
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat gs://skytruth-shared-datasets-1/path/to/object
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload ./local-file gs://skytruth-shared-datasets-1/path/to/object
```

For replacements, capture the current object generation with `stat` and upload
with `--replace-generation`. For deletes, use the generation-checked `delete`
command and document the changed remote paths in the pull request.

Do not place new files at the bucket root. This `README.md` is the only
intentional root-level object.
