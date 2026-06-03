---
name: static-catalog-web-preview
description: Use when building, updating, QAing, or deploying the static shared-datasets catalog web preview under _catalog/web, especially when catalog.json, docs rendering, PMTiles previews, dense point tiles, browser caching, or GCS cache metadata are involved.
---

# Static Catalog Web Preview

## Overview

Use this skill for work on the zero-backend shared-datasets catalog web preview
deployed at:

```text
gs://skytruth-shared-datasets-1/_catalog/web/
```

The catalog is a static data product generated from repo-controlled metadata,
served from GCS, and used to preview PMTiles display artifacts in the browser.

## When To Use

Use this skill when:

- Building or changing `scripts/catalog_site.py`.
- Editing files under `web/catalog/`.
- Rebuilding or deploying `_catalog/web/` objects.
- Debugging stale live catalog data, stale docs, stale PMTiles, or browser cache behavior.
- Adding or changing PMTiles previews, basemaps, feature inspection, or multi-dataset map rendering.
- Publishing same-path PMTiles replacements used by the catalog preview.
- Verifying dense point layers where low-zoom feature retention matters.

## When Not To Use

Do not use this skill for:

- General dataset uploads with no catalog web impact; use `publish-shared-dataset`.
- Cloud Run or Scheduler deployment unrelated to catalog PMTiles behavior; use `deploy-scheduled-ingestion`.
- Read-only compliance walkthroughs; use `shared-datasets-compliance-audit`.
- Pure docs typo fixes that do not affect generated catalog output or deployed web behavior.

Negative examples:

- Updating a dataset README sentence without changing catalog fields or web deployment does not need this skill.
- Uploading a canonical FGB for analysis only, with no PMTiles or `_catalog/web/` change, does not need this skill.

## Workflow

1. Load required context:
   - Read `AGENTS.md`.
   - Read `.claude/skills/publish-shared-dataset/SKILL.md` when manually publishing or updating dataset assets.
   - Read `.claude/skills/gcp-shared-datasets/SKILL.md` before any GCS object write.
   - Read `.claude/skills/protected-terraform-apply/SKILL.md` before any
     catalog access-tier, PMTiles CDN, Cloud Run viewer, CORS, IAM, or other
     production Terraform mutation.
   - Read `docs/catalog-web-preview.md`.
   - Inspect `catalog/shared-datasets-catalog.csv`, relevant `docs/assets/*.md`, `scripts/catalog_site.py`, and `web/catalog/*`.

2. Keep generated metadata authoritative:
   - Update asset frontmatter first when changing asset docs.
   - Run:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py check
```

   - Confirm `catalog/shared-datasets-catalog.csv`, `docs/assets/index.md`, and affected asset docs agree.

3. Build and inspect the static bundle:

```bash
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --out "$WORK_ROOT/catalog-web"
```

   - Inspect generated `catalog.json` with `jq`.
   - Verify affected assets have expected `available_formats`, `has_pmtiles`, `has_geojson`, `citation`, `public_url`, `pmtiles_url`, `docs_url`, and `versions`.
   - For new or refreshed vector/table assets, also verify `row_count` and
     `data_profile` are emitted for the metadata cards, including
     `field_count`, checked identity candidates, or a concise no-candidate note.

4. PMTiles fidelity checks:
   - Shared vector PMTiles display artifacts should use
     `scripts/vector_asset.py build` auto maxzoom. The helper generates the FGB,
     profiles it, then chooses maxzoom from source scale/resolution hints and
     measured geometry detail. It biases toward detailed presentation and caps
     at zoom 12 by default. Lower than zoom 8 requires source/profile evidence
     or a documented override.
   - The standard `scripts/vector_asset.py build` Tippecanoe path adds
     `--no-feature-limit`, `--no-tile-size-limit`, and `--drop-rate=1` by
     default so low-zoom tiles retain published point features. Do not override
     those defaults for point assets unless the human explicitly accepts sparse
     low-zoom display tiles.
   - PMTiles display artifacts must preserve the feature properties needed by
     the catalog inspector. For release-oriented metadata lookup assets, the
     required properties are stable `feature_id` and `ext_id`; full
     attributes and display labels are served from the metadata sidecar/API. Do
     not use Tippecanoe
     `--exclude-all`; `scripts/vector_asset.py` rejects it, and manual
     multi-layer Tippecanoe builds must use the same standard.
   - For dense point layers, confirm the effective command includes the
     retention flags:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/vector_asset.py build ./source.fgb \
  --asset-slug example-points
```

   - Decode low-zoom tiles before and after when possible:

