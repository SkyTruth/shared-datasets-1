# Private PMTiles Signed Cookies

Read this reference before implementing, reviewing, or debugging private
shared-datasets PMTiles in this consumer repo or app.

## Shared-Datasets Prerequisites

Before deploying this consumer environment, identify the backend or UI runtime
service account that serves this app's PMTiles session endpoint. Grant that
service account access to the shared signing-key secret from the upstream
`shared-datasets-1` project; do not create or distribute a service account key
file.

Shared signing material:

```text
Secret: configured PMTiles CDN signing-key secret in the upstream project
Cloud CDN key name: shared-datasets-pmtiles-v1
Signed prefix: https://tiles.skytruth.org/pmtiles/private/
```

In the upstream shared-datasets-1 Terraform, add this app's signer principal to
the signer allowlist used by
the PMTiles CDN cookie-signer IAM binding. For this repo or app, use its real
runtime service account. A reader service account is not automatically the
signer; the signer should be the service account actually running the endpoint
that issues the cookie.

CDN backend-bucket mode requires exact browser origins; credentialed CORS
cannot use `*`, `https://*.skytruth.org`, or redirector-only regex lists. Add
each browser origin before deploying private PMTiles in that environment.
Re-check the upstream infrastructure review materials before relying on any
origin list.

Any upstream shared-datasets-1 Terraform change must land through a reviewed PR
and the protected production workflow after merge, not a local apply.

## App Backend Endpoint

Add an endpoint like:

```text
GET /api/pmtiles/session?tier=public
GET /api/pmtiles/session?tier=private
DELETE /api/pmtiles/session
```

Use the TypeScript SDK server helpers for cookie signing rather than
reimplementing the Cloud CDN policy format:

```ts
import {
  decodePmtilesCdnSigningKey,
  getExpiredPmtilesCookies,
  getPrivatePmtilesSessionCookies
} from "@skytruth/shared-datasets/server";
import { getPmtilesTier } from "@skytruth/shared-datasets";
```

Required behavior:

- `tier=public` returns `204` without a cookie.
- `tier=private` requires an authenticated user session.
- For the first rollout, allow only SkyTruth or admin users unless this app has
  a more specific entitlement model.
- Unknown `tier` returns `400`.
- Unauthorized private users return `401` or `403`.
- Authorized private users read the configured PMTiles CDN signing key from the
  backend secret store.
- Decode the secret value from base64url to the raw 16-byte Cloud CDN key.
- Sign `https://tiles.skytruth.org/pmtiles/private/`, not individual PMTiles
  URLs.
- Use HMAC-SHA1 and key name `shared-datasets-pmtiles-v1`.
- Set every `Set-Cookie` header returned by `getPrivatePmtilesSessionCookies`;
  the helper returns an array that clears the legacy path and sets the private
  signed cookie.
- `DELETE` clears every cookie returned by `getExpiredPmtilesCookies` and
  returns `204`.
- For both helper calls, send the returned string array as separate
  `Set-Cookie` headers; do not comma-join it into one header value.
- Set `Cache-Control: no-store`.
- Never log or return the key bytes, HMAC input, HMAC output, unsigned policy,
  full cookie value, or signed URL.

The private session cookie must be named `Cloud-CDN-Cookie` and set with:

```text
Domain=.skytruth.org
Path=/pmtiles/private
Secure
HttpOnly
SameSite=None
Max-Age=86400
Expires=<24 hours from now>
```

Cloud CDN signed-cookie policy fields must be in this order:

```text
URLPrefix=<base64url-prefix>:Expires=<unix-seconds>:KeyName=shared-datasets-pmtiles-v1:Signature=<base64url-hmac>
```

Keep base64url padding characters if the runtime's encoder emits them. The key
used for HMAC is the decoded raw 16-byte key, not the encoded secret text.

## App Frontend Loading

Use the TypeScript SDK browser helpers before enabling private shared layers:

```ts
import {
  ensurePmtilesCdnSession,
  getPmtilesFetchCredentials
} from "@skytruth/shared-datasets";

const result = await ensurePmtilesCdnSession({
  accessTier: "private",
  endpoint: "/api/pmtiles/session"
});

if (!result.ok) {
  throw new Error("Private PMTiles access was not granted.");
}
```

Only add the private layer if the result is successful. Public layers may skip
the call or use `accessTier: "public"`, which does not contact the endpoint.

On app sign-out, call the same endpoint with `clearPmtilesCdnSession` before or
alongside the app's own session cleanup so private CDN cookies are expired:

```ts
import { clearPmtilesCdnSession } from "@skytruth/shared-datasets";

await clearPmtilesCdnSession({ endpoint: "/api/pmtiles/session" });
```

PMTiles byte-range requests must include credentials so the browser sends the
cross-site cookie to `tiles.skytruth.org`. If the PMTiles library hides
byte-range fetches, wrap or subclass its fetch source so every header,
directory, and tile range request includes:

```ts
fetch(url, {
  headers: { range: "bytes=start-end" },
  credentials: getPmtilesFetchCredentials(url)
});
```

Keep CSP `connect-src` allowing `https://tiles.skytruth.org`. Remove direct
`storage.googleapis.com` dependencies only for shared-dataset PMTiles access; do
not remove `storage.googleapis.com` if the app still uses it for unrelated
features.

## Validation

Run this repo's lint, type, test, and build checks. Also verify:

- Public PMTiles load without a cookie.
- Anonymous private users are denied.
- Authorized private users receive `Cloud-CDN-Cookie` and `Cache-Control:
  no-store`.
- Private PMTiles range requests carry the `Cloud-CDN-Cookie`.
- Expired or malformed cookies fail with `403`.
- The deployed browser origin is exactly allowlisted for credentialed CORS.
- No logs contain secret bytes, HMAC material, signed cookie values, or GCS
  credentials.
