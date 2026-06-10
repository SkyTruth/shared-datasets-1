---
name: publish-shared-dataset
description: Use before manually adding, updating, publishing, or documenting a shared dataset asset, including under-specified uploads, taxonomy classification, local publish artifact builds, asset docs, catalog regeneration, safe GCS publication, and dataset upload/schema alerts.
---

# Publish Shared Dataset

Use this skill for manual dataset additions and updates in `shared-datasets-1`.
It covers the dataset-facing workflow. Load `gcp-shared-datasets` before any
remote GCS inspection or write.

## Trigger Discipline

Treat plain-language requests such as "add this dataset," "upload this file,"
"put this data in shared-datasets," or "publish this" as official manual
dataset intake requests unless the user explicitly says scratch-only,
diagnostic-only, or no publication workflow. Do not satisfy those requests by
copying the supplied file to GCS alone.

The default deliverable is a complete reviewed publish proposal: discover
source metadata, choose taxonomy and asset slug, build every required approved
artifact and companion file, update asset documentation and catalog outputs,
stage all promotion candidates with generation safety, and open the reviewed PR
with a publish plan. For vector assets, that normally means canonical FGB,
PMTiles display tiles, metadata sidecar, schema, manifest, README/catalog
updates, and release/run metadata where the asset layout requires them.

If any required artifact fails to build or validate, the publish request is not
complete. Record the failure, retain diagnostic artifacts in the standard temp
workspace or reviewed scratch area as appropriate, and report the blocker
instead of calling the upload successful.

## Required Context

Read these before choosing names, paths, formats, or remote writes:

- `AGENTS.md`
- `catalog/categories.yaml`
- `docs/standards/dataset-taxonomy.md`
- `docs/standards/asset-layout-and-formats.md`
- `.claude/skills/gcp-shared-datasets/SKILL.md` before any GCS operation
- `.claude/skills/static-catalog-web-preview/SKILL.md` before refreshing the
  live catalog UI cache after dataset edits or uploads
- `.claude/skills/update-feature-metadata-translations/SKILL.md` before adding,
  editing, reviewing, regenerating, or publishing release-oriented feature
  metadata translations
- `.claude/skills/protected-terraform-apply/SKILL.md` before any access-tier
  change, PMTiles CDN route sync, public managed-folder IAM change, or other
  production Terraform mutation
- `scripts/publishing_concierge.py` before new manual asset intake or
  under-specified upload planning
- Relevant existing `docs/assets/{asset-slug}.md` files and nearby catalog rows

## Under-Specified Upload Discovery

Minimal user context does not justify vague metadata, arbitrary bucket
placement, or weak naming. Before proposing or performing an upload, infer as
much as practical from:

- File names, local paths, remote prefixes, and source URLs.
- Object metadata, file metadata, layer names, embedded dataset metadata, and
  README/source documentation.
- Schemas, property names, property types, and example rows.
- Existing nearby bucket assets, asset docs, README files, and catalog rows.
- Source documentation and internet searches when the source identity is
  discoverable.

Use the evidence to propose a lowercase kebab-case asset slug, taxonomy
classification, canonical format, target asset directory, and README content. If
confidence is still low after discovery, stop before remote writes and ask for
the missing confirmation.

Do not treat an unsupported source format as permission to do a scratch-only
upload. For requests phrased as "upload this," "add this data," or "publish this
file," the default outcome is a reviewed canonical dataset proposal. If the
provided file is a source container or analyst-friendly export such as `.xlsx`,
`.zip`, raw GeoTIFF, NetCDF, or another noncanonical format, use it as source
material, infer the approved canonical output, and keep working through artifact
build, docs, catalog generation, staging, and PR creation. Stage the original
source file only as reviewed scratch evidence unless `source/` or `archive/`
promotion is allowed by the format standards and path validation. In either
case, the asset README/PR must explain why consumers should use the canonical
artifact instead.

## Publishing Concierge

For a new manual asset, an under-specified upload, or any intake where the
slug/taxonomy/format/doc path is not already settled, start the local
stateful concierge before building artifacts or writing asset docs:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/publishing_concierge.py start ./source.shp \
  --asset-slug example-asset \
  --title "Example Asset" \
  --category 100-geographic-reference \
  --subcategory 110-boundaries \
  --source-name "Example source v1" \
  --license "Example terms" \
  --request-classification canonical-publish \
  --proposal-id pr-123 \
  --release-date YYYY-MM-DD
```

Then repeat this loop:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/publishing_concierge.py next \
  --state-file "$STATE_FILE"
# Do the requested research, local build, validation, or scratch staging outside
# the concierge.
UV_CACHE_DIR=.uv-cache uv run python scripts/publishing_concierge.py confirm \
  --state-file "$STATE_FILE" --step STEP_ID --evidence-json evidence.json
```

The concierge intentionally waits at each step until the agent submits
structured evidence. It does not advance merely because a step was printed, and
`--yes` is rejected for steps that require evidence. Use `status`, `validate`,
and `render-pr` to inspect the workflow, check PR readiness, and generate the PR
body with the fenced `shared-datasets-publish-plan`, and render a final
completion-report scaffold.

