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
| `metadata_sidecar_v1` | `.metadata.ndjson.gz` | Canonical feature metadata sidecar keyed by stable `feature_id` |
| `release_schema_v1` | `.schema.json` | Release feature schema for sidecar/API field validation |
| `release_manifest_v1` | `.manifest.json` | Release manifest tying source inputs, artifacts, checksums, IDs, validation, and index status policy |

Rules:

- CSV must not contain geometry columns such as WKT, WKB, GeoJSON geometry
  blobs, latitude/longitude pairs intended as geometry, or encoded geometries
  unless clearly documented as noncanonical source/debug content.
- `.fgb` is the preferred canonical vector format.
- `.pmtiles` is a serving/display artifact, not the canonical analytical source.
- For release-oriented vector assets, the conceptual source of truth is the
  reproducible release feature model plus release manifest. The FGB remains the
  canonical vector artifact for consumers, but do not treat FGB alone as the
  only release truth.
- Release-oriented PMTiles should be intentionally lightweight: geometry plus
  stable `feature_id` properties only. Full attributes and display
  labels belong in the FGB and metadata sidecar/API.
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
    {asset-slug}.metadata.ndjson.gz # release-oriented vector metadata sidecar
    {asset-slug}.metadata.es.ndjson.gz # generated localized metadata view
    {asset-slug}.schema.json        # release-oriented vector schema
    {asset-slug}.manifest.json      # release-oriented vector manifest
    {asset-slug}.metadata-translations.csv # optional editable translation source
    manifest.json        # only for multi-object assets such as Zarr
  releases/
    YYYY-MM-DD/
      {asset-slug}.{ext}
      {asset-slug}.metadata.ndjson.gz # release-oriented vector metadata sidecar
      {asset-slug}.metadata.es.ndjson.gz # generated localized metadata view
      {asset-slug}.schema.json        # release-oriented vector schema
      {asset-slug}.manifest.json      # release-oriented vector manifest
      {asset-slug}.metadata-translations.csv # optional editable translation source
      {asset-slug}.zarr/ # only for Zarr and other approved prefix formats
  previews/
    {asset-slug}-preview.png
  runs/
    YYYY-MM-DD.json
  index-loads/
    YYYY-MM-DD/
      {load-id}.json
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

Use `index-loads/YYYY-MM-DD/{load-id}.json` when a release metadata sidecar is
loaded into a NoSQL serving index. These records are operational status for the
rebuildable serving copy; do not rewrite release manifests just to update index
load state.

Single-object assets, including COGs, may use standard `latest/` and
`releases/YYYY-MM-DD/` file copies.

## Release-Oriented Vector Metadata

Every vector release must be built from a normalized release feature model
before publication. That model must assign:

- `feature_id`: always-present row-level ID, stable and unique per feature
  within an asset release, and stable across releases whenever the source
  identity remains stable. It may be generated from a verified source field ID or
  generated by SkyTruth when no source field ID is selected.
- `geometry_hash`: content fingerprint over canonical geometry.
- `properties_hash`: content fingerprint over published non-geometry
  attributes. Hashes change when feature content changes and must not be used as
  the feature lookup ID.

Identity strategy priority:

1. Verified source/source field ID field with uniqueness, non-emptiness, stability,
   datatype, duplicate, skew, and top-value evidence.
2. Curator-approved composite source-field key with the same evidence.
3. Curator-approved generated per-feature ID. The approving curator is the
   maintainer uploading the file the first time or setting up the cron job.

`feature_id` is the public/user-facing URL handle. It must be unique,
nonblank, and match
`^[A-Za-z0-9]{1,64}$`; do not publish colon, underscore, slash, dot, hyphen,
space, or other punctuation in `feature_id`. Source fields may be used only
when every value is unique, nonblank, and matches that regex. When maintainers
choose generated IDs, publish generated decimal sequence handles (`1`, `2`,
`3`, ...), preserving prior-release identity-key to `feature_id` mappings on
refreshes and never reusing retired sequence values.

