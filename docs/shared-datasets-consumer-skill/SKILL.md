---
name: shared-datasets-consumer
description: Use when driving shared-datasets-1 adoption in downstream SkyTruth repos, especially scanning for hardcoded shared dataset URLs, replacing direct GCS PMTiles links with tiered tiles.skytruth.org URLs and signed-cookie private PMTiles access, using the Python resolver SDK for backend fetches, preserving resolved dataset identity, and opening small focused PRs.
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
   - Browser map PMTiles should use tiered `tiles.skytruth.org` URLs.
   - Private browser PMTiles need a backend signed-cookie session endpoint.
   - Backend/server downloads or catalog resolution should use the Python SDK.
   - Unrelated references, docs, tests, and comments should only change when
     they would otherwise keep the old integration pattern alive.
3. Make the smallest coherent replacement. Prefer a tiny helper such as
   `sharedPmtilesUrl("<slug>", "<access-tier>")` over broad map-layer
   refactors.
4. Keep PRs focused by repo, surface, and dataset. Do not combine unrelated UI,
   infrastructure, dependency, or formatting changes with adoption work.
5. In the PR description, call out the old hardcoded access path, the new shared
   access path, and the focused validation that was run.

## Recommended Path

Start here. Do not add more machinery than the consumer actually needs.

1. For browser map layers that already know the PMTiles slug and only use public
   assets, use the public tiered URL template directly. Do not install the
   Python SDK.
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
4. For browser map layers that may use private shared-dataset PMTiles, resolve
   the PMTiles URL from the shared catalog `access_tier`, add a signed-cookie
   session endpoint in the consuming backend, and make PMTiles range requests
   with browser credentials included.

## Frontend PMTiles

Use this when the consuming repo needs browser PMTiles and already knows the
asset slug, or can resolve the slug from the shared catalog.

Use these URL shapes:

```text
https://tiles.skytruth.org/pmtiles/public/{slug}.pmtiles
https://tiles.skytruth.org/pmtiles/private/{slug}.pmtiles
```

Example:

```text
https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles
https://tiles.skytruth.org/pmtiles/private/iucn-mammal-ranges.pmtiles
```

If the repo only uses known public assets, a tiny helper is enough:

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

Replace direct GCS PMTiles URLs like:

```text
https://storage.googleapis.com/skytruth-shared-datasets-1/.../latest/wdpa-marine.pmtiles
```

with:

```ts
sharedPmtilesUrl("wdpa-marine", "public")
```

The access tier is part of the stable URL contract. Use the same
`tiles.skytruth.org` URL in redirect mode and CDN mode; do not switch consumers
back to `storage.googleapis.com` for CDN rollout or fallback behavior.

Do not assume every PMTiles asset is public. The catalog can include private
PMTiles; as of the cookie-mediated CDN rollout, `iucn-mammal-ranges` and
`iucn-reptile-ranges` are private-tier PMTiles. If a consumer derives layers
from the catalog, preserve `citation` in service-facing metadata and parse
`access_tier` to emit:

```ts
`${SHARED_PMTILES_BASE_URL}/${accessTier}/${assetSlug}.pmtiles`
```

When a config API caches shared-dataset layer config, bump the cache key after
changing URL generation so stale direct GCS PMTiles URLs are flushed.

In redirect mode, `public` PMTiles may be served by a temporary `307` redirect
to public GCS. A final browser request to `storage.googleapis.com` is expected
until CDN mode replaces the redirector. Private-tier paths should not be treated
as readable in redirect mode.

In CDN mode, successful PMTiles reads should stay on `tiles.skytruth.org` and
return normal object or range responses such as `200` or `206`. Public-tier CDN
access does not require signed cookies.

## Private PMTiles Signed-Cookie Pattern

Use this pattern for every downstream repo that may display private shared
PMTiles, including 30x30.

### Shared-Datasets Prerequisites

Before deploying a new consumer, identify the backend or UI runtime service
account that serves the consumer's session endpoint. Grant that service account
access to the shared signing-key secret from `shared-datasets-1`; do not create
or distribute a service account key file.

Current shared signing material:

```text
Secret: projects/shared-datasets-1/secrets/pmtiles-cdn-signed-request-key/versions/latest
Cloud CDN key name: shared-datasets-pmtiles-v1
Signed prefix: https://tiles.skytruth.org/pmtiles/private/
```

