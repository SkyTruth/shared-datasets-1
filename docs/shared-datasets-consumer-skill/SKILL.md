---
name: shared-datasets-consumer
description: >-
  Use when integrating shared-datasets-1 into a SkyTruth consumer repo or app,
  including choosing between the Python SDK and TypeScript SDK, replacing direct
  GCS or hardcoded PMTiles URLs, using catalog.json-driven layer/search/config
  metadata, adding signed-cookie restricted PMTiles access, preserving
  DatasetRef.resolved_id lineage, or preparing focused consumer adoption PRs.
---

# Shared Datasets Consumer Integration

Use this skill when the current consumer repo or app needs to adopt
shared-datasets with the smallest safe change. The job is adoption, not
redesign: find hardcoded shared dataset access paths, replace them with the
stable shared access contract, and package each consumer change as a focused PR.

Before changing a consumer repo, re-check the current shared catalog and
upstream shared-datasets docs when the change depends on a specific asset list,
access tier, browser origin, runtime service account, or signing grant. Treat
examples here as patterns, not live asset allowlists.

## Upstream Sources

Prefer a local checkout of the upstream shared-datasets-1 repo when available.
Otherwise use these sources:

```text
Canonical repo: https://github.com/SkyTruth/shared-datasets-1
Consumer guide: docs/consumer-guide.md
Python SDK README: api/python/README.md
TypeScript SDK README: api/typescript/README.md
Machine catalog: https://tiles.skytruth.org/_catalog/web/catalog.json
Catalog CSV: https://tiles.skytruth.org/_catalog/shared-datasets-catalog.csv
Restricted PMTiles reference: docs/shared-datasets-consumer-skill/references/private-pmtiles-signed-cookies.md
```

Do not guess current access tiers, CORS origins, reader service accounts, signer
grants, or private asset lists from memory.

## Choose The API

| Consumer surface | Use |
|---|---|
| Browser map PMTiles, public/private/internal | TypeScript SDK or catalog-derived `pmtiles_url`. |
| Backend route that issues restricted PMTiles cookies | TypeScript SDK server entrypoint. |
| Backend service config generated from catalog JSON | TypeScript SDK catalog helpers or direct catalog JSON parsing. |
| Python jobs or services that download data files | Python SDK with the `gcs` extra. |
| Python jobs or services that need catalog resolution but not bytes | Python SDK `resolve_dataset` or `Catalog`. |
| Diagnostics and one-off backend checks | Python SDK CLI. |

Do not install the Python SDK for browser-only PMTiles work. Do not use the
TypeScript SDK for backend GCS downloads.

## Adoption Workflow

1. Search the consumer repo for hardcoded shared dataset URLs, PMTiles paths,
   dataset slugs, catalog reads, `access_tier`, `Cloud-CDN-Cookie`,
   `pmtiles/session`, and ad hoc GCS access.
2. Classify each hit by runtime surface:
   - Browser PMTiles should use `https://tiles.skytruth.org/pmtiles/{tier}/{slug}.pmtiles`
     or catalog JSON `pmtiles_url`.
   - Private and internal browser PMTiles need a backend signed-cookie session endpoint.
   - Browser or service catalog discovery should use
     `https://tiles.skytruth.org/_catalog/web/catalog.json` or an app-owned API.
   - Backend data downloads should use the Python SDK and ADC.
3. Make the smallest coherent replacement. Prefer catalog-derived URLs or a
   tiny helper over broad map-layer refactors.
4. Keep PRs focused by surface and dataset. Do not combine unrelated UI,
   infrastructure, dependency, or formatting changes with adoption work.
5. In the PR description, call out the old access path, new access path, changed
   files, and validation run.

## TypeScript PMTiles Pattern

Use this when the consumer repo has browser PMTiles, restricted PMTiles, or a
backend route that issues PMTiles cookies.

The TypeScript package is published on npm as `@skytruth/shared-datasets`. If
`npm install @skytruth/shared-datasets` returns 404, verify the registry, scope,
and package name before using a local-path install. Use local-path installs only
for development against unreleased local changes, and do not commit that local
path to production consumers.

Browser-safe imports:

```ts
import {
  ensurePmtilesCdnSession,
  getPmtilesFetchCredentials,
  resolveSharedDatasetPmtilesRef
} from "@skytruth/shared-datasets";
```

Server-only imports for the consumer backend route:

```ts
import {
  decodePmtilesCdnSigningKey,
  getExpiredPmtilesCookies,
  getPrivatePmtilesSessionCookies
} from "@skytruth/shared-datasets/server";
```

Rules:

- Use catalog `pmtiles_url` when available; it is already the tiered
  `tiles.skytruth.org` URL.
- Preserve release metadata sidecar references in PMTiles layer config when
  present; labels and feature inspectors should read the selected metadata
  sidecar instead of PMTiles source-native fields, and should use
  `review_state` values for confidence cues.
- Reject missing or unknown `access_tier`; do not silently default to public.
- Public PMTiles do not need a cookie.
- Private and internal PMTiles require the session endpoint before mounting the layer.
- Every restricted PMTiles byte-range request must include
  `credentials: "include"`.
- Browser code must never receive GCS credentials, service account keys, raw
  signing key bytes, or full signed cookie values.
- Do not import `@skytruth/shared-datasets/server` into browser bundles.

Roll out restricted PMTiles in this order: replace direct PMTiles URLs with
catalog-derived `pmtiles_url`, bump layer-config cache keys, add the session
endpoint, add credentialed PMTiles range fetches, call the session endpoint
before mounting restricted layers, verify CSP/CORS, then run lint, type, test,
and build checks.

## Python Backend Data Pattern

Use this when backend/server Python code needs actual data files, a canonical
`gs://` URI, catalog-driven format resolution, or durable lineage.

