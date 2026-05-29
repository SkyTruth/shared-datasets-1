# SkyTruth Shared Datasets TypeScript Helpers

Framework-neutral helpers for consuming SkyTruth shared-datasets PMTiles assets
through the SkyTruth CDN.

This package intentionally does not own application authentication, routing,
secret storage, logging, UI behavior, retries, or HTTP/error translation. A
consumer should keep those decisions in its own adapter layer.

## Entrypoints

Use the browser-safe entrypoint from client code:

```ts
import {
  clearPmtilesCdnSession,
  ensurePmtilesCdnSession,
  getPmtilesFetchCredentials,
  isPrivatePmtilesUrl,
  resolveSharedDatasetPmtilesRef
} from '@skytruth/shared-datasets';
```

Use the server-only entrypoint from API routes, server actions, or backend code:

```ts
import {
  decodePmtilesCdnSigningKey,
  getExpiredPmtilesCookies,
  getPrivatePmtilesSessionCookies
} from '@skytruth/shared-datasets/server';
```

Do not import the server entrypoint from browser bundles. It uses Node crypto.

## Installation

```bash
npm install @skytruth/shared-datasets
```

## CDN Session Route

Consumers should expose their own session endpoint. That endpoint should:

1. Return `204` for public PMTiles access.
2. Authenticate the user before issuing private PMTiles cookies.
3. Authorize whether that user may access private shared PMTiles.
4. Load the CDN signing key from the consumer's secret store.
5. Set the cookie headers returned by `getPrivatePmtilesSessionCookies`.
6. Clear cookies on sign-out using `getExpiredPmtilesCookies`.

Example:

```ts
import {
  decodePmtilesCdnSigningKey,
  getExpiredPmtilesCookies,
  getPrivatePmtilesSessionCookies
} from '@skytruth/shared-datasets/server';
import { getPmtilesTier } from '@skytruth/shared-datasets';

export async function handlePmtilesSession(req, res) {
  res.setHeader('Cache-Control', 'no-store');

  if (req.method === 'DELETE') {
    res.setHeader('Set-Cookie', getExpiredPmtilesCookies());
    res.statusCode = 204;
    res.end();
    return;
  }

  if (req.method !== 'GET') {
    res.statusCode = 405;
    res.end('Method not allowed');
    return;
  }

  const tier = getPmtilesTier(req.query.tier);
  if (!tier) {
    res.statusCode = 400;
    res.end('Invalid PMTiles tier');
    return;
  }

  if (tier === 'public') {
    res.statusCode = 204;
    res.end();
    return;
  }

  const session = await getCurrentUserSession(req);
  if (!session) {
    res.statusCode = 401;
    res.end('Authentication required');
    return;
  }

  const allowed = await canAccessPrivateSharedPmtiles(session);
  if (!allowed) {
    res.statusCode = 403;
    res.end('PMTiles access denied');
    return;
  }

  const encodedSigningKey = await readPrivatePmtilesSigningKey();
  const signingKey = decodePmtilesCdnSigningKey(encodedSigningKey);
  res.setHeader('Set-Cookie', getPrivatePmtilesSessionCookies(signingKey));
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

You can override these values by passing a partial config to
`getPrivatePmtilesSessionCookies` or `getExpiredPmtilesCookies`.

## Browser Session Handshake

Before fetching a private PMTiles URL, call `ensurePmtilesCdnSession`. It does
not throw for HTTP or network failures. It returns a result object so the
consumer can decide whether to warn, retry, hide a layer, redirect to sign-in,
or ignore the failure.

```ts
const result = await ensurePmtilesCdnSession({
  accessTier: pmtilesAccessTier,
  endpoint: '/api/pmtiles/session'
});

if (!result.ok) {
  reportPmtilesSessionFailure(result);
  return;
}

renderPmtilesLayer(pmtilesUrl);
```

On sign-out, clear CDN cookies with the same endpoint:

```ts
await clearPmtilesCdnSession({ endpoint: '/api/pmtiles/session' });
await signOutUser();
```

`clearPmtilesCdnSession` also returns a result object. Treating cleanup as
best-effort is a consumer decision.

## Fetching PMTiles Assets

Use `getPmtilesFetchCredentials` anywhere PMTiles bytes are fetched.

```ts
const response = await fetch(pmtilesUrl, {
  credentials: getPmtilesFetchCredentials(pmtilesUrl),
  headers: {
    Range: `bytes=${start}-${end}`
  }
});
```

The helper returns:

- `include` for URLs under `/pmtiles/private/`
- `same-origin` for public PMTiles URLs

Relative URLs are resolved against `https://tiles.skytruth.org` by default.

## Catalog Helpers

The catalog helpers resolve PMTiles references from the SkyTruth shared-datasets
catalog:

```ts
import {
  resolveAllSharedDatasetPmtilesRefs,
  resolveSharedDatasetPmtilesRef,
  resolveSharedDatasetPmtilesRefs
} from '@skytruth/shared-datasets';

const ref = await resolveSharedDatasetPmtilesRef('example-public-layer');
```

The default catalog URL is:

```text
https://tiles.skytruth.org/_catalog/web/catalog.json
```

Each resolved ref includes:

```ts
type SharedDatasetCatalogRef = {
  accessTier: 'public' | 'private';
  url: string;
  title: string | null;
  description: string | null;
  citation: string | null;
  source: string | null;
  sourceUrl: string | null;
  lastUpdated: string | null;
};
```

If your app already fetched catalog JSON, avoid a second network call:

```ts
import { resolveSharedDatasetPmtilesRefsFromCatalogJson } from '@skytruth/shared-datasets';

const refs = resolveSharedDatasetPmtilesRefsFromCatalogJson(catalogJson, [
  'example-public-layer',
  'example-private-layer'
]);
```

## Access-Tier Cache Helpers

Use `createSharedDatasetAccessTierLookup` when a server needs a lightweight
cached lookup from asset slug to `public` or `private`.

```ts
import {
  createSharedDatasetAccessTierLookup,
  getAccessTiersFromSharedDatasetPmtilesRefs,
  resolveAllSharedDatasetPmtilesRefs
} from '@skytruth/shared-datasets';

const getAccessTier = createSharedDatasetAccessTierLookup({
  loadAccessTiers: async () =>
    getAccessTiersFromSharedDatasetPmtilesRefs(
      await resolveAllSharedDatasetPmtilesRefs()
    )
});

const tier = await getAccessTier('example-public-layer');
```

The default cache TTL is 5 minutes. Pass `ttlMs` and `now` to customize or test
cache behavior.

## Error Handling

The package makes only low-level guarantees:

- server signing helpers throw when required signing inputs are invalid.
- catalog resolution helpers throw `SharedDatasetCatalogResolutionError` when
  catalog data is missing, malformed, or cannot resolve requested assets.
- browser session helpers return `{ ok: false, status?, error? }` for failed
  session requests.

Consumers should decide how to translate these failures into logs, HTTP
responses, UI states, retries, or sign-in redirects.
