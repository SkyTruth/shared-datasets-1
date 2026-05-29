---
title: SkyTruth Shared Datasets Consumer Guide
description: How downstream applications discover, fetch, cite, and display shared SkyTruth datasets, including PMTiles CDN signed cookies and the Python and TypeScript SDKs.
last_updated: 2026-05-29
audience: SkyTruth application developers, data pipeline owners, and map frontend maintainers
---

# SkyTruth Shared Datasets Consumer Guide

SkyTruth Shared Datasets is the common access layer for reusable datasets that
multiple SkyTruth projects need to discover, cite, fetch, or display. The
shared bucket stores the data. This repository defines the catalog, metadata,
access contracts, SDK, and operational guardrails that keep those assets stable
for consumers.

This guide is for downstream applications and data pipelines. It explains the
consumer contract, the PMTiles CDN and signed-cookie model, and the Git-hosted
Python and TypeScript SDKs. Maintainer-only publishing and infrastructure
procedures live in
[GCP asset operations](./gcp-asset-operations.md),
[PMTiles browser access](./pmtiles-cdn.md), and
[catalog web preview](./catalog-web-preview.md).

## Quick Start

Choose the smallest integration that matches your runtime.

| Scenario | Do this |
|---|---|
| Browser displays known public PMTiles | Build `https://tiles.skytruth.org/pmtiles/public/{slug}.pmtiles`, or use the TypeScript helper package for catalog-derived URLs. No cookie is required. |
| Browser may display private PMTiles | Use the TypeScript helper package, add a backend `/api/pmtiles/session` endpoint, and send PMTiles range requests with `credentials: "include"`. |
| Backend needs a data file | Install `skytruth-shared-datasets[gcs]` from GitHub and call `fetch_dataset(slug, format)` under a runtime service account. |
| Backend needs a URI or browser URL but not bytes | Call `resolve_dataset(slug, format)` and use the returned `DatasetRef`. |
| App needs search or layer config | Fetch the public catalog from `https://tiles.skytruth.org/_catalog/`, or have a backend load the catalog with ADC and expose an app-owned config API. Preserve `access_tier`, `citation`, and `last_updated`. |

For production consumers, pin the Git dependency to a tag or commit SHA and run
with Application Default Credentials. Do not use service account JSON keys.
Do not build new integrations on anonymous `storage.googleapis.com` reads from
`skytruth-shared-datasets-1`; direct public bucket access is a temporary proof
of concept bypass and is planned to be removed.

## Consumer Contract

Use the shared catalog as the source of truth. Do not infer dataset location,
access tier, format, citation, or freshness from hand-built paths.

| Need | Use |
|---|---|
| Find assets, formats, access tiers, citations, and update cadence | `https://tiles.skytruth.org/_catalog/shared-datasets-catalog.csv`, SDK `Catalog.load_gcs()`, repo catalog files, or an app-owned API generated from the catalog |
| Display latest PMTiles in a browser | `https://tiles.skytruth.org/pmtiles/{access_tier}/{slug}.pmtiles` |
| Display private PMTiles in a browser | Tiered CDN URL plus a backend-issued `Cloud-CDN-Cookie` |
| Fetch data from backend code | `skytruth-shared-datasets[gcs]` SDK with Application Default Credentials |
| Resolve a durable object identity | SDK `DatasetRef.gs_uri` |
| Record lineage for `latest` | SDK `DatasetRef.resolved_id` |
| Cite source data | Catalog `citation` field |
| Inspect dataset-specific schema and notes | `https://tiles.skytruth.org/_catalog/web/docs/assets/{asset-slug}.md`, `docs/assets/{asset-slug}.md` in this repo, or docs content served by a consumer-controlled website or backend |

Canonical catalog identities:

```text
https://tiles.skytruth.org/_catalog/shared-datasets-catalog.csv
https://tiles.skytruth.org/_catalog/web/catalog.json
https://tiles.skytruth.org/_catalog/web/index.html
https://tiles.skytruth.org/_catalog/web/docs/assets/{asset-slug}.md
https://tiles.skytruth.org/_catalog/releases/{asset-slug}.json
gs://skytruth-shared-datasets-1/_catalog/shared-datasets-catalog.csv
gs://skytruth-shared-datasets-1/_catalog/web/catalog.json
catalog/shared-datasets-catalog.csv
docs/assets/{asset-slug}.md
```

