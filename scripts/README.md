# scripts/

This directory contains small operational scripts for maintainers and AI agents.

The most important script is:

```text
scripts/gcs_asset.py
```

It is the default interface for safe Cloud Storage object operations.

Use `publish-release` when local artifacts are ready for an existing catalog
asset. It builds a JSON plan, rejects existing release objects, uploads
`releases/YYYY-MM-DD/` with no-clobber preconditions, replaces `latest/` with
observed generations, writes a run record, and emits schema/upload alerts:

```bash
uv run python scripts/gcs_asset.py publish-release \
  --asset-slug example-asset \
  --release-date 2026-05-01 \
  --publish-dir "$TMPDIR/shared-datasets-1/vector-assets/example-asset/publish" \
  --source-version "source version or URL" \
  --dry-run
```

If intentionally publishing only a subset of catalog-listed formats, name each
unchanged companion explicitly with `--allow-stale-format`.

Catalog and asset README generation lives in:

```text
scripts/catalog_docs.py
```

Edit `docs/assets/{asset_slug}.md`, then regenerate local derived files:

```bash
uv run python scripts/catalog_docs.py generate
uv run python scripts/catalog_docs.py check
```

The generator refreshes managed asset-doc blocks,
`catalog/shared-datasets-catalog.csv`, and `docs/assets/index.md`. To prepare
bucket README files without uploading them:

```bash
uv run python scripts/catalog_docs.py export-readmes --output-dir /tmp/shared-dataset-readmes
```

Raster validation helpers live in:

```text
scripts/raster_asset.py
```

Use `validate-cog` before publishing a local Cloud Optimized GeoTIFF:

```bash
uv run python scripts/raster_asset.py validate-cog ./asset.tif
```

Vector artifact build helpers live in:

```text
scripts/vector_asset.py
```

Use `build` before publishing a manual vector asset that needs canonical FGB and
PMTiles. The helper writes generated files outside the repository by default,
under the system temp namespace:

```text
$TMPDIR/shared-datasets-1/vector-assets/{asset-slug}/
```

Override the temp root with `SHARED_DATASETS_WORKDIR` or pass `--work-dir`.
Do not write generated datasets into the repo unless they are tiny intentional
fixtures.

Example:

```bash
uv run python scripts/vector_asset.py build ./source.shp \
  --asset-slug example-asset \
  --layer-name example_asset \
  --title "Example Asset" \
  --description "Example vector tiles" \
  --maxzoom 8 \
  --tile-simplify 0.001
```

The build sequence is standardized as:

1. `ogr2ogr` to FlatGeobuf with `PROMOTE_TO_MULTI` and `SPATIAL_INDEX=YES`.
2. `ogr2ogr` to temporary GeoJSON in EPSG:4326 for tiling.
3. `tippecanoe` direct PMTiles output with explicit min/max zoom metadata.
4. Local validation with `ogrinfo`, `pmtiles verify`, and a decoded PMTiles
   property sample when those tools exist.

Shared PMTiles should be built to maxzoom 8 or higher. The helper rejects lower
maxzoom values unless `--allow-low-maxzoom` is passed for a documented
exception.

Use `--tile-simplify` for dense coastline, boundary, or global polygon display
tiles. This simplification applies only to PMTiles generation; the canonical FGB
remains unsimplified.

Run the helper through `uv run python` so repo Python dependencies come from the
project environment. GDAL, Tippecanoe, and PMTiles are external command-line
tools resolved from `PATH`; the helper records their versions in the build plan.
The helper rejects Tippecanoe `--exclude-all` because it creates geometry-only
display tiles and leaves catalog feature-inspector clicks with no properties.

Use `--pmtiles-engine gdal-mbtiles --pmtiles-bin /path/to/pmtiles` only as an
explicit fallback when Tippecanoe is unavailable; it builds temporary MBTiles
with GDAL and converts them with `pmtiles convert`.

The helper does not upload anything. Use `scripts/gcs_asset.py upload` for all
Cloud Storage writes so no-clobber and generation preconditions stay enforced.

Publishing concierge planning lives in:

```text
scripts/publishing_concierge.py
```

Use it at the start of a manual dataset publish when you want one local plan
that ties together slug, taxonomy, canonical path, draft asset docs, build
commands, catalog checks, and the eventual safe upload path. The concierge never
writes to Cloud Storage.

Example:

```bash
uv run python scripts/publishing_concierge.py ./source.shp \
  --asset-slug example-asset \
  --title "Example Asset" \
  --category 100-geographic-reference \
  --subcategory 110-boundaries \
  --source-name "Example source v1" \
  --license "Example terms" \
  --with-pmtiles \
  --release-date 2026-05-01 \
  --write-draft-doc
```

Review the generated JSON plan and draft `docs/assets/{asset-slug}.md`, then run
the suggested existing helpers. Remote writes still go through
`scripts/gcs_asset.py`.

Slack notification helpers live in:

```text
scripts/dataset_alerts.py
scripts/repo_alerts.py
scripts/slack_notify.py
```

The committing agent decides whether a commit adds substantially exciting new
repository functionality. When it does, the agent appends one or more fenced
`repo-alert` blocks to the commit message. The `repo-functionality-alert`
GitHub Actions workflow runs after pushes to `main` and posts any fenced alert
blocks it finds.

Alert blocks use this format:

````text
```repo-alert
emoji: 🗺️
headline: Vector publishing helper added
summary: A new command builds FlatGeobuf and PMTiles artifacts from source vectors.
why_excited: Manual publishes are faster, more repeatable, and easier to review.
```
````

Preview alerts from a saved GitHub push event JSON:

```bash
uv run python scripts/repo_alerts.py send-from-github-event \
  --event-path /path/to/push-event.json \
  --dry-run
```

After a successful manual dataset upload, post a lightweight summary:

```bash
uv run python scripts/dataset_alerts.py upload-summary \
  --asset-slug example-asset \
  --changed-path gs://skytruth-shared-datasets-1/path/to/object.fgb \
  --dataset-path ./example-asset.fgb
```

For canonical vector/table assets, compare and update the schema snapshot:

```bash
uv run python scripts/dataset_alerts.py check-schema \
  --asset-slug example-asset \
  --dataset-path ./example-asset.fgb
```

Schema deltas are emitted as structured Cloud Logging warnings and delivered
through the Cloud Monitoring Slack alert channel.

Production Terraform applies should use:

```bash
uv run python scripts/terraform_prod_apply.py
```

Static catalog web builds use:

```bash
uv run python scripts/catalog_site.py --out /tmp/shared-datasets-1/catalog-web
```

The generated site reads `catalog.json` at runtime and provides search, metadata
copy buttons, and PMTiles previews for assets with map tiles.

Install dependencies:

```bash
uv sync
```

Show help:

```bash
uv run python scripts/gcs_asset.py --help
```

Validate a target object path before upload:

```bash
uv run python scripts/gcs_asset.py validate-path \
  gs://skytruth-shared-datasets-1/100-geographic-reference/130-protected-areas/wdpa/latest/wdpa.fgb
```

Delete only with an explicit object generation:

```bash
uv run python scripts/gcs_asset.py delete gs://skytruth-shared-datasets-1/path/to/object \
  --generation 123456789 \
  --confirm DELETE
```
