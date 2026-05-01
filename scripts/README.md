# scripts/

This directory contains small operational scripts for maintainers and AI agents.

The most important script is:

```text
scripts/gcs_asset.py
```

It is the default interface for safe Cloud Storage object operations.

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
4. Local validation with `ogrinfo` and `pmtiles verify` when those tools exist.

Use `--tile-simplify` for dense coastline, boundary, or global polygon display
tiles. This simplification applies only to PMTiles generation; the canonical FGB
remains unsimplified.

Run the helper through `uv run python` so repo Python dependencies come from the
project environment. GDAL, Tippecanoe, and PMTiles are external command-line
tools resolved from `PATH`; the helper records their versions in the build plan.

Use `--pmtiles-engine gdal-mbtiles --pmtiles-bin /path/to/pmtiles` only as an
explicit fallback when Tippecanoe is unavailable; it builds temporary MBTiles
with GDAL and converts them with `pmtiles convert`.

The helper does not upload anything. Use `scripts/gcs_asset.py upload` for all
Cloud Storage writes so no-clobber and generation preconditions stay enforced.

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

Install dependencies:

```bash
uv sync
```

Show help:

```bash
uv run python scripts/gcs_asset.py --help
```