Some `https://storage.googleapis.com/skytruth-shared-datasets-1/...` URLs may
work during the proof-of-concept public bucket window. Treat those URLs as
diagnostic or transitional only. The durable consumer path is authenticated GCS
from backend code, `tiles.skytruth.org/_catalog/` for public catalog files,
app-owned catalog/config APIs, and the PMTiles CDN for browser map tiles.

### Access Tiers

`access_tier` is part of the consumer contract.

| Tier | Meaning | Browser PMTiles behavior | Backend data behavior |
|---|---|---|---|
| `public` | Asset is intended for public consumer use through approved access surfaces. | Use the public CDN URL directly. No cookie is required. | Use the SDK and ADC; do not rely on anonymous direct GCS reads. |
| `private` | Asset is discoverable in the catalog but bytes require authorized access. | Use the private CDN URL only after the app backend issues a signed cookie for an allowed user. | Use the SDK with a runtime service account that has bucket read access. |

Private assets may have license, terms, or redistribution constraints. Treat the
asset documentation and catalog `citation`, `license`, and `notes` as part of
the data contract.

### Formats

Shared datasets publish one canonical format and may publish companion formats.
Common consumer-facing formats are:

| Format | Consumer use |
|---|---|
| `fgb` | Canonical vector data for backend processing and spatial joins. |
| `pmtiles` | Browser map tiles and visual exploration. |
| `csv` | Non-geometry tables. |
| `geojson` / `ndgeojson` | Small previews, interchange, or debugging. |
| `cog` | Cloud Optimized GeoTIFF raster data. |
| `zarr` | Chunked multidimensional array products. |

Check `available_formats` before requesting a format. Do not assume an asset
has PMTiles just because it is spatial, and do not use PMTiles as analytical
source data when the canonical vector or raster format is available.

### Version And Freshness Semantics

`latest/` is a convenience pointer to the current release. It is not a durable
lineage value. When a backend process requests `version="latest"` and records
lineage, persist the resolved identity returned by the SDK, for example:

```text
wdpa-marine@2026-05-02
```

Dated releases use exact `releases/YYYY-MM-DD/` objects. They are the right
choice for reproducible historical runs. Dated PMTiles release URLs resolve to
their exact GCS object URL, not to the CDN alias, because the CDN URL is reserved
for latest PMTiles. Treat those exact GCS URLs as object identities unless your
runtime is authenticated; they are not a promise of anonymous public access.

## Catalog Discovery

For a user-facing catalog, use the public `tiles.skytruth.org/_catalog/` route
or serve catalog content from a docs website or application backend that can
read the shared bucket with ADC. Do not send browsers directly to
`storage.googleapis.com` bucket URLs for catalog JSON, Markdown docs, or data
files.

For application code, use one of these sources:

| Source | Best for | Notes |
|---|---|---|
| Catalog CSV | Python SDK, backend services, simple scripts, CI checks | Stable contract, easy to parse. Load from `tiles.skytruth.org/_catalog/`, GCS with ADC, or the repo checkout. |
| Catalog JSON | Browser apps, TypeScript helpers, and service config APIs | Includes web-friendly fields such as `pmtiles_url`, docs URLs, optional bounds, geometry type, and release metadata. Serve it from `tiles.skytruth.org/_catalog/`, your app, or a docs site, not from direct public GCS. |
| Asset Markdown docs | Human-readable schema, source notes, caveats, citations | Use for docs pages and analyst-facing context. |

Important catalog fields:

| Field | Use |
|---|---|
| `asset_slug` | Stable identifier for SDK calls and PMTiles URLs. |
| `title` | Display name. |
| `status` | Consumer lifecycle state: `active`, `deprecated`, `superseded`, or `retired`. |
| `consumer_guidance` | Migration or usage guidance for non-active assets. |
| `access_tier` | Required for PMTiles URL construction and authorization decisions. |
| `canonical_path` | Durable GCS object identity for the canonical latest object. |
| `canonical_format` | Format to use for primary analytical work. |
| `available_formats` | Semicolon-separated list of formats available for the latest release. |
| `update_cadence` | Expected refresh cadence. |
| `citation` | Preferred citation for consumer outputs. |
| `license` | License or terms summary. |
| `notes` | Asset-specific operational and data-quality notes. |