In shared-datasets Terraform, add the consumer signer principal to the signer
allowlist used by `google_secret_manager_secret_iam_member.pmtiles_cdn_cookie_signers`.
The Cerulean rollout used:

```bash
terraform -chdir=terraform/envs/prod apply \
  -var "pmtiles_cdn_grant_fill_service_account=true" \
  -var 'cerulean_pmtiles_cookie_signer_service_accounts=["serviceAccount:734798842681-compute@developer.gserviceaccount.com"]'
```

For another repo, use that repo's real runtime service account instead. The
30x30 reader service account is
`shared-datasets-reader@x30-399415.iam.gserviceaccount.com`, but the signer
should be the service account actually running the endpoint that issues the
cookie. HTTPS SkyTruth subdomains are allowed by
`pmtiles_cdn_allowed_origin_regexes` only while the temporary Cloud Run
redirector serves `/pmtiles/*`. CDN backend-bucket mode cannot use regex
origins: external URL-map CORS regexes are not allowed, and Cloud Armor edge
policies on backend buckets cannot evaluate request-header expressions. Add
each browser origin to `pmtiles_cdn_allowed_origins` as an exact origin before
CDN cutover. Credentialed CORS cannot use `*`.

The current exact CDN allowlist is `http://localhost:3000`,
`https://localhost:3000`, `https://feature-three.cerulean.skytruth.org`,
`https://test.cerulean.skytruth.org`,
`https://develop.cerulean.skytruth.org`, `https://cerulean.skytruth.org`, and
`https://30x30.skytruth.org`, and `https://monitor.skytruth.org`.

The shared bucket CORS origins use the same exact list because backend-bucket
CDN range responses can otherwise inherit wildcard CORS headers from GCS, which
browsers reject when PMTiles requests include credentials.

### Consumer Backend Endpoint

Add an endpoint like:

```text
GET /api/pmtiles/session?tier=public
GET /api/pmtiles/session?tier=private
```

Required behavior:

- `tier=public` returns `204` without a cookie.
- `tier=private` requires an authenticated user session.
- For the first rollout, allow only SkyTruth or admin users unless the consumer
  has a more specific entitlement model.
- Read
  `projects/shared-datasets-1/secrets/pmtiles-cdn-signed-request-key/versions/latest`.
- Decode the secret value from base64url to the raw 16-byte Cloud CDN key.
- Sign `https://tiles.skytruth.org/pmtiles/private/`, not individual PMTiles
  URLs.
- Use HMAC-SHA1 and key name `shared-datasets-pmtiles-v1`.
- Set `Cache-Control: no-store`.
- Never log or return the key bytes or full cookie value.

The cookie must be named `Cloud-CDN-Cookie` and set with:

```text
Domain=.skytruth.org
Path=/pmtiles
Secure
HttpOnly
SameSite=None
Max-Age=3600
Expires=<1 hour from now>
```

Cloud CDN signed-cookie policy fields must be in this order:

```text
URLPrefix=<base64url-prefix>:Expires=<unix-seconds>:KeyName=shared-datasets-pmtiles-v1:Signature=<base64url-hmac>
```

Keep base64url padding characters if the runtime's encoder emits them. The key
used for HMAC is the decoded raw 16-byte key, not the encoded secret text.

### Consumer Frontend Loading

Before enabling private shared AOI layers, call:

```ts
await fetch("/api/pmtiles/session?tier=private", {
  credentials: "include"
});
```

Only add the private layer if the response is successful. Public layers may skip
the call or call `tier=public`, which should return `204`.

PMTiles byte-range requests must include credentials so the browser sends the
cross-site cookie to `tiles.skytruth.org`. Add `credentials: "include"` to the
PMTiles fetch/load options. If the PMTiles library hides byte-range fetches,
wrap or subclass its fetch source so every header, directory, and tile range
request includes:

```ts
fetch(url, {
  headers: { range: "bytes=start-end" },
  credentials: "include"
});
```

In a browser trace, private PMTiles requests to `tiles.skytruth.org` should carry
the `Cloud-CDN-Cookie` cookie. Do not try to send GCS credentials from browser
code.

Keep CSP `connect-src` allowing `https://tiles.skytruth.org`. Remove direct
`storage.googleapis.com` dependencies only for shared-dataset PMTiles access;
do not remove `storage.googleapis.com` if the app still uses it for unrelated
features.