The concierge is guide-and-verify tooling: it never stages Git changes, commits,
pushes, opens PRs, writes canonical Cloud Storage objects, or promotes data. Do not
use it to run Terraform apply; production Terraform still routes through
protected PR workflows. Scratch upload commands are suggestions; the agent must run
the appropriate `gcs_asset.py` commands separately and feed the resulting URIs
and generations back as evidence. Artifact validation evidence must include the
commands run and resolved tool paths/versions or explicit not-applicable notes.
The final publish-plan is validated through `scripts/reviewed_dataset_plan.py`,
so it matches the protected workflow's schema.

If an approved mutation workflow fails after some promotions have already run,
use the concierge retry helper instead of rerunning the stale PR body:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/publishing_concierge.py refresh-retry-plan \
  --plan publish-plan.json \
  --stat-gcs \
  --stats-output retry-gcs-stats.json \
  --output retry-publish-plan.json \
  --summary-output retry-summary.json
```

The command refreshes destination generations only after the staged source
generation is still present and, for existing destinations, the current
destination CRC32C matches the staged source CRC32C. It leaves still-absent
destinations as no-clobber creates. Use `--remove-waivers` only after confirming
that a partial run already advanced the schema snapshot and the protected schema
preflight no longer reports blocked schema changes.

For FGB vector assets, the concierge plans both canonical FGB and PMTiles
display artifacts by default.

Prefer the concierge output over ad hoc path math for:

- Asset root and canonical `gs://` path.
- Canonical format and PMTiles companion expectations.
- Draft asset doc location and first-pass frontmatter.
- Standard work directory and publish directory.
- Suggested build, catalog, validation, and dry-run publish commands.

## Classification And Standards

- Classify by what the dataset is, not by the project that first needed it.
- Treat `catalog/categories.yaml` as the category/subcategory data source.
- Use `docs/standards/dataset-taxonomy.md` for classification principles and
  examples.
- Use `docs/standards/asset-layout-and-formats.md` for approved formats, asset
  layout, README requirements, naming rules, COG rules, and Zarr rules.
- Do not create a new top-level category or add a new canonical file format
  without explicit human approval.

## Local Artifact Preparation

- Keep generated publishable data outside the repo tree unless it is a tiny
  intentional fixture.
- Convert source files into approved canonical formats before staging promotion
  candidates. Source spreadsheets with coordinates, WKT, WKB, GeoJSON geometry,
  or other mappable geometry should become canonical vector artifacts, normally
  FGB plus PMTiles. Source spreadsheets without geometry should become canonical
  CSV after checking that geometry-like columns are not intended for spatial
  analysis. Raw rasters should become COGs unless Zarr is the documented better
  access pattern. Do not add a new canonical format just because the user
  supplied that format.
- For vector assets, prefer:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/vector_asset.py build ./source.fgb \
  --asset-slug example-asset \
  --layer-name example_asset \
  --title "Example Asset" \
  --description "Example vector tiles"
