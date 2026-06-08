# SkyTruth Shared Datasets TypeScript Helpers

Framework-neutral TypeScript helpers for browser and server code that consumes
SkyTruth shared-datasets PMTiles through the SkyTruth CDN.

Use this package for catalog-driven PMTiles URLs, browser CDN session handshakes,
PMTiles fetch credential selection, lightweight access-tier lookups, and
server-only Cloud CDN signed-cookie helpers. It does not own application
authentication, authorization, routing, secret storage, logging, UI behavior,
retries, or HTTP/error translation.

## Package Status And Installation

The package name is:

```text
@skytruth/shared-datasets
```

The package is published on npm. Consumers install it with:

```bash
npm install @skytruth/shared-datasets
```

Use a local path only for package development and integration testing against
unreleased local changes:

```bash
npm install ../shared-datasets-1/api/typescript
```

Do not commit local-path installs to production consumers. Verify the registry
version before changing production consumers:

```bash
npm view @skytruth/shared-datasets version
```

## Entrypoints

Use the browser-safe main entrypoint from client code and shared code that may
be bundled into a browser:

```ts
import {
  clearPmtilesCdnSession,
  ensurePmtilesCdnSession,
  getPmtilesFetchCredentials,
  isPrivatePmtilesUrl,
  resolveSharedDatasetPmtilesRef
} from "@skytruth/shared-datasets";
```

Use the server-only entrypoint from API routes, server actions, or backend code
that can import Node built-ins:

```ts
import {
  decodePmtilesCdnSigningKey,
  getExpiredPmtilesCookies,
  getPrivatePmtilesSessionCookies
} from "@skytruth/shared-datasets/server";
```

Do not import `@skytruth/shared-datasets/server` from browser bundles. It uses
Node crypto and should stay behind the consumer application's backend boundary.

## Recommended Setup By Runtime

| Runtime | Use | Setup |
|---|---|---|
| Browser displaying public PMTiles | Catalog helpers and `getPmtilesFetchCredentials` | Resolve `pmtiles_url` from catalog JSON or use a known public URL; no session endpoint is required. |
| Browser displaying private PMTiles | Main entrypoint session and fetch helpers | Call a consumer-owned backend session endpoint before mounting private layers and use credentialed PMTiles range requests. |
| Browser click needs public feature attributes | Metadata artifact helpers | Resolve the release-index sidecar and fetch the public artifact URL directly from the CDN. |
| Browser click needs private feature attributes | App backend route to a signed metadata URL or metadata lookup API | PMTiles expose `feature_id`; URL workflows should use the same canonical feature ID. The backend authenticates, authorizes, and returns only app-approved metadata access. |
| Backend PMTiles session route | Server entrypoint signing helpers plus `getPmtilesTier` from the main entrypoint | Authenticate and authorize the user, load the signing key from the consumer secret store, set cookies, and return `204`. |
| Backend private metadata URL route | Server entrypoint artifact signing helper | Validate slug, release, locale, asset tier, and entitlement before signing an exact sidecar path. |
| Backend layer/config API | Catalog helpers or access-tier cache helpers | Resolve catalog JSON once, preserve `accessTier`, `url`, citation, source, release, and release metadata sidecar references in consumer-owned config. |

Use the Python SDK instead when backend code needs to download canonical data
files or resolve durable `gs://` object identities with Application Default
Credentials.

Release-oriented vector PMTiles are intentionally lightweight and should not be
treated as the source of full feature attributes. Use the PMTiles `feature_id`
property for internal click-to-metadata joins through an app-owned backend route
that calls:

```http
POST /v1/assets/{slug}/releases/{release}:lookup
```

For user-visible URLs, pass the URL-safe public `feature_id` handle through the app
backend and call:

```http
POST /v1/assets/{slug}/releases/{release}:lookup
```

## Catalog Helpers

The default catalog JSON URL is:

```text
https://tiles.skytruth.org/_catalog/web/catalog.json
```

Resolve one PMTiles reference:

```ts
import { resolveSharedDatasetPmtilesRef } from "@skytruth/shared-datasets";

const ref = await resolveSharedDatasetPmtilesRef("example-public-layer");
```

Resolve several or all PMTiles references:

```ts
import {
  resolveAllSharedDatasetPmtilesRefs,
  resolveSharedDatasetPmtilesRefs
} from "@skytruth/shared-datasets";

const selectedRefs = await resolveSharedDatasetPmtilesRefs([
  "example-public-layer",
  "example-private-layer"
]);

const allRefs = await resolveAllSharedDatasetPmtilesRefs();
```

If your app already fetched catalog JSON, avoid a second network call:

```ts
import { resolveSharedDatasetPmtilesRefsFromCatalogJson } from "@skytruth/shared-datasets";

const refs = resolveSharedDatasetPmtilesRefsFromCatalogJson(catalogJson, [
  "example-public-layer",
  "example-private-layer"
]);
```

