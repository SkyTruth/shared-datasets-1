# scripts/

This directory contains small operational scripts for maintainers and AI agents.

The most important script is:

```text
scripts/gcs_asset.py
```

It is the default interface for safe Cloud Storage object operations.

Use `publish-release` when local artifacts are ready for an existing catalog
asset. It builds a JSON plan, rejects existing release objects, uploads
`releases/YYYY-MM-DD/` with no-clobber preconditions, blocks incompatible
canonical schema changes before any remote write, replaces `latest/` with
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

Edit `docs/assets/{asset_slug}.md`, including the required `citation` for the
original source publication or authoritative dataset release, then regenerate
local derived files:

```bash
uv run python scripts/catalog_docs.py generate
uv run python scripts/catalog_docs.py check
```

The generator refreshes managed asset-doc blocks,
`catalog/shared-datasets-catalog.csv`, and `docs/assets/index.md`. To prepare
bucket README files without uploading them:

```bash
uv run python scripts/catalog_docs.py export-readmes \
  --output-dir "$TMPDIR/shared-datasets-1/readmes"
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
under the repo temp root described in
`docs/standards/local-temp-workspaces.md`:

```text
${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/vector-assets/{asset-slug}/
```

Override the temp root with `SHARED_DATASETS_WORKDIR` or pass `--work-dir`.
Do not write generated datasets into the repo unless they are tiny intentional
fixtures. Put ad hoc scratch files under
`$TMPDIR/shared-datasets-1/_scratch/{task}-{timestamp}/`, not directly under
`/tmp` or the repository root.

Example:

```bash
uv run python scripts/vector_asset.py build ./source.shp \
  --asset-slug example-asset \
  --layer-name example_asset \
  --title "Example Asset" \
  --description "Example vector tiles" \
  --tile-simplify 0.001
```

The build sequence is standardized as:

1. `ogr2ogr` to FlatGeobuf with `PROMOTE_TO_MULTI` and `SPATIAL_INDEX=YES`.
2. Profile the generated FGB to choose PMTiles maxzoom from source hints and
   measured geometry detail.
3. Stream EPSG:4326 GeoJSONSeq from `ogr2ogr` directly into Tippecanoe for the
   default PMTiles path; no full tiling GeoJSON is written.
4. `tippecanoe` direct PMTiles output with explicit min/max zoom metadata.
5. Local validation with `ogrinfo`, `pmtiles verify`, and a decoded PMTiles
   property sample when those tools exist.

`--maxzoom auto` is the default. Auto maxzoom generates the canonical FGB first,
profiles the FGB, and writes `pmtiles-profile.json` next to the generated
artifacts with the resolved maxzoom and evidence. The policy biases toward
detailed presentation and caps at zoom 12 by default. Lower than zoom 8 requires
source/profile evidence or a documented override; manual `--maxzoom N` requires
`--maxzoom-reason`, and values above zoom 12 require `--allow-high-maxzoom`.

Use source hints when they are stable properties of the upstream data:

```bash
uv run python scripts/vector_asset.py build ./source.shp \
  --asset-slug natural-earth-10m-land \
  --source-scale-denominator 10000000
```

For a read-only acceptance check against an existing local FGB, use the same
profiler without rebuilding artifacts:

```bash
uv run python scripts/vector_asset.py recommend-maxzoom --fgb ./asset.fgb
```

Use `--tile-simplify` for dense coastline, boundary, or global polygon display
tiles. This simplification applies only to PMTiles generation; the canonical FGB
remains unsimplified.

Run the helper through `uv run python` so repo Python dependencies come from the
project environment. GDAL, Tippecanoe, and PMTiles are external command-line
tools resolved from `PATH`; the helper records their versions in the build plan.
The helper rejects Tippecanoe `--exclude-all` because it creates geometry-only
display tiles and leaves catalog feature-inspector clicks with no properties.

Generated group IDs are opt-in. Do not pick a grouping field inside the build
step. First run the publishing concierge or another attribute profile and show
the curator the standard decision table: likely provider `ext_id` candidates and
likely grouping/search/filter candidates, with row/column counts, datatype,
distinction, emptiness, domination, skew ratio, top examples, and concerns.
Distinction is role-dependent: provider IDs should be close to row-unique,
while grouping fields are often useful at middle cardinality; very
low-cardinality fields are usually filters and near-row-unique fields are
usually search-only. The concierge profiles all local rows when practical and
uses a deterministic random sample of about 10,000 rows when exact profiling is
too expensive; do not use first-N-row samples for this decision. Only after the
curator chooses group-level addressing for an asset that lacks a useful provider
row ID should you pass one or more `--group-id-field FIELD` flags:

```bash
uv run python scripts/vector_asset.py build ./source.shp \
  --asset-slug example-asset \
  --group-id-field NAME
```

The helper writes `shared_datasets_group_id` before FGB creation and validates
that the property is present in the FGB schema and decoded PMTiles features.
Use `--group-id-fail-on-ambiguous-geometry` when identical collective geometry
should fail the build instead of being reported for curator review. If
Tippecanoe `--include` filters are passed, include `shared_datasets_group_id`.

If the curator rejects both provider IDs and grouping fields but still needs
row-level addresses, pass `--generate-row-id` instead. This writes
`shared_datasets_row_id` using `shared-datasets-row-id:v1`: per-feature
canonical OGR EPSG:4326 geometry hashes, duplicate geometries disambiguated by
source feature order, and the same base62 collision policy used for group IDs.
This column is a last-resort row address, not a provider/entity/group ID, and is
stable only while geometry and duplicate-geometry source order remain unchanged.
Do not combine `--generate-row-id` with `--group-id-field`.

Use `--pmtiles-engine gdal-mbtiles --pmtiles-bin /path/to/pmtiles` only as an
explicit fallback when Tippecanoe is unavailable; it builds temporary MBTiles
with GDAL and converts them with `pmtiles convert`.

The helper does not upload anything. Stage manual publish candidates under
`_scratch/pending-publishes/{asset-slug}/{proposal-id}/` with
`scripts/gcs_asset.py upload`, then reference those staged objects from an
explicit PR with a fenced publish plan. After the PR merges, the approved GitHub
publisher workflow promotes the reviewed canonical objects so no-clobber and
generation preconditions stay enforced, then deletes the promoted scratch source
objects with their source-generation preconditions. Remaining pending-publish
prefixes are handled by `scripts/scratch_cleanup.py` through the scheduled
`Scratch cleanup audit` workflow.

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

For canonical vector/table assets, enforce schema compatibility before publish:

```bash
uv run python scripts/dataset_alerts.py check-schema-compatibility \
  --asset-slug example-asset \
  --dataset-path ./example-asset.fgb
```

Additive fields pass. Removed fields, renamed fields, and type changes fail
unless a reviewed compatibility waiver is supplied. After a successful
compatible or waived publish, `check-schema` can still emit structured Cloud
Logging warnings and update the snapshot for monitoring.

Production Terraform mutations must land through reviewed PRs and protected
GitHub Actions workflows. Local use of `scripts/terraform_prod_apply.py` is
reserved for explicitly approved break-glass emergencies.

Static catalog web builds use:

```bash
uv run python scripts/catalog_site.py \
  --out "$TMPDIR/shared-datasets-1/catalog-web"
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

Delete only with an explicit object generation. Canonical deletions should flow
through a reviewed PR with a fenced `shared-datasets-delete-plan`; direct CLI
deletes are for the approved workflow, approved publisher identity, or
documented break-glass path:

```bash
uv run python scripts/gcs_asset.py delete gs://skytruth-shared-datasets-1/path/to/object \
  --generation 123456789 \
  --confirm DELETE
```
