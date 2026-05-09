# PMTiles Browser Access

Shared PMTiles are stored once under canonical
`gs://skytruth-shared-datasets-1/.../latest/{asset}.pmtiles` object paths.
Browser clients use the tiered shared URL surface:

```text
https://tiles.skytruth.org/pmtiles/public/{asset}.pmtiles
https://tiles.skytruth.org/pmtiles/private/{asset}.pmtiles
```

The internal catalog viewer does not depend on these routes for private map
preview. It is a Cloud Run service in `shared-datasets-1`, protected by direct
Cloud Run IAP on its generated `run.app` URL, and it resolves private PMTiles
through short-lived signed `storage.googleapis.com` URLs from
`/api/pmtiles/signed-url?slug={asset-slug}`. The CDN remains the compatibility
path for downstream consumers that use `tiles.skytruth.org` directly.

Public-tier PMTiles are anonymously readable. Private-tier PMTiles are present
in the public catalog but require application authorization and a valid
Cloud CDN signed cookie before the bytes are readable through the CDN. As of
May 9, 2026, the catalog includes private PMTiles for
`global-coral-reefs`, `iucn-mammal-ranges`, and `iucn-reptile-ranges`.

Cloud CDN signed cookies do not block unsigned requests by themselves. Google
Cloud CDN forwards unsigned cache misses to the origin and can serve unsigned
cache hits from entries populated by unsigned requests. Private protection
therefore depends on removing public origin access for private GCS prefixes,
granting Cloud CDN fill access, and invalidating `/pmtiles/*` during cutover so
stale unsigned cache entries cannot keep private objects readable. See Google
Cloud CDN signed-cookie documentation:
https://cloud.google.com/cdn/docs/using-signed-cookies

## Terraform-Owned Resources

The production Terraform stack owns:

- A global HTTPS load balancer address and managed certificate for
  `tiles.skytruth.org`.
- A temporary Cloud Run redirector used by `pmtiles_serving_mode="redirect"`.
  Terraform keeps it deployed but unrouted in CDN mode to avoid a destructive
  mode-switch cycle and preserve a fast rollback path.
- A Cloud CDN backend bucket for `skytruth-shared-datasets-1`.
- URL map rules from `/pmtiles/{access-tier}/{asset}.pmtiles` paths to
  canonical `latest/{asset}.pmtiles` objects, generated from active PMTiles
  catalog rows.
- Optional Cloud CDN fill access:
  `service-${PROJECT_NUMBER}@cloud-cdn-fill.iam.gserviceaccount.com` receives
  `roles/storage.objectViewer` when
  `pmtiles_cdn_grant_fill_service_account=true`.
- Managed folders for `_catalog/` and every catalog asset root. Only folders
  whose catalog row has `access_tier=public` grant `allUsers`
  `roles/storage.objectViewer`.
- A temporary bucket-wide `allUsers` `roles/storage.objectViewer` grant,
  controlled by `shared_bucket_public_object_viewer_enabled`. Keep it enabled
  while managed-folder grants are first applied; set it to `false` only after
  public managed-folder coverage has been planned, applied, and verified.
- A Secret Manager secret container named `pmtiles-cdn-signed-request-key`.
- Optional `roles/secretmanager.secretAccessor` grants for consumer runtime
  service accounts listed in
  `cerulean_pmtiles_cookie_signer_service_accounts`. The variable name is
  Cerulean-specific for historical reasons; use it for additional approved
  signer runtimes unless it is renamed in a separate Terraform cleanup.

The raw Cloud CDN signing key value is intentionally not managed by Terraform.
Do not put signed request key material into Terraform variables, resources,
outputs, commit history, logs, or PR comments.

## Current Implementation State

As of the May 5, 2026 cookie-mediated CDN prep:

- `pmtiles_serving_mode` is still `redirect`.
- The managed certificate for `tiles.skytruth.org` is active.
- Public managed folders and managed-folder `allUsers` objectViewer grants have
  been applied for `_catalog/` and every current public asset root.
- The bucket-wide `allUsers` objectViewer grant is still enabled as the
  temporary bridge.
- The backend bucket has signed request key
  `shared-datasets-pmtiles-v1`.
- Secret Manager secret `pmtiles-cdn-signed-request-key` has enabled version
  `1`.
