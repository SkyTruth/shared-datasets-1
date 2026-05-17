# GCP asset operations

This document summarizes how this repo expects maintainers and AI agents to inspect, upload, edit, and publish files in the shared datasets bucket.

For remote object safety and commands, read
`.claude/skills/gcp-shared-datasets/SKILL.md`. For manual dataset add/update
publishing, read `.claude/skills/publish-shared-dataset/SKILL.md`.

## Chosen approach

Use a small repo-owned Python CLI/library built on `google-cloud-storage` for data object operations.

Use `uv` for local Python dependency management and command execution.

Use Terraform for GCP infrastructure.

Use `gcloud storage` only for manual diagnostics, emergency downloads, and
documented break-glass operations.

Do not use Terraform or Pulumi to manage frequently changing dataset files.

Do not use Cloud Storage FUSE for canonical writes.

Canonical writes are controlled. Humans and general-purpose agents should stage
manual publish bytes under `_scratch/pending-publishes/` and promote approved
objects only through an explicit PR with a fenced publish or delete plan. After
that PR merges, the GitHub `Approved dataset mutation` workflow promotes the
planned objects under the `shared-datasets-production` environment. Do not use
standalone workflow dispatch or single-object fallback inputs to bypass a PR.
Direct local writes to `latest/`, `releases/`, dataset README files, or
`_catalog/` are reserved for the approved publisher identity or documented
break-glass response.

## Why

Remote dataset files need safe, repeatable object mutations. GCS generation preconditions let us prevent accidental overwrites. A Python CLI can make those safety checks the default and can also enforce SkyTruth naming/path conventions.

## Standard commands

```bash
uv run python scripts/gcs_asset.py list gs://$SHARED_DATASETS_BUCKET/
uv run python scripts/gcs_asset.py stat gs://$SHARED_DATASETS_BUCKET/path/to/object
uv run python scripts/gcs_asset.py download gs://$SHARED_DATASETS_BUCKET/path/to/object \
  "$TMPDIR/shared-datasets-1/downloads/object"
uv run python scripts/gcs_asset.py upload ./local-file \
  gs://$SHARED_DATASETS_BUCKET/_scratch/pending-publishes/example-asset/123/local-file
```

## Safe update pattern

1. `stat` destination object.
2. Download it if editing.
3. Make local changes.
4. Stage the edited file under `_scratch/pending-publishes/`.
5. Open an explicit PR with a fenced publish plan, then promote through the
   approved publisher workflow after merge, passing the destination generation
   as the workflow `destination_generation` precondition.
6. Update README/catalog when relevant, including `citation` when source
   publication metadata changes.

## New object pattern

By default, `upload` uses no-clobber behavior and fails if a live object already exists.

For dataset roots, create or update the adjacent `README.md` with enough schema
detail for a consumer to inspect the asset without opening the full data file.
Include the source citation when preparing catalog-backed metadata. Where
possible, include a properties/columns table with field names, types, and short
explanations. If field meanings are not available, list names/types and mark
definitions as needing source confirmation.

## Local generated files

Follow `docs/standards/local-temp-workspaces.md` for local downloads, scratch
files, generated artifacts, and cleanup. Do not create publishable data
artifacts in the repository tree. Build generated files under the repo temp root
by default:

```text
${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}
```

Use named children such as `vector-assets/{asset-slug}/`, `downloads/{asset}/`,
`catalog-web/`, `readmes/`, or `_scratch/{task}-{timestamp}/`. The environment
variable `SHARED_DATASETS_WORKDIR` may override the temp root for large local
builds. Keep final upload candidates in the work directory's `publish/`
subdirectory and intermediates in `build/`.

For manual vector assets, use the repo-owned vector helper:

```bash
uv run python scripts/vector_asset.py build ./source.shp \
  --asset-slug example-asset \
  --layer-name example_asset \
  --title "Example Asset" \
  --description "Example vector tiles" \
  --tile-simplify 0.001
```

The standard vector build is:

1. Write canonical `.fgb` with `ogr2ogr -f FlatGeobuf`, `PROMOTE_TO_MULTI`, and
   `SPATIAL_INDEX=YES`.
2. Profile the generated FGB and resolve PMTiles maxzoom from source
   scale/resolution hints plus measured geometry detail. Auto maxzoom is the
   default, biases toward detailed presentation, and caps at zoom 12 unless a
   documented high-zoom override is passed.
3. Stream EPSG:4326 GeoJSONSeq from `ogr2ogr` directly into Tippecanoe for the
   default PMTiles path. The helper does not materialize a full tiling GeoJSON.