## CDN And Cookie Access

Use the PMTiles CDN for browser map layers. Browser code should not read shared
PMTiles from direct `storage.googleapis.com` URLs and should never receive GCS
credentials.

### URL Shape

Latest PMTiles use this stable URL shape:

```text
https://tiles.skytruth.org/pmtiles/{access_tier}/{asset_slug}.pmtiles
```

Examples:

```text
https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles
https://tiles.skytruth.org/pmtiles/private/iucn-mammal-ranges.pmtiles
```

If a frontend already knows a public asset slug, a tiny helper is enough:

```ts
const SHARED_PMTILES_BASE_URL =
  process.env.NEXT_PUBLIC_SHARED_PMTILES_BASE_URL ??
  "https://tiles.skytruth.org/pmtiles";

export function sharedPmtilesUrl(
  assetSlug: string,
  accessTier: "public" | "private" = "public"
) {
  return `${SHARED_PMTILES_BASE_URL}/${accessTier}/${assetSlug}.pmtiles`;
}
```

If a config API derives map layers from the shared catalog, parse
`access_tier` and emit exactly the tiered URL. Reject missing or unknown tiers
instead of silently defaulting private assets to public.

### Public PMTiles

Public PMTiles can be loaded directly from the public tier URL:

```ts
const sourceUrl = sharedPmtilesUrl("wdpa-marine", "public");
```

No cookie, service account, or signed URL is required for public browser map
access.

### Private PMTiles Overview

Private PMTiles use a browser cookie issued by the consuming application
backend. The backend authenticates the user, authorizes access, signs a Cloud
CDN cookie policy, and sets `Cloud-CDN-Cookie` for the shared CDN domain.

The private serving contract is:

```text
Signed prefix: https://tiles.skytruth.org/pmtiles/private/
Cookie name:   Cloud-CDN-Cookie
Key name:      shared-datasets-pmtiles-v1
Signing key:   loaded by the backend from its configured secret store
TTL:           24 hours
```

The cookie is scoped to the private PMTiles prefix, not to an individual asset.
Use application authorization to decide whether a user may receive that cookie.
For the first rollout of a private layer, limit access to SkyTruth or admin
users unless the consuming app has a more specific entitlement model.

### Required Backend Endpoint

Applications that may display private shared PMTiles should expose an endpoint
such as:

```text
GET /api/pmtiles/session?tier=public
GET /api/pmtiles/session?tier=private
```

Required behavior:

| Request | Response |
|---|---|
| `tier=public` | Return `204 No Content` without setting a cookie. |
| `tier=private` and unauthenticated user | Return `401 Unauthorized`. |
| `tier=private` and authenticated but unauthorized user | Return `403 Forbidden`. |
| `tier=private` and authorized user | Set `Cloud-CDN-Cookie`, return `204 No Content`, and set `Cache-Control: no-store`. |
| Unknown `tier` | Return `400 Bad Request`. |

The endpoint must run under a service account that has
`roles/secretmanager.secretAccessor` on the shared signing-key secret. This is
separate from bucket read access. Use the runtime identity that actually serves
the endpoint. Do not create service account JSON keys.

Cookie attributes:

```text
Domain=.skytruth.org
Path=/pmtiles/private
Secure
HttpOnly
SameSite=None
Max-Age=86400
Expires=<24 hours from now>
```

Set `Cache-Control: no-store` on the session response. Never log or return the
secret bytes, decoded key, unsigned policy, HMAC output, or complete cookie
value.

### Signing The Cookie

The cookie value contains four policy fields in this order:

```text
URLPrefix=<base64url-prefix>:Expires=<unix-seconds>:KeyName=shared-datasets-pmtiles-v1:Signature=<base64url-hmac>
```

Sign the unsigned policy string with HMAC-SHA1. The HMAC key is the decoded raw
16-byte key from Secret Manager, not the encoded secret text.

TypeScript example:

```ts
import crypto from "node:crypto";

const CDN_PRIVATE_PREFIX = "https://tiles.skytruth.org/pmtiles/private/";
const CDN_KEY_NAME = "shared-datasets-pmtiles-v1";

function toBase64Url(value: Buffer | string) {
  return Buffer.from(value)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
}

function decodeCdnKey(secretValue: string) {
  return Buffer.from(secretValue.replace(/-/g, "+").replace(/_/g, "/"), "base64");
}

export function signCloudCdnCookie(secretValue: string, expiresUnixSeconds: number) {
  const urlPrefix = toBase64Url(CDN_PRIVATE_PREFIX);
  const unsignedPolicy =
    `URLPrefix=${urlPrefix}:Expires=${expiresUnixSeconds}:KeyName=${CDN_KEY_NAME}`;
  const signature = crypto
    .createHmac("sha1", decodeCdnKey(secretValue))
    .update(unsignedPolicy)
    .digest("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");

  return `${unsignedPolicy}:Signature=${signature}`;
}
```

This example preserves base64 padding if the runtime emits it. That matches the
shared-datasets signing contract.

### Required Frontend Behavior

Before mounting a private PMTiles layer, call the backend session endpoint with
credentials included:

```ts
const response = await fetch("/api/pmtiles/session?tier=private", {
  credentials: "include"
});

if (!response.ok) {
  throw new Error("Private PMTiles access was not granted.");
}
```

Then load the private CDN URL. Every PMTiles range request must include browser
credentials so the cross-site cookie is sent to `tiles.skytruth.org`:

```ts
await fetch(pmtilesUrl, {
  headers: { Range: "bytes=0-16383" },
  credentials: "include"
});
```

Many PMTiles libraries perform internal range requests for headers,
directories, and tiles. If the library hides those requests, wrap or subclass
its fetch implementation so all PMTiles requests include
`credentials: "include"`.

Keep the application content security policy allowing:

```text
connect-src https://tiles.skytruth.org
```

### CORS And Allowed Origins

Credentialed PMTiles requests require exact allowed origins. Wildcards such as
`*` and literal wildcard domains such as `https://*.skytruth.org` do not work
with credentialed browser requests.

The exact CDN allowed-origin list is operational infrastructure, not a consumer
contract. If your frontend origin is not already approved, request a
shared-datasets infrastructure PR that adds the exact origin through the
protected production workflow before relying on private PMTiles in that
environment.

### CDN Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Public PMTiles URL returns `404` | Slug is wrong, asset is not active, asset has no PMTiles, or tier does not match catalog. | Resolve from catalog instead of building from stale config. |
| Private PMTiles returns `403` | Missing, expired, malformed, or unauthorized cookie. | Re-call the session endpoint and verify the PMTiles range request includes credentials. |
| Browser says credentials are not allowed by CORS | Origin is not exactly allowlisted or response inherited non-credentialed CORS headers. | Add the exact origin in shared-datasets Terraform and redeploy. |
| Cookie exists but is not sent | Cookie domain/path/SameSite/Secure settings are wrong, or request is not HTTPS. | Use `Domain=.skytruth.org`, `Path=/pmtiles/private`, `SameSite=None`, `Secure`, and HTTPS. |
| PMTiles library still fails after session succeeds | Internal byte-range requests are missing `credentials: "include"`. | Override the PMTiles fetch implementation. |
| Private layer works locally but not in deployment | Deployment origin is missing from the CDN allowlist. | Add the deployed origin exactly. |

## Git And SDKs

The TypeScript package is distributed as `@skytruth/shared-datasets` on npm.
Use it for browser-safe catalog JSON parsing, PMTiles URL credential selection,
browser CDN session requests, access-tier lookups, and server-only Cloud CDN
cookie signing helpers. The main entrypoint is browser-safe:

```ts
import {
  ensurePmtilesCdnSession,
  getPmtilesFetchCredentials,
  resolveSharedDatasetPmtilesRef
} from "@skytruth/shared-datasets";
```

The server entrypoint is for backend routes only:

```ts
import {
  decodePmtilesCdnSigningKey,
  getPrivatePmtilesSessionCookies
} from "@skytruth/shared-datasets/server";
```

The TypeScript package does not own application authentication, routes, secret
stores, logging, UI behavior, or HTTP error translation. Keep those in the
consumer application.

The Python SDK is distributed from the `SkyTruth/shared-datasets-1` GitHub
repository, not PyPI. Use it for backend services, batch jobs, scheduled
pipelines, and CI tasks that need catalog resolution or data downloads.

