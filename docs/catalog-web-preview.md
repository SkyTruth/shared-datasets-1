# Catalog Web Preview

The static catalog is the browser-facing index for shared datasets. It is a
zero-backend bundle generated from repo-controlled metadata and deployed under:

```text
gs://skytruth-shared-datasets-1/_catalog/web/
```

The public entry point is:

```text
https://tiles.skytruth.org/_catalog/web/index.html
```

Direct `https://storage.googleapis.com/skytruth-shared-datasets-1/_catalog/...`
reads are a temporary proof-of-concept bypass while bucket public access is
being removed. Public browser access should use `tiles.skytruth.org/_catalog/`.

The authenticated internal entry point is the IAP-protected Cloud Run `run.app`
URL exposed by the Terraform `catalog_viewer_uri` output.

The feature-branch preview environment has a separate IAP-protected catalog
viewer backed by `gs://skytruth-shared-datasets-1-preview/`; see
`docs/feature-preview.md`. It is refreshed by the preview index-load workflow
and intentionally lists only assets with preview-bucket release indexes.
Preview-bucket objects are not served by the production
`https://tiles.skytruth.org/private/` CDN route. Private preview artifacts,
including localized metadata sidecars, continue to resolve through the preview
viewer as signed GCS URLs unless a separate preview CDN is explicitly created.

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
Release-index-backed `versions[]` entries preserve the complete release
`files` list, including sidecar datafiles such as feature indexes, metadata
sidecars, schemas, and manifests.

The browser-facing freshness labels are `Latest release` for the newest
successful dataset release and `Last check-in` for the most recent success or
meaningful skip recorded by the cron job. Both values come from the release
index when present.

The runtime `catalog.json` includes the CSV catalog fields, including
`citation`, so browser and service consumers can cite the original source
publication or authoritative dataset release. Optional discovery fields in
asset-doc frontmatter are emitted when present: `bounds` as
`[min_lon, min_lat, max_lon, max_lat]`, `geometry_type`, `row_count`,
`data_profile`, `search_fields`, `localized_names`, `generated_group_id`,
`generated_row_id`, `source_url`, and frontmatter `license_flags` merged with
license-text-derived flags.
`data_profile` carries curated at-a-glance profiling facts such as column count,
provider identity-field candidates, distinct values, duplicate counts, and short
profile notes. `search_fields` surfaces curator-selected high-value filter
fields that are not provider IDs. `localized_names` records the official
localization CSV sidecar and metadata lookup contract, including `storage`,
`join_key`, `localization_file`, available locales, declared fields, aggregate
per-locale review state, and fallback field when present.
Release-oriented vector assets may also publish canonical and localized feature
metadata sidecars in the release index. The browser asks `/api/download-url` for
`format=metadata` and the active `locale`; the catalog viewer resolves that to
one materialized `{asset-slug}.metadata.{locale}.ndjson.gz` sidecar when present
or the canonical `{asset-slug}.metadata.ndjson.gz` fallback when absent. The
browser never fetches a separate translation overlay and does not merge
translation rows over canonical metadata. In production, private shared-bucket
metadata responses may use one signed
`https://tiles.skytruth.org/private/{bucket-object-path}` URL; public metadata
and preview-bucket metadata may continue to use one GCS URL.
`generated_group_id` records the policy and counts for a native
`shared_datasets_group_id` feature property when an asset needs generated group
IDs. `generated_row_id` records the policy and warning for a native
`shared_datasets_row_id` feature property when an asset needs a last-resort row
address. These fields are additive so existing CSV and JSON consumers can ignore
them.

## FGB downloads

The detail view includes a one-click `Download FGB` control for assets whose
canonical format is FlatGeobuf. The control follows the version selector: with
`Latest` selected it downloads the catalog `canonical_path`, and with a dated
release selected it downloads that release's canonical FGB.

Public assets use the generated `public_url` directly, which resolves to
`https://storage.googleapis.com/...`. The browser downloads from GCS; the static
catalog app does not proxy or read dataset bytes.

When the same UI is served from the authenticated Cloud Run viewer, private FGB
downloads are resolved through:

```text
GET /api/download-url?slug={asset-slug}&format=fgb&version={latest-or-YYYY-MM-DD}
```

The endpoint resolves the requested object only from generated catalog metadata
or the bucket release index, requires an IAP-authenticated SkyTruth identity for
private assets, and returns a short-lived signed GCS URL:

```json
{
  "download_url": "https://storage.googleapis.com/...",
  "expires_at": "2026-05-09T12:15:00Z",
  "gs_uri": "gs://skytruth-shared-datasets-1/...",
  "filename": "example-asset.fgb"
}
```

Public assets may also use this endpoint and return `expires_at: null`, but the
browser UI uses direct links for public FGBs.

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

PMTiles previews use the catalog `pmtiles_url` value, which should be the tiered
`https://tiles.skytruth.org/pmtiles/{public-or-private}/{slug}.pmtiles` URL for
latest releases. The public `tiles.skytruth.org/_catalog/web/` entry point is a
static/public viewer: public PMTiles fetch anonymously, and private PMTiles may
rely on an already-authorized `tiles.skytruth.org` setup when one exists.