- Cloud CDN fill service account
  `service-12695949518@cloud-cdn-fill.iam.gserviceaccount.com` has
  `roles/storage.objectViewer` on `gs://skytruth-shared-datasets-1`.
- Cerulean runtime service account
  `734798842681-compute@developer.gserviceaccount.com` has
  `roles/secretmanager.secretAccessor` on the signing-key secret.

Do not remove the bucket-wide public grant or switch to CDN mode until
consuming apps that need private PMTiles have deployed signed-cookie support.

## Temporary Redirect Mode

`pmtiles_serving_mode="redirect"` routes `/pmtiles/*` to the Cloud Run
redirector. The redirector accepts only tiered public URLs for current assets:

```text
https://tiles.skytruth.org/pmtiles/public/{asset}.pmtiles
```

It validates the asset slug, confirms the catalog row is `access_tier=public`,
and returns:

```text
307 Temporary Redirect
Location: https://storage.googleapis.com/skytruth-shared-datasets-1/.../latest/{asset}.pmtiles
```

The redirector returns `404` for:

- `/pmtiles/private/{asset}.pmtiles`
- unknown assets
- assets whose catalog tier does not match the requested tier

Redirect mode keeps the browser-facing URL stable before the signed-cookie CDN
path is ready. It does not make PMTiles private, and browser network tools will
still show the final `storage.googleapis.com` request.

Build and push the redirector image before opening the PR that switches
redirect mode:

```bash
IMAGE=us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/pmtiles-redirector:$(date -u +%Y%m%d%H%M%S)

docker build --platform linux/amd64 -f services/pmtiles_redirector/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"
```

Then update `pmtiles_serving_mode` and `pmtiles_redirector_image` in Terraform,
open a PR, and let the protected workflow apply after review and merge.

## DNS

After Terraform creates the load balancer address, point `tiles.skytruth.org` at
the `pmtiles_cdn_ip_address` output with an A record. The managed certificate
will not become active until DNS points at the load balancer.

## Signed Request Key Setup

Create the key file on a secured workstation or CI runner, upload it to the CDN
backend bucket key set, then store the same key bytes as a Secret Manager secret
version for authorized consumer cookie-signing runtimes:

```bash
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
KEY_FILE="$WORK_ROOT/_scratch/pmtiles-cdn-key-$(date -u +%Y%m%dT%H%M%SZ).txt"
mkdir -p "$(dirname "$KEY_FILE")"
umask 077
head -c 16 /dev/urandom | base64 | tr +/ -_ > "$KEY_FILE"

gcloud compute backend-buckets add-signed-url-key shared-datasets-pmtiles-cdn \
  --key-name=shared-datasets-pmtiles-v1 \
  --key-file="$KEY_FILE" \
  --project=shared-datasets-1

gcloud secrets versions add pmtiles-cdn-signed-request-key \
  --data-file="$KEY_FILE" \
  --project=shared-datasets-1
```

Delete `$KEY_FILE` after the key has been installed and verified. Because this
is a local sensitive file, agents must ask for action-time confirmation before
deleting it.

After adding the key, confirm that the Google-managed Cloud CDN fill service
account exists:

```bash
PROJECT_NUMBER=$(gcloud projects describe shared-datasets-1 \
  --format='value(projectNumber)')

gcloud iam service-accounts describe \
  service-${PROJECT_NUMBER}@cloud-cdn-fill.iam.gserviceaccount.com \
  --project=shared-datasets-1
```

Then set `pmtiles_cdn_grant_fill_service_account=true` in Terraform so the
service account can read PMTiles after public GCS access is removed. Open a PR
and let the protected workflow apply after review and merge.

Grant consumer runtime access to the signing secret in the same PR. The
Cerulean rollout used the current UI runtime service account:

```hcl
cerulean_pmtiles_cookie_signer_service_accounts = [
  "serviceAccount:734798842681-compute@developer.gserviceaccount.com",
]
```

For another repo such as 30x30, replace that member with the runtime service
account that serves the cookie endpoint. Do not assume the repo-provisioned
reader account is the signer unless that account actually runs the endpoint.
HTTPS SkyTruth subdomains are allowed through the
`pmtiles_cdn_allowed_origin_regexes` value only while the temporary Cloud Run
redirector serves `/pmtiles/*`. The external Cloud CDN backend-bucket URL map
cannot use URL-map CORS regexes, and Cloud Armor edge policies on backend
buckets cannot evaluate request-header expressions, so CDN mode requires exact
browser origins in `pmtiles_cdn_allowed_origins`. Add each non-local browser
origin explicitly before CDN cutover. Credentialed CORS must not use `*` or a
literal `https://*.skytruth.org` origin.