`geometry_hash` and `properties_hash` are stored identity evidence, not lookup
handles. They support generated-ID assignment, duplicate collapse, and
maintainer review of ambiguous refreshes.

Required release artifacts for vector releases:

| File | Role |
|---|---|
| `{asset-slug}.fgb` | Truth-preserving canonical vector artifact with full attributes, `feature_id`, `geometry_hash`, and `properties_hash`. |
| `{asset-slug}.pmtiles` | Lightweight display artifact with geometry and `feature_id` only. |
| `{asset-slug}.metadata.ndjson.gz` | Canonical durable metadata sidecar, one JSON object per `feature_id`. |
| `{asset-slug}.metadata.{locale}.ndjson.gz` | Optional generated locale-specific metadata view. Same row shape as the canonical sidecar, with translated display values already materialized into `properties`. |
| `{asset-slug}.metadata-translations.csv` | Optional editable translation source keyed by `feature_id`, field name, locale, and source-value hash. |
| `{asset-slug}.schema.json` | Field names, types, nullable fields, reserved fields, and projection allowlist. |
| `{asset-slug}.manifest.json` | Source inputs, artifact paths, checksums, destination generations for non-manifest artifacts, schema version, identity policy, validation, and dormant-index status. The manifest does not embed its own object generation. |

The metadata sidecar is canonical and durable in GCS. Firestore is the initial
serving index for lookup APIs, but it is a rebuildable copy/cache loaded from
the sidecar.

Locale-specific metadata sidecars are derived artifacts, not translation
sources of truth. Maintain translations in a compact CSV source named
`{asset-slug}.metadata-translations.csv` with these columns:

```csv
feature_id,field,locale,source_value_hash,value,review_state,notes
```

`feature_id`, `field`, `locale`, `source_value_hash`, and `value` are required.
`review_state` and `notes` are optional. `source_value_hash` is the SHA-256 hash
of the canonical source property value serialized with the release feature
model's stable JSON rules. During publish/build preparation, generate
`{asset-slug}.metadata.{locale}.ndjson.gz` with
`scripts/feature_metadata_localization.py`; translations apply only when the
current canonical property hash matches the translation row. Stale rows are
reported and skipped, untranslated values remain canonical, duplicate
translation keys fail validation, and the localized sidecar must preserve the
canonical sidecar's row count, `feature_id`, and `properties_hash` values.
Use `--all-locales` during publish/build preparation to materialize every
locale present in the translation source. After a reviewed publish plan promotes
a new `{asset-slug}.metadata-translations.csv`, the
`Feature metadata localization materialization` workflow regenerates sibling
localized metadata sidecars from the promoted CSV, canonical sidecar, and
schema, then uploads the derived sidecars with current-generation
preconditions from the approved publisher environment.

Catalog and app consumers must not fetch or merge a translation overlay. A
browser or resolver requests one metadata sidecar for the active locale; if the
localized sidecar is absent, the resolver returns the canonical
`{asset-slug}.metadata.ndjson.gz` fallback.

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
  notes: No documented feature_id candidate
```

Keep source identifiers in `data_profile.identity_candidates` only when they
come from the source and have been checked for uniqueness, non-emptiness, and
URL-safety. Use `search_fields` for high-value search/filter fields such as
names, labels, sites, regions, or source grouping labels; these fields do not
need to be unique.

For vector assets, add `feature_metadata` frontmatter:

```yaml
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  geometry_hash_column: geometry_hash
  properties_hash_column: properties_hash
  sidecar_file: latest/example-asset.metadata.ndjson.gz
  schema_file: latest/example-asset.schema.json
  manifest_file: latest/example-asset.manifest.json
  provenance_default: true
