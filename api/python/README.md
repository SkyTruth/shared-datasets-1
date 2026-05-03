# skytruth-shared-datasets

Tiny Python SDK for SkyTruth shared datasets.

## Fastest Paths

### Browser PMTiles: no SDK needed

If a frontend already knows the asset slug, build the PMTiles URL directly:

```text
https://tiles.skytruth.org/pmtiles/public/{slug}.pmtiles
```

Example:

```text
https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles
```

The access tier is part of the contract so public layers can coexist with
future logged-in/private layers.

### Backend/server data: one helper call

Install the GCS extra from this repository:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

Then let the runtime service account do the work:

```python
from skytruth_shared_datasets import fetch_dataset

ref = fetch_dataset("wdpa-marine", "fgb")
path = ref.cache_path
resolved_id = ref.resolved_id
```

That loads the catalog from `gs://skytruth-shared-datasets-1/_catalog/` and
downloads the current object with Application Default Credentials. The returned
reference includes both the local cached path and the resolved dataset identity,
such as `wdpa-marine@2026-05-02`. In Cloud Run, Cloud Scheduler jobs, GitHub
Actions with Workload Identity Federation, or other managed runtimes, there
should be no JSON key and no credential code.

For lineage, job records, or AOI joins that request `version="latest"`, record
`ref.resolved_id`, not `wdpa-marine@latest` and not a version inferred from the
cache path.

The consuming runtime service account needs this IAM grant:

```text
roles/storage.objectViewer on gs://skytruth-shared-datasets-1
```

The shared-datasets production Terraform sets up these reader service accounts
for common SkyTruth consumer projects:

```text
Cerulean:     shared-datasets-reader@cerulean-338116.iam.gserviceaccount.com
30x30:        shared-datasets-reader@x30-399415.iam.gserviceaccount.com
Monitor:      shared-datasets-reader@skytruth-monitor.iam.gserviceaccount.com
SkyTruthTech: shared-datasets-reader@skytruth-tech.iam.gserviceaccount.com
```

Run the backend job/service as the reader service account for its project, then
use `fetch_dataset(...)` without any credential setup code.

To resolve without downloading:

```python
from skytruth_shared_datasets import resolve_dataset

ref = resolve_dataset("wdpa-marine", "pmtiles")
print(ref.gs_uri)       # canonical object identity
print(ref.url)          # https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles
print(ref.access_tier)  # public
print(ref.cache_path)   # None; resolve_dataset does not download bytes
```

## Installation

This package is currently distributed from GitHub, not PyPI.

Public/browser-url-only usage:

```bash
pip install "skytruth-shared-datasets @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

Authenticated GCS usage:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

If your repository access is through SSH:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+ssh://git@github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

For production consumers, pin a tag or commit SHA instead of `main`.

For local development inside this repository:

```bash
pip install -e "api/python[gcs]"
```

## Lower-Level SDK Usage

Use `Catalog` directly when a backend needs to list, search, resolve, or fetch
multiple assets:

```python
from skytruth_shared_datasets import Catalog

catalog = Catalog.load_gcs()

for asset in catalog.search(format="pmtiles"):
    print(asset.slug, asset.access_tier)

ref = catalog.resolve("wdpa-marine", "pmtiles")
downloaded = catalog.fetch("wdpa-marine", "fgb", access="gcs")
path = downloaded.cache_path
```

`DatasetRef.gs_uri` is the durable object identity. `DatasetRef.url` is a
browser-facing access URL. `DatasetRef.resolved_id` is the stable value to
record when a caller requests `version="latest"` and needs the actual published
date. PMTiles default to the tiered shared URL:

```python
pmtiles = catalog.resolve("wdpa-marine", "pmtiles")
assert pmtiles.gs_uri.startswith("gs://")
assert pmtiles.url == "https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles"
```

Apps with their own PMTiles route can override only the browser base URL:

```python
pmtiles = catalog.resolve("wdpa-marine", "pmtiles", web_base_url="/pmtiles")
assert pmtiles.url == "/pmtiles/public/wdpa-marine.pmtiles"
```

To force current public GCS URLs while the bucket remains public:

```python
pmtiles = catalog.resolve("wdpa-marine", "pmtiles", url_strategy="public_gcs")
```

## CLI

```bash
skytruth-datasets list
skytruth-datasets list --access-tier public --format pmtiles
skytruth-datasets url wdpa-marine --format pmtiles
skytruth-datasets url wdpa-marine --format pmtiles --url-strategy public-gcs
skytruth-datasets fetch wdpa-marine --format fgb --access gcs
skytruth-datasets versions wdpa-marine --access gcs
```

The CLI is useful for diagnostics. Production backend code should prefer
`fetch_dataset(...)` or `resolve_dataset(...)` when authenticated GCS access is
the normal path.

## Private-Bucket Direction

All current assets are `public`. Future private PMTiles can use the same shape
with a different tier:

```text
https://tiles.skytruth.org/pmtiles/private/{slug}.pmtiles
```

Browser clients should not authenticate directly to private GCS. The intended
private model is:

- backend/server code uses ADC/service accounts and this SDK;
- browser PMTiles use `tiles.skytruth.org`;
- the consuming app issues Cloud CDN signed cookies only for users allowed to
  access private-tier PMTiles.

The SDK does not sign CDN cookies. It keeps canonical GCS object identity,
access tier metadata, and browser URL construction separate.