### Installation

For backend/server code that needs authenticated GCS access:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

For catalog resolution only, without the GCS optional dependency:

```bash
pip install "skytruth-shared-datasets @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

For production consumers, pin a tag or commit SHA instead of `main`:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@<tag-or-sha>#subdirectory=api/python"
```

If the runtime installer does not have `git`, use a pinned public archive URL:

```bash
pip install "skytruth-shared-datasets[gcs] @ https://github.com/SkyTruth/shared-datasets-1/archive/<tag-or-sha>.zip#subdirectory=api/python"
```

For local development inside this repository:

```bash
pip install -e "api/python[gcs]"
```

Before committing a GitHub dependency URL, verify the actual install surface.
Docker builds, GitHub Actions, and production deploys may use different
requirement files and may not have `git`, SSH credentials, or cross-repository
tokens available.

### IAM And Credentials

Backend consumers should use Application Default Credentials with a runtime
service account. Do not create service account JSON keys and do not put
credentials in code, environment variables, images, or Git history.

The consuming runtime service account needs:

```text
roles/storage.objectViewer on gs://skytruth-shared-datasets-1
```

The shared-datasets infrastructure can provision project-scoped reader service
accounts when a consumer project needs one. If your project has an existing
runtime identity, it is also acceptable to grant that identity bucket read
access. Use the approved internal infrastructure outputs or maintainer runbook
for exact principal names. Run the service, job, or CI workflow as the chosen
identity, then use the SDK without credential setup code.

Private PMTiles cookie signing needs a separate grant:

```text
roles/secretmanager.secretAccessor on the PMTiles CDN signing-key secret
```

Grant that only to the backend runtime that issues cookies.

### Fetch A Dataset

Use `fetch_dataset` when backend code needs local bytes:

```python
from skytruth_shared_datasets import fetch_dataset

ref = fetch_dataset("wdpa-marine", "fgb")

path = ref.cache_path
resolved_id = ref.resolved_id
canonical_uri = ref.gs_uri
```

`fetch_dataset` loads the shared catalog through authenticated GCS, resolves the
requested format, downloads the current object, and returns a `DatasetRef`.

Key fields:

| Field | Meaning |
|---|---|
| `ref.cache_path` | Local downloaded file path. |
| `ref.gs_uri` | Durable GCS object identity. |
| `ref.url` | Browser-facing URL for the resolved object. PMTiles latest defaults to the CDN URL. |
| `ref.access_tier` | `public` or `private`. |
| `ref.last_updated` | Catalog or release date used for the resolution. |
| `ref.resolved_id` | Stable lineage value, such as `wdpa-marine@2026-05-02`. |

When both bytes and lineage are needed, call `fetch_dataset` once and use both
`ref.cache_path` and `ref.resolved_id`. Do not separately call
`resolve_dataset` and `fetch_dataset` for the same work.

### Resolve Without Downloading

Use `resolve_dataset` when backend code needs metadata, a canonical GCS URI, or
a PMTiles browser URL but not local bytes:

```python
from skytruth_shared_datasets import resolve_dataset

ref = resolve_dataset("wdpa-marine", "pmtiles")

print(ref.gs_uri)       # gs://skytruth-shared-datasets-1/...
print(ref.url)          # https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles
print(ref.access_tier)  # public
print(ref.resolved_id)  # wdpa-marine@2026-05-02
```

### Work With The Catalog Directly

Use `Catalog` when a service needs to list, search, or resolve multiple assets:

```python
from skytruth_shared_datasets import Catalog

catalog = Catalog.load_gcs()

for asset in catalog.search(format="pmtiles", access_tier="public"):
    print(asset.slug, asset.title, asset.citation)

ref = catalog.resolve("wdpa-marine", "pmtiles")
downloaded = catalog.fetch("wdpa-marine", "fgb", access="gcs")
```

For local tests, `Catalog.load()` can load from a local CSV path or an HTTPS
URL you control. Production backend code should use `Catalog.load_gcs()` or the
top-level helper functions with ADC. Do not rely on the SDK's `public_gcs` URL
strategy for production data files; anonymous direct bucket reads are being
removed.

### Versions

Fetch an exact dated release when reproducibility matters:

```python
from skytruth_shared_datasets import fetch_dataset

ref = fetch_dataset("wdpa-marine", "fgb", version="2026-05-02")
print(ref.resolved_id)  # wdpa-marine@2026-05-02
```

Dated release resolution uses the bucket release index. If a requested date or
format is unavailable, the SDK raises `UnsupportedVersionError` or
`UnsupportedFormatError`.

### Cache Behavior

By default, downloads are cached under:

```text
~/.cache/skytruth-shared-datasets/{slug}/{format}/{last_updated}/{filename}
```

Override the cache root with `SKYTRUTH_SHARED_DATASETS_CACHE` or the
`cache_dir` argument:

```python
ref = fetch_dataset("wdpa-marine", "fgb", cache_dir="/tmp/shared-datasets-cache")
```

Pass `force=True` to re-download an object even when the cache path exists.

### Command-Line Interface

The package installs `skytruth-datasets`:

```bash
skytruth-datasets list
skytruth-datasets list --access-tier public --format pmtiles
skytruth-datasets url wdpa-marine --format pmtiles
skytruth-datasets url wdpa-marine --format pmtiles --url-strategy public-gcs
skytruth-datasets fetch wdpa-marine --format fgb --access gcs
skytruth-datasets versions wdpa-marine --access gcs
```

The CLI is useful for diagnostics and one-off checks. Production backend code
should use the Python API so it can preserve `DatasetRef` metadata.

### SDK Error Handling

Handle these errors at integration boundaries:

| Error | Meaning |
|---|---|
| `CatalogLoadError` | Catalog could not be loaded or parsed. Check ADC, IAM, source URL, and network access. |
| `DatasetNotFoundError` | Unknown `asset_slug`. Refresh the catalog or fix the slug. |
| `UnsupportedFormatError` | The asset does not publish the requested format. Check `available_formats`. |
| `UnsupportedVersionError` | Requested version is not `latest` and not an indexed `YYYY-MM-DD` release. |
| `FetchError` | Object download failed. Check IAM, object existence, network, and cache filesystem permissions. |

## Integration Patterns

### Browser-Only Public Layer

Use this when the app displays known public PMTiles and does not need backend
data files.

1. Store the asset slug and access tier in config, or resolve them from the
   catalog JSON.
2. Build `https://tiles.skytruth.org/pmtiles/public/{slug}.pmtiles`.
3. Keep `https://tiles.skytruth.org` in `connect-src`.
4. Do not install the SDK.
5. Do not add cookies, service accounts, or GCS credentials.

### Browser Layer With Private PMTiles

Use this when a logged-in user may view private shared PMTiles.

1. Resolve `asset_slug` and `access_tier` from the catalog.
2. Add an authorized backend session endpoint.
3. Grant the backend runtime service account access to the signing-key secret.
4. Request an approved exact-origin CORS update for the frontend origin.
5. Before mounting a private layer, call the session endpoint with
   `credentials: "include"`.
6. Ensure every PMTiles range request also uses `credentials: "include"`.
7. Test anonymous denial, authorized success, expired cookie behavior, and CORS.

### Backend Data Pipeline

Use this when code needs actual data files.

1. Install `skytruth-shared-datasets[gcs]` from a pinned Git tag or SHA.
2. Run the job or service as a runtime service account with
   `roles/storage.objectViewer` on the shared bucket.
3. Call `fetch_dataset(slug, format)` once.
4. Use `ref.cache_path` for local processing.
5. Persist `ref.resolved_id`, `ref.gs_uri`, and the source `citation` when
   writing lineage, reports, model artifacts, or audit logs.

### Service Config API

Use this when a backend generates frontend layer configuration.

1. Load the shared catalog.
2. Validate each configured slug exists and is `active`.
3. Validate the required format is in `available_formats`.
4. Preserve `access_tier` in the generated response.
5. Emit the tiered PMTiles URL for latest PMTiles.
6. Include `citation`, `license`, and `last_updated` where the UI or API
   displays data provenance.
7. Bump any config cache key when changing URL generation from direct GCS to
   the CDN.

## Testing Checklist

Use the checks that match your integration surface.

Frontend PMTiles:

- Public PMTiles URLs use `https://tiles.skytruth.org/pmtiles/public/...`.
- Private PMTiles URLs use `https://tiles.skytruth.org/pmtiles/private/...`.
- Catalog-derived URLs use catalog `access_tier`; test one public fixture and
  one private fixture.