```

- Feature identity is a recorded decision, not a default. Before building
  release artifacts, present the curator with the standard feature identity
  decision table from `publishing_concierge.py` or an equivalent profile. The
  table must include file row/column counts and, for displayed candidates,
  datatype, distinction (`distinct values / profiled rows`), emptiness,
  domination, skew ratio, top-value examples, and concerns. Show likely
  URL-safe source-field `feature_id` candidates, the generated monotonic
  decimal sequence fallback, and likely search/filter fields by default; keep
  the full per-field profile in JSON or notes for inspection. Run exact stats
  on all local rows when the source is small enough. When exact full-column
  counters would be too expensive, use a deterministic random sample of about
  10,000 rows, never the first N rows, and label the output as sampled. Prefer
  a verified unique, nonblank source field whose values already satisfy the
  `feature_id` rules (`^[A-Za-z0-9]{1,64}$`) and copy it directly as
  `feature_id`. If no source field is suitable, assign generated monotonic
  decimal `feature_id` values from an approved assignment key: one or two
  source fields, or the pair of stored geometry and properties hashes.
  Preserve prior-release identity-key to `feature_id` mappings when
  regenerating IDs. Do not infer and publish any generated ID from guessed
  fields or without the recorded decision point.
- Local downloads, generated artifacts, and scratch files must follow
  `docs/standards/local-temp-workspaces.md`.
- The default vector work directory is
  `${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/vector-assets/{asset-slug}/`,
  with upload candidates under `publish/` and intermediates under `build/`.
- PMTiles display artifacts must preserve compact feature properties for the
  catalog inspector. Use the repo vector helper's validated path: export a
  WGS84 GeoJSONSeq tile source from the canonical FGB with GDAL, build MBTiles
  with Tippecanoe, then convert with `pmtiles convert`. Do not use the old
  GDAL-based projection path; metadata-lookup SQL that selected `feature_id`
  and `ext_id` failed to carry geometry through and produced empty or bad
  MBTiles output.
- A PMTiles artifact is valid only after archive-level and content-level
  checks pass. Do not trust the file extension or a successful tile-builder exit
  code. For every PMTiles build, verify that the file is not MBTiles/SQLite,
  confirm the PMTiles magic bytes, run `pmtiles verify`, inspect `pmtiles show`
  for min/max zoom, tile type, compression, layer metadata, and bounds, and
  decode representative tiles to confirm the expected layer and the compact
  `feature_id` lookup property. If the build tool produced MBTiles, convert
  it with `pmtiles convert` and validate the converted archive before upload.
  Record these validation commands in the PR or final response.
- Release-oriented vector assets use a strict artifact set: canonical FGB with
  `feature_id`, `geometry_hash`, and `properties_hash`, PMTiles filtered to
  geometry plus `feature_id` only, a canonical `{asset-slug}.metadata.ndjson.gz`
  sidecar, `{asset-slug}.schema.json`, and `{asset-slug}.manifest.json`. Use
  `scripts/release_feature_model.py` helpers for IDs, hashes, sidecar
  serialization, validation, and manifest creation.
  Use `--metadata-lookup` (or `--pmtiles-feature-id-property feature_id`) with
  `scripts/vector_asset.py` to build lightweight metadata-lookup tiles
  containing only `feature_id` and to require the three identity columns in the
  canonical FGB.
- For the first upload of a release-oriented vector asset, ask the maintainer
  before finalizing artifacts:

```text
Which locales should be autogenerated by the agent during first upload?
Which metadata fields should be autogenerated by the agent during first upload?
```

  Do not infer these translation choices silently. If the maintainer chooses no
  autogenerated translations, record that decision in the PR or final response.
  If the maintainer chooses locales and fields, create and complete
  `{asset-slug}.metadata-translations.csv` rows for those locales and fields
  using `feature_id`, `field`, `locale`, `source_value_hash`, `value`,
  `review_state`, and `notes`. Prefer
  `scripts/feature_metadata_machine_translate.py` with `deep-translator` for
  the exact asset, locales, and fields recorded by the concierge instead of
  spending agent tokens on bulk translation; then run
  `scripts/feature_metadata_localization.py --all-locales` so generated
  `{asset-slug}.metadata.{locale}.ndjson.gz` sidecars are present and validated.
  The upload is incomplete until the requested translation CSV rows and
  generated sidecars are complete.
- Shared vector PMTiles display artifacts should use `scripts/vector_asset.py
  build` auto maxzoom. The helper generates the FGB, profiles it, then chooses
  maxzoom from source scale/resolution hints and measured geometry detail. It
  biases toward detailed presentation and caps at zoom 12 by default. Lower
  than zoom 8 requires source/profile evidence or a documented override.
- Add stable source hints such as `--source-resolution-meters`,
  `--source-scale-denominator`, `--pmtiles-detail-hint`, or
  `--pmtiles-maxzoom` when they reflect the upstream dataset. Manual
  `--maxzoom N` requires `--maxzoom-reason`.
- Use `scripts/vector_asset.py recommend-maxzoom --fgb ./asset.fgb` for a
  read-only recommendation against an existing local FGB before rebuilding or
  replacing PMTiles.
- Set `SHARED_DATASETS_WORKDIR` or pass `--work-dir` only when a different temp
  root is needed. Put one-off scratch work under `_scratch/{task}-{timestamp}/`
  inside the repo temp root, not directly under `/tmp`.
- Run repo-owned helpers through `uv run python`.
- When generated bytes depend on native geospatial CLIs, record tool versions
  and paths from the repo environment:

```bash
UV_CACHE_DIR=.uv-cache uv run ogrinfo --version
UV_CACHE_DIR=.uv-cache uv run ogr2ogr --version
UV_CACHE_DIR=.uv-cache uv run pmtiles version
UV_CACHE_DIR=.uv-cache uv run which ogr2ogr
UV_CACHE_DIR=.uv-cache uv run which pmtiles
```

Use a pinned repo-owned toolchain for reproducibility-sensitive publishes.

## Asset Docs And Catalog

For normal asset metadata changes:

1. Create or update `docs/assets/{asset-slug}.md`; this asset doc owns
   machine-readable catalog metadata and human-readable bucket README content.
2. Use the template structure from `templates/dataset_README.template.md` or
   `templates/dataset_README.minimal.template.md`.
3. Include owner, source, license/terms, citation, update cadence, canonical
   path, file table, update notes, and schema/property notes.
4. For fields/properties, list names, types, and short explanations when they
   can be derived. If meanings are unknown, list names/types and say definitions
   need source confirmation.
5. For vector and table assets, calculate and populate discovery/profile
   frontmatter from the canonical artifact after conversion:
   - `row_count`: feature or table row count in the canonical artifact.
   - `data_profile.field_count`: required whenever `data_profile` is present;
     number of published non-geometry columns.
   - `data_profile.identity_candidates`: stable source identifier fields checked
     for uniqueness. For each candidate, record `field`, `distinct_values`,
     `duplicate_value_count`, `duplicate_row_count`, `status`, and concise
     `notes`.
   - If no credible identifier field exists, set
     `identity_candidates: []` and add a short `data_profile.notes` explanation
     such as `No documented unique source ID candidate`.
   Compute duplicate counts over non-empty candidate values: duplicate values
   are distinct values appearing more than once, and duplicate rows are all rows
   carrying those repeated values.
   - `search_fields`: curator-selected high-value search/filter fields such as
     names, labels, site names, region names, or source grouping labels. Keep
     these separate from provider identity candidates.
   - `feature_identity`: required for release-oriented vector assets. Record
     `strategy` as one of `source_field`, `generated_sequence_source_fields`,
     or `generated_sequence_content_hash`. `source_field` requires
     `source_fields` naming the copied field. The generated-sequence strategies
     require `generated_id_type: monotonic_integer_string` and an
     `assignment_key`: one or two `source_fields` for
     `generated_sequence_source_fields`, or exactly
     `[geometry_hash, properties_hash]` for
     `generated_sequence_content_hash`.
   - `feature_metadata`: required when the asset publishes the release feature
     metadata contract. Record `storage: metadata_sidecar_v1`; the
     `feature_id`, `geometry_hash`, and `properties_hash` column names; and the
     `latest/` sidecar, schema, and manifest paths, each of which must also
     appear exactly once in `files`.
   During dataset creation, present the curator with the standard feature
   identity decision table from `publishing_concierge.py`: likely source-field
   `feature_id` candidates and likely search/filter fields with distinction,
   emptiness, domination, skew ratio, examples, and concerns. Do not assign
   generated `feature_id` values until the curator-approved assignment key is
   known.
6. For COG or Zarr assets, include raster metadata: CRS, resolution, dimensions,
   band semantics, dtype, nodata, units, scale/offset, and sampling where
   applicable.
7. Regenerate and check derived catalog outputs:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py check
```

Do not edit `catalog/shared-datasets-catalog.csv` directly for normal asset
metadata changes.

## Dataset Admission

New canonical assets and new ingestion pipelines need admission evidence in the
PR before reviewed publish promotion. A single-consumer asset is allowed, but
multi-project reuse is preferred. Every admitted asset must have a citable
source, confirmed license or terms, preferred citation, named steward, and clear
update expectations.

For new assets, include the Dataset Admission PR-template answers: intended
consumer(s), shared-datasets rationale, source/license/citation status, named
steward, update expectations, estimated published footprint, alternatives
considered, and deprecation or exit policy. If the proposed published footprint
is **>= 10 GB**, including canonical files, companion artifacts, and expected
release copies, include an explicit large-data exception explaining why project
storage, scratch storage, or direct upstream access is not the better answer.

Use the optional `admission` frontmatter block in
`docs/assets/{asset-slug}.md` when preserving the admission decision beside the
catalog metadata is useful. Existing assets are grandfathered unless a PR
changes their dataset contract.

## Catalog UI Cache Refresh

Every manual dataset edit or upload must refresh the live catalog UI cache after
the local catalog metadata is current and any data-plane writes are complete.
This applies even when only asset docs, README metadata, `latest/`, `releases/`,
or PMTiles changed.

Load `static-catalog-web-preview`, then rebuild the static catalog bundle:

```bash
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --out "$WORK_ROOT/catalog-web"
```

For normal dataset metadata changes, stage the rebuilt `catalog.json` under
`_scratch/pending-publishes/{asset-slug-or-catalog}/{proposal-id}/` and include
it in an explicit PR with a fenced publish plan. The approved publisher workflow
promotes it to the deployed path with a generation precondition only after that
PR merges. The deployed object should keep no-cache metadata; capture the
current destination generation and pass it to the workflow as
`destination_generation`:

```bash
DEST=gs://skytruth-shared-datasets-1/_catalog/web
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat "$DEST/catalog.json"
```

Local agents should stage the file under `_scratch/pending-publishes/` and
record it in the PR publish plan, passing the publish-plan `cache_control` field
with `no-cache, max-age=0, must-revalidate`. Do not use standalone workflow
dispatch or single-object fallback inputs to bypass a PR.

If the dataset publish replaced same-path PMTiles display artifacts, also set
no-cache metadata on the affected `latest/*.pmtiles` and
`releases/YYYY-MM-DD/*.pmtiles` objects. For corrective PMTiles-only rebuilds,
replace the PMTiles object under the matching canonical release date; do not
create a PMTiles-only dated release directory unless PMTiles is the asset's
canonical format. If static UI files changed, follow the full deployment and
verification flow in `static-catalog-web-preview`.

## Publication Workflow

### Fresh-Agent Minimal Upload Runbook

When a user asks to add, update, upload, or publish a dataset file with minimal
input, follow this ordered path unless the user explicitly asks to stop earlier:

1. Read `AGENTS.md`, load this skill, and load `gcp-shared-datasets`. If the
   change affects the catalog UI, also load `static-catalog-web-preview`.
2. Identify the supplied file or source URL, infer source identity, title,
   license/terms, citation, schema, geometry/raster properties, update cadence,
   and source version from filenames, embedded metadata, nearby docs, and source
   documentation. If source, license, citation, or safe canonical placement
   cannot be inferred, ask before remote writes.
3. Run `scripts/publishing_concierge.py` for new or under-specified assets, using
   `--write-draft-doc` when creating a new `docs/assets/{asset-slug}.md`.
   Resolve all `blocking_questions`. If the concierge cannot infer a canonical
   format because the supplied file is noncanonical, choose the approved output
   format from the data contents and standards, then rerun or document the plan;
   do not fall back to scratch-only staging.
4. Choose the lowercase asset slug, category, subcategory, canonical format,
   asset root, release layout, and companion formats using
   `catalog/categories.yaml`, `docs/standards/dataset-taxonomy.md`, and
   `docs/standards/asset-layout-and-formats.md`.
5. Build publishable artifacts outside the repo tree under the standard temp
   workspace. Convert source-only formats to approved canonical artifacts. For
   vector data, prefer `scripts/vector_asset.py build` so FGB and PMTiles are
   generated consistently and tool versions are recorded. For tabular source
   files, export a clean CSV only when it is a non-geometry table; otherwise
   create a vector artifact from the geometry or coordinate columns.
6. For first uploads of release-oriented vector assets, ask which locales and
   which metadata fields the maintainer wants autogenerated during first
   upload. If locales and fields are requested, complete
   `{asset-slug}.metadata-translations.csv` with those rows. Use
   `scripts/feature_metadata_machine_translate.py` for any asset field the
   concierge was asked to translate, then run the localization generator before
   staging publish candidates.
7. Create or update `docs/assets/{asset-slug}.md` with full frontmatter,
   source/license/citation, canonical paths, file table, update notes, and
   schema/properties. Populate `row_count` and `data_profile` from the
   canonical artifact, including checked identifier candidates or a short
   no-candidate note. Include the dataset admission evidence for a new asset
   slug. Do not edit `catalog/shared-datasets-catalog.csv` directly.
8. Run:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py check
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py export-readmes \
  --output-dir "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/readmes"
```

9. Rebuild catalog web outputs when the public catalog must reflect the change:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --out "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/catalog-web"
```

10. Pick a stable `proposal-id` before staging bytes. Prefer an existing issue or
   PR number; otherwise use the focused branch slug or a timestamped proposal ID
   that will be recorded in the PR.
11. Stage every remote publish candidate under
    `_scratch/pending-publishes/{asset-slug}/{proposal-id}/` with no-clobber
    uploads: canonical data files, companion PMTiles, exported bucket README,
    catalog web/cache-refresh objects, completed translation CSVs, generated
    localized metadata sidecars, and any reviewed release-index JSON that must
    be promoted. If the original source file is staged too, keep it out of
    the promotion plan unless its canonical `source/` or `archive/` destination
    passes standards validation. Document that scratch copy as source evidence.
    Record each staged source URI and generation.
12. Stat existing canonical destinations when replacing objects. Record the
    expected destination generation, or record that the destination must be
    absent for no-clobber promotion.
13. Create a focused branch, stage only related repo changes, commit, push, and
    open a PR requesting review from `jonaraphael`, unless `jonaraphael` is also
    the PR author and GitHub blocks the reviewer request. The PR body must
    include the asset slug, changed repo files, staged source URIs and
    generations, intended canonical destination URIs, destination-generation
    expectations, needed `content_type` or `cache_control` publish-plan fields,
    validation commands, and any unresolved assumptions. If using the GitHub CLI,
    pass `--reviewer jonaraphael` to `gh pr create` when `jonaraphael` is not the
    PR author; if using a GitHub connector, set `jonaraphael` as requested
    reviewer when allowed. If the authenticated author is already `jonaraphael`,
    do not spend a known-failing reviewer request; record the restricted
    self-acceptance note in the PR body directly. If GitHub refuses because
    `jonaraphael` authored the PR, record that note in the PR body. Include a fenced
    `shared-datasets-publish-plan` JSON block so merge or restricted
    self-acceptance dispatch can trigger promotion:

````markdown
```shared-datasets-publish-plan
{
  "asset_slug": "example-asset",
  "proposal_id": "pr-123",
  "promotions": [
    {
      "source_uri": "gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example-asset/pr-123/example-asset.fgb",
      "source_generation": "123456789",
      "destination_uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/latest/example-asset.fgb",
      "destination_generation": "",
      "content_type": "application/octet-stream",
      "cache_control": ""
    }
  ]
}
```
````

14. Do not promote canonical objects from the local terminal. After a same-repo
    PR containing the publish-plan block is merged to `main`, the GitHub
    `Approved dataset mutation` workflow verifies `jonaraphael` acceptance and
    promotes the listed staged objects under the `shared-datasets-production`
    environment. If GitHub blocks self-review because `jonaraphael` authored the
    PR, the merged PR is treated as restricted self-acceptance. Promotion still
    requires that explicit PR; do not use standalone workflow dispatch or
    single-object fallback inputs to bypass the PR.
    If the asset doc changes `access_tier`, rely on the protected
    `catalog-web-deploy` and `pmtiles-cdn-sync` workflows after merge to update
    `_catalog/web`, PMTiles CDN routes, and public managed-folder IAM. Do not
    run local production Terraform apply for access-tier changes.
15. Verify promoted remote objects, metadata, catalog UI freshness, and release
    index behavior where applicable. Report changed remote paths, generations,
    alert state, and any residual uncertainty in the PR or final response.

Scratch-only staging of the supplied source file satisfies this runbook only
when the human explicitly asks to stage a file for later manual review and does
not ask to add, update, upload, or publish a dataset.

### Fresh-Agent Existing Dataset Version Runbook

When a user asks to upload a new version of an existing dataset, follow this
ordered path unless the user explicitly asks to stop earlier:

1. Read `AGENTS.md`, load this skill, load `gcp-shared-datasets`, and identify
   the existing `asset-slug` from the user request, file names, source metadata,
   or nearby paths. If more than one asset could match, ask before staging bytes.
2. Open `docs/assets/{asset-slug}.md`, the generated catalog row, and the
   relevant existing bucket prefix. Preserve the existing slug, category,
   subcategory, canonical path, and approved formats unless the user explicitly
   requests a reviewed asset-contract change.
3. Determine whether a scheduled ingestion job owns this asset. If the asset is
   cron-owned, do not bypass the job with a manual upload unless the user
   explicitly asks for a corrective manual publish; otherwise use
   `deploy-scheduled-ingestion`.
4. Inspect current remote state with read-only commands: current `latest/`
   generations, existing `releases/YYYY-MM-DD/` dates, run records, and
   `_catalog/releases/{asset-slug}.json` when present. Record whether the new
   version should create a dated release, replace only `latest/`, or repair
   metadata for an existing release.
5. Compare the new source version with the existing asset contract: source name
   and URL, license/terms, citation, schema/properties, CRS, geometry or raster
   characteristics, row/feature counts, data-profile uniqueness checks, bounds,
   available formats, and consumer impact. Ask before incompatible schema
   changes, slug/path changes, new
   formats, or unclear license/citation changes.
6. Build the replacement artifacts outside the repo tree under the standard temp
   workspace. Match the catalog-listed formats. If intentionally leaving a
   companion format unchanged, document that and use `--allow-stale-format` in
   dry-run validation.
7. Validate the prepared release with `publish-release --dry-run` whenever the
   asset uses a single-object canonical format supported by the command:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py publish-release \
  --asset-slug example-asset \
  --release-date YYYY-MM-DD \
  --publish-dir "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/vector-assets/example-asset/publish" \
  --source-version "source version or URL" \
  --dry-run
```

   Review the plan for release URIs, `latest/` destination generations, stale
   formats, metadata uploads, and checks. Do not run non-dry-run
   `publish-release` from a local human or agent terminal.
8. Update `docs/assets/{asset-slug}.md` only for durable contract changes:
   source/license/citation changes, schema notes, file table changes, format
   changes, access/cadence changes, or update notes that consumers need. Do not
   edit `catalog/shared-datasets-catalog.csv` directly, and do not churn tracked
   docs solely to advance cron freshness fields that belong in the bucket
   release index.
9. Regenerate and check the doc set:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py check
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py export-readmes \
  --output-dir "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/readmes"
```

10. Rebuild catalog web outputs when the public catalog must reflect the new
    version or changed metadata:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --out "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/catalog-web"
```

11. Pick a stable `proposal-id`. Prefer an existing issue or PR number;
    otherwise use the focused branch slug or a timestamped proposal ID that will
    be recorded in the PR.
12. Stage every reviewed remote publish candidate under
    `_scratch/pending-publishes/{asset-slug}/{proposal-id}/` with no-clobber
    uploads. This usually includes the new data artifacts, companion PMTiles,
    exported bucket README when changed, catalog web/cache-refresh objects when
    changed, and any reviewed run-record or release-index repair JSON that must
    be promoted after the release objects exist. Record each staged source URI
    and generation.
13. Stat each intended canonical destination. For new dated release and run
    objects, record that the destination must be absent. For `latest/`, README,
    catalog web, or release-index replacements, record the current destination
    generation that the approved workflow must use.
14. Create a focused branch, stage only related repo changes, commit, push, and
    open a PR requesting review from `jonaraphael`, unless `jonaraphael` is also
    the PR author and GitHub blocks the reviewer request. The PR body must
    include the asset slug, release date/source version, changed repo files,
    staged source URIs and generations, intended canonical destination URIs,
    destination-generation expectations, whether `publish-release --dry-run`
    passed, any stale companion formats, validation commands, and consumer
    impact. If using the GitHub CLI, pass `--reviewer jonaraphael` to
    `gh pr create` when `jonaraphael` is not the PR author; if using a GitHub
    connector, set `jonaraphael` as requested reviewer when allowed. If the
    authenticated author is already `jonaraphael`, do not spend a known-failing
    reviewer request; record the restricted self-acceptance note in the PR body
    directly. If GitHub refuses because `jonaraphael` authored the PR, record
    that note in the PR body. Include a fenced `shared-datasets-publish-plan` JSON block whose
    `promotions` are ordered exactly as they should be copied.
15. Do not promote canonical objects from the local terminal. After a same-repo
    PR containing the publish-plan block is merged to `main`, the GitHub
    `Approved dataset mutation` workflow verifies `jonaraphael` acceptance and
    promotes the listed staged objects under the `shared-datasets-production`
    environment. If GitHub blocks self-review because `jonaraphael` authored the
    PR, the merged PR is treated as restricted self-acceptance. Promotion still
    requires that explicit PR; do not use standalone workflow dispatch or
    single-object fallback inputs to bypass the PR. Order the publish plan so
    dated release objects come before `latest/` replacements, and run records and
    `_catalog/releases/{asset-slug}.json` come only after the object metadata
    they describe has been promoted.
16. Verify the new version: release objects exist, `latest/` points to or
    contains the intended bytes, generations match the approved operations, the
    release index reports the newest successful release and latest run, catalog
    web freshness is correct, upload/schema alert state is reported, and any
    residual uncertainty is documented in the PR or final response.

### Approved Mutation Retry Recovery

Use this only after a reviewed publish PR has merged and the GitHub `Approved
dataset mutation` workflow failed before completing all publish-plan
promotions. It is a recovery path for the same reviewed object set, not a way to
approve new data after merge.

1. Inspect the failed workflow enough to identify the failed step and last
   attempted promotion. Avoid dumping full logs unless the short failure
   snippet is insufficient.
2. Extract the current PR publish plan to a file. `reviewed_dataset_plan.py
   extract --output ...` prints only a compact summary by default; use
   `--print-plan` only when the full JSON is truly needed on stdout.
3. Stat every planned staged source and every planned destination. Confirm all
   staged sources still exist at their approved source generations.
4. For each destination that now exists, compare CRC32C with the staged source.
   If any destination differs, stop for human review. Do not refresh generation
   preconditions over a content mismatch.
5. Refresh the publish plan so existing destinations use their current
   generations and still-missing destinations keep an empty
   `destination_generation`.
6. Reassess schema waivers. If the first run already updated the schema
   snapshot, `check-schema-compatibility` may now report no blocked changes and
   reject a supplied waiver. Remove waivers in the retry plan only in that
   condition; otherwise keep the waiver on schema-breaking replacements.
7. Edit the merged PR body only to update generation preconditions, remove
   stale waivers, and add a retry note describing the partial run. Do not add
   new source URIs, destination URIs, delete operations, catalog semantics, or
   data bytes after merge.
8. Dispatch `Approved dataset mutation` from `main` with the reviewed PR number.
   Continue monitoring until promotion, finalization, release-index rebuild,
   upload summary, scratch cleanup, and downstream catalog/localization workflows
   either succeed or report a new specific failure.

### Fresh-Agent Reviewed Deletion Runbook

When a user asks to delete canonical dataset objects, follow this ordered path
unless the user explicitly asks to stop earlier:

1. Confirm the request is explicitly destructive. Do not infer canonical
   deletion from vague cleanup, replacement, or refresh language.
2. Load `gcp-shared-datasets`, inspect the relevant asset docs/catalog row, and
   prefer non-destructive alternatives first: catalog deprecation, asset status
   changes, removing catalog visibility, or publishing a corrected replacement.
3. Enumerate exact object URIs. Prefix deletes, wildcard deletes, and
   generation-less deletes are invalid.
4. Stat every target object and record the current generation:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat \
  gs://skytruth-shared-datasets-1/path/to/object
```

5. Check references before deletion: `docs/assets/{asset-slug}.md`,
   `catalog/shared-datasets-catalog.csv`, bucket README content,
   `_catalog/releases/{asset-slug}.json`, run records, catalog web output, and
   known consumer paths. Update repo docs/catalog outputs first when references
   must change.
6. If deletion is part of a replacement, stage the replacement publish plan in
   the same PR and order the publish plan before the delete plan. The approved
   workflow promotes listed publish objects before it deletes listed objects.
7. Create a focused branch, stage only related repo changes, commit, push, and
   open a PR requesting review from `jonaraphael`, unless `jonaraphael` is also
   the PR author and GitHub blocks the reviewer request. The PR body must include
   exact target URIs, current generations, deletion rationale, consumer impact,
   replacement/deprecation state, validation commands, and a fenced
   `shared-datasets-delete-plan` JSON block:

````markdown
```shared-datasets-delete-plan
{
  "asset_slug": "example-asset",
  "proposal_id": "pr-123",
  "deletions": [
    {
      "uri": "gs://skytruth-shared-datasets-1/100-geographic-reference/110-boundaries/example-asset/releases/2026-05-08/example-asset.fgb",
      "generation": "123456789",
      "reason": "Incorrect duplicate release superseded by approved replacement."
    }
  ]
}
```
````

8. Do not delete canonical objects from the local terminal. After a same-repo PR
   containing the delete-plan block is merged to `main`, the GitHub `Approved
   dataset mutation` workflow verifies `jonaraphael` acceptance and deletes the
   listed objects under the publisher identity using exact generation
   preconditions. If GitHub blocks self-review because `jonaraphael` authored
   the PR, the merged PR is treated as restricted self-acceptance. Deletion
   still requires that explicit PR; do not use standalone workflow dispatch or
   single-object fallback inputs to bypass the PR.
9. Verify the deletion: the live object is absent, no unintended objects were
   touched, catalog/release-index references no longer point at deleted paths,
   delete alerts/logs show the publisher identity, and any residual uncertainty
   is documented in the PR or final response.

### Standard Manual Asset Flow

For a new manual asset:

1. Run the publishing concierge unless all slug, taxonomy, format, doc, and
   target path decisions are already explicit and verified.
2. Complete discovery, classification, slug selection, and local artifact build.
   Unsupported source formats must be converted to approved canonical artifacts;
   scratch staging the source file alone is not a completed manual asset flow.
3. Update the asset doc and regenerate catalog outputs.
4. Load `gcp-shared-datasets`.
5. Validate target paths and inspect existing remote objects if replacing.
6. Stage manual publish bytes under
   `_scratch/pending-publishes/{asset-slug}/{proposal-id}/` with no-clobber
   behavior.
7. Promote reviewed canonical objects only through an explicit PR with a fenced
   publish plan and the GitHub `Approved dataset mutation` workflow after merge.
   The workflow runs under the `shared-datasets-production` environment and
   approved publisher identity. Do not mutate canonical paths directly from a
   local human or agent terminal.
8. Use `releases/YYYY-MM-DD/` when the asset is cron-updated, multi-project
   critical, difficult to recreate, or needs reproducible snapshots.
9. Verify remote paths and object metadata.
10. Run dataset upload/schema alert helpers when applicable. Dataset upload
   announcements are operational notifications, not Git commit gates. Do not
   block requested commits to send or verify an announcement, and do not rerun
   Slack upload announcements for corrective renames, cache refreshes,
   README/catalog metadata repairs, or same-release republishing unless the
   human explicitly asks for one. Report whether the announcement was sent,
   skipped, or uncertain in the final response.
11. Refresh the catalog UI cache using the steps above.

For an update to an existing versioned asset, prefer `publish-release` when the
local publish directory is ready for an existing catalog asset:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py publish-release \
  --asset-slug example-asset \
  --release-date YYYY-MM-DD \
  --publish-dir "$TMPDIR/shared-datasets-1/vector-assets/example-asset/publish" \
  --source-version "source version or URL" \
  --dry-run
```

Review the dry-run plan before running without `--dry-run`, and run the
non-dry-run publish only under the approved publisher identity. `publish-release`
validates local files, rejects existing release objects, captures current
`latest/` generations, uploads `releases/YYYY-MM-DD/` first, updates `latest/`,
writes a run record, and emits upload/schema alerts. If the operation is a
corrective rename or follow-up repair for a release whose catalog-update commit
already exists, do not run a second announceable publish path unless the user
explicitly asks for another Slack notification.

If intentionally publishing only a subset of catalog-listed formats, name each
unchanged companion explicitly with `--allow-stale-format`.

## Alerts And Schema Checks

After a meaningful manual dataset upload or update, run the dataset upload
announcement before creating the repo commit that updates the catalog metadata:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/dataset_alerts.py upload-summary \
  --asset-slug example-asset \
  --changed-path gs://skytruth-shared-datasets-1/path/to/object.fgb \
  --dataset-path ./example-asset.fgb
```

For canonical vector/table assets, run schema validation before publish:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/dataset_alerts.py check-schema-compatibility \
  --asset-slug example-asset \
  --dataset-path ./example-asset.fgb
```

Schema validation reports added, removed, renamed, reordered, and type-changed
fields so reviewers can confirm the new release schema is intentional. After a
successful reviewed publish, `check-schema` remains available for diagnostic
warnings and schema snapshot updates.

Alert once per meaningful release. If a Slack upload alert already went out for
the dataset release, or if the catalog-update commit for that release already
exists, skip additional Slack alerts for corrective renames, cache-control fixes,
static catalog refreshes, README wording, slug/title repairs, or other
non-material follow-up work. Still verify the changed objects, schema snapshots,
and catalog state locally/remotely; document the skipped alert explicitly in the
final response.

## Completion Criteria

Report:

- Asset slug, category/subcategory, canonical format, and source/version.
- Concierge plan path or command run, or why it was intentionally skipped.
- Local files generated and validation commands run.
- Catalog/doc generation commands run.
- Catalog UI cache refresh commands run, including `catalog.json` generation,
  generation-preconditioned upload, cache-control metadata update, and PMTiles
  cache-control updates when PMTiles were replaced.
- Remote paths changed, including `latest/`, `releases/`, `runs/`, and README
  paths.
- Object generations for replacements when available.
- Dataset upload/schema alert commands run or intentionally skipped. Do not
  block requested commits on dataset upload announcements; report whether the
  announcement was sent, skipped, or uncertain.
- Any metadata, source, license, schema, or classification uncertainty.
