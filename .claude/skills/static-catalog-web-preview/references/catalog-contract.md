# Catalog runtime contract

Read for catalog output, release selection, viewer APIs or UI changes.
The skill owns build/deploy/QA procedure.

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
URL exposed by the Terraform `catalog_viewer_uri` output. The production staff
entry point is `https://catalog-viewer-vmust7rama-uc.a.run.app/`; keep the
`STAFF_CATALOG_URL` constant and HTML sign-in links aligned if it changes.

The public catalog lists every access tier. Private/internal cards show
`Log in required`; selecting one shows a prominent `Log in to see this data`
action instead of maps, feature data, downloads, documentation, paths, code,
or comparison controls. Mixed selections render public layers only and keep
the sign-in action visible for restricted selections. The persistent staff
link and dataset action open the IAP-protected viewer with repeated `asset`
parameters, per-asset `version.{asset-key}` parameters, and the selected
`locale`. The viewer restores that selection after sign-in. These presentation
rules also apply on direct GCS static hosting. IAP and backend authorization
continue to enforce restricted access; catalog visibility does not grant access.

The feature-branch preview environment has a separate IAP-protected catalog
viewer backed by `gs://skytruth-shared-datasets-1-preview/`; see
the feature-preview skill. It is refreshed by a preserve-mode preview deploy
and intentionally lists only assets with preview-bucket release indexes.
Preview-bucket objects are not served by the production
`https://tiles.skytruth.org/artifacts/` CDN route. Private preview artifacts,
including localized metadata sidecars, continue to resolve through the preview
viewer as signed GCS URLs unless a separate preview CDN is explicitly created.

Catalog web files publish before the separately protected viewer image deploy.
Startup dependencies must therefore remain in JavaScript modules already served
by the existing viewer allowlist. WDPA execution formatting lives in
`release-reference.js`; adding it as a new eager `execution-status.js` import
made the catalog fail to start on older viewer revisions. The browser acceptance
transport enforces the previous module allowlist while exercising current code.

## Catalog output

```text
index.html
styles.css
app.js
map-preview.js
release-reference.js
workspace.js
workspace-contract.js
catalog.json
docs/assets/*.md
```

`catalog.json` is the runtime contract. The browser app does not parse CSV or
Markdown at runtime.

Release history is release-index-backed. When
`_catalog/releases/{asset-slug}.json` is supplied to the generator,
`catalog.json` emits the resulting `versions` array as a first-class runtime
object. If no local release index is supplied, `versions` stays empty instead of
being derived from asset-doc file tables. At runtime, the browser also fetches
the adjacent release index and replaces `versions` with that complete release
history when available. This keeps the release selector current after cron jobs
run without requiring a Git commit or a tracked `last_updated` edit.
Release-index-backed `versions[]` entries preserve the complete release
`files` list, including sidecar datafiles such as metadata sidecars, schemas,
and manifests.

The browser-facing freshness labels are `Latest release` for the newest
successful dataset release and `Last check-in` for the most recent success or
meaningful skip recorded by the cron job. Both values come from the release
index when present.

The runtime `catalog.json` includes the CSV catalog fields, including
`citation`, so browser and service consumers can cite the original source
publication or authoritative dataset release. Optional discovery fields in
asset-doc frontmatter are emitted when present: `bounds` as
`[min_lon, min_lat, max_lon, max_lat]`, `geometry_type`, `row_count`,
`data_profile`, `search_fields`, `feature_metadata`, `feature_identity`,
`colorizer_metadata`, `source_url`, and frontmatter `license_flags` merged with
license-text-derived flags.
`data_profile` carries curated at-a-glance profiling facts such as column count,
provider identity-field candidates, distinct values, duplicate counts, and short
profile notes. `search_fields` surfaces curator-selected high-value filter
fields that are not source field IDs. `feature_metadata` records the canonical
metadata sidecar, schema, manifest, and optional localized sidecars recorded in
release metadata.
`colorizer_metadata` records the bounded source of display color fields for each
asset: `metadata_sidecar_schema` for release-oriented feature metadata assets,
`pmtiles_vector_layers` for ordinary PMTiles assets whose fields are declared in
the PMTiles archive metadata, and `none` for assets without PMTiles.
Release-oriented vector assets may also publish canonical and localized feature
metadata sidecars in the release index. The static public viewer resolves
public sidecars from the hydrated release index and fetches them directly from
`https://tiles.skytruth.org/artifacts/{bucket-object-path}`. It tries the
active `locale` first, then falls back to the canonical
`{asset-slug}.metadata.ndjson.gz` sidecar. The IAP-protected catalog viewer
keeps `/api/download-url?format=metadata` for authorized private inspection and
returns one signed `/private/{bucket-object-path}` URL when CDN signing is
configured. The browser never fetches a separate translation overlay and does
not merge translation rows over canonical metadata. Metadata sidecars must be
`.ndjson.gz`; the browser expects gzip-compressed NDJSON.
When no release index is present, top-level metadata declarations remain discovery
information. Only a legacy map-only latest alias can preview; metadata/schema
joins wait for an indexed release with exact generations. Explicit missing dates
fail visibly. An indexed PMTiles file without a generation cannot render as an
exact snapshot. An optional sidecar/schema without a generation leaves exact
tiles renderable with compact properties and a metadata capability note.
The PMTiles colorizer discovers metadata-backed fields from the release
`{asset-slug}.schema.json` artifact, not by scanning the full metadata sidecar
or loaded vector-tile features. Public schema files resolve through the same
artifact CDN path as public sidecars. The authenticated catalog viewer can sign
restricted schema files through `/api/download-url?format=schema`. This keeps
colorizer field discovery bounded to a small JSON contract.

