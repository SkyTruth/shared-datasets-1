---
name: shared-datasets-consumer
description: Use when driving shared-datasets-1 adoption in downstream SkyTruth repos, especially scanning for hardcoded shared dataset URLs, replacing direct GCS PMTiles links with tiles.skytruth.org or the Python resolver SDK, preserving resolved dataset identity for backend fetches, and opening small focused PRs.
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
   `fetch_dataset(...)` or `resolve_dataset(...)`. A successful
   `fetch_dataset(...)` returns a `DatasetRef`; use `ref.cache_path` as the
   local file path and `ref.resolved_id` as the durable resolved identity to
   record when callers request `version="latest"`. When both bytes and lineage
   are needed, call `fetch_dataset(...)` once; do not call
   `resolve_dataset(...)` and `fetch_dataset(...)` separately.
3. For credentials, prefer runtime identity: Cloud Run, jobs, or CI should run
   as an established runtime or reader service account that already has bucket
   read access. Do not create service account JSON keys.
4. Leave private PMTiles auth out of the consuming repo until the private tier
   is explicitly enabled. Current PMTiles are public-tier assets, including when
   they are served through the CDN path.

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

The access tier is part of the stable URL contract. Use the same
`tiles.skytruth.org` URL in redirect mode and CDN mode; do not switch consumers
back to `storage.googleapis.com` for CDN rollout or fallback behavior.

Today, `public` PMTiles may be served by a temporary `307` redirect to public
GCS. A final browser request to `storage.googleapis.com` is expected until CDN
mode replaces the redirector. PMTiles are not private during redirect mode.
No browser authentication handshake is required for public-tier PMTiles.

In CDN mode, successful PMTiles reads should stay on `tiles.skytruth.org` and
return normal object or range responses such as `200` or `206`. Public-tier CDN
access still does not require browser credentials, signed cookies, or service
account setup in the consuming frontend.

## WDPA MPA Identity

For WDPA MPA features and new shared-dataset joins, use `site_id` / source
`SITE_ID` as the durable feature identity. Do not build new behavior around
`WDPAID`. Do not rewrite legacy backfills or tables that only contain `WDPAID`
unless an explicit alias or backfill plan exists.

## Backend Data

Use this when backend/server code needs the actual data file, a canonical
`gs://` URI, or catalog-driven format resolution.

If the consuming project has backend/server code and a runtime service account,
install the SDK with the GCS extra. The package is installed from GitHub for
now, not PyPI. `SkyTruth/shared-datasets-1` is public, so unauthenticated public
HTTPS installs are acceptable:

```bash
pip install "skytruth-shared-datasets[gcs] @ git+https://github.com/SkyTruth/shared-datasets-1.git@main#subdirectory=api/python"
```

For production, pin a tag or commit SHA instead of `main`. Before committing a
GitHub dependency URL, verify the exact install surface can reach it. Docker
builds and GitHub Actions may use different requirement files and may lack
`git`, SSH, or cross-repo token access; do not treat a local checkout or local
editable install as proof the committed requirement works.

Use `git+https` when the install surface has `git`. If the runtime installer
lacks `git`, a pinned public archive URL is acceptable:

```bash
pip install "skytruth-shared-datasets[gcs] @ https://github.com/SkyTruth/shared-datasets-1/archive/<tag-or-sha>.zip#subdirectory=api/python"
```

Do not use unauthenticated `archive/<sha>.zip` URLs for private repos or future
private forks unless runtime authentication is explicitly wired.

Then fetch a dataset with one call:

```python
from skytruth_shared_datasets import fetch_dataset

ref = fetch_dataset("wdpa-marine", "fgb")
path = ref.cache_path
resolved_id = ref.resolved_id
```

For AOI joins, job records, lineage tables, or other durable references, record
`resolved_id` values such as `wdpa-marine@2026-05-02`, not
`wdpa-marine@latest` and not a value inferred from the cache path.

When both bytes and lineage are needed, call `fetch_dataset(...)` once and use
`ref.cache_path` plus `ref.resolved_id`; do not call `resolve_dataset(...)` and
`fetch_dataset(...)` separately.

Or resolve without downloading:

```python
from skytruth_shared_datasets import resolve_dataset

ref = resolve_dataset("wdpa-marine", "pmtiles")
print(ref.gs_uri)
print(ref.url)
print(ref.resolved_id)
```

This path uses Application Default Credentials. In Cloud Run, scheduled jobs, or
CI with Workload Identity Federation, the established runtime service account
should be enough.

The only required IAM setup is:

```text
Grant the consuming runtime service account roles/storage.objectViewer on gs://skytruth-shared-datasets-1.
```

Prefer a repo-provisioned reader service account when that is the consuming
repo's established deployment model:

```text
Cerulean:     shared-datasets-reader@cerulean-338116.iam.gserviceaccount.com
30x30:        shared-datasets-reader@x30-399415.iam.gserviceaccount.com
Monitor:      shared-datasets-reader@skytruth-monitor.iam.gserviceaccount.com
SkyTruthTech: shared-datasets-reader@skytruth-tech.iam.gserviceaccount.com
```

Otherwise grant the existing runtime service account `roles/storage.objectViewer`
on the shared bucket. Run backend jobs/services as the chosen runtime identity,
then use `fetch_dataset(...)` or `resolve_dataset(...)`. Do not treat
`fetch_dataset(...)` as a path-only helper; it returns the resolved reference
that also carries the populated cache path.

For Cloud Run, that means the service configuration names the existing runtime
service account or the repo-provisioned reader service account. The Python code
stays credential-free:

```bash
gcloud run services update SERVICE_NAME \
  --project=PROJECT_ID \
  --region=REGION \
  --service-account=RUNTIME_SERVICE_ACCOUNT_EMAIL
```

```python
from skytruth_shared_datasets import fetch_dataset

ref = fetch_dataset("wdpa-marine", "fgb")
path = ref.cache_path
resolved_id = ref.resolved_id
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

- Apply only the checklist items for surfaces that exist in the consuming repo.
  Backend-only adopters should not add PMTiles helpers, frontend env vars, or
  browser URL changes.
- Add a configurable PMTiles base URL with default
  `https://tiles.skytruth.org/pmtiles/public`.
- Replace direct `storage.googleapis.com` PMTiles sources with
  `sharedPmtilesUrl("<slug>")`.
- Keep PMTiles asset slugs lowercase kebab-case.
- If backend code downloads shared data, add the SDK dependency and use
  `fetch_dataset("<slug>", "<format>")`; read the local path from
  `ref.cache_path` and record resolved dataset identity from `ref.resolved_id`
  when lineage matters.
- If backend code runs in GCP, confirm the runtime identity has
  `roles/storage.objectViewer` on the shared bucket.
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

Signed-cookie support belongs in the consuming backend, not in static frontend
configuration. The backend should sign the allowed tier prefix, such as
`https://tiles.skytruth.org/pmtiles/private/`, and set `Cloud-CDN-Cookie` with a
short TTL. When signed cookies are required, browser PMTiles fetches must send
credentials so the cross-site cookie is included, for example
`credentials: "include"` in fetch-capable PMTiles client configuration.

## Tests

Add focused tests that prove:

- PMTiles URLs use `https://tiles.skytruth.org/pmtiles/public/...`.
- PMTiles layer source configuration does not hardcode
  `storage.googleapis.com`.
- The PMTiles base URL is configurable by environment.
- Public-tier PMTiles do not add signed-cookie or service-account auth.
- Private signed-cookie PMTiles, if implemented, send browser fetch credentials
  and do not expose GCS credentials.
- WDPA MPA selection/join logic uses `site_id`, not `WDPAID`.
- Backend code that needs data files uses `fetch_dataset(...)` or
  `resolve_dataset(...)` rather than service account keys.
- Backend code that requests `version="latest"` and records lineage persists
  `ref.resolved_id`, not `<slug>@latest` and not a cache-path-derived version.

## Non-Goals

Do not implement Cloud CDN infrastructure in the consuming project.
Do not create service account keys.
Do not expose GCS credentials to the browser.
Do not mirror PMTiles into the consuming project.
Do not make broad UI rewrites while doing the minimal shared-datasets adoption.