When a PMTiles layer needs localized labels or feature-inspector display names,
resolve the selected release metadata sidecar instead of reading display
columns from PMTiles. Prefer the requested locale-specific
`{asset-slug}.metadata.{locale}.ndjson.gz` sidecar when present, then the
canonical `{asset-slug}.metadata.ndjson.gz` fallback. Do not hardcode
source-native fields; localized source data is materialized from
`{asset-slug}.metadata-translations.csv` rows keyed by `feature_id`, `field`,
`locale`, and `source_value_hash`, while PMTiles expose only `feature_id`.
Use `review_state` values from metadata records to show or filter confidence
for source-provided, machine-translated, human-reviewed, and mixed labels.

Each resolved ref includes:

```ts
type SharedDatasetCatalogRef = {
  accessTier: "public" | "private";
  url: string;
  title: string | null;
  description: string | null;
  status: string | null;
  consumerGuidance: string | null;
  citation: string | null;
  license: string | null;
  source: string | null;
  sourceUrl: string | null;
  docsUrl: string | null;
  releaseIndexUrl: string | null;
  latestRelease: Record<string, unknown> | null;
  lastUpdated: string | null;
  localizedNames: {
    storage?: string | null;
    join_key?: string | null;
    localization_file?: string | null;
    property_template?: string | null;
    locale_code_format?: string | null;
    fallback_locale?: string | null;
    fallback_field?: string | null;
    available_locales?: string[];
    translations?: Array<{
      locale_code: string;
      field: string;
      review_state_field?: string | null;
      label?: string | null;
      review_state: "source_provided" | "machine_translated" | "human_reviewed" | "mixed";
    }>;
  } | null;
};
```

`localizedNames` is retained for older catalog JSON and consumers. New
release-oriented metadata sidecar integrations should prefer the release index
metadata artifact helpers described below.

Catalog resolution throws `SharedDatasetCatalogResolutionError` when catalog
data is missing, malformed, or cannot resolve a requested PMTiles asset.
These helpers return PMTiles-capable assets only. For full catalog screens or
default production layer lists, use `status === "active"` and preserve the
license, citation, source, docs, release, and metadata sidecar references
returned with each ref; if your app needs non-PMTiles assets or fields outside
this type, fetch and parse the catalog JSON directly.

## Metadata Artifact Helpers

Release indexes list metadata sidecars for feature-inspector fields. Public
assets can fetch those sidecars directly from the CDN artifact route:

```ts
import {
  resolvePublicSharedDatasetMetadataSidecarUrl
} from "@skytruth/shared-datasets";

const sidecar = resolvePublicSharedDatasetMetadataSidecarUrl({
  accessTier: ref.accessTier,
  releaseIndex,
  version: "latest",
  locale: userLocale
});

if (sidecar) {
  const response = await fetch(sidecar.url, { cache: "no-store" });
  const metadataBytes = await response.arrayBuffer();
}
```

The resolver tries the requested locale first and falls back to the canonical
`.metadata.ndjson.gz` sidecar when a localized sidecar is absent. It returns
`null` when the release index has no metadata sidecar.

Private assets should not expose direct sidecar URLs from browser code. Consumer
backends should expose an app-owned route such as:

```text
GET /api/shared-datasets/metadata-url?slug=&version=&locale=
```

That route should authenticate the user, apply app-specific authorization,
resolve the exact sidecar from catalog and release-index data, verify the asset
is private, sign the artifact URL, return `Cache-Control: no-store`, and never
sign arbitrary caller-provided object paths.

Server code can sign an exact resolved artifact path:

```ts
import { getSignedSharedDatasetArtifactUrl } from "@skytruth/shared-datasets/server";

const signedUrl = getSignedSharedDatasetArtifactUrl(gsUri, signingKey);
```

By default this server helper signs `https://tiles.skytruth.org/private/...`
URLs for private metadata sidecars. Pass `artifactBaseUrl` only for tests or a
deliberate deployment-specific CDN route.

## Browser PMTiles Fetching

Before fetching private PMTiles, call the consumer backend session endpoint.
Public layers can skip the session call because they do not need a cookie.

```ts
const result = await ensurePmtilesCdnSession({
  accessTier: ref.accessTier,
  endpoint: "/api/pmtiles/session"
});

if (!result.ok) {
  reportPmtilesSessionFailure(result);
  return;
}

renderPmtilesLayer(ref.url);
```

Use `getPmtilesFetchCredentials` anywhere PMTiles bytes are fetched:

```ts
const response = await fetch(ref.url, {
  credentials: getPmtilesFetchCredentials(ref.url),
  headers: {
    Range: `bytes=${start}-${end}`
  }
});
```

The helper returns:

- `include` for private PMTiles URLs under `/pmtiles/private/`
- `same-origin` for public PMTiles URLs

Relative PMTiles URLs are resolved against `https://tiles.skytruth.org` by
default. Pass `baseUrl` and `privatePathPrefix` only for tests or a deliberate
consumer-owned PMTiles route.

On sign-out, clear CDN cookies through the same consumer endpoint:

```ts
await clearPmtilesCdnSession({ endpoint: "/api/pmtiles/session" });
await signOutUser();
```

