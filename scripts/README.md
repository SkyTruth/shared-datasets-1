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
3. Export a WGS84 GeoJSONSeq tile source from the generated FGB with GDAL.
4. Build temporary MBTiles from that GeoJSONSeq with Tippecanoe using explicit
   min/max zoom metadata and any compact property filters such as `feature_id`
   and `ext_id`.
5. Convert the MBTiles archive to PMTiles with `pmtiles convert`.
6. Local validation with `ogrinfo`, PMTiles v3 magic-byte checks,
   `pmtiles verify`, `pmtiles show`, and a decoded PMTiles property sample when
   those tools exist.

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
project environment. GDAL and PMTiles are external command-line tools resolved
from `PATH`; the helper records their versions in the build plan.

Generated group IDs are opt-in. Do not pick a grouping field inside the build
step. First run the publishing concierge or another attribute profile and show
the curator the standard decision table: likely provider `ext_id` candidates and
likely grouping/search/filter candidates, plus the generated numeric sequence
fallback for `ext_id`, with row/column counts, datatype, distinction,
emptiness, domination, skew ratio, top examples, and concerns. Provider or group
fields may be used as `ext_id` only when every value is unique, nonblank, and
matches `^[A-Za-z0-9]{1,64}$`.
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
should fail the build instead of being reported for curator review.

If the curator rejects both provider IDs and grouping fields but still needs
row-level addresses, pass `--generate-row-id` instead. This writes
`shared_datasets_row_id` using `shared-datasets-row-id:v1`: per-feature
canonical OGR EPSG:4326 geometry hashes, duplicate geometries disambiguated by
source feature order, and the same base62 collision policy used for group IDs.
This column is a last-resort row address, not a provider/entity/group ID, and is
stable only while geometry and duplicate-geometry source order remain unchanged.
Do not combine `--generate-row-id` with `--group-id-field`.

PMTiles generation exports a WGS84 GeoJSONSeq tile source with GDAL, writes the
temporary MBTiles with Tippecanoe, and converts that archive with
`pmtiles convert`. Do not use the old GDAL-based projection path; for
metadata-lookup tiles it can omit geometry and produce empty or bad MBTiles
output before PMTiles conversion.

Release feature metadata helpers live in:

```text
scripts/release_feature_model.py
scripts/feature_metadata_localization.py
scripts/feature_metadata_index.py
```

Use `release_feature_model.py` from publishing or ingestion code to construct
stable `feature_id` values, compute separate `feature_hash` values, serialize
`{asset-slug}.metadata.ndjson.gz`, validate sidecar rows, and build release
manifests. Use `feature_metadata_index.py --dry-run` to validate a sidecar
without writing Firestore, and without `--dry-run` only from an approved runtime
that is meant to refresh the rebuildable serving index.

Use `feature_metadata_localization.py` to materialize generated locale-specific
metadata views from the canonical sidecar plus an editable translation source:

```bash
uv run python scripts/feature_metadata_localization.py \
  --canonical-sidecar "$WORK_ROOT/vector-assets/example-asset/publish/example-asset.metadata.ndjson.gz" \
  --translation-source "$WORK_ROOT/vector-assets/example-asset/publish/example-asset.metadata-translations.csv" \
  --schema "$WORK_ROOT/vector-assets/example-asset/publish/example-asset.schema.json" \
  --translatable-field name \
  --locale es \
  --output-sidecar "$WORK_ROOT/vector-assets/example-asset/publish/example-asset.metadata.es.ndjson.gz" \
  --asset-slug example-asset \
  --release 2026-05-01 \
  --report "$WORK_ROOT/vector-assets/example-asset/publish/example-asset.metadata.es.report.json"
```

Generate every locale present in a translation source with deterministic
localized filenames and one report per locale:

```bash
uv run python scripts/feature_metadata_localization.py \
  --canonical-sidecar "$WORK_ROOT/vector-assets/example-asset/publish/example-asset.metadata.ndjson.gz" \
  --translation-source "$WORK_ROOT/vector-assets/example-asset/publish/example-asset.metadata-translations.csv" \
  --schema "$WORK_ROOT/vector-assets/example-asset/publish/example-asset.schema.json" \
  --all-locales \
  --output-dir "$WORK_ROOT/vector-assets/example-asset/publish" \
  --report-dir "$WORK_ROOT/vector-assets/example-asset/reports" \
  --asset-slug example-asset \
  --release 2026-05-01 \
  --report "$WORK_ROOT/vector-assets/example-asset/reports/localization-summary.json"
```

Translation rows are keyed by `feature_id`, `field`, `locale`, and
`source_value_hash`. The generator applies only rows whose source hash still
matches the canonical property value, reports stale rows, leaves untranslated
values canonical, rejects duplicate keys, and writes deterministic gzip NDJSON.
The catalog viewer resolver serves one localized sidecar for the active locale
when available and falls back to the canonical sidecar when it is not.

`feature_metadata_translation_pipeline.py` is the GitHub Actions pipeline entry
point for reviewed translation-source updates. The
`Feature metadata localization materialization` workflow runs after the
approved dataset mutation workflow succeeds, extracts any promoted
`{asset-slug}.metadata-translations.csv` objects from the reviewed publish
plan, downloads the sibling canonical sidecar and schema, materializes all
available locale sidecars, and uploads those generated sidecars with current
generation preconditions from the approved publisher environment. Manual
dispatch can run the same pipeline for an explicit canonical translation-source
URI.