When the app is served from the authenticated Cloud Run viewer, private PMTiles
are resolved through the same-origin signer endpoint:

```text
GET /api/pmtiles/signed-url?slug={asset-slug}
```

The endpoint reads generated `catalog.json`, requires the asset to publish
PMTiles, signs the exact catalog `pmtiles_path`, and returns:

```json
{
  "pmtiles_url": "https://storage.googleapis.com/...",
  "expires_at": "2026-05-09T12:00:00Z"
}
```

Signed GCS PMTiles URLs are loaded without credentialed browser fetches. Public
PMTiles do not call the signer and continue to use their catalog URL.

If a nonstandard internal host needs an explicit signer endpoint, configure it
before `app.js` loads with either:

```html
<meta name="shared-datasets-pmtiles-signer-url" content="/api/pmtiles/signed-url" />
```

or:

```html
<script>
  window.SHARED_DATASETS_PMTILES_SIGNER_URL = "/api/pmtiles/signed-url";
</script>
```

The older signed-cookie session hook is still available for static deployments
that intentionally use `tiles.skytruth.org` private routes. Configure it before
`app.js` loads with either:

```html
<meta name="shared-datasets-pmtiles-session-url" content="/api/pmtiles/session?tier=private" />
```

or:

```html
<script>
  window.SHARED_DATASETS_PMTILES_SESSION_URL = "/api/pmtiles/session?tier=private";
</script>
```

When no session URL is configured, the viewer still sends credentials for
private tier URLs and relies on an existing valid `Cloud-CDN-Cookie` for
`tiles.skytruth.org`.

The rest of the catalog remains usable when those dependencies or PMTiles range
requests fail. Vendor these files under `web/catalog/vendor/` before deployment
if fully offline/self-contained hosting becomes a requirement.

## Deploy

After trusted PRs merge to `main`, `.github/workflows/catalog-web-deploy.yml`
rebuilds the catalog web bundle and publishes both `_catalog/web/` and the root
`_catalog/shared-datasets-catalog.csv` contract with the approved publisher
identity, generation preconditions, and `no-cache, max-age=0,
must-revalidate`. This workflow is the normal PR-backed promotion path for
repo-generated catalog web changes. Do not also add a
`shared-datasets-publish-plan` for `_catalog/web/catalog.json` when the catalog
web deploy workflow will run from the same PR; that duplicates the promotion and
can race the automatic deploy. The live catalog drift guard runs after that
deploy workflow completes so it does not race the object promotion.

Use `scripts/gcs_asset.py` for every object write so generation preconditions are
explicit. Manual catalog web deployments should stage files under
`_scratch/pending-publishes/catalog-web/{proposal-id}/` and promote approved
objects only through an explicit PR with a fenced publish plan. After that PR
merges, the `Approved dataset mutation` GitHub workflow applies the plan. Do not
use standalone workflow dispatch or single-object fallback inputs for catalog
refreshes. New files should use no-clobber promotion. Existing files should be
replaced only after reading the current generation. Set the publish-plan
`cache_control` field for `catalog.json` and any other cache-sensitive
replacement that needs no-cache metadata.

Publisher-identity CLI reference only:

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

Humans and agents should stage these files under `_scratch/pending-publishes/`
and use the approved workflow instead of running the publisher-identity commands
locally. For replacements, stat each object first and use the returned
generation as the workflow `destination_generation` precondition:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat "$DEST/catalog.json"
```

Repeat for changed static files and copied docs. Do not use unsafe overwrites for
catalog deployment.

Keep the live web shell and runtime contract revalidating on every request. The
catalog app also appends cache-busting query strings to `catalog.json`, docs
Markdown, and PMTiles URLs, but the object metadata should not invite a browser
or CDN to hold stale catalog or tile bytes after a same-path replacement. Pass
the publish-plan `cache_control` field with
`no-cache, max-age=0, must-revalidate` for `catalog.json`, web shell/runtime
objects, and same-path PMTiles replacements that need no-cache metadata.

Corrective PMTiles rebuilds should replace both `latest/*.pmtiles` and the
matching dated release PMTiles object for the canonical dataset release. Do not
create PMTiles-only dated release directories for display repairs unless
PMTiles is the asset's canonical format; release indexes and catalog versions
should represent release dates that include the canonical format.

## Authenticated viewer infrastructure

Production Terraform owns the production IAP-protected Cloud Run viewer. It
does not require a SkyTruth custom domain or load balancer; direct Cloud Run IAP
protects the service's generated `run.app` URL. Build and push an immutable
viewer image, then update `catalog_viewer_image` in Terraform by PR. The
protected production workflow applies after review and merge:

```bash
IMAGE=us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/catalog-viewer:$(date -u +%Y%m%d%H%M%S)

docker build --platform linux/amd64 -f services/catalog_viewer/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"
```

After the protected workflow applies, open the `catalog_viewer_uri` output. The
service account is read-only on `_catalog/` and canonical dataset prefixes, and
can only sign blobs as itself for 15-minute PMTiles URLs.

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