Clicking a color legend label filters the map to that exact value; clicking the
selected label again restores all features without moving the camera. Every
label includes its count in square brackets. Metadata-backed counts use the
complete loaded release sidecar, including records absent from the current
display tiles. Tile-backed counts cover loaded source tiles and count each
identified feature once per source layer, rather than once per tile fragment.
The legend identifies which count scope is available. Counts are computed before
color sampling and refresh as tiles load.

Whole-sidecar hydration is a consumer policy decision, not a producer-granted
permission. Release file entries expose enough information, including sidecar
size when known, for each consumer to choose a strategy. The catalog viewer's
browser policy follows a 24 MiB default autoload budget — sized to cover every
current catalog sidecar except `ucdp-ged-events`, whose colorize-by-metadata
stays withheld in favor of the bounded lookup API — and can be configured by
setting `window.SHARED_DATASETS_METADATA_SIDECAR_AUTOLOAD_MAX_BYTES` or a
`shared-datasets-metadata-sidecar-autoload-max-bytes` meta tag before `app.js`
runs. When a sidecar is larger than the configured budget or has no declared
size, clicked feature inspection falls back to the bounded
`POST /v1/assets/{asset-slug}/releases/{release}:lookup` API when the viewer API
is available. Public static previews without that API stream the public sidecar
only until the clicked `feature_id` values are found, without building a full
browser-side metadata index. If neither lookup path is available, the inspector
shows the compact PMTiles feature properties and the reason metadata is unavailable.
Bounded API responses must identify the sidecar generation actually enforced by
the serving backend. Unverified or mismatched responses cannot enrich the layer.
The bounded API returns canonical values; when a display language is selected,
the inspector explicitly labels these values as source-language metadata.
Metadata-backed color fields remain discoverable from the schema, but the
catalog viewer does not hydrate sidecars outside its budget to compute full-map
color values; it keeps dataset coloring until a bounded value source is
available. Other consumers may choose different thresholds or always use the
sidecar/API path that best fits their runtime.
`feature_identity` records the policy and counts for a native
`feature_id` feature property when an asset needs generated group IDs or a
last-resort row address. These fields are additive so existing CSV and JSON
consumers can ignore them.

## FGB downloads

The detail view includes `Download FGB` for FlatGeobuf assets. Both latest and
dated selections use the captured indexed FGB path and generation. Public links
use the artifact CDN route with `generation=...`. Legacy no-index latest retains
its map/download alias behavior without claiming dated lineage.

Authenticated downloads use:

```text
GET /api/download-url?slug={asset-slug}&format=fgb&version={YYYY-MM-DD}&generation={generation}
```

The same endpoint selects `metadata` (with optional `locale`) and `schema`.
Private/internal requests require the existing IAP SkyTruth identity. The server
selects only the catalog-owned release artifact, checks an expected generation,
and returns `download_url`, `expires_at`, `gs_uri`, `resolved_release`, and
`generation` (plus format-specific filename/locale fields). The browser verifies
that identity before following the URL. A missing date/file returns 404 and a
changed generation returns 409; neither falls back to latest. Unknown URI/path
parameters cannot authorize another object. Public responses have null expiry;
restricted URLs keep the existing short lifetime and no-store response policy.

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

With a usable release index, both `Latest` and dated selections capture one
release entry and exact artifact generations. Public tiles use
`https://tiles.skytruth.org/artifacts/{bucket-object-path}?generation=...`.
The catalog's tiered `pmtiles_url` remains a legacy latest alias for assets with
no index; it cannot establish a coherent cross-artifact metadata join.

Restricted indexed tiles require the same-origin signer:

```text
GET /api/pmtiles/signed-url?slug={asset-slug}&version={YYYY-MM-DD}&generation={generation}
```

The endpoint authorizes the existing catalog-owned asset and selects its indexed
primary PMTiles file. It returns `pmtiles_url`, `expires_at`, `gs_uri`,
`resolved_release`, and `generation`. Generation is covered by the signature.
The browser checks the identity and loads signed GCS URLs without credentialed
fetches. Missing signer/authentication or a changed generation fails visibly;
it never switches an exact layer to the mutable cookie alias.