```bash
tippecanoe-decode ./example.pmtiles 0 0 0 | jq '[.features[].features | length] | add'
```

   - Decode a sample tile and confirm feature properties are present before
     publishing:

```bash
tippecanoe-decode ./example.pmtiles 0 0 0 \
  | jq '[.features[].features[].properties | keys[]] | unique'
```

   - Record feature counts, resolved maxzoom evidence from
     `pmtiles-profile.json`, tool paths, tool versions, output sizes, and
     SHA-256 hashes in asset docs/catalog notes when manually publishing
     PMTiles.
   - For read-only acceptance checks against existing local FGBs, run
     `UV_CACHE_DIR=.uv-cache uv run python scripts/vector_asset.py
     recommend-maxzoom --fgb ./asset.fgb` and review the recommendation
     evidence before rebuilding PMTiles.

5. Local browser QA:
   - Serve the generated bundle from the output directory:

```bash
python3 -m http.server 4173 --bind 127.0.0.1 \
  --directory "${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/catalog-web"
```

   - Check search, filters, selected detail view, docs modal, copy buttons, map preview, feature inspector, version selector, and mobile/narrow layout.
   - For PMTiles regressions, test at zoomed-out levels where feature dropping is most visible.

6. Safe GCS deployment:
   - Stage manual deployment files under `_scratch/pending-publishes/catalog-web/{proposal-id}/`.
   - Promote approved objects through the GitHub `Approved dataset mutation` workflow.
   - For repo-generated catalog web changes that will trigger
     `.github/workflows/catalog-web-deploy.yml` after the PR merges, treat that
     workflow as the PR-backed promotion path and do not also include
     `_catalog/web/catalog.json` in a `shared-datasets-publish-plan`; the
     duplicate promotion can race the automatic deploy.
   - Stat existing objects before replacement and use the returned generation as the publish-plan destination precondition.
   - Pass the publish-plan `cache_control` field for `catalog.json`, PMTiles, and other cache-sensitive replacements when no-cache metadata is required.
   - Use no-clobber promotion for new objects.
   - Do not use unsafe overwrites.

   Access-tier changes that affect PMTiles CDN routes or public managed-folder
   IAM must land by reviewed PR and protected `main` workflows. Do not apply
   the Terraform path locally.

7. Cache control and live-site freshness:
   - When promoting web shell/runtime objects, pass the publish-plan
     `cache_control` field with `no-cache, max-age=0, must-revalidate`.
   - After same-path PMTiles replacement, make sure the approved workflow
     promotion also sets no-cache metadata on the replaced PMTiles objects.
   - Corrective PMTiles-only rebuilds should replace the PMTiles object under the matching canonical release date, not create a new PMTiles-only dated release directory unless PMTiles is the canonical format.

   - The frontend should also cache-bust `catalog.json`, docs Markdown, and PMTiles URLs.
   - Do not assume `fetch(..., { cache: "no-store" })` alone bypasses all GCS/browser stale-content cases.

8. Public verification:
   - Verify public headers through the `tiles.skytruth.org` route:

```bash
curl -I -sS 'https://tiles.skytruth.org/_catalog/web/catalog.json?verify=<generation>'
curl -I -sS 'https://tiles.skytruth.org/pmtiles/public/asset.pmtiles?v=<sha256>'
```

   - Confirm:
     - HTTP 200.
     - `cache-control: no-cache, max-age=0, must-revalidate`.
     - `accept-ranges: bytes`.
     - CORS exposes `Content-Length`, `Content-Range`, `ETag`, and `Range`.
     - `x-goog-generation` matches the uploaded object.

9. Completion notes:
   - Report changed remote paths and generations.
   - Report PMTiles hashes, sizes, and low-zoom decoded counts for dense point fixes.
   - Report tests and browser QA performed.
   - If a live tab still shows stale data, instruct one hard reload only after verifying deployed cache headers and cache-busting code.
