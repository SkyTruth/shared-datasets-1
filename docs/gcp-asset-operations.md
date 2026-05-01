# GCP asset operations

This document summarizes how this repo expects maintainers and AI agents to inspect, upload, edit, and publish files in the shared datasets bucket.

For the full operational procedure, read `.claude/skills/gcp-shared-datasets/SKILL.md`.

## Chosen approach

Use a small repo-owned Python CLI/library built on `google-cloud-storage` for data object operations.

Use `uv` for local Python dependency management and command execution.

Use Terraform for GCP infrastructure.

Use `gcloud storage` only for manual diagnostics or emergency one-off operations.

Do not use Terraform or Pulumi to manage frequently changing dataset files.

Do not use Cloud Storage FUSE for canonical writes.

## Why

Remote dataset files need safe, repeatable object mutations. GCS generation preconditions let us prevent accidental overwrites. A Python CLI can make those safety checks the default and can also enforce SkyTruth naming/path conventions.

## Standard commands

```bash
uv run python scripts/gcs_asset.py list gs://$SHARED_DATASETS_BUCKET/
uv run python scripts/gcs_asset.py stat gs://$SHARED_DATASETS_BUCKET/path/to/object
uv run python scripts/gcs_asset.py download gs://$SHARED_DATASETS_BUCKET/path/to/object /tmp/object
uv run python scripts/gcs_asset.py upload ./local-file gs://$SHARED_DATASETS_BUCKET/path/to/new-object
uv run python scripts/gcs_asset.py upload ./local-file gs://$SHARED_DATASETS_BUCKET/path/to/existing-object --replace-generation <generation>
```

## Safe update pattern

1. `stat` destination object.
2. Download it if editing.
3. Make local changes.
4. Upload with `--replace-generation`.
5. Verify with `stat`.
6. Update README/catalog when relevant.

## New object pattern

By default, `upload` uses no-clobber behavior and fails if a live object already exists.

For dataset roots, create or update the adjacent `README.md` with enough schema detail for a consumer to inspect the asset without opening the full data file. Where possible, include a properties/columns table with field names, types, and short explanations. If field meanings are not available, list names/types and mark definitions as needing source confirmation.

## Local generated files

Do not create publishable data artifacts in the repository tree. Build generated
files under the system temp namespace by default:

```text
$TMPDIR/shared-datasets-1/vector-assets/{asset-slug}/
```

The environment variable `SHARED_DATASETS_WORKDIR` may override the temp root
for large local builds. Keep final upload candidates in the work directory's
`publish/` subdirectory and intermediates in `build/`.

For manual vector assets, use the repo-owned vector helper:

```bash
uv run python scripts/vector_asset.py build ./source.shp \
  --asset-slug example-asset \
  --layer-name example_asset \
  --title "Example Asset" \
  --description "Example vector tiles" \
  --maxzoom 8 \
  --tile-simplify 0.001
```

The standard vector build is:

1. Write canonical `.fgb` with `ogr2ogr -f FlatGeobuf`, `PROMOTE_TO_MULTI`, and
   `SPATIAL_INDEX=YES`.
2. Write temporary EPSG:4326 GeoJSON for tiling.
3. Generate direct `.pmtiles` output with Tippecanoe, explicit tileset name,
   description, and min/max zoom.
4. Validate the FGB with `ogrinfo` and the PMTiles archive with `pmtiles verify`
   when those tools are installed.

`--tile-simplify` is for dense display tiles only. It is applied to the
temporary tiling input and does not simplify the canonical FGB.

For dense point layers where low-zoom completeness matters, pass explicit
Tippecanoe retention flags instead of allowing feature dropping:

```bash
uv run python scripts/vector_asset.py build ./source.fgb \
  --asset-slug example-points \
  --tippecanoe-arg=--no-feature-limit \
  --tippecanoe-arg=--no-tile-size-limit \
  --tippecanoe-arg=--drop-rate=1
```

Use this for point catalogs where users need to see most or all point features
while zoomed far out. Expect larger low-zoom tiles and validate browser
performance before publishing.
Run the helper through `uv run python` so repo Python dependencies come from the
project environment. GDAL, Tippecanoe, and PMTiles are external command-line
tools resolved from `PATH`; the helper records their versions in the build plan.
Use `--pmtiles-engine gdal-mbtiles` only as an explicit fallback when
Tippecanoe is unavailable; that path writes temporary MBTiles with GDAL and
converts them with `pmtiles convert`.

Upload the resulting files with `scripts/gcs_asset.py`; the vector helper never
mutates Cloud Storage.

## Unsafe overwrites

Only use `--unsafe-overwrite` when the user explicitly requests it or when operating under `_scratch/`.

Record unsafe overwrites in the PR or final response.