## Localized display-name sidecars

Localized display names use a same-asset CSV sidecar rather than localized
columns in the canonical FGB or PMTiles. The FGB and PMTiles must have a unique
nonblank URL-safe `ext_id` matching `^[A-Za-z0-9]{1,64}$`; PMTiles also carry
`feature_id` for metadata lookup. The localization CSV must have `ext_id`,
fallback `name`, `name_review_state`, and optional `name_{locale_code}` /
`name_{locale_code}_review_state` pairs.

Seed missing rows from a freshly built FGB without overwriting existing
translations:

```bash
uv run python scripts/localized_vector_asset.py seed-localizations \
  --fgb "$TMPDIR/shared-datasets-1/vector-assets/example-asset/publish/example-asset.fgb" \
  --ext-id-field ext_id \
  --fallback-name-field source_name \
  --localizations "$TMPDIR/shared-datasets-1/vector-assets/example-asset/publish/example-asset-localizations.csv"
```

Validate the sidecar against the asset doc and FGB coverage:

```bash
uv run python scripts/localized_vector_asset.py validate-localizations \
  --fgb ./example-asset.fgb \
  --localizations ./example-asset-localizations.csv \
  --asset-doc docs/assets/example-asset.md
```

Build release-oriented PMTiles from the unchanged FGB geometry plus metadata
lookup IDs:

```bash
uv run python scripts/vector_asset.py build \
  ./example-asset.fgb \
  --asset-slug example-asset \
  --pmtiles-feature-id-property feature_id
```

The vector helper projects PMTiles properties to `feature_id` and `ext_id`.
Display labels for release-oriented catalog preview maps are resolved through
the metadata API or one materialized locale-specific metadata sidecar, not by
fetching a translation overlay in the browser. Review-state fields stay in the
CSV and catalog metadata. For PR descriptions,
`draft-publish-plan` can draft the data-object promotion list,
including the translation-only shape that leaves `latest/{asset-slug}.fgb`
unchanged.

The helper does not upload anything. Stage manual publish candidates under
`_scratch/pending-publishes/{asset-slug}/{proposal-id}/` with
`scripts/gcs_asset.py upload`, then reference those staged objects from an
explicit PR with a fenced publish plan. After the PR merges, the approved GitHub
publisher workflow promotes the reviewed canonical objects so no-clobber and
generation preconditions stay enforced, then deletes the promoted scratch source
objects with their source-generation preconditions. Remaining pending-publish
prefixes are handled by `scripts/scratch_cleanup.py` through the scheduled
`Scratch cleanup audit` workflow.

Publishing concierge planning and first-upload workflow guidance live in:

```text
scripts/publishing_concierge.py
```

Use it at the start of a manual dataset publish when you want the agent to stop
playing checklist memory games. The stateful workflow prints exactly one next
required step, waits while the agent performs the requested research/build/check
outside the script, and advances only after structured evidence validates.

Start a workflow:

```bash
uv run python scripts/publishing_concierge.py start ./source.shp \
  --asset-slug example-asset \
  --title "Example Asset" \
  --category 100-geographic-reference \
  --subcategory 110-boundaries \
  --source-name "Example source v1" \
  --license "Example terms" \
  --request-classification canonical-publish \
  --proposal-id pr-123 \
  --release-date 2026-05-01
```

The start command writes state under
`${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/publishing-concierge/{asset-slug}/{proposal-id}.state.json`
unless `--state-file` is provided.

Then loop:

```bash
uv run python scripts/publishing_concierge.py next --state-file "$STATE_FILE"
uv run python scripts/publishing_concierge.py confirm \
  --state-file "$STATE_FILE" \
  --step resolve-metadata \
  --evidence-json evidence.json
```

Useful inspection commands:

```bash
uv run python scripts/publishing_concierge.py status --state-file "$STATE_FILE"
uv run python scripts/publishing_concierge.py validate --state-file "$STATE_FILE"
uv run python scripts/publishing_concierge.py render-pr --state-file "$STATE_FILE"
uv run python scripts/publishing_concierge.py render-report --state-file "$STATE_FILE"
```

The concierge is guide-and-verify only. It never stages Git changes, commits,
pushes, opens PRs, uploads scratch objects, writes canonical Cloud Storage
objects, or promotes data. Do not use it to run Terraform apply; production
Terraform still routes through protected PR workflows. When `next` asks for scratch
staging, run `scripts/gcs_asset.py upload` separately and provide the staged URI
and generation as evidence. `render-pr` validates the final fenced
`shared-datasets-publish-plan` with `scripts/reviewed_dataset_plan.py`, the same
schema used by the protected promotion workflow.

`start` requires an explicit request classification and only proceeds for
`canonical-publish`. It also blocks duplicate first-upload asset slugs unless
`--allow-existing-asset` is passed after review. Later evidence gates require
metadata/admission fields, generated-ID decisions, artifact paths, validation
commands, resolved tool paths/versions or not-applicable notes, scratch source
generations, and canonical destination generation expectations before the
workflow can render a PR.

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

For canonical vector/table assets, run schema validation before publish:

```bash
uv run python scripts/dataset_alerts.py check-schema-compatibility \
  --asset-slug example-asset \
  --dataset-path ./example-asset.fgb
```

Schema validation reports added, removed, renamed, reordered, and type-changed
fields so reviewers can confirm the new release schema is intentional. After a
successful reviewed publish, `check-schema` can still emit structured Cloud
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
