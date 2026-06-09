# PMTiles Browser Access

Shared PMTiles are stored once under canonical
`gs://skytruth-shared-datasets-1/.../latest/{asset}.pmtiles` object paths.
Browser clients use the tiered shared URL surface:

```text
https://tiles.skytruth.org/pmtiles/public/{asset}.pmtiles
https://tiles.skytruth.org/pmtiles/private/{asset}.pmtiles
```

Release artifacts that are not PMTiles, including generated localized metadata
sidecars, use the artifact route:

```text
https://tiles.skytruth.org/artifacts/{bucket-object-path}
```

Public metadata sidecars are fetched from this route anonymously. Private
metadata sidecars use short-lived signed URLs under
`/private/{bucket-object-path}`, issued only
by a consuming backend or the IAP-protected catalog viewer after authorization.
Both artifact routes strip their prefix before
fetching from `gs://skytruth-shared-datasets-1/{bucket-object-path}`. Unsigned
requests to private release artifacts must not return readable bytes after
cutover.

The internal catalog viewer does not depend on these routes for private map
preview. It is a Cloud Run service in `shared-datasets-1`, protected by direct
Cloud Run IAP on its generated `run.app` URL, and it resolves private PMTiles
through short-lived signed `storage.googleapis.com` URLs from
`/api/pmtiles/signed-url?slug={asset-slug}`. The CDN remains the compatibility
path for downstream consumers that use `tiles.skytruth.org` directly. For
private metadata sidecars, the production catalog viewer may return a signed
`tiles.skytruth.org/private/...` URL from `/api/download-url`; private FGB and
PMTiles download behavior remains signed GCS.

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
- Public `_catalog/*` routes on `tiles.skytruth.org` so catalog CSV, generated
  catalog web files, docs Markdown, and release indexes remain freely readable
  after direct public GCS access is removed.
- A canonical artifact route from `/artifacts/*` to bucket object paths, used
  for public metadata sidecars.
- A private artifact route from `/private/*` to bucket object paths, used for
  signed private metadata sidecars.
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
- A Secret Manager secret container for the PMTiles CDN signing key.
- Optional `roles/secretmanager.secretAccessor` grants for consumer runtime
  service accounts listed in the PMTiles cookie-signer allowlist.

The raw Cloud CDN signing key value is intentionally not managed by Terraform.
Do not put signed request key material into Terraform variables, resources,
outputs, commit history, logs, or PR comments.

## Current Implementation State

As of May 12, 2026:

- `pmtiles_serving_mode` is `cdn`.
- The managed certificate for `tiles.skytruth.org` is active.
- The bucket-wide `allUsers` objectViewer bridge is disabled in
  `production.auto.tfvars`.
- The URL map serves latest PMTiles through `/pmtiles/{tier}/{asset}.pmtiles`.
- The URL map serves public catalog files through `/_catalog/*` so the catalog
  remains freely readable after direct public GCS access is removed.
- The URL map serves release-artifact URLs through
  `/artifacts/{bucket-object-path}`, including
  `{asset}.metadata.{locale}.ndjson.gz` sidecars.
- The URL map still accepts legacy signed private release-artifact URLs through
  `/private/{bucket-object-path}` as a compatibility alias.
- The backend bucket has signed request key
  `shared-datasets-pmtiles-v1`.
- The PMTiles CDN signing-key secret has an enabled version.
- The Cloud CDN fill service account has `roles/storage.objectViewer` on
  `gs://skytruth-shared-datasets-1`.
- Approved consumer runtime service accounts have
  `roles/secretmanager.secretAccessor` on the signing-key secret.

Managed-folder `allUsers` grants for direct GCS reads are a temporary
proof-of-concept bypass and should be removed after the `tiles.skytruth.org`
catalog route and consumer PMTiles paths are verified.

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
GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:?}"
PMTILES_CDN_BACKEND_BUCKET="${PMTILES_CDN_BACKEND_BUCKET:?}"
PMTILES_CDN_SIGNING_KEY_NAME="${PMTILES_CDN_SIGNING_KEY_NAME:-shared-datasets-pmtiles-v1}"
PMTILES_CDN_SIGNING_SECRET_ID="${PMTILES_CDN_SIGNING_SECRET_ID:?}"
KEY_FILE="$WORK_ROOT/_scratch/pmtiles-cdn-key-$(date -u +%Y%m%dT%H%M%SZ).txt"
mkdir -p "$(dirname "$KEY_FILE")"
umask 077
head -c 16 /dev/urandom | base64 | tr +/ -_ > "$KEY_FILE"

gcloud compute backend-buckets add-signed-url-key "$PMTILES_CDN_BACKEND_BUCKET" \
  --key-name="$PMTILES_CDN_SIGNING_KEY_NAME" \
  --key-file="$KEY_FILE" \
  --project="$GOOGLE_CLOUD_PROJECT"