Install the SDK with the GCS extra for data downloads:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@<tag-or-sha>#subdirectory=api/python"
```

For production, pin a tag or commit SHA. Verify the actual install surface can
reach the dependency; Docker builds and GitHub Actions may differ from local
development.

Fetch a dataset with one call:

```python
from skytruth_shared_datasets import fetch_dataset

ref = fetch_dataset("wdpa-marine", "fgb")
path = ref.cache_path
resolved_id = ref.resolved_id
```

Resolve without downloading:

```python
from skytruth_shared_datasets import resolve_dataset

ref = resolve_dataset("wdpa-marine", "pmtiles")
print(ref.gs_uri)
print(ref.url)
print(ref.resolved_id)
```

Credential rules:

- Use Application Default Credentials and a runtime service account.
- Grant the runtime identity `roles/storage.objectViewer` on
  `gs://skytruth-shared-datasets-1`.
- Do not create service account JSON keys.
- When callers request `version="latest"` and lineage matters, persist
  `ref.resolved_id`, not `<slug>@latest` and not a cache-path-derived value.
- When both bytes and lineage are needed, call `fetch_dataset(...)` once and use
  both `ref.cache_path` and `ref.resolved_id`.

## Catalog Discovery

Use catalog JSON for browser and service layer configuration:

```text
https://tiles.skytruth.org/_catalog/web/catalog.json
```

Minimum contract for consumer layer/config code:

- Read the top-level `assets` array; do not infer assets from bucket paths.
- Use `slug`, `title`, `status`, `access_tier`, `available_formats`,
  `canonical_format`, `has_pmtiles`, `pmtiles_url`, `docs_url`,
  `release_index_url`, `latest_release`, `last_updated`, `citation`, `license`,
  `bounds`, `geometry_type`, `feature_metadata`, and `feature_identity` when
  present.
- Use only `status="active"` for default production layer lists.
- If a UI intentionally shows non-active assets, display `consumer_guidance`.
- Resolve relative docs and release-index URLs against
  `https://tiles.skytruth.org/_catalog/web/`.
- Treat `latest_release.date` as the freshest release date when present;
  otherwise use `last_updated`.

Do not build new behavior on direct
`https://storage.googleapis.com/skytruth-shared-datasets-1/_catalog/...` reads.

## Search Targets

Look for:

```text
storage.googleapis.com/skytruth-shared-datasets-1
gs://skytruth-shared-datasets-1
tiles.skytruth.org/pmtiles/
tiles.skytruth.org/_catalog/
catalog.json
shared-datasets-catalog.csv
.pmtiles
access_tier
Cloud-CDN-Cookie
pmtiles/session
skytruth_shared_datasets
skytruth-shared-datasets
@skytruth/shared-datasets
```

Prefer replacing only the access path first. Do not refactor unrelated map-layer
behavior.

## Minimum Patch Checklist

- Apply only the checklist items for surfaces that exist in the consumer repo.
- Replace direct `storage.googleapis.com` PMTiles sources with catalog-derived
  `pmtiles_url` or the tiered `tiles.skytruth.org` URL shape.
- If the repo reads the shared catalog, parse `access_tier` and do not preserve
  old direct GCS `pmtiles_url` overrides.
- If the repo reads public catalog files, use `https://tiles.skytruth.org/_catalog/`
  or an app-owned API.
- Bump any config cache key that could retain old PMTiles URLs.
- If restricted PMTiles are possible, add a backend session endpoint using the
  TypeScript server helpers.
- If restricted PMTiles are possible, ensure PMTiles range requests include
  browser credentials and restricted layers wait for the session endpoint.
- If backend Python code downloads shared data, add the Python SDK dependency
  and use `fetch_dataset("<slug>", "<format>")`.
- If backend code runs in GCP, confirm the runtime identity has
  `roles/storage.objectViewer` on the shared bucket.
- If backend code signs restricted PMTiles cookies, confirm the signer runtime has
  access to the shared signing-key secret.
- Do not expose GCS credentials to browser code.

## Tests

Add focused tests that prove the surfaces touched:

- PMTiles URLs use `https://tiles.skytruth.org/pmtiles/public/...`.
- Private PMTiles URLs use
  `https://tiles.skytruth.org/pmtiles/private/...`.
- Internal PMTiles URLs use
  `https://tiles.skytruth.org/pmtiles/internal/...`.
- Catalog-derived PMTiles URLs use `access_tier`; include public, private, and
  internal fixture rows.
- Browser/service config reads use
  `https://tiles.skytruth.org/_catalog/web/catalog.json` or an app-owned API.
- PMTiles layer source configuration does not hardcode
  `storage.googleapis.com`.
- The PMTiles base URL is configurable by environment when locally wrapped.
- Public-tier PMTiles do not add signed-cookie or service-account auth.
- Private signed-cookie PMTiles reject anonymous users, set `Cache-Control:
  no-store`, set `Cloud-CDN-Cookie` for authorized users, send browser fetch
  credentials, and do not expose GCS credentials.
- Backend Python code that needs data files uses `fetch_dataset(...)`, not
  service account keys.
- Backend Python code that needs only metadata, a durable `gs://` URI, or a
  browser-facing URL uses `resolve_dataset(...)`.
- Backend Python code that requests `version="latest"` and records lineage
  persists `ref.resolved_id`.

## Non-Goals

Do not implement Cloud CDN infrastructure in the consumer repo.
Do not store Cloud CDN signing keys in the consumer repo or Terraform state.
Do not create service account keys.
Do not expose GCS credentials to the browser.
Do not mirror shared PMTiles into the consumer project.
Do not make broad UI rewrites while doing minimal shared-datasets adoption.