```

### Metadata Translation Sidecars

Release-oriented metadata sidecar assets use the
`{asset-slug}.metadata-translations.csv` source and generated
`{asset-slug}.metadata.{locale}.ndjson.gz` views described above. Datasets
without localized display metadata omit this metadata. Release-oriented PMTiles
do not carry `name` or `name_*` properties; they carry only `feature_id`, and
feature inspectors or apps resolve display labels through one materialized
locale-specific metadata sidecar, or through the metadata API after Firestore
serving is enabled. Any dataset that publishes localized display metadata must
stage a translation CSV at
`latest/{asset-slug}.metadata-translations.csv` and list it with the generated
locale-specific metadata sidecars in the release metadata files.

```yaml
files:
  - path: latest/example-asset.metadata-translations.csv
    format: csv
    role: metadata_translation_source
  - path: latest/example-asset.metadata.es.ndjson.gz
    format: ndjson_gzip
    role: metadata_localized
```

The metadata translation CSV schema is:

```csv
feature_id,field,locale,source_value,translated_value,source_value_hash,review_state
```

`feature_id`, `field`, `locale`, and `source_value_hash` form the translation
row key. Locale codes use lower-case, ASCII, field-safe BCP 47 tags with hyphens
normalized to underscores, such as `en`, `es`, `pt_br`, `es_419`, or
`zh_hans`. Per-value review states must be `source_provided`,
`machine_translated`, or `human_reviewed`; the asset-doc aggregate
`review_state` may also be `mixed` when a locale has values from more than one
state.

The canonical FGB must contain unique nonblank URL-safe `feature_id` values, and
PMTiles must carry the same `feature_id` value as a feature property. For
release-oriented assets that do not use a URL-safe source field ID as
`feature_id`, this column is the generated decimal sequence handle.

Translation-only updates publish the updated translation source and regenerated
localized metadata sidecars, then reload or refresh any serving metadata index
that uses those localized views. For versioned assets, a translation-only
release should copy byte-identical current FGB and PMTiles objects into the new
release directory when a new release is needed, publish the updated translation
source under release and `latest/`, publish regenerated localized metadata
sidecars under release and `latest/`, and rebuild the release index after
promotion. For first uploads of release-oriented vector assets, maintainers
must choose which locales and which metadata fields, if any, the agent should
autogenerate before publish artifacts are considered complete; requested rows
belong in `{asset-slug}.metadata-translations.csv`, not in the canonical
metadata sidecar.

Use `feature_identity` to document the selected identity policy:

```yaml
feature_identity:
  column: feature_id
  strategy: source_field
  source_fields:
  - MRGID
```

Generated-ID assets instead record:

```yaml
feature_identity:
  column: feature_id
  strategy: generated_sequence_source_fields
  source_fields:
  - SITE_PID
  hash_algorithm: sha256
  canonicalization_version: release-feature-model-v1
  generated_id_type: monotonic_integer_string
  assignment_key:
  - SITE_PID
  previous_release: 2026-06-05
  next_generated_id: "18430"
```

Generated IDs are monotonically increasing decimal strings. Refreshes reuse a
prior `feature_id` when the same assignment key is seen, never reuse retired
IDs, collapse exact duplicate generated-ID rows, and emit an ambiguity report
for partial hash matches that require maintainer review. The
`generated_sequence_source_fields` strategy accepts one or two source fields.
Use one field when a single source column is unique and non-null but not
URL-safe; use two only when the pair is the reviewed stable identity key. If no
source-field identity key is valid, use `generated_sequence_content_hash` with
`assignment_key: [geometry_hash, properties_hash]`. Use the standard decision
table from `scripts/publishing_concierge.py` or an equivalent profile before
publishing a new vector/table asset. It must show file row and column counts,
likely URL-safe source field candidates, the generated sequence fallback, and
likely search/filter fields.

Use `templates/dataset_README.template.md` for important assets and
`templates/dataset_README.minimal.template.md` for small/simple assets.
