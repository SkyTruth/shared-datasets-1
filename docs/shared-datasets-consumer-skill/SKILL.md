---
name: shared-datasets-consumer
description: Use when driving shared-datasets-1 adoption in downstream SkyTruth repos, especially scanning for hardcoded shared dataset URLs, replacing direct GCS PMTiles links with tiles.skytruth.org or the Python resolver SDK, and opening small focused PRs.
---

# Shared Datasets Consumer Integration

Use this skill to make another SkyTruth project adopt shared-datasets-1 with
the smallest safe change. The main job is adoption, not redesign: find
hardcoded shared dataset access paths, replace them with the stable shared
access contract, and package each consumer change as a small focused PR.

## Adoption Workflow

Use this workflow when moving downstream repos off direct shared bucket URLs.

1. Scan the consuming repo for hardcoded shared dataset URLs, PMTiles paths,
   dataset slugs, and ad hoc GCS access.
2. Classify each hit by runtime surface:
   - Browser map PMTiles should use `tiles.skytruth.org`.
   - Backend/server downloads or catalog resolution should use the Python SDK.
   - Unrelated references, docs, tests, and comments should only change when
     they would otherwise keep the old integration pattern alive.
3. Make the smallest coherent replacement. Prefer a tiny helper such as
   `sharedPmtilesUrl("<slug>")` over broad map-layer refactors.
4. Keep PRs focused by repo, surface, and dataset. Do not combine unrelated UI,
   infrastructure, dependency, or formatting changes with adoption work.
5. In the PR description, call out the old hardcoded access path, the new shared
   access path, and the focused validation that was run.

## Recommended Path

Start here. Do not add more machinery than the consumer actually needs.

1. For browser map layers that already know the PMTiles slug, use the public
   tiered URL template directly. Do not install the Python SDK.
2. For backend/server code that needs to download or resolve shared dataset
   files, install the Python SDK with the `gcs` extra and use
   `fetch_dataset(...)` or `resolve_dataset(...)`.
3. For credentials, prefer runtime identity: Cloud Run, jobs, or CI should run
   as a reader service account that already has bucket read access. Do not
   create service account JSON keys.
4. Leave private PMTiles auth out of the consuming repo until the private tier
   is explicitly enabled. Current PMTiles are public-tier assets.

## Frontend PMTiles

Use this when the consuming repo only needs browser PMTiles and already knows
the asset slug.

Use this URL shape:

```text
https://tiles.skytruth.org/pmtiles/public/{slug}.pmtiles
```

Example:

```text
https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles
```

In frontend code:

```ts
const SHARED_PMTILES_BASE_URL =
  process.env.NEXT_PUBLIC_SHARED_PMTILES_BASE_URL ??
  "https://tiles.skytruth.org/pmtiles/public";

export function sharedPmtilesUrl(assetSlug: string) {
  return `${SHARED_PMTILES_BASE_URL}/${assetSlug}.pmtiles`;
}
```

Replace direct GCS PMTiles URLs like:

```text
https://storage.googleapis.com/skytruth-shared-datasets-1/.../latest/wdpa-marine.pmtiles
```

with:

```ts
sharedPmtilesUrl("wdpa-marine")
```

The access tier is part of the stable URL contract.

Today, `public` PMTiles may be served by a temporary `307` redirect to public
GCS. A final browser request to `storage.googleapis.com` is expected until CDN
mode replaces the redirector. PMTiles are not private during redirect mode.
No browser authentication handshake is required for public-tier PMTiles.

For WDPA MPA features, use `site_id` / source `SITE_ID` as the durable feature
identity. Do not build new UI behavior around `WDPAID`.

## Backend Data

Use this when backend/server code needs the actual data file, a canonical
`gs://` URI, or catalog-driven format resolution.

If the consuming project has backend/server code and a runtime service account,
install the SDK with the GCS extra. The package is installed from GitHub for
now, not PyPI:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