### Rollout Order For A Consumer Repo

1. Scan for direct shared bucket PMTiles URLs and hardcoded public-only helpers.
2. Change config generation to emit
   `https://tiles.skytruth.org/pmtiles/{access_tier}/{slug}.pmtiles`.
3. Bump any config cache key.
4. Add `/api/pmtiles/session`.
5. Add `credentials: "include"` to every PMTiles range request path.
6. Ensure private layers call the session endpoint before mounting.
7. Confirm CSP and CORS origins allow `https://tiles.skytruth.org`.
8. Run the repo's lint, type, and build checks.
9. Deploy to test, then develop, then production.
10. After all consumers are deployed, shared-datasets can remove the bucket-wide
    public grant, switch `pmtiles_serving_mode="cdn"`, invalidate `/pmtiles/*`,
    and run live public/private checks.

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
access_tier
Cloud-CDN-Cookie
pmtiles/session
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
  `https://tiles.skytruth.org/pmtiles`.
- Replace direct `storage.googleapis.com` PMTiles sources with
  `sharedPmtilesUrl("<slug>", "<access-tier>")` or an equivalent catalog-driven
  tiered URL.
- If the repo reads the shared catalog, parse `access_tier` and do not preserve
  old direct GCS `pmtiles_url` overrides for shared-dataset layers.
- Bump any config cache key that could retain old PMTiles URLs.
- Keep PMTiles asset slugs lowercase kebab-case.
- If private PMTiles are possible, add a backend session endpoint that issues
  `Cloud-CDN-Cookie` for allowed users only.
- If private PMTiles are possible, ensure PMTiles range requests include
  browser credentials and private layers wait for the session endpoint.
- If backend code downloads shared data, add the SDK dependency and use
  `fetch_dataset("<slug>", "<format>")`; read the local path from
  `ref.cache_path` and record resolved dataset identity from `ref.resolved_id`
  when lineage matters.
- If backend code runs in GCP, confirm the runtime identity has
  `roles/storage.objectViewer` on the shared bucket.
- If backend code signs private PMTiles cookies, confirm the runtime identity
  has `roles/secretmanager.secretAccessor` on the shared signing-key secret.
- Do not expose GCS credentials to browser code.

## Private PMTiles Rules

Private PMTiles are active catalog concepts, not a future placeholder. Browser
clients still must not receive GCS credentials. The consuming backend issues a
Cloud CDN signed cookie only for users allowed to access the private tier.

Signed-cookie support belongs in the consuming backend, not in static frontend
configuration. The backend signs the allowed tier prefix,
`https://tiles.skytruth.org/pmtiles/private/`, and sets `Cloud-CDN-Cookie` with
a short TTL. Browser PMTiles fetches must send credentials so the cross-site
cookie is included.

## Tests

Add focused tests that prove:

- PMTiles URLs use `https://tiles.skytruth.org/pmtiles/public/...`.
- Private PMTiles URLs use
  `https://tiles.skytruth.org/pmtiles/private/...`.
- Catalog-derived PMTiles URLs use `access_tier`; tests should include at least
  one public and one private fixture row.
- PMTiles layer source configuration does not hardcode
  `storage.googleapis.com`.
- The PMTiles base URL is configurable by environment.
- Public-tier PMTiles do not add signed-cookie or service-account auth.
- Private signed-cookie PMTiles reject anonymous users, set `Cache-Control:
  no-store`, set `Cloud-CDN-Cookie` for authorized users, send browser fetch
  credentials, and do not expose GCS credentials.
- WDPA MPA selection/join logic uses `site_id`, not `WDPAID`.
- Backend code that needs data files uses `fetch_dataset(...)` or
  `resolve_dataset(...)` rather than service account keys.
- Backend code that requests `version="latest"` and records lineage persists
  `ref.resolved_id`, not `<slug>@latest` and not a cache-path-derived version.

## Non-Goals

Do not implement Cloud CDN infrastructure in the consuming project.
Do not store Cloud CDN signing keys in the consuming repo or Terraform state.
Do not create service account keys.
Do not expose GCS credentials to the browser.
Do not mirror PMTiles into the consuming project.
Do not make broad UI rewrites while doing the minimal shared-datasets adoption.
