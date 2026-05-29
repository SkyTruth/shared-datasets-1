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
- For vector and table assets, frontmatter `row_count` and `data_profile`
  summary metadata used by the browser catalog cards.
- Raster metadata table for canonical COG or Zarr assets, including CRS,
  resolution, dimensions, band semantics, dtype, nodata, units, scale/offset,
  and sampling where applicable.
- Update notes.

If property explanations are unknown, still list names/types and say definitions
need source confirmation.

### Data Profile Frontmatter

For canonical vector and table assets, include compact profile metadata in the
asset-doc frontmatter after the canonical artifact is built:

```yaml
row_count: 12345
data_profile:
  field_count: 8
  identity_candidates:
  - field: source_id
    distinct_values: 12345
    duplicate_value_count: 0
    duplicate_row_count: 0
    status: unique
    notes: Unique
```

Use the canonical artifact, not a source file or PMTiles display artifact, for
these counts. `field_count` is required whenever `data_profile` is present and
is the number of published non-geometry columns.
For identifier candidates, prefer stable source IDs, registry IDs, or fields
documented as identifiers. Count distinct and duplicate values after excluding
null/blank candidate values. `duplicate_value_count` is the number of distinct
candidate values appearing more than once; `duplicate_row_count` is the total
number of rows carrying those repeated values. If no credible identifier field
exists, use:

```yaml
data_profile:
  field_count: 3
  identity_candidates: []
  notes: No documented ext_id candidate
```

Provider ID fields and shared-datasets-generated group IDs are separate
concepts. Keep provider identifiers in `data_profile.identity_candidates` only
when they come from the source and have been checked for uniqueness. Use
`search_fields` for high-value search/filter fields such as names, labels,
sites, regions, or source grouping labels; these fields do not need to be
unique.

### Localized Name Fields

`name_${locale_code}` is the official shared-datasets storage contract for
translated feature display names in vector and table assets. Datasets without
translated display names omit this metadata. Any dataset that publishes
translated display names must declare `localized_names` in the asset-doc
frontmatter and must store consumer-facing translation fields as native
properties/columns named with this pattern:

```yaml
localized_names:
  property_template: name_{locale_code}
  locale_code_format: bcp47_field_safe
  fallback_locale: en
  fallback_field: name_en
  translations:
  - locale_code: en
    field: name_en
    label: English
    review_state: source_provided
  - locale_code: es
    field: name_es
    label: Spanish
    review_state: machine_translated
```

Locale codes use lower-case, ASCII, field-safe BCP 47 tags with hyphens
normalized to underscores, such as `en`, `es`, `pt_br`, `es_419`, or
`zh_hans`. Each `field` must exactly equal `name_${locale_code}`. If a source
uses different translation columns, preserve those source-native fields only
when useful, but add normalized `name_*` fields as the shared-datasets consumer
contract. Each translation must declare `review_state` as `source_provided`,
`machine_translated`, or `human_reviewed`. Use `source_provided` only for names
supplied by the authoritative upstream source, `machine_translated` for
unreviewed generated translations, and `human_reviewed` after human curator
review or correction. `fallback_field`, when present, must name one of the
declared translation fields. For assets with PMTiles companions, every declared
`name_*` field must be preserved in PMTiles feature properties; review state
lives in catalog metadata, not feature properties.

Use `generated_group_id` only when an asset needs generated group-level
addressing and lacks a useful provider row ID. The generated native column is
`shared_datasets_group_id`, produced with `shared-datasets-group-id:v1`.
`shared_datasets_group_id` must be a native property/column in the canonical
vector/table artifact and must be preserved in PMTiles feature properties.
Do not generate this column by default for every asset; first evaluate provider
IDs and group/search needs, and leave `generated_group_id` absent when no
curator-approved grouping field exists.
Agents must present provider ID candidates and grouping/search field candidates
before generating `shared_datasets_group_id`; if the current request has not
selected a grouping field, stop at the options step. Use the standard decision
table from `scripts/publishing_concierge.py` or an equivalent profile. It must
show file row and column counts, then compact sections for likely provider
`ext_id` options and likely grouping/search/filter options. For displayed
candidate fields, include datatype, distinction (`distinct values / profiled
rows`), emptiness, domination, skew ratio
(`top-value count / (non-empty rows / distinct values)`), top-value examples,
and concerns. Distinction is a different signal by role: provider IDs should be
near row-unique, while grouping fields are often most useful at middle
cardinality; very low-cardinality fields are usually filters, and
near-row-unique fields are usually search-only. When local full-row profiling is
practical, compute exact stats. If exact profiling is too expensive, use a
deterministic random sample of about 10,000 rows and clearly mark stats as
sample estimates; do not use first-N-row samples for this decision.
Generated group IDs are geometry-addressed within the asset: they are stable for
unchanged collective group geometry, including source-name or label changes, but
they are not persistent business/entity IDs across material source geometry
changes. If multiple grouping values share identical collective geometry, the
helper reports that ambiguity because those groups intentionally receive the
same generated ID unless a curator chooses an explicit stable disambiguator.

For vector assets, use `generated_row_id` only as the explicit last-resort
row-addressing fallback when no provider ID is suitable and no curator-approved
grouping field exists. The generated native column is
`shared_datasets_row_id`, produced with `shared-datasets-row-id:v1`. It is not a
provider ID, entity ID, or group ID. It must not be listed in
`data_profile.identity_candidates`, and it must not be combined with
`generated_group_id` for the same asset.

The row-ID algorithm is deterministic for the same asset slug, OGR feature
order, and geometry bytes after the standard OGR normalization step:

1. Stream source features with `ogr2ogr -f GeoJSONSeq -t_srs EPSG:4326`.
2. Normalize each GeoJSON geometry by sorting object keys, preserving array and
   ring order, requiring finite coordinates, rounding numeric coordinates with
   Python `.15g`, and normalizing negative zero to zero.
3. Compute `geometry_digest = sha256(canonical_json(normalized_geometry))`,
   where canonical JSON uses sorted keys, ASCII output, and no insignificant
   whitespace.
4. For duplicate geometry digests, assign a zero-based
   `geometry_duplicate_ordinal` in streamed source feature order; unique
   geometries use ordinal `0`.
5. Build the row preimage as canonical JSON containing exactly
   `algorithm`, `asset_slug`, `geometry_digest`, and
   `geometry_duplicate_ordinal`.
6. Hash that preimage with SHA-256, encode as fixed-length base62, and choose
   the shortest token length whose birthday-bound collision probability is at
   or below `2e-10`, with minimum length `8`. If an automatic token length
   produces a real token collision, increment length and retry; if an explicit
   token length collides, fail.

Because duplicate geometries are disambiguated by source feature order,
`shared_datasets_row_id` is stable only while canonical geometry and duplicate
geometry ordering remain unchanged. Document this limitation in
`generated_row_id.stability` and include duplicate-geometry counts when present.

Use `templates/dataset_README.template.md` for important assets and
`templates/dataset_README.minimal.template.md` for small/simple assets.
