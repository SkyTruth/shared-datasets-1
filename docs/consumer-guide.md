---
title: SkyTruth Shared Datasets Consumer Guide
description: Routing guide for choosing the Python or TypeScript shared-datasets API, catalog access surface, and PMTiles access pattern.
last_updated: 2026-05-29
audience: SkyTruth application developers, data pipeline owners, map frontend maintainers, and consumer-integration agents
---

# SkyTruth Shared Datasets Consumer Guide

SkyTruth Shared Datasets is the common access layer for reusable datasets that
multiple SkyTruth projects need to discover, cite, fetch, or display. This guide
routes consumers to the correct API and access surface. The detailed SDK
contracts live in the focused SDK READMEs:

- [Python SDK](../api/python/README.md): backend catalog resolution,
  authenticated GCS downloads, CLI diagnostics, and lineage.
- [TypeScript SDK](../api/typescript/README.md): browser-safe PMTiles helpers,
  catalog JSON resolution, private PMTiles session handshakes, fetch credential
  selection, and server-only Cloud CDN signed-cookie helpers.

Maintainer-only publishing and infrastructure procedures live in
[GCP asset operations](./gcp-asset-operations.md),
[PMTiles browser access](./pmtiles-cdn.md), and
[catalog web preview](./catalog-web-preview.md).

## Choose The API

| Consumer need | Use | Why |
|---|---|---|
| Backend Python code needs a local data file | Python SDK `fetch_dataset(...)` | Downloads canonical bytes with ADC and returns `DatasetRef.cache_path` plus lineage. |
| Backend Python code needs a URI or browser URL but not bytes | Python SDK `resolve_dataset(...)` or `Catalog.resolve(...)` | Preserves durable `gs://` identity, access tier, citation, and `resolved_id`. |
| Backend service needs to list/search assets | Python SDK `Catalog.load_gcs()` or catalog CSV | Good for batch jobs, service config generation, and CI checks. |
| Browser displays public PMTiles | TypeScript SDK or direct tiered CDN URL | No cookie or GCS credential is required. |
| Browser may display private PMTiles | TypeScript SDK plus app-owned backend session route | Private PMTiles require app authentication, authorization, signed cookies, and credentialed range requests. |
| Backend route issues private PMTiles cookies | TypeScript server entrypoint | Cookie signing uses Node crypto and should stay behind the app backend. |
| App needs layer/search config | Catalog JSON plus either SDK | Preserve `access_tier`, `pmtiles_url`, citation, license, and freshness metadata. |
| Browser needs public feature attributes | TypeScript `resolveSharedDatasetLayer` plus `fetchSharedDatasetMetadataRecords` | Resolve the layer and its release metadata sidecar together, then join clicked features by `feature_id`. |
| Browser needs private feature attributes | App-owned backend signed metadata URL route or metadata API proxy | The backend authenticates, authorizes, validates the catalog sidecar, and returns app-approved metadata access. |
| Backend has PMTiles feature IDs and needs full attributes | Release metadata sidecar, or Feature metadata API when serving is enabled | PMTiles intentionally carry only geometry plus `feature_id`; full metadata is release-scoped and keyed by feature ID. |

Do not build browser integrations on anonymous
`https://storage.googleapis.com/skytruth-shared-datasets-1/...` reads. Direct
public bucket access is transitional. Browser PMTiles should use
`tiles.skytruth.org`; backend data access should use ADC and managed service
accounts.

## Canonical Consumer Surfaces

Use catalog metadata as the source of truth. Do not infer dataset location,
format, access tier, citation, or freshness from hand-built paths.

```text
https://tiles.skytruth.org/_catalog/shared-datasets-catalog.csv
https://tiles.skytruth.org/_catalog/web/catalog.json
https://tiles.skytruth.org/_catalog/web/index.html
https://tiles.skytruth.org/_catalog/web/docs/assets/{asset-slug}.md
https://tiles.skytruth.org/_catalog/releases/{asset-slug}.json
gs://skytruth-shared-datasets-1/_catalog/shared-datasets-catalog.csv
catalog/shared-datasets-catalog.csv
docs/assets/{asset-slug}.md
```

Important fields to preserve in consumer config, logs, lineage tables, and UI
where relevant:

| Field | Use |
|---|---|
| `slug` | Stable asset identifier for SDK calls and PMTiles URLs. |
| `status` and `consumer_guidance` | Whether the asset should be shown by default and any migration guidance. |
| `access_tier` | Required for PMTiles URLs, authorization decisions, and private-cookie behavior. |
| `canonical_path` and `canonical_format` | Primary analytical data object and format. |
| `available_formats` | Whether requested formats such as `fgb`, `pmtiles`, or `csv` exist. |
| `pmtiles_url` | Browser-facing PMTiles URL in the catalog JSON. |
| `citation`, `license`, and `source_url` | Provenance for UI, reports, and downstream outputs. |
| `latest_release` or `last_updated` | Freshness metadata. |
| `feature_metadata` | Metadata sidecar, schema, manifest, and optional localized metadata sidecars for assets that publish feature-level metadata. Firestore-backed lookup is available only when serving is enabled. |

