# Catalog Web Preview

The static catalog is the browser-facing index for shared datasets. It is a
zero-backend bundle generated from repo-controlled metadata and deployed under:

```text
gs://skytruth-shared-datasets-1/_catalog/web/
```

The public entry point is:

```text
https://storage.googleapis.com/skytruth-shared-datasets-1/_catalog/web/index.html
```

## Build locally

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --out /tmp/shared-datasets-1/catalog-web
```

The generated bundle contains:

```text
index.html
styles.css
app.js
map-preview.js
catalog.json
docs/assets/*.md
```

`catalog.json` is the runtime contract. The browser app does not parse CSV or
Markdown at runtime.

For assets with `releases/YYYY-MM-DD/...` file entries in `docs/assets/*.md`,
the generator emits a `versions` array. Exact release dates are preserved. For
templated release paths, the current catalog `last_updated` date becomes the
selectable dated release for the web preview.

## Validate

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_catalog_site.py
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --out /tmp/shared-datasets-1/catalog-web
python3 -m http.server 4173 --directory /tmp/shared-datasets-1/catalog-web
```

Then open:

```text
http://127.0.0.1:4173/
```

Check search, filters, detail selection, copy buttons, mobile layout, and at
least one PMTiles preview.

## Browser dependencies

The map preview lazy-loads exact CDN versions from `web/catalog/map-preview.js`:

```text
maplibre-gl@5.9.0
pmtiles@4.3.0
```

Users can switch the preview background between a street-style map and satellite
imagery. The current v1 basemap sources are:

```text
Map: OpenStreetMap raster tiles
Satellite: Esri World Imagery raster tiles
```

The street-style map is the default on every page load. Satellite is available
as an explicit user-selected option.

The rest of the catalog remains usable when those dependencies or PMTiles range
requests fail. Vendor these files under `web/catalog/vendor/` before deployment
if fully offline/self-contained hosting becomes a requirement.

## Deploy

Use `scripts/gcs_asset.py` for every object write so generation preconditions are
explicit. New files should use no-clobber uploads. Existing files should be
replaced only after reading the current generation.

Initial upload example:

```bash
OUT=/tmp/shared-datasets-1/catalog-web
DEST=gs://skytruth-shared-datasets-1/_catalog/web

UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload "$OUT/index.html" "$DEST/index.html"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload "$OUT/styles.css" "$DEST/styles.css"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload "$OUT/app.js" "$DEST/app.js"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload "$OUT/map-preview.js" "$DEST/map-preview.js"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload "$OUT/catalog.json" "$DEST/catalog.json"
```

For replacements, stat each object first and pass the returned generation:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat "$DEST/catalog.json"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload \
  "$OUT/catalog.json" "$DEST/catalog.json" --replace-generation <generation>
```

Repeat for changed static files and copied docs. Do not use unsafe overwrites for
catalog deployment.

## CORS

PMTiles previews require browser range requests. The production bucket was
verified with this CORS shape:

```json
[
  {
    "origin": ["*"],
    "method": ["GET", "HEAD", "OPTIONS"],
    "responseHeader": ["Content-Length", "Content-Range", "ETag", "Range"],
    "maxAgeSeconds": 3600
  }
]
```

Terraform in `terraform/envs/prod/shared_bucket_public.tf` now records this
bucket configuration. Run a Terraform plan before applying, because the bucket is
an existing production bucket imported into Terraform control.

## Pre-launch hygiene

Run the read-only compliance audit before publishing the catalog and after
deployment:

```bash
UV_CACHE_DIR=.uv-cache GOOGLE_CLOUD_PROJECT=shared-datasets-1 SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1 \
  uv run python .claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py
```

Resolve visible hygiene issues before announcing the catalog, especially stray
desktop files, missing asset READMEs, stale bucket-side catalog objects, and
README sections required by `AGENTS.md`.
