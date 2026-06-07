# skytruth-shared-datasets

Tiny Python SDK for backend and batch consumers of SkyTruth shared datasets.

Use this SDK when Python code needs to load the shared catalog, resolve a
published dataset object, download canonical data files, list available assets,
or preserve lineage for `latest` requests. Use the TypeScript SDK instead for
browser PMTiles session handshakes and Cloud CDN signed-cookie helpers.

## When To Use This SDK

| Need | Use |
|---|---|
| Backend code needs a local data file | `fetch_dataset(slug, format)` |
| Backend code needs a durable object identity but not bytes | `resolve_dataset(slug, format)` |
| Service code needs to list or search assets | `Catalog.load_gcs()` plus `catalog.search(...)` |
| CI or diagnostics need quick checks | `skytruth-datasets` CLI |
| Service code needs feature metadata by PMTiles `feature_id` | Feature metadata HTTP API, not this SDK |
| Browser map code needs PMTiles | TypeScript helpers or direct tiered CDN URLs, not this SDK |
| Backend route signs private PMTiles cookies | TypeScript server helpers, not this SDK |

The SDK keeps canonical GCS object identity, access tier metadata, browser URL
construction, downloads, cache paths, and lineage values together in a
`DatasetRef`.

## Installation

This package is currently distributed from GitHub, not PyPI. For production
consumers, pin a tag or commit SHA instead of `main`.

| Scenario | Install |
|---|---|
| Public catalog and browser URL resolution with `Catalog.load().resolve(..., access="public")` | `pip install "skytruth-shared-datasets @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"` |
| Authenticated GCS catalog loading, `resolve_dataset(...)`, or data downloads | `pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"` |
| Runtime installer lacks `git` | `pip install "skytruth-shared-datasets[gcs] @ https://github.com/SkyTruth/shared-datasets-1/archive/<tag-or-sha>.zip#subdirectory=api/python"` |
| Local development in this repository | `pip install -e "api/python[gcs]"` |

For SSH-based repository access:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+ssh://git@github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

Before committing a dependency URL, verify the exact installer used by Docker,
GitHub Actions, or production deploys can reach it. Local editable installs do
not prove the production requirement works.

## Credentials And IAM

Backend consumers should use Application Default Credentials with a runtime
service account. Do not create service account JSON keys and do not put
credentials in code, environment variables, images, or Git history.

Authenticated catalog loading and data downloads require:

```text
roles/storage.objectViewer on gs://skytruth-shared-datasets-1
```

In Cloud Run, scheduled jobs, GitHub Actions with Workload Identity Federation,
or other managed runtimes, run the service as the approved runtime or reader
service account and call the SDK without credential setup code.

Private PMTiles cookie signing is a separate concern. This Python SDK does not
read the PMTiles signing key and does not sign Cloud CDN cookies. The backend
route that issues browser PMTiles cookies needs separate Secret Manager access
and should use the TypeScript server helpers.

Feature metadata lookup is also a separate service. Release-oriented vector
PMTiles carry geometry plus `feature_id` values; callers that need full
attributes should call the IAP-protected metadata API:

```http
POST /v1/assets/{slug}/releases/{release}:lookup
```

`feature_id` values are unique URL-safe alphanumeric strings, usually generated
decimal sequence values:

```http
POST /v1/assets/{slug}/releases/{release}:lookup
```

Use `release=latest` only for convenience and persist the response
`resolved_release` when lineage matters.

## Fastest Backend Path

Install the GCS extra, then fetch the current canonical file:

```python
from skytruth_shared_datasets import fetch_dataset

ref = fetch_dataset("wdpa-marine", "fgb")

path = ref.cache_path
resolved_id = ref.resolved_id
canonical_uri = ref.gs_uri
```

`fetch_dataset` loads the catalog from
`gs://skytruth-shared-datasets-1/_catalog/shared-datasets-catalog.csv`, resolves
the requested format, downloads the current object with ADC, and returns a
`DatasetRef`.

For lineage, job records, model artifacts, reports, or joins that request
`version="latest"`, record `ref.resolved_id`, such as
`wdpa-marine@2026-05-02`. Do not record `wdpa-marine@latest` and do not infer a
version from the cache path.

## Resolve Without Downloading

Use `resolve_dataset` when backend code has ADC and needs metadata, a canonical
GCS URI, or a browser-facing PMTiles URL but not local bytes:

```python
from skytruth_shared_datasets import resolve_dataset

ref = resolve_dataset("wdpa-marine", "pmtiles")

print(ref.gs_uri)       # gs://skytruth-shared-datasets-1/...
print(ref.url)          # https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles
print(ref.access_tier)  # public
print(ref.cache_path)   # None; resolve_dataset does not download bytes
```

PMTiles latest URLs default to the tiered shared CDN URL. Exact dated PMTiles
release references keep their exact object identity and are not a promise of
anonymous public access.

For public catalog and browser URL resolution without the `gcs` extra or ADC,
load the public catalog and resolve through `Catalog` explicitly:

```python
from skytruth_shared_datasets import Catalog

catalog = Catalog.load()
ref = catalog.resolve("wdpa-marine", "pmtiles", access="public")

print(ref.url)  # https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles
```

## Catalog API

Use `Catalog` directly when a backend needs to list, search, resolve, or fetch
multiple assets:

```python
from skytruth_shared_datasets import Catalog

catalog = Catalog.load_gcs()

for asset in catalog.search(format="pmtiles", access_tier="public"):
    print(asset.slug, asset.title, asset.citation, asset.localized_name_locales, dict(asset.localized_name_review_states))

ref = catalog.resolve("wdpa-marine", "pmtiles")
downloaded = catalog.fetch("wdpa-marine", "fgb", access="gcs")
```

For local tests, load a fixture CSV:

```python
catalog = Catalog.load("./catalog/shared-datasets-catalog.csv")
```

Public catalog loading without the GCS extra is available through the shared
CDN endpoint, but production backend data reads should use `Catalog.load_gcs()`
or the top-level helpers with ADC.

## API Reference

Primary helpers:

| API | Purpose |
|---|---|
| `fetch_dataset(slug, format="...", version="latest")` | Resolve and download one dataset object through authenticated GCS, returning a `DatasetRef` with `cache_path`. |
| `resolve_dataset(slug, format="...", version="latest")` | Resolve metadata and URLs through authenticated GCS without downloading bytes. |
| `Catalog.load()` | Load catalog CSV from the public CDN, a URL, a local path, or a `gs://` URI converted to HTTPS. |
| `Catalog.load_gcs()` | Load the catalog from GCS using ADC and the optional `google-cloud-storage` dependency. |
| `Catalog.search(...)` | Filter assets by category, status, format, or access tier. |
| `Catalog.versions(slug, access="gcs")` | Load indexed release metadata for exact dated releases. |

Important `DatasetRef` fields:

| Field | Meaning |
|---|---|
| `ref.slug` | Catalog asset slug. |
| `ref.format` | Resolved format. |
| `ref.gs_uri` | Durable GCS object identity. |
| `ref.url` | Browser-facing URL for the resolved object. PMTiles latest defaults to the CDN URL. |
| `ref.access_tier` | `public` or `private`. |
| `ref.cache_path` | Local downloaded path after `fetch_dataset`; `None` after `resolve_dataset`. |
| `ref.resolved_id` | Stable lineage value, such as `wdpa-marine@2026-05-02`. |

Common errors:

| Error | Meaning |
|---|---|
| `CatalogLoadError` | Catalog could not be loaded or parsed. Check source URL, ADC, IAM, and network access. |
| `DatasetNotFoundError` | Unknown slug. Refresh the catalog or fix the slug. |
| `UnsupportedFormatError` | The asset does not publish the requested format. Check `available_formats`. |
| `UnsupportedVersionError` | Requested version is not `latest` and not an indexed `YYYY-MM-DD` release. |
| `FetchError` | Object download failed. Check IAM, object existence, network, and cache filesystem permissions. |

## PMTiles URL Behavior

Browser PMTiles should use the shared CDN:

```text
https://tiles.skytruth.org/pmtiles/public/{slug}.pmtiles
https://tiles.skytruth.org/pmtiles/private/{slug}.pmtiles
```

The SDK keeps the canonical `gs://` identity in `DatasetRef.gs_uri` and exposes
the browser URL in `DatasetRef.url`.

Apps with their own PMTiles route can override only the browser base URL:

```python
from skytruth_shared_datasets import Catalog

catalog = Catalog.load_gcs()
pmtiles = catalog.resolve("wdpa-marine", "pmtiles", web_base_url="/pmtiles")

assert pmtiles.gs_uri.startswith("gs://")
assert pmtiles.url == "/pmtiles/public/wdpa-marine.pmtiles"
```

To inspect the underlying current GCS URL during the temporary public-bucket
window:

```python
pmtiles = catalog.resolve("wdpa-marine", "pmtiles", url_strategy="public_gcs")
```

Do not use `url_strategy="public_gcs"` in production consumers. Direct public
GCS object access is being removed; browser PMTiles should use the shared CDN,
and backend data reads should use authenticated GCS.

## Cache Behavior

By default, downloads are cached under:

```text
~/.cache/skytruth-shared-datasets/{slug}/{format}/{last_updated}/{filename}
```

Override the cache root with `SKYTRUTH_SHARED_DATASETS_CACHE` or the
`cache_dir` argument:

```python
ref = fetch_dataset("wdpa-marine", "fgb", cache_dir="/tmp/shared-datasets-cache")
```

Pass `force=True` to download again even when the cache path already exists.

## CLI

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
should prefer the Python API so it can preserve `DatasetRef` metadata.

## Browser Boundary

Do not put this Python SDK in browser code. Browser clients should not receive
GCS credentials, service account keys, raw signing keys, or signed cookie
values. Browser PMTiles code should use the tiered CDN URL and, for private
layers, a consumer backend session endpoint implemented with the TypeScript
server helpers.
