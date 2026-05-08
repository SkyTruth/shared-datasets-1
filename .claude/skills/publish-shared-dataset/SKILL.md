---
name: publish-shared-dataset
description: Use before manually adding, updating, publishing, or documenting a shared dataset asset, including under-specified uploads, taxonomy classification, local publish artifact builds, asset docs, catalog regeneration, safe GCS publication, and dataset upload/schema alerts.
---

# Publish Shared Dataset

Use this skill for manual dataset additions and updates in `shared-datasets-1`.
It covers the dataset-facing workflow. Load `gcp-shared-datasets` before any
remote GCS inspection or write.

## Required Context

Read these before choosing names, paths, formats, or remote writes:

- `AGENTS.md`
- `catalog/categories.yaml`
- `docs/standards/dataset-taxonomy.md`
- `docs/standards/asset-layout-and-formats.md`
- `.claude/skills/gcp-shared-datasets/SKILL.md` before any GCS operation
- `.claude/skills/static-catalog-web-preview/SKILL.md` before refreshing the
  live catalog UI cache after dataset edits or uploads
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

## Publishing Concierge

For a new manual asset, an under-specified upload, or any intake where the
slug/taxonomy/format/doc path is not already settled, run the local concierge
before building artifacts or writing asset docs:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/publishing_concierge.py ./source.shp \
  --asset-slug example-asset \
  --title "Example Asset" \
  --category 100-geographic-reference \
  --subcategory 110-boundaries \
  --source-name "Example source v1" \
  --license "Example terms" \
  --release-date YYYY-MM-DD
```

Use `--write-draft-doc` only when you want the concierge to create a local
`docs/assets/{asset-slug}.md` draft. Review the JSON plan and resolve every
`blocking_questions` item before any remote write. The concierge is a planner:
it never writes to Cloud Storage, and it does not replace `gcs_asset.py`,
`publish-release`, catalog checks, or the GCS safety rules.
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
- For vector assets, prefer:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/vector_asset.py build ./source.fgb \
  --asset-slug example-asset \
  --layer-name example_asset \
  --title "Example Asset" \
  --description "Example vector tiles"
```

- Local downloads, generated artifacts, and scratch files must follow
  `docs/standards/local-temp-workspaces.md`.
- The default vector work directory is
  `${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/vector-assets/{asset-slug}/`,
  with upload candidates under `publish/` and intermediates under `build/`.
- PMTiles display artifacts must preserve compact feature properties for the
  catalog inspector. Do not use Tippecanoe `--exclude-all`; the repo vector
  helper rejects it, and manual multi-layer builds must verify decoded feature
  properties before publication.
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
UV_CACHE_DIR=.uv-cache uv run tippecanoe --version
UV_CACHE_DIR=.uv-cache uv run pmtiles version
UV_CACHE_DIR=.uv-cache uv run which ogr2ogr
UV_CACHE_DIR=.uv-cache uv run which tippecanoe
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
5. For COG or Zarr assets, include raster metadata: CRS, resolution, dimensions,
   band semantics, dtype, nodata, units, scale/offset, and sampling where
   applicable.
6. Regenerate and check derived catalog outputs:

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
`_scratch/pending-publishes/{asset-slug-or-catalog}/{proposal-id}/` and promote
it to the deployed path with a generation precondition through the approved
publisher workflow. The deployed object should keep no-cache metadata; capture
the current destination generation and pass it to the workflow as
`destination_generation`:

```bash
DEST=gs://skytruth-shared-datasets-1/_catalog/web
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat "$DEST/catalog.json"
```

Local agents should stage the file under `_scratch/pending-publishes/` and use
the GitHub workflow to promote it, passing the workflow `cache_control` input
with `no-cache, max-age=0, must-revalidate`.

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
   Resolve all `blocking_questions`.
4. Choose the lowercase asset slug, category, subcategory, canonical format,
   asset root, release layout, and companion formats using
   `catalog/categories.yaml`, `docs/standards/dataset-taxonomy.md`, and
   `docs/standards/asset-layout-and-formats.md`.
5. Build publishable artifacts outside the repo tree under the standard temp
   workspace. For vector data, prefer `scripts/vector_asset.py build` so FGB and
   PMTiles are generated consistently and tool versions are recorded.
6. Create or update `docs/assets/{asset-slug}.md` with full frontmatter,
   source/license/citation, canonical paths, file table, update notes, and
   schema/properties. Do not edit `catalog/shared-datasets-catalog.csv`
   directly.
7. Run:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py check
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py export-readmes \
  --output-dir "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/readmes"
```

8. Rebuild catalog web outputs when the public catalog must reflect the change:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --out "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/catalog-web"
```

9. Pick a stable `proposal-id` before staging bytes. Prefer an existing issue or
   PR number; otherwise use the focused branch slug or a timestamped proposal ID
   that will be recorded in the PR.