- Direct shared-dataset PMTiles URLs do not point at `storage.googleapis.com`.
- Private layers call the session endpoint before mounting.
- Every PMTiles range request includes `credentials: "include"`.
- Unauthorized private users receive `401` or `403`.
- Authorized private users receive `Cloud-CDN-Cookie` and `Cache-Control:
  no-store`.
- CSP allows `https://tiles.skytruth.org`.

Backend SDK:

- The dependency installs in the actual production build path.
- The runtime identity has bucket `roles/storage.objectViewer`.
- Code uses ADC or managed identity, not JSON keys.
- Requested slugs and formats are validated against the catalog.
- Jobs that request `latest` persist `ref.resolved_id`.
- Dated release requests use exact `YYYY-MM-DD` values.
- Cache paths are writable in the runtime environment.

Private cookie signing:

- The signer runtime has `roles/secretmanager.secretAccessor` on the configured
  signing-key secret.
- The secret value is decoded before HMAC use.
- The signed prefix is exactly `https://tiles.skytruth.org/pmtiles/private/`.
- The policy fields are ordered `URLPrefix`, `Expires`, `KeyName`, `Signature`.
- The cookie uses `Domain=.skytruth.org`, `Path=/pmtiles/private`, `Secure`,
  `HttpOnly`, and `SameSite=None`.
- Secret material and cookie values are not logged.
- Deployed browser origins are exactly allowlisted for credentialed CORS.

## Migration Checklist

When moving an existing app to shared-datasets:

1. Search for direct shared bucket URLs and old PMTiles helpers:

   ```text
   storage.googleapis.com/skytruth-shared-datasets-1
   tiles.skytruth.org/pmtiles/
   .pmtiles
   access_tier
   Cloud-CDN-Cookie
   pmtiles/session
   ```

2. Classify each use:

   | Existing use | Replacement |
   |---|---|
   | Browser PMTiles | Tiered CDN URL. |
   | Private browser PMTiles | Tiered CDN URL plus signed-cookie session endpoint. |
   | Backend data download | SDK `fetch_dataset`. |
   | Backend URI resolution | SDK `resolve_dataset` or `Catalog.resolve`. |
   | Docs-only reference | Update only if it would otherwise keep an obsolete access pattern alive. |

3. Keep the first PR narrow. Replace access paths before refactoring map layer
   behavior.
4. Preserve or add citation and lineage fields where consumer outputs depend on
   the data.
5. Run the consumer repo's lint, type, test, and production build checks.
6. Record old path, new path, changed files, and validation results in the PR.

## Security Rules

Do not:

- Expose GCS credentials to browser code.
- Store service account JSON keys in repositories, containers, CI variables, or
  frontend config.
- Sign Cloud CDN cookies in frontend code.
- Log signing secrets, decoded key bytes, HMAC inputs, HMAC outputs, signed
  cookie values, or signed URLs.
- Treat `_scratch/` bucket objects as canonical dataset contracts.
- Mirror shared PMTiles into consuming projects.
- Make private assets public without explicit approval.
- Assume `public` when catalog `access_tier` is missing or malformed.
- Use direct `storage.googleapis.com` browser PMTiles URLs as a fallback for
  shared-datasets layers.

Do:

- Use catalog metadata as the source of truth.
- Use runtime service accounts and ADC.
- Pin the SDK dependency for production.
- Record `DatasetRef.resolved_id` for reproducible runs.
- Keep PMTiles browser access on `tiles.skytruth.org`.
- Scope private cookies to `/pmtiles/private` and a 24-hour TTL.
- Request exact-origin CORS updates before deploying private PMTiles to a new
  frontend domain.

## References

- [PMTiles browser access](./pmtiles-cdn.md)
- TypeScript helpers: `@skytruth/shared-datasets` on npm
- [Python SDK README](https://github.com/SkyTruth/shared-datasets-1/tree/main/api/python)
- [Catalog web preview](./catalog-web-preview.md)
- [GCP asset operations](./gcp-asset-operations.md)
- [Asset layout and formats](./standards/asset-layout-and-formats.md)
- [Dataset taxonomy](./standards/dataset-taxonomy.md)