Localized display-name consumers should resolve labels through a materialized
locale-specific metadata sidecar such as
`{asset-slug}.metadata.es.ndjson.gz`. The browser-facing catalog resolver
accepts one active locale and returns either that localized sidecar or the
canonical `{asset-slug}.metadata.ndjson.gz` fallback. Browser apps should not
fetch a separate translation overlay or merge translations over canonical
metadata client-side. Canonical FGB consumers should rely on `feature_id` for joins
and should not expect localized name columns in the FGB.
Public sidecars should be fetched from
`https://tiles.skytruth.org/artifacts/{bucket-object-path}`. Private sidecars
should be returned by a consumer backend as one short-lived signed artifact URL
only after slug, release, locale, tier, and user entitlement checks pass.

Release-oriented vector PMTiles carry geometry plus `feature_id` only. Use the
release metadata sidecar, or the metadata API when Firestore serving is active,
for full attributes, display labels, hashes, and provenance instead of
expecting source columns in PMTiles. After loading the sidecar, use
`geometry_hash` as the stable geometry-equivalence key for grouping or
de-duplicating footprints; do not use hashes as URL lookup handles.

Default production layer lists should use `status="active"`. If a UI
intentionally shows deprecated, superseded, or retired assets, display
`consumer_guidance`.

## Python Backend API

Use the [Python SDK](../api/python/README.md) for backend and batch workflows.

Install from GitHub because the package is not distributed on PyPI:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@<tag-or-sha>#subdirectory=api/python"
```

Runtime requirements:

- Use Application Default Credentials with a runtime service account.
- Grant the runtime identity `roles/storage.objectViewer` on
  `gs://skytruth-shared-datasets-1`.
- Do not create service account JSON keys.
- Record `DatasetRef.resolved_id` when a run requests `version="latest"` and
  needs durable lineage.

Typical backend fetch:

```python
from skytruth_shared_datasets import fetch_dataset

ref = fetch_dataset("wdpa-marine", "fgb")
path = ref.cache_path
resolved_id = ref.resolved_id
canonical_uri = ref.gs_uri
```

The Python SDK does not sign Cloud CDN cookies. Use the TypeScript server
helpers for private PMTiles cookie issuance.

## TypeScript Browser And Session API

Use the [TypeScript SDK](../api/typescript/README.md) for browser PMTiles and
backend PMTiles session routes.

The package is published on npm:

```bash
npm install @skytruth/shared-datasets
```

Use a local path only for development against unreleased local changes. Do not
commit local-path installs to production consumers:

```bash
npm install ../shared-datasets-1/api/typescript
```

Browser-safe entrypoint:

```ts
import {
  ensurePmtilesCdnSession,
  fetchSharedDatasetMetadataRecords,
  getPmtilesFetchCredentials,
  resolvePublicSharedDatasetMetadataSidecarUrl,
  resolveSharedDatasetLayer,
  resolveSharedDatasetPmtilesRef
} from "@skytruth/shared-datasets";
```

Server-only entrypoint for the consumer app's backend route:

```ts
import {
  decodePmtilesCdnSigningKey,
  getSignedSharedDatasetArtifactUrl,
  getExpiredPmtilesCookies,
  getPrivatePmtilesSessionCookies
} from "@skytruth/shared-datasets/server";
```

Do not import the server entrypoint from browser bundles.

## PMTiles Browser Access

Latest PMTiles use a tiered CDN URL:

```text
https://tiles.skytruth.org/pmtiles/public/{slug}.pmtiles
https://tiles.skytruth.org/pmtiles/private/{slug}.pmtiles
```

Public PMTiles can be loaded directly. Private PMTiles require a browser cookie
issued by the consuming application's backend after authentication and
authorization.

Minimum private PMTiles flow:

1. Resolve `pmtiles_url` and `access_tier` from catalog JSON.
2. Add a backend endpoint such as `/api/pmtiles/session`.
3. For `tier=private`, authenticate and authorize the user.
4. Load the PMTiles CDN signing key from the consumer backend's secret store.
5. Set `Cloud-CDN-Cookie` with `Domain=.skytruth.org`,
   `Path=/pmtiles/private`, `Secure`, `HttpOnly`, and `SameSite=None`.
6. Return `Cache-Control: no-store`.
7. Before mounting a private layer, call the session endpoint with
   `credentials: "include"`.
8. Ensure every PMTiles header, directory, and tile range request also uses
   `credentials: "include"`.

Exact CORS origins and signer IAM grants are infrastructure concerns. Request a
shared-datasets infrastructure PR before relying on private PMTiles from a new
deployed frontend origin.

Private metadata should use a separate backend route, for example:

```text
GET /api/shared-datasets/metadata-url?slug=&version=&locale=
```

The route should return `Cache-Control: no-store`, validate the requested asset
against catalog and release-index data, apply app-owned authorization, and sign
only the exact metadata sidecar path selected by the release index. Do not sign
caller-provided bucket object paths.

## Feature Metadata API

The metadata API is an IAP-protected Cloud Run service. Initial access is
SkyTruth-only for all assets, even when the underlying catalog asset is public.
While Firestore metadata serving is inactive, otherwise valid lookup requests
return `409 index_not_ready`; use release metadata sidecars for active
consumer workflows.

Lookup endpoint:

```http
POST /v1/assets/{slug}/releases/{release}:lookup
```

Use `release=latest` for convenience; every response includes the concrete
`resolved_release`. Persist that resolved release in downstream lineage when a
result must be reproducible.

Request:

```json
{
  "ids": ["1", "2"],
  "fields": ["name", "source_id"]
}
```

`fields` omitted or `null` returns all properties. `fields: []` returns only
identifiers, hashes, and provenance. Provenance is included by default; pass
`"include_provenance": false` only when a caller explicitly wants a smaller
response.

Limits:

- 500 feature IDs per request.
- 500 projected fields per request.
- 10 MiB maximum JSON response.

If a response would exceed the size limit, the service returns
`413 response_too_large`; request fewer IDs or explicit fields. Missing feature
IDs are item-level `"found": false` items in a `200` response. Unknown assets or
releases return `404`; unloaded indexes return `409 index_not_ready`; invalid
fields return `400`.

## Migration Checklist

When moving an existing app to shared-datasets:

1. Search for direct shared bucket URLs, hardcoded PMTiles paths, catalog reads,
   `access_tier`, `Cloud-CDN-Cookie`, and existing PMTiles session routes.
2. Replace browser PMTiles URLs with catalog-derived `pmtiles_url` or the
   tiered `tiles.skytruth.org` URL shape.
3. Replace public metadata sidecar fetches with release-index-derived
   `/artifacts/{bucket-object-path}` URLs.
4. Add an authorized backend metadata URL route before exposing private
   metadata sidecars.
5. Use the TypeScript SDK for browser/private PMTiles work and the Python SDK
   for backend data downloads.
6. Preserve citation, license, access tier, and freshness metadata in consumer
   config where the UI or outputs show provenance.
7. Bump any layer/config cache key that may contain old direct GCS URLs.
8. Run the consumer repo's lint, type, test, and production build checks.
9. Record old path, new path, changed files, and validation results in the PR.

## Testing Checklist

Use the checks that match the integration surface.

Frontend PMTiles:

- Public PMTiles URLs use `https://tiles.skytruth.org/pmtiles/public/...`.
- Private PMTiles URLs use `https://tiles.skytruth.org/pmtiles/private/...`.
- Catalog-derived PMTiles URLs preserve catalog `access_tier`.
- Direct shared-dataset browser PMTiles URLs do not point at
  `storage.googleapis.com`.
- Private layers call the session endpoint before mounting.
- PMTiles range requests include `credentials: "include"` for private layers.
- Public metadata sidecars use `https://tiles.skytruth.org/artifacts/...`.
- Private metadata URL routes return `Cache-Control: no-store` and never sign
  caller-provided object paths.

Backend data:

- The Python dependency installs in the actual production build path.
- Runtime identity has bucket `roles/storage.objectViewer`.
- Code uses ADC or managed identity, not JSON keys.
- Jobs that request `latest` persist `ref.resolved_id`.
- Cache paths are writable in the runtime environment.

Private cookie signing:

- The signer runtime has access to the configured signing-key secret.
- The secret value is decoded before HMAC use.
- The signed prefix is exactly `https://tiles.skytruth.org/pmtiles/private/`.
- The policy fields are ordered `URLPrefix`, `Expires`, `KeyName`,
  `Signature`.
- Secret material and cookie values are not logged.
- Deployed browser origins are exactly allowlisted for credentialed CORS.

## Security Rules

Do not:

- Expose GCS credentials to browser code.
- Store service account JSON keys in repositories, images, CI variables, or
  frontend config.
- Sign Cloud CDN cookies in frontend code.
- Log signing secrets, decoded key bytes, HMAC inputs, HMAC outputs, signed
  cookie values, or signed URLs.
- Treat `_scratch/` bucket objects as canonical dataset contracts.
- Mirror shared PMTiles into consuming projects.
- Assume `public` when catalog `access_tier` is missing or malformed.

Do:

- Use catalog metadata as the source of truth.
- Use runtime service accounts and ADC.
- Pin SDK dependencies for production.
- Record `DatasetRef.resolved_id` for reproducible backend runs.
- Keep PMTiles browser access on `tiles.skytruth.org`.
- Scope private cookies to `/pmtiles/private` and a 24-hour TTL.