4. Generate direct `.pmtiles` output with Tippecanoe, explicit tileset name,
   description, and min/max zoom. Lower than zoom 8 requires source/profile
   evidence or a documented override.
5. Validate the FGB with `ogrinfo`, the PMTiles archive with `pmtiles verify`
   when available, and a decoded PMTiles sample to confirm feature properties
   are present for the catalog inspector.

Generated group IDs are opt-in. Present provider ID candidates and
grouping/search field candidates before adding `--group-id-field`. Use the
standard concierge decision table: row/column counts plus likely provider
`ext_id` options and likely grouping/search/filter options, each with datatype,
distinction, emptiness, domination, skew ratio, top examples, and concerns. Run
exact stats on all local rows when practical; if that is too expensive, use a
deterministic random sample of about 10,000 rows, not a first-N-row sample. When
a curator chooses group-level addressing for an asset that lacks a useful
provider row ID, pass `--group-id-field FIELD` to `scripts/vector_asset.py
build`, repeating the flag for composite grouping fields. The helper writes
`shared_datasets_group_id` before FGB creation and validates that the property
survives into decoded PMTiles features. If Tippecanoe `--include` filters are
used, include `shared_datasets_group_id`.

If no provider ID or grouping field is suitable and the curator explicitly
requires row-level addresses, pass `--generate-row-id` instead. This writes
`shared_datasets_row_id` using `shared-datasets-row-id:v1`: canonical OGR
EPSG:4326 geometry hashes per feature, duplicate geometries disambiguated by
source feature order, and the same base62 collision policy as group IDs. This is
a last-resort row address, not a provider/entity/group ID, and must not be
combined with `--group-id-field`.

For corrective PMTiles-only rebuilds on a versioned asset, replace the
`latest/*.pmtiles` object and the PMTiles object under the matching canonical
`releases/YYYY-MM-DD/` directory with generation preconditions. Do not create a
new dated release directory that contains only PMTiles unless PMTiles is the
canonical format; release-index dates should correspond to releases that include
the canonical asset file.

`--tile-simplify` is for dense display tiles only. It is applied in the streamed
PMTiles conversion and does not simplify the canonical FGB.

To run a read-only maxzoom acceptance check against an existing canonical FGB,
download the object with `scripts/gcs_asset.py download` and profile the local
copy:

```bash
uv run python scripts/vector_asset.py recommend-maxzoom --fgb ./asset.fgb
```

For dense point layers where low-zoom completeness matters, pass explicit
Tippecanoe retention flags instead of allowing feature dropping:

```bash
uv run python scripts/vector_asset.py build ./source.fgb \
  --asset-slug example-points
```

The standard Tippecanoe path adds `--no-feature-limit`, `--no-tile-size-limit`,
and `--drop-rate=1` by default so low-zoom PMTiles previews retain published
point features.

Do not use Tippecanoe `--exclude-all` for shared-datasets PMTiles. The vector
helper rejects that flag because it strips feature properties and leaves clicked
catalog objects with an empty inspector. If a display tile needs fewer
attributes, use narrower property filters or add compact synthetic properties
such as `source_layer`.

Use this for point catalogs because shared point PMTiles should keep all point
features at all generated zoom levels. Expect larger low-zoom tiles and validate
browser performance before publishing.
Run the helper through `uv run python` so repo Python dependencies come from the
project environment. GDAL, Tippecanoe, and PMTiles are external command-line
tools resolved from `PATH`; the helper records their versions in the build plan.
Use `--pmtiles-engine gdal-mbtiles` only as an explicit fallback when
Tippecanoe is unavailable; that path writes temporary MBTiles with GDAL and
converts them with `pmtiles convert`.

Upload the resulting files with `scripts/gcs_asset.py`; the vector helper never
mutates Cloud Storage.

## Delete pattern

Only use `delete` with an explicit object generation. Canonical deletions should
flow through a reviewed PR containing a fenced `shared-datasets-delete-plan` so
the approved workflow can delete with generation preconditions under the
publisher identity. Prefix deletes, wildcards, and generation-less deletes are
invalid.

```bash
uv run python scripts/gcs_asset.py delete gs://$SHARED_DATASETS_BUCKET/path/to/object \
  --generation 123456789 \
  --confirm DELETE
```

## Unsafe overwrites

Only use `--unsafe-overwrite` when the user explicitly requests it or when operating under `_scratch/`.

Record unsafe overwrites in the PR or final response.
