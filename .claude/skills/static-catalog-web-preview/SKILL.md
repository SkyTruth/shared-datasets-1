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
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --out /tmp/shared-datasets-1/catalog-web
```

   - Inspect generated `catalog.json` with `jq`.
   - Verify affected assets have expected `available_formats`, `has_pmtiles`, `has_geojson`, `public_url`, `pmtiles_url`, `docs_url`, and `versions`.

4. PMTiles fidelity checks:
   - The standard `scripts/vector_asset.py build` Tippecanoe path adds
     `--no-feature-limit`, `--no-tile-size-limit`, and `--drop-rate=1` by
     default so low-zoom tiles retain published point features. Do not override
     those defaults for point assets unless the human explicitly accepts sparse
     low-zoom display tiles.
   - PMTiles display artifacts must preserve at least one useful feature
     property for the catalog inspector. Do not use Tippecanoe `--exclude-all`;
     `scripts/vector_asset.py` rejects it, and manual multi-layer Tippecanoe
     builds must use the same standard.
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

   - Record feature counts, tool paths, tool versions, output sizes, and SHA-256 hashes in asset docs/catalog notes when manually publishing PMTiles.

5. Local browser QA:
   - Serve the generated bundle from the output directory:

```bash
python3 -m http.server 4173 --bind 127.0.0.1 --directory /tmp/shared-datasets-1/catalog-web
```

   - Check search, filters, selected detail view, docs modal, copy buttons, map preview, feature inspector, version selector, and mobile/narrow layout.
   - For PMTiles regressions, test at zoomed-out levels where feature dropping is most visible.

6. Safe GCS deployment:
   - Use `scripts/gcs_asset.py` for object writes.
   - Stat existing objects before replacement.
   - Upload replacements with `--replace-generation`.
   - Use no-clobber uploads for new objects.
   - Do not use unsafe overwrites.

7. Cache control and live-site freshness:
   - After deploying web shell/runtime objects, set no-cache metadata:

```bash
gcloud storage objects update \
  --cache-control='no-cache, max-age=0, must-revalidate' \
  gs://skytruth-shared-datasets-1/_catalog/web/index.html \
  gs://skytruth-shared-datasets-1/_catalog/web/styles.css \
  gs://skytruth-shared-datasets-1/_catalog/web/app.js \
  gs://skytruth-shared-datasets-1/_catalog/web/map-preview.js \
  gs://skytruth-shared-datasets-1/_catalog/web/catalog.json
```

   - After same-path PMTiles replacement, set no-cache metadata on the replaced PMTiles objects:

```bash
gcloud storage objects update \
  --cache-control='no-cache, max-age=0, must-revalidate' \
  gs://skytruth-shared-datasets-1/path/to/asset/latest/asset.pmtiles \
  gs://skytruth-shared-datasets-1/path/to/asset/releases/YYYY-MM-DD/asset.pmtiles
```

   - The frontend should also cache-bust `catalog.json`, docs Markdown, and PMTiles URLs.
   - Do not assume `fetch(..., { cache: "no-store" })` alone bypasses all GCS/browser stale-content cases.

8. Public verification:
   - Verify public headers:

```bash
curl -I -sS 'https://storage.googleapis.com/skytruth-shared-datasets-1/_catalog/web/catalog.json?verify=<generation>'
curl -I -sS 'https://storage.googleapis.com/skytruth-shared-datasets-1/path/to/asset/latest/asset.pmtiles?v=<sha256>'
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