For production, pin a tag or commit SHA instead of `main`.

Then fetch a dataset with one call:

```python
from skytruth_shared_datasets import fetch_dataset

path = fetch_dataset("wdpa-marine", "fgb")
```

Or resolve without downloading:

```python
from skytruth_shared_datasets import resolve_dataset

ref = resolve_dataset("wdpa-marine", "pmtiles")
print(ref.gs_uri)
print(ref.url)
```

This path uses Application Default Credentials. In Cloud Run, scheduled jobs, or
CI with Workload Identity Federation, the established runtime service account
should be enough.

The only required IAM setup is:

```text
Grant the consuming runtime service account roles/storage.objectViewer on gs://skytruth-shared-datasets-1.
```

For common SkyTruth consumer projects, prefer the repo-provisioned reader
service account:

```text
Cerulean:     shared-datasets-reader@cerulean-338116.iam.gserviceaccount.com
30x30:        shared-datasets-reader@x30-399415.iam.gserviceaccount.com
Monitor:      shared-datasets-reader@skytruth-monitor.iam.gserviceaccount.com
SkyTruthTech: shared-datasets-reader@skytruth-tech.iam.gserviceaccount.com
```

Run backend jobs/services as the reader service account for their project, then
use `fetch_dataset(...)` or `resolve_dataset(...)`.

For Cloud Run, that means the service configuration names the reader service
account. The Python code stays credential-free:

```bash
gcloud run services update SERVICE_NAME \
  --project=PROJECT_ID \
  --region=REGION \
  --service-account=shared-datasets-reader@PROJECT_ID.iam.gserviceaccount.com
```

```python
from skytruth_shared_datasets import fetch_dataset

path = fetch_dataset("wdpa-marine", "fgb")
```

Only pass a custom `google.cloud.storage.Client` if the repo already has a
deliberate ADC or service-account impersonation pattern. Do not add service
account key files.

## Search Targets

Look for:

```text
storage.googleapis.com/skytruth-shared-datasets-1
tiles.skytruth.org/pmtiles/
.pmtiles
wdpa
WDPAID
```

Prefer replacing only the PMTiles access path first. Do not refactor unrelated
map-layer behavior.

## Minimum Patch Checklist

- Add a configurable PMTiles base URL with default
  `https://tiles.skytruth.org/pmtiles/public`.
- Replace direct `storage.googleapis.com` PMTiles sources with
  `sharedPmtilesUrl("<slug>")`.
- Keep PMTiles asset slugs lowercase kebab-case.
- If backend code downloads shared data, add the SDK dependency and use
  `fetch_dataset("<slug>", "<format>")`.
- If backend code runs in GCP, confirm the runtime uses the project reader
  service account.
- Do not expose GCS credentials to browser code.

## Future Private PMTiles

All current shared PMTiles are public-tier:

```text
https://tiles.skytruth.org/pmtiles/public/{slug}.pmtiles
```

Future logged-in layers can use:

```text
https://tiles.skytruth.org/pmtiles/private/{slug}.pmtiles
```

When private PMTiles are enabled, browser clients should still not receive GCS
credentials. The consuming backend should issue a Cloud CDN signed cookie only
for users allowed to access the private tier.

## Tests

Add focused tests that prove:

- PMTiles URLs use `https://tiles.skytruth.org/pmtiles/public/...`.
- PMTiles layer source configuration does not hardcode
  `storage.googleapis.com`.
- The PMTiles base URL is configurable by environment.
- WDPA MPA selection/join logic uses `site_id`, not `WDPAID`.
- Backend code that needs data files uses `fetch_dataset(...)` or
  `resolve_dataset(...)` rather than service account keys.

## Non-Goals

Do not implement Cloud CDN infrastructure in the consuming project.
Do not create service account keys.
Do not expose GCS credentials to the browser.
Do not mirror PMTiles into the consuming project.
Do not make broad UI rewrites while doing the minimal shared-datasets adoption.