gcloud secrets versions add "$PMTILES_CDN_SIGNING_SECRET_ID" \
  --data-file="$KEY_FILE" \
  --project="$GOOGLE_CLOUD_PROJECT"
```

Delete `$KEY_FILE` after the key has been installed and verified. Because this
is a local sensitive file, agents must ask for action-time confirmation before
deleting it.

After adding the key, confirm that the Google-managed Cloud CDN fill service
account exists:

```bash
PROJECT_NUMBER=$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" \
  --format='value(projectNumber)')

gcloud iam service-accounts describe \
  service-${PROJECT_NUMBER}@cloud-cdn-fill.iam.gserviceaccount.com \
  --project="$GOOGLE_CLOUD_PROJECT"
```

Then set `pmtiles_cdn_grant_fill_service_account=true` in Terraform so the
service account can read PMTiles after public GCS access is removed. Open a PR
and let the protected workflow apply after review and merge.

Grant consumer runtime access to the signing secret in the same PR. Use the
runtime service account that serves the cookie endpoint. Do not assume the
repo-provisioned reader account is the signer unless that account actually runs
the endpoint.
HTTPS SkyTruth subdomains can be allowed by regex only while the temporary
Cloud Run redirector serves `/pmtiles/*`. The external Cloud CDN backend-bucket
URL map cannot use URL-map CORS regexes, and Cloud Armor edge policies on
backend buckets cannot evaluate request-header expressions, so CDN mode
requires exact browser origins in the credentialed CDN CORS allowlist. Add each
non-local browser origin explicitly before CDN cutover. Credentialed CORS must
not use `*` or a literal `https://*.skytruth.org` origin. The exact CDN
allowlist is operational infrastructure; use internal Terraform outputs or
maintainer runbooks to verify it.

The shared bucket CORS origins should use the same exact list so backend-bucket
CDN range responses do not inherit a wildcard `Access-Control-Allow-Origin`
from GCS when requests include credentials.

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

The catalog viewer and approved consuming backends use the same Cloud CDN key
format for private metadata sidecar signed URLs. Those URLs sign the exact
artifact object URL, for example:

```text
https://tiles.skytruth.org/private/100-geographic-reference/130-protected-areas/wdpa-marine/releases/2026-05-01/wdpa-marine.metadata.es.ndjson.gz
```

Use signed URLs for metadata sidecars, not signed cookies, because the frontend
fetches exactly one metadata sidecar for the active locale and does not need a
broader PMTiles cookie session.

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
- Reads the configured PMTiles CDN signing key from the consumer backend's
  secret store.
- Signs `https://tiles.skytruth.org/pmtiles/private/`, not individual PMTiles
  object URLs.
- Uses key name `shared-datasets-pmtiles-v1`.
- Sets `Cloud-CDN-Cookie` with `Domain=.skytruth.org`,
  `Path=/pmtiles/private`,
  `Secure`, `HttpOnly`, `SameSite=None`, and a 24-hour TTL.
- Returns `Cache-Control: no-store`.
- Never logs or returns the key or cookie value.

Any consumer that may display private feature metadata should expose a separate
backend route such as
`/api/shared-datasets/metadata-url?slug=&version=&locale=`. That route must
validate the slug, release, locale, catalog access tier, and user entitlement
before signing an exact artifact URL. It must return `Cache-Control: no-store`
and must never sign arbitrary caller-provided bucket object paths.

Consumer TypeScript apps should use `@skytruth/shared-datasets/server` for the
cookie helpers and the browser-safe `@skytruth/shared-datasets` entrypoint for
session and PMTiles fetch helpers. See the
[TypeScript SDK README](../api/typescript/README.md) for install and entrypoint
details.

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
cached, bump its cache key as part of the change. PMTiles layer/config responses
should also preserve release metadata sidecar references when present so
browser labels, popups, and feature inspectors can resolve display labels
through the selected metadata sidecar instead of hardcoding source-native
fields. Localized canonical views live in generated
`{asset-slug}.metadata.{locale}.ndjson.gz` sidecars created from
`{asset-slug}.metadata-translations.csv`; PMTiles carry `feature_id`, not
`name` or `name_*` fields. Use translation `review_state` values from metadata
records to distinguish source-provided names, machine translations,
human-reviewed translations, and mixed review state in user-facing confidence
cues.

Before mounting a private PMTiles layer, the frontend should call the session
endpoint, preferably through `ensurePmtilesCdnSession`:

```ts
const result = await ensurePmtilesCdnSession({
  accessTier: "private",
  endpoint: "/api/pmtiles/session"
});
```

Only mount the private layer after a successful result. If the PMTiles library
hides byte-range requests, wrap its source or protocol implementation so header,
directory, and tile range requests use `getPmtilesFetchCredentials(url)`.

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

For consumer rollouts, record the approved consumer runtime service account,
exact origin, changed repo files, and validation results before asking
shared-datasets to remove public origin access or switch serving mode.

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

2. Replace direct browser PMTiles URLs with catalog `pmtiles_url` or tiered CDN
   URLs. TypeScript consumers should prefer the SDK catalog helpers when they
   read catalog JSON:

   ```ts
   import { resolveSharedDatasetPmtilesRef } from "@skytruth/shared-datasets";

   const ref = await resolveSharedDatasetPmtilesRef(assetSlug);
   const pmtilesUrl = ref.url;
   const releaseIndexUrl = ref.releaseIndexUrl;
   const latestRelease = ref.latestRelease;
   ```

3. If the app has a config API, parse catalog `access_tier`, reject missing or
   unknown tiers, preserve release metadata sidecar references for PMTiles
   layer labels and feature inspectors when present, and bump any Redis or
   process cache key.
4. Add a backend session endpoint with the behavior in
   [Consumer Runtime Contract](#consumer-runtime-contract).
5. Add a backend metadata URL endpoint if the app exposes private metadata
   sidecars in the browser.
6. Use `getPmtilesFetchCredentials(url)` or an equivalent credentialed fetch
   wrapper for every PMTiles fetch path, including internal range fetches used
   by PMTiles libraries.
7. Before enabling a private layer, call `ensurePmtilesCdnSession`; public
   layers do not require a cookie.
8. Keep `https://tiles.skytruth.org` in CSP `connect-src`.
9. Add or update tests for public URL generation, private URL generation,
   anonymous private rejection, authorized cookie issuance, no-store response
   headers, and credentialed PMTiles fetches.
10. Run the consumer repo's lint, type-check, and production build.

The signed cookie must be scoped to:

```text
Domain=.skytruth.org; Path=/pmtiles/private; Secure; HttpOnly; SameSite=None
```

Use a 24-hour TTL. Do not log the secret bytes, HMAC
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
`GCP_TERRAFORM_SERVICE_ACCOUNT` and
`GCP_TERRAFORM_WORKLOAD_IDENTITY_PROVIDER`; otherwise PMTiles access-tier
changes are not allowed to merge as silently skipped post-merge work. Catalog
and asset-documentation changes trigger PMTiles CDN sync only after the catalog
web deploy workflow completes, so route verification sees the refreshed
`_catalog/web/` objects. PMTiles Terraform-file changes still trigger the
protected sync workflow directly after merge. Workflow-only changes to
`.github/workflows/pmtiles-cdn-sync.yml` trigger the consolidated
`Protected Terraform readiness` workflow on PRs, but do not trigger the
protected apply workflow after merge. Dispatch `PMTiles CDN sync` manually from
`main` when a workflow-only change is intended to apply already-merged URL-map
configuration.

Live checks after CDN cutover and direct public GCS removal:

- Public direct GCS asset requests without credentials return `403`.
- Private direct GCS asset returns `403`.
- `https://tiles.skytruth.org/_catalog/shared-datasets-catalog.csv` returns
  `200`.
- `https://tiles.skytruth.org/_catalog/web/catalog.json` returns `200`.
- `https://tiles.skytruth.org/_catalog/web/index.html` returns `200`.
- Public CDN range request without a cookie returns `200` or `206`.
- Public metadata sidecar requests under `/artifacts/...` return `200`.
- Private CDN range request without a cookie returns `403`.
- Private CDN range request with a valid cookie returns `200` or `206`.
- Expired or malformed cookie returns `403`.
- Unsigned `/private/.../{asset}.metadata.{locale}.ndjson.gz` requests for
  private assets return
  `403` or another non-readable status.
- `/api/download-url?...format=metadata&locale={locale}` for an authorized
  private asset returns one signed
  `https://tiles.skytruth.org/private/...metadata.{locale}.ndjson.gz` URL when
  that locale exists, or one signed canonical `.metadata.ndjson.gz` fallback
  URL when it does not.
- Fetching the signed private metadata URL returns `200` with
  `application/x-ndjson`, `application/gzip`, or gzip-compatible metadata
  headers.
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

Catalog objects use revalidating cache metadata, but stale redirect responses
from URL-map changes can persist at the edge. After PMTiles CDN URL-map changes
that affect `/_catalog/*`, or after observing stale redirects from catalog
paths to `/pmtiles/`, invalidate the catalog prefix:

```bash
gcloud compute url-maps invalidate-cdn-cache shared-datasets-pmtiles-cdn \
  --path="/_catalog/*" \
  --project=shared-datasets-1
```

Then verify the exact catalog URLs without following redirects. Each first
response must be `200` with the expected content type:

```bash
curl -sS -o /dev/null -D - https://tiles.skytruth.org/_catalog/shared-datasets-catalog.csv
curl -sS -o /dev/null -D - https://tiles.skytruth.org/_catalog/web/catalog.json
curl -sS -o /dev/null -D - https://tiles.skytruth.org/_catalog/web/index.html
```