10. Stage every remote publish candidate under
    `_scratch/pending-publishes/{asset-slug}/{proposal-id}/` with no-clobber
    uploads: canonical data files, companion PMTiles, exported bucket README,
    catalog web/cache-refresh objects, and any reviewed release-index JSON that
    must be promoted. Record each staged source URI and generation.
11. Stat existing canonical destinations when replacing objects. Record the
    expected destination generation, or record that the destination must be
    absent for no-clobber promotion.
12. Create a focused branch, stage only related repo changes, commit, push, and
    open a PR requesting review from `jonaraphael`. The PR body must include the
    asset slug, changed repo files, staged source URIs and generations, intended
    canonical destination URIs, destination-generation expectations, needed
    `content_type` or `cache_control` workflow inputs, validation commands, and
    any unresolved assumptions. If using the GitHub CLI, pass
    `--reviewer jonaraphael` to `gh pr create`; if using a GitHub connector,
    set `jonaraphael` as the requested reviewer. Include a fenced
    `shared-datasets-publish-plan` JSON block so approval can trigger automatic
    promotion:

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

13. Do not promote canonical objects from the local terminal. When `jonaraphael`
    approves a same-repo PR that contains the publish-plan block, the GitHub
    `Approved dataset mutation` workflow promotes the listed staged objects under
    the `shared-datasets-production` environment. Manual workflow dispatch is
    restricted to `jonaraphael`; use it only as a fallback for off-repo PRs,
    failed automatic promotion, or explicit human direction.
14. Verify promoted remote objects, metadata, catalog UI freshness, and release
    index behavior where applicable. Report changed remote paths, generations,
    alert state, and any residual uncertainty in the PR or final response.

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
   characteristics, row/feature counts, bounds, available formats, and consumer
   impact. Ask before incompatible schema changes, slug/path changes, new
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
    open a PR requesting review from `jonaraphael`. The PR body must include the
    asset slug, release date/source version, changed repo files, staged source
    URIs and generations, intended canonical destination URIs,
    destination-generation expectations, whether `publish-release --dry-run`
    passed, any stale companion formats, validation commands, and consumer
    impact. If using the GitHub CLI, pass `--reviewer jonaraphael` to
    `gh pr create`; if using a GitHub connector, set `jonaraphael` as the
    requested reviewer. Include a fenced `shared-datasets-publish-plan` JSON
    block whose `promotions` are ordered exactly as they should be copied.
15. Do not promote canonical objects from the local terminal. When `jonaraphael`
    approves a same-repo PR that contains the publish-plan block, the GitHub
    `Approved dataset mutation` workflow promotes the listed staged objects under
    the `shared-datasets-production` environment. Order the publish plan so dated
    release objects come before `latest/` replacements, and run records and
    `_catalog/releases/{asset-slug}.json` come only after the object metadata
    they describe has been promoted. Manual workflow dispatch is restricted to
    `jonaraphael`; use it only as a fallback for off-repo PRs, failed automatic
    promotion, or explicit human direction.
16. Verify the new version: release objects exist, `latest/` points to or
    contains the intended bytes, generations match the approved operations, the
    release index reports the newest successful release and latest run, catalog
    web freshness is correct, upload/schema alert state is reported, and any
    residual uncertainty is documented in the PR or final response.

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
   open a PR requesting review from `jonaraphael`. The PR body must include
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

8. Do not delete canonical objects from the local terminal. When `jonaraphael`
   approves a same-repo PR that contains the delete-plan block, the GitHub
   `Approved dataset mutation` workflow deletes the listed objects under the
   publisher identity using exact generation preconditions.
9. Verify the deletion: the live object is absent, no unintended objects were
   touched, catalog/release-index references no longer point at deleted paths,
   delete alerts/logs show the publisher identity, and any residual uncertainty
   is documented in the PR or final response.

### Standard Manual Asset Flow

For a new manual asset:

1. Run the publishing concierge unless all slug, taxonomy, format, doc, and
   target path decisions are already explicit and verified.
2. Complete discovery, classification, slug selection, and local artifact build.
3. Update the asset doc and regenerate catalog outputs.
4. Load `gcp-shared-datasets`.
5. Validate target paths and inspect existing remote objects if replacing.
6. Stage manual publish bytes under
   `_scratch/pending-publishes/{asset-slug}/{proposal-id}/` with no-clobber
   behavior.
7. Promote reviewed canonical objects through the GitHub `Approved dataset
   mutation` workflow, which runs under the `shared-datasets-production`
   environment and approved publisher identity. Do not mutate canonical paths
   directly from a local human or agent terminal.
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

For canonical vector/table assets, compare and update the schema snapshot:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/dataset_alerts.py check-schema \
  --asset-slug example-asset \
  --dataset-path ./example-asset.fgb
```

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
