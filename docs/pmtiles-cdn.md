# PMTiles Browser Access

Shared PMTiles are stored once, under their canonical
`gs://skytruth-shared-datasets-1/.../latest/{asset}.pmtiles` object paths. Browser
clients should use the shared `tiles.skytruth.org` surface instead:

```text
https://tiles.skytruth.org/pmtiles/{asset}.pmtiles
```

Today this path is served by a temporary Cloud Run redirector that returns
`307 Temporary Redirect` to the current public GCS PMTiles object. The future
serving mode is Cloud CDN with signed cookies. The SDK resolves PMTiles to the
same browser URL in both modes while keeping `DatasetRef.gs_uri` as the durable
object identity.

## Terraform-Owned Resources

The production Terraform stack owns:

- A global HTTPS load balancer address and managed certificate for
  `tiles.skytruth.org`.
- A temporary Cloud Run redirector for `pmtiles_serving_mode="redirect"`.
- A Cloud CDN backend bucket for `skytruth-shared-datasets-1`.
- URL map rules from flat `/pmtiles/{asset}.pmtiles` paths to canonical
  `latest/{asset}.pmtiles` objects. The rules are generated from
  `catalog/shared-datasets-catalog.csv` rows with active PMTiles.
- Optional Cloud CDN fill access:
  `service-${PROJECT_NUMBER}@cloud-cdn-fill.iam.gserviceaccount.com` receives
  `roles/storage.objectViewer` on the shared bucket when
  `pmtiles_cdn_grant_fill_service_account=true`.
- A Secret Manager secret container named `pmtiles-cdn-signed-request-key`.
- Optional `roles/secretmanager.secretAccessor` grants for Cerulean runtime
  service accounts listed in
  `cerulean_pmtiles_cookie_signer_service_accounts`.

The raw Cloud CDN signing key value is intentionally not managed by Terraform.
The current Terraform state lives in the shared bucket, and the bucket remains
public during rollout. Do not put signed request key material into Terraform
variables, resources, outputs, commit history, logs, or PR comments.

## Temporary Redirect Mode

`pmtiles_serving_mode="redirect"` routes `/pmtiles/*` to the Cloud Run
redirector. The redirector validates `/pmtiles/{asset}.pmtiles`, resolves the
asset through the shared catalog, and returns:

```text
307 Temporary Redirect
Location: https://storage.googleapis.com/skytruth-shared-datasets-1/.../latest/{asset}.pmtiles
```

This mode keeps the browser-facing `tiles.skytruth.org` URL stable before the
signed-cookie CDN path is ready. It does not make PMTiles private, and browser
network tools will still show the final `storage.googleapis.com` request.

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

After Terraform creates the load balancer address, point
`tiles.skytruth.org` at the `pmtiles_cdn_ip_address` output with an A record.
The managed certificate will not become active until DNS points at the load
balancer.

## Signed Request Key Setup

Create the key file on a secured workstation or CI runner, upload it to the CDN
backend bucket key set, then store the same key bytes as a Secret Manager secret
version for authorized Cerulean cookie-signing runtimes:

```bash
head -c 16 /dev/urandom | base64 | tr +/ -_ > /tmp/pmtiles-cdn-key.txt

gcloud compute backend-buckets add-signed-url-key shared-datasets-pmtiles-cdn \
  --key-name=shared-datasets-pmtiles-v1 \
  --key-file=/tmp/pmtiles-cdn-key.txt \
  --project=shared-datasets-1

gcloud secrets versions add pmtiles-cdn-signed-request-key \
  --data-file=/tmp/pmtiles-cdn-key.txt \
  --project=shared-datasets-1
```

Delete `/tmp/pmtiles-cdn-key.txt` after the key has been installed and verified.

The Google-managed Cloud CDN fill service account is created by Google after a
signed request key exists on the backend bucket. After adding the key, confirm
that the service account exists:

```bash
PROJECT_NUMBER=$(gcloud projects describe shared-datasets-1 \
  --format='value(projectNumber)')

gcloud iam service-accounts describe \
  service-${PROJECT_NUMBER}@cloud-cdn-fill.iam.gserviceaccount.com \
  --project=shared-datasets-1
```

