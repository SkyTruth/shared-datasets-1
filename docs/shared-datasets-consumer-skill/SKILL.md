---
name: shared-datasets-consumer
description: Use when integrating a consuming SkyTruth project with shared-datasets-1 using the minimum safe change, especially replacing direct GCS PMTiles URLs with the shared PMTiles CDN contract or adding the Python resolver SDK to a backend.
---

# Shared Datasets Consumer Integration

Use this skill to make a consuming project use SkyTruth shared datasets with the
smallest safe change.

## Goal

Replace hardcoded dataset file paths, especially PMTiles paths pointing at
`storage.googleapis.com`, with the shared-datasets contract.

Shared datasets have two separate identities:

- Canonical object identity: `gs://skytruth-shared-datasets-1/...`
- Browser PMTiles access path:
  `https://tiles.skytruth.org/pmtiles/{asset}.pmtiles`

Today, `tiles.skytruth.org` may be served by a temporary project-controlled
redirector. A final redirected request to `storage.googleapis.com` is expected
until the signed-cookie CDN mode replaces the redirector. Do not treat PMTiles
as private during redirect mode.

Do not copy shared dataset files into the consuming repo. Do not edit the
`shared-datasets-1` repo from the consuming project.

## Minimum PMTiles Change

If the project only needs browser PMTiles, do not add the Python SDK to frontend
code.

Use configured CDN URLs:

```ts
const SHARED_PMTILES_BASE_URL =
  process.env.NEXT_PUBLIC_SHARED_PMTILES_BASE_URL ??
  "https://tiles.skytruth.org/pmtiles";

export function sharedPmtilesUrl(assetSlug: string) {
  return `${SHARED_PMTILES_BASE_URL}/${assetSlug}.pmtiles`;
}
```

Then replace direct GCS URLs like:

```text
https://storage.googleapis.com/skytruth-shared-datasets-1/.../latest/wdpa-marine.pmtiles
```

with:

```text
https://tiles.skytruth.org/pmtiles/wdpa-marine.pmtiles
```

or:

```ts
sharedPmtilesUrl("wdpa-marine")
```

This keeps the consuming project on the stable URL contract even if the current
serving mode is a `307 Temporary Redirect` to public GCS.

For WDPA MPA features, use `site_id` / source `SITE_ID` as the durable feature
identity. Do not build new UI behavior around `WDPAID`.

## Python Backend Option

Install the SDK from GitHub. It is not on PyPI yet.

```bash
pip install "skytruth-shared-datasets @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

For production, pin a tag or commit SHA instead of `main`.

For authenticated server-side GCS reads later:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

Minimal backend resolver:

```python
from functools import lru_cache
from skytruth_shared_datasets import Catalog

@lru_cache(maxsize=1)
def shared_catalog() -> Catalog:
    return Catalog.load()

def shared_dataset_url(asset_slug: str, format: str = "pmtiles") -> str:
    return shared_catalog().resolve(asset_slug, format=format).url

def shared_dataset_gs_uri(asset_slug: str, format: str = "fgb") -> str:
    return shared_catalog().resolve(asset_slug, format=format).gs_uri
```

Example:

```python
shared_dataset_url("wdpa-marine", "pmtiles")
# "https://tiles.skytruth.org/pmtiles/wdpa-marine.pmtiles"
```

## Future Private-Bucket / Signed-Cookie Path

Browser clients should not authenticate directly to private GCS.

When CDN signed cookies are enabled, add a backend endpoint such as:

```text
/api/pmtiles/session
```

That endpoint should issue or refresh the Cloud CDN signed cookie for:

```text
https://tiles.skytruth.org/pmtiles/
```

Frontend map initialization should:

1. Call `/api/pmtiles/session` before adding PMTiles layers.
2. Fetch PMTiles with credentials included if the PMTiles library exposes fetch
   options.
3. Retry session refresh once on CDN `403`.

Also add this to CSP `connect-src` if the app has CSP:

```text
https://tiles.skytruth.org
```

## Search Targets

Look for and replace:

```text
storage.googleapis.com/skytruth-shared-datasets-1
skytruth-shared-datasets-1
.pmtiles
wdpa
WDPAID
```

Prefer replacing only the PMTiles access path first. Do not refactor unrelated
map-layer behavior.

## Tests

Add focused tests that prove:

- PMTiles URLs use `https://tiles.skytruth.org/pmtiles/...`.
- PMTiles layer source configuration does not hardcode
  `storage.googleapis.com`.
- The PMTiles URL is configurable by environment.
- WDPA MPA selection/join logic uses `site_id`, not `WDPAID`.
- If a PMTiles session endpoint exists, map setup calls it before layer
  creation and retries once on `403`.

## Non-Goals

Do not implement Cloud CDN infrastructure in the consuming project.
Do not create service account keys.
Do not expose GCS credentials to the browser.
Do not mirror PMTiles into the consuming project.
Do not make broad UI rewrites while doing the minimal shared-datasets adoption.