`ensurePmtilesCdnSession` and `clearPmtilesCdnSession` return result objects
instead of throwing for HTTP or network failures. Consumers decide whether to
warn, retry, hide a layer, redirect to sign-in, or ignore cleanup failures.

## Backend CDN Session Route

Consumers should expose their own session endpoint, for example:

```text
GET /api/pmtiles/session?tier=public
GET /api/pmtiles/session?tier=private
DELETE /api/pmtiles/session
```

The endpoint should:

1. Set `Cache-Control: no-store`.
2. Return `204` for public PMTiles access without setting cookies.
3. Authenticate the user before issuing private PMTiles cookies.
4. Authorize whether that user may access private shared PMTiles.
5. Load the CDN signing key from the consumer application's secret store.
6. Set all cookie headers returned by `getPrivatePmtilesSessionCookies`.
7. Clear cookies on sign-out using `getExpiredPmtilesCookies`.

Both cookie helpers return arrays. Send each returned string as a separate
`Set-Cookie` header; do not comma-join the array into one header value.

Example:

```ts
import {
  decodePmtilesCdnSigningKey,
  getExpiredPmtilesCookies,
  getPrivatePmtilesSessionCookies
} from "@skytruth/shared-datasets/server";
import { getPmtilesTier } from "@skytruth/shared-datasets";

export async function handlePmtilesSession(req, res) {
  res.setHeader("Cache-Control", "no-store");

  if (req.method === "DELETE") {
    res.setHeader("Set-Cookie", getExpiredPmtilesCookies());
    res.statusCode = 204;
    res.end();
    return;
  }

  if (req.method !== "GET") {
    res.statusCode = 405;
    res.end("Method not allowed");
    return;
  }

  const tier = getPmtilesTier(req.query.tier);
  if (!tier) {
    res.statusCode = 400;
    res.end("Invalid PMTiles tier");
    return;
  }

  if (tier === "public") {
    res.statusCode = 204;
    res.end();
    return;
  }

  const session = await getCurrentUserSession(req);
  if (!session) {
    res.statusCode = 401;
    res.end("Authentication required");
    return;
  }

  const allowed = await canAccessPrivateSharedPmtiles(session);
  if (!allowed) {
    res.statusCode = 403;
    res.end("PMTiles access denied");
    return;
  }

  const encodedSigningKey = await readPrivatePmtilesSigningKey();
  const signingKey = decodePmtilesCdnSigningKey(encodedSigningKey);
  res.setHeader("Set-Cookie", getPrivatePmtilesSessionCookies(signingKey));
  res.statusCode = 204;
  res.end();
}
```

The default cookie settings target SkyTruth's PMTiles CDN:

- cookie name: `Cloud-CDN-Cookie`
- cookie domain: `.skytruth.org`
- private cookie path: `/pmtiles/private`
- private URL prefix: `https://tiles.skytruth.org/pmtiles/private/`
- signing key name: `shared-datasets-pmtiles-v1`
- TTL: 24 hours

Override these values only for tests or an explicitly different CDN route by
passing a partial config to `getPrivatePmtilesSessionCookies` or
`getExpiredPmtilesCookies`.

## Access-Tier Cache Helpers

Use `createSharedDatasetAccessTierLookup` when a server needs a lightweight
cached lookup from asset slug to `public` or `private`:

```ts
import {
  createSharedDatasetAccessTierLookup,
  getAccessTiersFromSharedDatasetPmtilesRefs,
  resolveAllSharedDatasetPmtilesRefs
} from "@skytruth/shared-datasets";

const getAccessTier = createSharedDatasetAccessTierLookup({
  loadAccessTiers: async () =>
    getAccessTiersFromSharedDatasetPmtilesRefs(
      await resolveAllSharedDatasetPmtilesRefs()
    )
});

const tier = await getAccessTier("example-public-layer");
```

The default cache TTL is 5 minutes. Pass `ttlMs` and `now` to customize or test
cache behavior.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `npm install @skytruth/shared-datasets` returns 404 | The registry, scope, or package name is wrong, or npm has a transient registry issue. | Verify `npm view @skytruth/shared-datasets version` and use the public npm registry. |
| Browser bundle includes `node:crypto` | The server entrypoint was imported into client code. | Move signing helpers behind a backend route and import browser helpers from the main entrypoint only. |
| Private PMTiles session succeeds but tiles fail | The PMTiles library's internal range requests are missing credentials. | Configure or wrap its fetch implementation so all PMTiles requests use `credentials: "include"`. |
| Private PMTiles return `401` or `403` | User is unauthenticated, unauthorized, or the signed cookie is missing/expired. | Re-call the session endpoint and verify the backend authorization path. |
| Public PMTiles fail with a cookie/session error | Public layers are unnecessarily using the private session path. | Skip `ensurePmtilesCdnSession` for known public layers or pass the catalog `accessTier` accurately. |

## Development

Install package dependencies and run tests:

```bash
npm ci
npm test
```

Check the publish artifact before a release:

```bash
npm pack --dry-run
```