Then set `pmtiles_cdn_grant_fill_service_account=true` and apply Terraform again
so the service account can read PMTiles from the private bucket after public GCS
access is removed.

Cloud CDN uses the same signed request key mechanism for signed cookies and
signed URLs. Cerulean should read the secret at runtime, decode the base64url key
to raw bytes, and sign a cookie policy for this URL prefix:

```text
https://tiles.skytruth.org/pmtiles/
```

The cookie value must contain `URLPrefix`, `Expires`, `KeyName`, and
`Signature`, in that order, using HMAC-SHA1 over the unsigned policy.

## Cerulean Runtime Contract

In temporary redirect mode, Cerulean can load the stable PMTiles URLs directly
without a PMTiles session endpoint. The request will redirect to public GCS.

In future CDN signed-cookie mode, Cerulean should expose an endpoint such as
`/api/pmtiles/session` that:

- Verifies the user is allowed to load the Cerulean map.
- Signs the shared URL prefix, not individual PMTiles object URLs.
- Sets `Cloud-CDN-Cookie` with `Secure`, `SameSite=None`, `Path=/pmtiles`, and
  `Domain=.skytruth.org`.
- Uses `HttpOnly` unless client code has a specific need to inspect the cookie.
- Uses a short TTL, typically 1 to 6 hours.

Map PMTiles fetches must use `credentials: "include"` so the browser sends the
cross-site cookie to `tiles.skytruth.org`.

For WDPA MPA layers, consumers should use the site ID field as the feature
identity and should not bind UI behavior to `WDPAID`. The current shared
datasets WDPA ingestion uses the upstream `SITE_ID` field for deterministic
sampling, and the browser-facing contract should treat site ID as the durable
MPA selection/join key.

## Rollout

1. Build and push the PMTiles redirector image.
2. Apply Terraform with `pmtiles_serving_mode="redirect"` and
   `pmtiles_redirector_image` set to the pushed image.
3. Configure DNS and wait for the managed certificate to become active.
4. Verify `https://tiles.skytruth.org/pmtiles/{asset}.pmtiles` returns `307`
   and follows to a public GCS PMTiles object.
5. Deploy consuming apps with stable `tiles.skytruth.org` PMTiles URLs.

Future CDN signed-cookie rollout:

1. Keep `allUsers` public object access in place.
2. Configure DNS and wait for the managed certificate to become active.
3. Install the signed request key on the backend bucket and Secret Manager.
4. Enable `pmtiles_cdn_grant_fill_service_account=true` and apply Terraform
   again so Cloud CDN can fill from private GCS.
5. Grant the Cerulean runtime service account access to the secret with
   `cerulean_pmtiles_cookie_signer_service_accounts`.
6. Switch Terraform to `pmtiles_serving_mode="cdn"`.
7. Deploy Cerulean with the PMTiles session endpoint and the same PMTiles URLs.
8. Verify the Cerulean map makes no PMTiles requests to
   `storage.googleapis.com`.
9. Invalidate `/pmtiles/*` or the PMTiles paths touched during rollout before
   removing public GCS access. Signed and unsigned Cloud CDN cache entries are
   separate, and public-rollout testing can leave temporary unsigned cache
   entries behind.
10. Verify unsigned CDN requests return `403` once public bucket access is
   removed, and signed requests return `200` or `206`.
11. Remove `google_storage_bucket_iam_member.shared_bucket_public_object_viewer`
   in a separate Terraform change after CDN access is proven.

## Cache Operations

PMTiles under `latest/` are treated as mostly immutable serving artifacts. When
a `latest/*.pmtiles` object is replaced, invalidate the corresponding flat CDN
path:

```bash
gcloud compute url-maps invalidate-cdn-cache shared-datasets-pmtiles-cdn \
  --path="/pmtiles/wdpa-marine.pmtiles" \
  --project=shared-datasets-1
```

Prefer targeted invalidations. Use `/pmtiles/*` only for broad emergency
rollbacks.
