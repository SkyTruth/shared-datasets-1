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
  stable `feature_id` and `ext_id` properties only. Full attributes and display
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
  identity remains stable. It may be generated from a verified provider ID or
  generated by SkyTruth when no provider ID is selected.
- `feature_hash`: content fingerprint over canonical geometry and published
  nonvolatile attributes. This changes when feature content changes and must not
  be used as the feature lookup ID.

Identity strategy priority:

1. Verified source/provider ID field with uniqueness, non-emptiness, stability,
   datatype, duplicate, skew, and top-value evidence.
2. Curator-approved composite provider key with the same evidence.
3. Curator-approved generated per-feature ID. The approving curator is the
   maintainer uploading the file the first time or setting up the cron job.

`shared_datasets_group_id` and `shared_datasets_row_id` remain separate
concepts. A group ID is not unique per feature. A generated row ID is a
last-resort row address and does not replace the release `feature_id` decision.
When maintainers choose neither a provider ID nor a group ID for `ext_id`,
publish `ext_id` as the same value as `feature_id`.

Required release artifacts for vector releases:

| File | Role |
|---|---|
| `{asset-slug}.fgb` | Truth-preserving canonical vector artifact with full attributes, `feature_id`, and `feature_hash`. |
| `{asset-slug}.pmtiles` | Lightweight display artifact with geometry, `feature_id`, and `ext_id` only. |
| `{asset-slug}.metadata.ndjson.gz` | Canonical durable metadata sidecar, one JSON object per `feature_id`. |
| `{asset-slug}.metadata.{locale}.ndjson.gz` | Optional generated locale-specific metadata view. Same row shape as the canonical sidecar, with translated display values already materialized into `properties`. |
| `{asset-slug}.metadata-translations.csv` | Optional editable translation source keyed by `feature_id`, field name, locale, and source-value hash. |
| `{asset-slug}.schema.json` | Field names, types, nullable fields, reserved fields, and projection allowlist. |
| `{asset-slug}.manifest.json` | Source inputs, artifact paths, checksums, destination generations for non-manifest artifacts, schema version, ID strategy, validation, and note that index status is tracked under `index-loads/`. The manifest does not embed its own object generation. |

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
canonical sidecar's row count, `feature_id`, and `feature_hash` values.
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
  notes: No documented ext_id candidate
```

Provider ID fields and shared-datasets-generated group IDs are separate
concepts. Keep provider identifiers in `data_profile.identity_candidates` only
when they come from the source and have been checked for uniqueness. Use
`search_fields` for high-value search/filter fields such as names, labels,
sites, regions, or source grouping labels; these fields do not need to be
unique.

For vector assets, add `feature_metadata` frontmatter:

```yaml
feature_metadata:
  storage: metadata_sidecar_v1
  index_backend: firestore
  feature_id_column: feature_id
  feature_hash_column: feature_hash
  sidecar_file: latest/example-asset.metadata.ndjson.gz
  schema_file: latest/example-asset.schema.json
  manifest_file: latest/example-asset.manifest.json
  provenance_default: true
```

### Localized Name Sidecars

Older/simple localized display-name workflows use a same-asset CSV sidecar
keyed by a stable canonical FGB `ext_id` column. Release-oriented metadata
sidecar assets should instead use the
`{asset-slug}.metadata-translations.csv` source and generated
`{asset-slug}.metadata.{locale}.ndjson.gz` views described above. Datasets
without localized display names omit this metadata. Release-oriented PMTiles do
not carry `name` or `name_*` properties; they carry `feature_id` and `ext_id`,
and feature inspectors or apps resolve display labels through the metadata API
or one materialized locale-specific metadata sidecar.
Any dataset that publishes localized display names must declare
`localized_names` in the asset-doc frontmatter and must stage a localization CSV
at `latest/{asset-slug}-localizations.csv`:

```yaml
localized_names:
  storage: localization_csv_v1
  join_key: ext_id
  localization_file: latest/example-asset-localizations.csv
  property_template: name_{locale_code}
  locale_code_format: bcp47_field_safe
  fallback_field: name
  translations:
  - locale_code: es
    field: name_es
    review_state_field: name_es_review_state
    label: Spanish
    review_state: mixed
```

The localization CSV schema is:

```csv
ext_id,name,name_review_state,name_es,name_es_review_state
```

`ext_id`, `name`, and `name_review_state` are required. Optional locale pairs
use `name_${locale_code}` and `name_${locale_code}_review_state`. Locale codes
use lower-case, ASCII, field-safe BCP 47 tags with hyphens normalized to
underscores, such as `en`, `es`, `pt_br`, `es_419`, or `zh_hans`. Per-value
review states must be `source_provided`, `machine_translated`, or
`human_reviewed`; the asset-doc aggregate `review_state` may also be `mixed`
when a locale has values from more than one state.

The canonical FGB must contain a unique nonblank `ext_id` column, and PMTiles
must carry the same `ext_id` value as a feature property. For release-oriented
assets that do not use a provider ID or group ID as `ext_id`, this column is the
always-present `feature_id`. The localization CSV must contain one unique
nonblank row for every current FGB `ext_id`. The fallback `name` value is
required for every row. Blank localized values must have blank review states,
and nonblank localized values must have a review state. Per-value review-state
fields stay in the CSV and catalog metadata.

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

Use `generated_group_id` only when an asset needs generated group-level
addressing and lacks a useful provider row ID. The generated native column is
`shared_datasets_group_id`, produced with `shared-datasets-group-id:v1`.
`shared_datasets_group_id` must be a native property/column in the canonical
vector/table artifact and metadata sidecar. Do not generate this column by
default for every asset; first evaluate provider IDs and group/search needs,
and leave `generated_group_id` absent when no curator-approved grouping field
exists.
Agents must present provider ID candidates and grouping/search field candidates
before generating `shared_datasets_group_id`; if the current request has not
selected a grouping field, stop at the options step. Use the standard decision
table from `scripts/publishing_concierge.py` or an equivalent profile. It must
show file row and column counts, then compact sections for likely provider
`ext_id` options, the `feature_id` fallback, and likely grouping/search/filter
options. For displayed candidate fields, include datatype, distinction
(`distinct values / profiled rows`), emptiness, domination, skew ratio
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