The current exact CDN allowlist is:

- `http://localhost:3000`
- `https://localhost:3000`
- `https://feature-three.cerulean.skytruth.org`
- `https://test.cerulean.skytruth.org`
- `https://develop.cerulean.skytruth.org`
- `https://cerulean.skytruth.org`
- `https://30x30.skytruth.org`
- `https://monitor.skytruth.org`

The shared bucket CORS origins use the same exact list so backend-bucket CDN
range responses do not inherit a wildcard `Access-Control-Allow-Origin` from
GCS when requests include credentials.

Keep redirector regexes free of capture groups when possible; Cloud Armor
rejected capture groups during testing, and non-capturing groups are portable.

Consumers should sign cookie policies only for the private tier prefix:

```text
https://tiles.skytruth.org/pmtiles/private/
```

The cookie value must contain `URLPrefix`, `Expires`, `KeyName`, and
`Signature`, in that order, using HMAC-SHA1 over the unsigned policy.
Preserve base64url padding if the runtime emits it. The HMAC key is the decoded
raw 16-byte key, not the encoded secret text.

## Consumer Runtime Contract

In temporary redirect mode, consumers can load public-tier PMTiles URLs directly
without a PMTiles session endpoint:

```text
https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles
```

Any consumer that may display private PMTiles should expose
`/api/pmtiles/session` or an equivalent backend route that:

- Returns `204` without a cookie for `tier=public`.
- Requires an authenticated session for `tier=private`.
- Allows private PMTiles to SkyTruth or admin users for the first rollout.
- Reads
  `projects/shared-datasets-1/secrets/pmtiles-cdn-signed-request-key/versions/latest`.
- Signs `https://tiles.skytruth.org/pmtiles/private/`, not individual PMTiles
  object URLs.
- Uses key name `shared-datasets-pmtiles-v1`.
- Sets `Cloud-CDN-Cookie` with `Domain=.skytruth.org`, `Path=/pmtiles`,
  `Secure`, `HttpOnly`, `SameSite=None`, and a 1-hour TTL.
- Returns `Cache-Control: no-store`.
- Never logs or returns the key or cookie value.

Map PMTiles fetches must use `credentials: "include"` when signed cookies are
required so the browser sends the cross-site cookie to `tiles.skytruth.org`.

Consumer config APIs should derive shared PMTiles URLs from catalog
`access_tier` and emit only this URL shape:

```text
https://tiles.skytruth.org/pmtiles/{public-or-private}/{slug}.pmtiles
```

Do not emit direct
`https://storage.googleapis.com/skytruth-shared-datasets-1/.../*.pmtiles`
browser URLs for shared-dataset PMTiles. If the consumer config response is
cached, bump its cache key as part of the change.

Before mounting a private PMTiles layer, the frontend should call the session
endpoint:

```ts
await fetch("/api/pmtiles/session?tier=private", {
  credentials: "include"
});
```

Only mount the private layer after a successful response. If the PMTiles
library hides byte-range requests, wrap its source or protocol implementation
so header, directory, and tile range requests all include browser credentials.

For WDPA MPA layers, consumers should use the site ID field as the feature
identity and should not bind UI behavior to `WDPAID`.

## Rollout

Current redirect rollout:

1. Build and push the PMTiles redirector image.
2. Apply Terraform with `pmtiles_serving_mode="redirect"` and
   `pmtiles_redirector_image` set to the pushed image.
3. Configure DNS and wait for the managed certificate to become active.
4. Verify `https://tiles.skytruth.org/pmtiles/public/{asset}.pmtiles` returns
   `307` and follows to a public GCS PMTiles object.
5. Deploy consuming apps with tiered `tiles.skytruth.org` PMTiles URLs.

CDN signed-cookie rollout:

1. Apply managed-folder public grants while keeping
   `shared_bucket_public_object_viewer_enabled=true`.
2. Install the signed request key on the backend bucket and Secret Manager.
3. Enable `pmtiles_cdn_grant_fill_service_account=true`, grant signer access
   for each approved consumer runtime, and apply Terraform.
4. Deploy each consumer environment with PMTiles session endpoint support,
   tiered CDN URLs, and credentialed PMTiles range fetches.