Map clicks carry their captured release reference. Metadata/schema caches use
path and generation, and render/inspection serials discard superseded responses.
Same-date replacements therefore require a new complete selection. An old
retained generation may still read exactly, but a refreshed signer refuses it
when the index no longer authorizes it. If bytes are no longer retained, the
exact read fails without retrying a current generation. Search and docs remain
usable after a map failure.

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

## Viewer infrastructure

Production Terraform owns the production IAP-protected Cloud Run viewer. It
does not require a SkyTruth custom domain or load balancer; direct Cloud Run IAP
protects the service's generated `run.app` URL. Use the protected deployment procedure in SKILL.md.

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

## WDPA execution status

Both WDPA detail pages show the independent observer's
`_catalog/wdpa-monthly-execution.json` beside the asset check-in and published
release. Execution status describes the whole job; a marine release may have
published successfully before a terrestrial failure. A newer running execution
also displays the last completed execution. Status is revalidated every minute
while details are visible, and observations older than 15 minutes are stale.
Missing or malformed observations display unavailable status without concealing
the published release. Raw execution messages and configuration are excluded.
The protected viewer serves execution formatting in the existing
`release-reference.js` module and maps `/wdpa-monthly-execution.json` to the
observer object under `_catalog/`, preserving revalidation headers and reading
each observation afresh. Keep startup imports within the existing viewer
allowlist so a web deploy remains compatible with the previous viewer image.
Verify status on static and protected WDPA pages after protected deployment.

The observer is deployed with the worker through protected Terraform and has
read-only WDPA execution access plus an exact-object generation-preconditioned
status writer. See [WDPA operations](../../../../ingestion/wdpa_monthly/README.md) for
resource profiling, rollout gates, failure probes and publication recovery.

## Compare releases

The authenticated catalog viewer compares generation-pinned canonical release
sidecars, schemas and manifests through `scripts/compare_releases.py` and the
same-origin `/api/comparisons` job routes. The catalog offers release selection,
complete change counts, a searchable table, source property inspection, a union
map (green additions/new positions, red removals/old positions, yellow stationary
metadata edits, faint unchanged objects) in the primary map. Comparable IDs keep
independent statuses even when their geometry is identical. Complete reports
are available through the CLI/API. Before/After selections rerun automatically; Close comparison
restores ordinary browsing. Summaries use compact tables. Geometry colors remain
available when feature-ID contracts differ, explicitly labeled geometry-only;
yellow then means contents differ at that geometry. Map clicks show every overlapping
Before/After polygon with release-bound source metadata, even when IDs cannot be
matched; these details sit below the map without expanding the summary.
Field highlights require compatible identity contracts and matching feature IDs
among already-loaded Before/After hits. Localization
is excluded from source changes. Identity incompatibility withholds authoritative
feature classification; static catalogs retain visual inspection and a local CLI
route. See [comparison semantics, authorization, budgets and CLI](../../../../docs/compare-releases.md).

## Dataset integration

**Use this dataset** is an inline section immediately above the canonical paths. Its
Python/TypeScript tabs show 3–5 line examples and a single Copy code action.
Citation, source and licensing remain in Source and terms; this section only
provides integration code and an SDK setup link.
Setup lives in the consumer guide; the UI has no integration dialog, Save
workspace action, lockfile download, artifact identity dump, or provenance output.
Dataset descriptions span the detail header independently of its action buttons.

Latest is captured once into the existing release-reference objects. Examples
use dated artifact URIs with exact generations; `fetch_artifact` and
`showDataset` hide cache, map setup, and metadata joining. Python uses ADC.
The optional TypeScript MapLibre entrypoint uses the same pinned MapLibre and
PMTiles versions as the catalog. Restricted integrations need an app-owned
snapshot authorization route; imported references never authorize access.

The catalog has no workspace file-import controls. The [v1 snapshot contract](../../../../docs/standards/workspace-snapshot-v1.md)
and SDK snapshot APIs remain available for programmatic consumers.
Full source terms remain in the dataset's Source section and authoritative docs.

The browser smoke suite executes the five-line TypeScript example with real
MapLibre, PMTiles, and SDK modules. It checks copying, version changes, desktop
and narrow layouts, and the absence of workspace import controls.
Python tests execute each generated format example through the real package
entrypoint using a synthetic GCS client. These checks do not prove live
IAP/CDN/GCS retention configuration.

## Private dataset usage report

The production catalog viewer packages `/usage` and its assets in the viewer
image. Authenticated `GET /api/usage` follows the private usage manifest with
generation and integrity checks and returns `Cache-Control: no-store`. Both the
page and direct endpoint reuse the viewer's IAP/domain policy. Public catalog
artifacts contain no usage data. Missing reports return 503; the viewer and page
independently detect reports older than 48 hours. Search and application
filters, last-activity sorting and 30/90-day windows preserve separate source
counts, metadata interest, bytes, failures and unknown attribution.

See [usage operations and rollout](../../../../ingestion/dataset_usage/README.md) for
collection/activation gates, source health and the limits of observed activity.
