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
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --out "$WORK_ROOT/catalog-web"
```

When validating a specific release selector locally, download the relevant
release index JSON files and pass that directory:

```bash
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --release-index-dir "$WORK_ROOT/release-indexes" \
  --out "$WORK_ROOT/catalog-web"
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
the generator emits a fallback `versions` array. At runtime, the browser also
fetches `_catalog/releases/{asset-slug}.json` and replaces that fallback with
the complete release history from the bucket-side release index. This keeps the
release selector current after cron jobs run without requiring a Git commit or a
tracked `last_updated` edit.

The browser-facing freshness labels are `Latest release` for the newest
successful dataset release and `Last check-in` for the most recent success or
meaningful skip recorded by the cron job. Both values come from the release
index when present.

Optional discovery fields in asset-doc frontmatter are emitted when present:
`bounds` as `[min_lon, min_lat, max_lon, max_lat]`, `geometry_type`, `row_count`,
`source_url`, and frontmatter `license_flags` merged with license-text-derived
flags. These fields are additive so existing CSV and JSON consumers can ignore
them.

## Validate

```bash
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_catalog_site.py
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_site.py \
  --out "$WORK_ROOT/catalog-web"
python3 -m http.server 4173 --directory "$WORK_ROOT/catalog-web"
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
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
OUT="$WORK_ROOT/catalog-web"
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

After upload, keep the live web shell and runtime contract revalidating on every
request. The catalog app also appends cache-busting query strings to
`catalog.json`, docs Markdown, and PMTiles URLs, but the object metadata should
not invite a browser or CDN to hold stale catalog or tile bytes after a same-path
replacement:

```bash
gcloud storage objects update \
  --cache-control='no-cache, max-age=0, must-revalidate' \
  "$DEST/index.html" "$DEST/styles.css" "$DEST/app.js" "$DEST/map-preview.js" "$DEST/catalog.json"
```

When replacing same-path PMTiles display artifacts, also set no-cache metadata on
the replaced PMTiles objects after the generation-preconditioned upload:

```bash
gcloud storage objects update \
  --cache-control='no-cache, max-age=0, must-revalidate' \
  gs://skytruth-shared-datasets-1/path/to/asset/latest/asset.pmtiles \
  gs://skytruth-shared-datasets-1/path/to/asset/releases/YYYY-MM-DD/asset.pmtiles
```

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
README sections required by `docs/standards/asset-layout-and-formats.md`.