5. Set `shared_bucket_public_object_viewer_enabled=false` and apply Terraform
   after verifying public managed-folder coverage.
6. Set `pmtiles_serving_mode="cdn"` and apply Terraform.
7. Invalidate `/pmtiles/*`.
8. Verify public anonymous access and private signed-cookie access before
   declaring cutover complete.

Use the current Cerulean UI runtime service account for this rollout:

```text
serviceAccount:734798842681-compute@developer.gserviceaccount.com
```

For later consumer rollouts, record the consumer runtime service account, exact
origin, changed repo files, and validation results before asking shared-datasets
to remove public origin access or switch serving mode.

## Consumer Patch Recipe

Apply this recipe to any downstream repo, including 30x30:

1. Search for:

   ```text
   storage.googleapis.com/skytruth-shared-datasets-1
   tiles.skytruth.org/pmtiles/
   .pmtiles
   access_tier
   Cloud-CDN-Cookie
   pmtiles/session
   ```

2. Replace direct browser PMTiles URLs with tiered CDN URLs from the catalog:

   ```ts
   const SHARED_PMTILES_BASE_URL =
     process.env.NEXT_PUBLIC_SHARED_PMTILES_BASE_URL ??
     "https://tiles.skytruth.org/pmtiles";

   const pmtilesUrl =
     `${SHARED_PMTILES_BASE_URL}/${accessTier}/${assetSlug}.pmtiles`;
   ```

3. If the app has a config API, parse catalog `access_tier`, reject missing or
   unknown tiers, and bump any Redis or process cache key.
4. Add a backend session endpoint with the behavior in
   [Consumer Runtime Contract](#consumer-runtime-contract).
5. Add `credentials: "include"` to every PMTiles fetch path, including internal
   range fetches used by PMTiles libraries.
6. Before enabling a private layer, call
   `/api/pmtiles/session?tier=private`; public layers do not require a cookie.
7. Keep `https://tiles.skytruth.org` in CSP `connect-src`.
8. Add or update tests for public URL generation, private URL generation,
   anonymous private rejection, authorized cookie issuance, no-store response
   headers, and credentialed PMTiles fetches.
9. Run the consumer repo's lint, type-check, and production build.

The signed cookie must be scoped to:

```text
Domain=.skytruth.org; Path=/pmtiles; Secure; HttpOnly; SameSite=None
```

Use a one-hour TTL for the first rollout. Do not log the secret bytes, HMAC
input key, HMAC output, or final cookie value.

## Validation

Shared-datasets validation before each protected Terraform workflow apply:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest \
  tests/test_shared_dataset_sdk.py \
  tests/test_catalog_site.py \
  tests/test_pmtiles_redirector.py \
  tests/test_terraform_prod_apply.py

terraform -chdir=terraform/envs/prod validate
terraform -chdir=terraform/envs/prod plan ...
```

The automatic PMTiles CDN sync workflow fails loudly when Terraform
authentication is not configured. Repository variables must include
`GCP_TERRAFORM_SERVICE_ACCOUNT` and either
`GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER` or the shared
`GCP_WORKLOAD_IDENTITY_PROVIDER`; otherwise PMTiles access-tier changes are not
allowed to merge as silently skipped post-merge work.

Live checks after CDN cutover:

- Public direct GCS asset still returns `200`.
- Private direct GCS asset returns `403`.
- `_catalog/shared-datasets-catalog.csv` still returns `200`.
- Public CDN range request without a cookie returns `200` or `206`.
- Private CDN range request without a cookie returns `403`.
- Private CDN range request with a valid cookie returns `200` or `206`.
- Expired or malformed cookie returns `403`.
- CORS preflight from each configured exact consumer origin allows credentials
  and `Range`; a non-configured `Origin` such as `https://skytruth.org` or
  `https://evilskytruth.org` is rejected.

## Cache Operations

PMTiles under `latest/` are treated as mostly immutable serving artifacts. When
a `latest/*.pmtiles` object is replaced, invalidate the corresponding tiered CDN
path:

```bash
gcloud compute url-maps invalidate-cdn-cache shared-datasets-pmtiles-cdn \
  --path="/pmtiles/public/wdpa-marine.pmtiles" \
  --project=shared-datasets-1
```

Prefer targeted invalidations. Use `/pmtiles/*` only for broad emergency
rollbacks.
