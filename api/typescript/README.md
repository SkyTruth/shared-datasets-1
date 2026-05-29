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
| Backend PMTiles session route | Server entrypoint signing helpers plus `getPmtilesTier` from the main entrypoint | Authenticate and authorize the user, load the signing key from the consumer secret store, set cookies, and return `204`. |
| Backend layer/config API | Catalog helpers or access-tier cache helpers | Resolve catalog JSON once, preserve `accessTier`, `url`, citation, and source metadata in consumer-owned config. |

Use the Python SDK instead when backend code needs to download canonical data
files or resolve durable `gs://` object identities with Application Default
Credentials.

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

Each resolved ref includes:

```ts
type SharedDatasetCatalogRef = {
  accessTier: "public" | "private";
  url: string;
  title: string | null;
  description: string | null;
  citation: string | null;
  source: string | null;
  sourceUrl: string | null;
  lastUpdated: string | null;
};
```

Catalog resolution throws `SharedDatasetCatalogResolutionError` when catalog
data is missing, malformed, or cannot resolve a requested PMTiles asset.

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
- legacy clear path: `/pmtiles`
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
