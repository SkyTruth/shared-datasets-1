# PMTiles Browser Access

Shared PMTiles are stored once under canonical
`gs://skytruth-shared-datasets-1/.../latest/{asset}.pmtiles` object paths.
Browser clients use the tiered shared URL surface:

```text
https://tiles.skytruth.org/pmtiles/public/{asset}.pmtiles
```

All current PMTiles assets are `public`. The `private` tier is reserved for
future logged-in business logic:

```text
https://tiles.skytruth.org/pmtiles/private/{asset}.pmtiles
```

Cloud CDN signed cookies do not block unsigned requests by themselves. Google
Cloud CDN forwards unsigned cache misses to the origin, so private protection
later requires private origin access or an origin that rejects unsigned
requests. For Cloud Storage backends, remove public access after signed CDN
access is proven. See Google Cloud CDN signed-cookie documentation:
https://cloud.google.com/cdn/docs/using-signed-cookies

## Terraform-Owned Resources

The production Terraform stack owns:

- A global HTTPS load balancer address and managed certificate for
  `tiles.skytruth.org`.
- A temporary Cloud Run redirector for `pmtiles_serving_mode="redirect"`.
- A Cloud CDN backend bucket for `skytruth-shared-datasets-1`.
- URL map rules from `/pmtiles/{access-tier}/{asset}.pmtiles` paths to
  canonical `latest/{asset}.pmtiles` objects, generated from active PMTiles
  catalog rows.
- Optional Cloud CDN fill access:
  `service-${PROJECT_NUMBER}@cloud-cdn-fill.iam.gserviceaccount.com` receives
  `roles/storage.objectViewer` when
  `pmtiles_cdn_grant_fill_service_account=true`.
- A Secret Manager secret container named `pmtiles-cdn-signed-request-key`.
- Optional `roles/secretmanager.secretAccessor` grants for Cerulean runtime
  service accounts listed in
  `cerulean_pmtiles_cookie_signer_service_accounts`.

The raw Cloud CDN signing key value is intentionally not managed by Terraform.
Do not put signed request key material into Terraform variables, resources,
outputs, commit history, logs, or PR comments.

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

Build and push the redirector image before applying redirect mode:

```bash
IMAGE=us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/pmtiles-redirector:$(date -u +%Y%m%d%H%M%S)

docker build --platform linux/amd64 -f services/pmtiles_redirector/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"

UV_CACHE_DIR=.uv-cache uv run python scripts/terraform_prod_apply.py \
  --var "pmtiles_serving_mode=redirect" \
  --var "pmtiles_redirector_image=$IMAGE"
```

## DNS

After Terraform creates the load balancer address, point `tiles.skytruth.org` at
the `pmtiles_cdn_ip_address` output with an A record. The managed certificate
will not become active until DNS points at the load balancer.

## Signed Request Key Setup

Create the key file on a secured workstation or CI runner, upload it to the CDN
backend bucket key set, then store the same key bytes as a Secret Manager secret
version for authorized Cerulean cookie-signing runtimes:

```bash
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
KEY_FILE="$WORK_ROOT/_scratch/pmtiles-cdn-key-$(date -u +%Y%m%dT%H%M%SZ).txt"
mkdir -p "$(dirname "$KEY_FILE")"
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

Then set `pmtiles_cdn_grant_fill_service_account=true` and apply Terraform again
so the service account can read PMTiles after public GCS access is removed.

Cerulean should sign cookie policies for the tier prefix the user is allowed to
read:

```text
https://tiles.skytruth.org/pmtiles/public/
https://tiles.skytruth.org/pmtiles/private/
```

The cookie value must contain `URLPrefix`, `Expires`, `KeyName`, and
`Signature`, in that order, using HMAC-SHA1 over the unsigned policy.

## Cerulean Runtime Contract

In temporary redirect mode, Cerulean can load public-tier PMTiles URLs directly
without a PMTiles session endpoint:

```text
https://tiles.skytruth.org/pmtiles/public/wdpa-marine.pmtiles
```

In future CDN signed-cookie mode, Cerulean should expose an endpoint such as
`/api/pmtiles/session` that:

- Verifies the user is allowed to load the requested tier.
- Signs the tier URL prefix, not individual PMTiles object URLs.
- Sets `Cloud-CDN-Cookie` with `Secure`, `SameSite=None`, `Path=/pmtiles`, and
  `Domain=.skytruth.org`.
- Uses `HttpOnly` unless client code has a specific need to inspect the cookie.
- Uses a short TTL, typically 1 to 6 hours.

Map PMTiles fetches must use `credentials: "include"` when signed cookies are
required so the browser sends the cross-site cookie to `tiles.skytruth.org`.

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

Future CDN signed-cookie rollout:

1. Keep `allUsers` public object access in place for functional overlap.
2. Install the signed request key on the backend bucket and Secret Manager.
3. Enable `pmtiles_cdn_grant_fill_service_account=true` and apply Terraform.
4. Grant the Cerulean runtime service account secret access with
   `cerulean_pmtiles_cookie_signer_service_accounts`.
5. Switch Terraform to `pmtiles_serving_mode="cdn"`.
6. Deploy Cerulean with PMTiles session endpoint support and the same tiered
   PMTiles URLs.
7. Verify rendering, range requests, CORS, and no app regressions.
8. Invalidate `/pmtiles/*` or the PMTiles paths touched during rollout before
   removing public GCS access.
9. Remove public bucket/object access only after signed CDN requests return
   `200` or `206` and unsigned/private-tier requests fail as expected.

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
