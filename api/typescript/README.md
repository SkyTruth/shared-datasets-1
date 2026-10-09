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
| Browser displaying private or internal PMTiles | Main entrypoint session and fetch helpers | Call a consumer-owned backend session endpoint before mounting restricted layers and use credentialed PMTiles range requests. |
| Browser click needs public feature attributes | `resolveSharedDatasetLayer` plus `fetchSharedDatasetMetadataRecords` | Resolve the layer and its release metadata sidecar together, then join clicked features by `feature_id`. |
| Browser click needs private feature attributes | App backend route to a signed metadata sidecar URL | PMTiles expose `feature_id`; URL workflows should use the same canonical feature ID. The backend authenticates, authorizes, and returns only app-approved metadata access. |
| Backend PMTiles session route | `createPmtilesSessionHandler` / `createNextPmtilesSessionHandler` from the server entrypoint | Provide `getViewer` and `getSigningKey`; the handler implements the full tiered session contract. |
| Backend private metadata URL route | Server entrypoint artifact signing helper | Validate slug, release, locale, asset tier, and entitlement before signing an exact sidecar path. |
| Backend layer/config API | Catalog helpers or access-tier cache helpers | Resolve catalog JSON once, preserve `accessTier`, `url`, citation, source, release, and release metadata sidecar references in consumer-owned config. |

Use the Python SDK instead when backend code needs to download canonical data
files or resolve durable `gs://` object identities with Application Default
Credentials.

Release-oriented vector PMTiles are intentionally lightweight and should not be
treated as the source of full feature attributes. They expose exactly one
feature property, `feature_id`. Use it for click-to-metadata joins through a
release metadata sidecar resolved from the release index. Catalog and preview
viewers also serve same-origin sidecar lookups at
`POST /v1/assets/{slug}/releases/{release}:lookup`. The standalone Firestore
endpoint is retired; SDK sidecar readers are unchanged.

`feature_id` values are URL-safe strings matching `^[A-Za-z0-9]{1,64}$`, either
copied from a verified-unique source field or assigned as monotonic decimal
sequence strings preserved across releases. For user-visible URLs, pass that
public `feature_id` handle through the app backend and resolve metadata with
the same release-scoped sidecar contract.

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
Sidecar/API records include `geometry_hash`; use it as the stable
geometry-equivalence key for grouping or de-duplicating footprints after
metadata is loaded, not as a URL lookup handle.

New localized records may include `translation.state` (`complete`, `partial`, or
`fallback`) and field lists for translated, fallback, machine, and human-reviewed
values. Treat that block as optional for older releases. Catalog JSON can also
include `translation_locales` and `translation_fields`, the maintained locale
and field lists; coverage and editorial review are separate concepts.

Each resolved ref includes:

```ts
type SharedDatasetCatalogRef = {
  accessTier: "public" | "private" | "internal";
  url: string;
  pmtilesPath: string | null;
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

## Layer And Metadata Resolution

Because PMTiles expose only `feature_id`, mounting a layer and resolving its
release metadata belong together. `resolveSharedDatasetLayer` is the
recommended path: one call resolves the catalog ref, fetches the release
index, and resolves the metadata sidecar from the same release:

```ts
import {
  fetchSharedDatasetMetadataRecords,
  resolveSharedDatasetLayer
} from "@skytruth/shared-datasets";

const layer = await resolveSharedDatasetLayer("example-public-layer", {
  locale: userLocale
});

if (layer.ref.url) renderPmtilesLayer(layer.ref.url);

if (layer.sidecar?.url) {
  const records = await fetchSharedDatasetMetadataRecords(layer.sidecar.url);
  const record = records.get(clickedFeatureId);
}
```

The returned layer includes the selected `ref`, `pmtiles` descriptor
(`file`, `gsUri`, `generation`), `releaseIndex`, concrete `resolvedRelease`, and
optional `sidecar`. Both PMTiles and metadata come from the same indexed release;
public URLs include the indexed object generation. Pass `version: "YYYY-MM-DD"`
for historical data. The default resolves the index's latest entry once. Retain
the returned descriptors together and key caches by path and generation, because
a same-date replacement can change both artifacts.

An explicit missing release, missing PMTiles, ambiguous variant, or indexed
artifact without a usable generation throws `SharedDatasetCatalogResolutionError`.
An existing release without a sidecar returns `sidecar: null` without changing
`resolvedRelease`. If the index is absent (including HTTP 404), only `latest`
returns a legacy map-only alias with `resolvedRelease`, `pmtiles`, and `sidecar`
all null; 403, malformed JSON, and network failures do not activate that fallback.
Custom index fetchers should expose a numeric `status: 404` for definitive absence.
`resolveSharedDatasetPmtilesRef(s)` remains a catalog-only alias resolver and does
not promise a coherent metadata join.

For indexed private/internal layers, **both `ref.url` and `sidecar.url` are null**.
This layer-specific nullability is intentional; catalog-only reference types
remain unchanged. Send slug, concrete release, and expected generation to your
existing authorized backend. It must reselect the catalog-owned artifact, reject
an expected-generation mismatch, and sign the exact descriptor after applying
its existing access policy. Never accept an arbitrary URI from the browser.
For example, inside that backend after authorization and selection:

```ts
const layer = await resolveSharedDatasetLayer(assetSlug, { version: requestedDate });
if (!layer.pmtiles || layer.pmtiles.generation !== expectedGeneration) {
  throw new Error("Selected artifact changed; reload the catalog and reselect");
}
const pmtilesUrl = getSignedSharedDatasetArtifactUrl(
  layer.pmtiles.gsUri, signingKey, { generation: layer.pmtiles.generation }
);
// Return the selected date/path/generation with the URL; the caller verifies
// they match its captured layer before mounting it.
```

Import the signing helper from `@skytruth/shared-datasets/server`. The configured
artifact route must support the artifact and access policy. This helper signs
bytes already authorized by your backend; it does not authorize a path. The
existing tiered session helpers still apply to catalog-only alias workflows.
If a selected generation is no longer authorized or retained, fail/reselect the
whole layer rather than loading new metadata into old tiles.

`fetchSharedDatasetMetadataRecords` downloads the sidecar, transparently
handles both CDN-decompressed NDJSON and raw gzip bytes, parses each line, and
returns a `Map` keyed by `feature_id`. Loading eagerly is fine for small
assets; for assets with very large sidecars, load lazily on first interaction
and keep the parsed map cached. `parseSharedDatasetMetadataRecords` is exported
separately for callers that fetch sidecar text themselves.

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
`null` when the selected release has no metadata sidecar. A missing release or
advertised sidecar without a usable generation throws; it is not optional absence.

Each sidecar is gzip NDJSON with one JSON record per feature:

```json
{
  "schema_version": 2,
  "asset_slug": "example-boundary-layer",
  "release": "2026-06-09",
  "feature_id": "12345",
  "geometry_hash": "sha256:...",
  "properties_hash": "sha256:...",
  "properties": { "SOURCE_ID": 12345, "NAME": "Example feature" },
  "provenance": { "source": "Example source release" }
}
```

Join PMTiles features to records by `feature_id`. Localized sidecars keep the
same record shape with translated display values already materialized into
`properties`.

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

const signedUrl = getSignedSharedDatasetArtifactUrl(sidecar.gsUri, signingKey, {
  generation: sidecar.generation
});
```

By default this server helper signs `https://tiles.skytruth.org/private/...`
URLs for private metadata sidecars. Pass `artifactBaseUrl` only for tests or a
deliberate deployment-specific CDN route.

## Browser PMTiles Fetching

Before fetching private or internal PMTiles, call the consumer backend session
endpoint. Public layers can skip the session call because they do not need a
cookie.

```ts
const result = await ensurePmtilesCdnSession({
  accessTier: ref.accessTier,
  endpoint: "/api/pmtiles/session"
});

if (!result.ok) {
  if (result.denied) {
    hidePmtilesLayer(ref);
  } else {
    reportPmtilesSessionFailure(result);
  }
  return;
}

renderPmtilesLayer(ref.url);
```

A `denied: true` result (HTTP 403) means the signed-in viewer is not
authorized for this tier — hide the layer instead of retrying. To decide which
layers to offer before mounting anything, probe the viewer's qualifying tiers:

```ts
const grants = await getPmtilesCdnGrants({ endpoint: "/api/pmtiles/session" });
if (grants.ok && grants.tiers.includes("internal")) {
  showInternalDatasetGroup();
}
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

- `include` for restricted PMTiles URLs under `/pmtiles/private/` or
  `/pmtiles/internal/`
- `same-origin` for public PMTiles URLs

Relative PMTiles URLs are resolved against `https://tiles.skytruth.org` by
default. Pass `baseUrl` and `restrictedPathPrefixes` only for tests or a
deliberate consumer-owned PMTiles route.

On sign-out, clear CDN cookies through the same consumer endpoint:

```ts
await clearPmtilesCdnSession({ endpoint: "/api/pmtiles/session" });
await signOutUser();
```

`ensurePmtilesCdnSession` and `clearPmtilesCdnSession` return result objects
instead of throwing for HTTP or network failures. Consumers decide whether to
warn, retry, hide a layer, redirect to sign-in, or ignore cleanup failures.

## Backend CDN Session Route

Consumers should expose their own session endpoint:

```text
GET /api/pmtiles/session?tier=public
GET /api/pmtiles/session?tier=private
GET /api/pmtiles/session?tier=internal
GET /api/pmtiles/session?tier=grants
DELETE /api/pmtiles/session
```

Do not hand-write the contract. `createPmtilesSessionHandler` (and the
pages-router adapter `createNextPmtilesSessionHandler`) implement all of it:
`Cache-Control: no-store` on every response, `204` for public, `401` for
anonymous restricted-tier requests, `403` for authenticated-but-unauthorized
viewers, one signed cookie per tier the viewer qualifies for, a
`?tier=grants` probe that reports qualifying tiers without setting cookies,
`DELETE` expiry of every restricted-tier cookie, and `500` on signing
failures without leaking the key.

```ts
// pages/api/pmtiles/session.ts
import { createNextPmtilesSessionHandler } from "@skytruth/shared-datasets/server";

export default createNextPmtilesSessionHandler({
  getViewer: async req => {
    const session = await getCurrentUserSession(req);
    if (!session) return null;
    return {
      email: session.user.email,
      emailVerified: session.user.emailVerified,
      tierGrants: await getAppTierGrants(session.user)
    };
  },
  getSigningKey: async () =>
    decodePmtilesCdnSigningKey(await readPmtilesSigningKey())
});
```

Tier authorization is decided by `isViewerAuthorizedForTier` (exported from
the main entrypoint): `public` allows anyone, `private` allows any
authenticated viewer, and `internal` requires a verified email in the allowed
domain list (default `skytruth.org`) or an unexpired app-level `tierGrants`
entry. Use the same function for API payload filtering and metadata URL
entitlement checks so cookie issuance and payload gates cannot drift apart.
Guest-granted cookies are automatically clamped to the grant expiry.

For non-Next runtimes, call the framework-neutral handler directly and apply
the returned `{ status, headers, cookies, body }` to your response. The
low-level helpers (`getPmtilesSessionCookiesForTiers`,
`getExpiredPmtilesCookies`, `decodePmtilesCdnSigningKey`) remain available
for custom routes. Cookie helpers return arrays; send each returned string as
a separate `Set-Cookie` header — do not comma-join the array into one header
value.

The default cookie settings target SkyTruth's PMTiles CDN:

- cookie name: `Cloud-CDN-Cookie`
- cookie domain: `.skytruth.org`
- tier cookie paths: `/pmtiles/private`, `/pmtiles/internal`
- tier URL prefixes: `https://tiles.skytruth.org/pmtiles/private/`,
  `https://tiles.skytruth.org/pmtiles/internal/`
- signing key name: `shared-datasets-pmtiles-v1`
- TTL: 30 days (clamped to grant expiry for guest-granted internal access)

Override these values only for tests or an explicitly different CDN route by
passing a partial config (`tierPaths`, `ttlSeconds`, ...) to the handler or
cookie helpers.

## Access-Tier Cache Helpers

Use `createSharedDatasetAccessTierLookup` when a server needs a lightweight
cached lookup from asset slug to `public`, `private`, or `internal`:

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

`createCatalogSharedDatasetAccessTierLookup` wires the same lookup to the
shared datasets catalog directly, so the common server case is one call:

```ts
import { createCatalogSharedDatasetAccessTierLookup } from "@skytruth/shared-datasets";

const getAccessTier = createCatalogSharedDatasetAccessTierLookup();
const tier = await getAccessTier("example-public-layer");
```

It accepts the standard catalog fetch options (`catalogUrl`, `fetchJson`) plus
`ttlMs` and `now`.

## Filtering Private Rows Out Of Untrusted Payloads

When a server endpoint returns rows derived from shared datasets to
unauthenticated or otherwise untrusted requesters, use
`filterPrivateSharedDatasetRows` instead of hand-rolling tier checks. It
applies the standard access policy: rows from non-public datasets are dropped,
rows whose tier cannot be resolved are dropped (fail closed), and rows without
an asset slug pass through unchanged.

```ts
import {
  createCatalogSharedDatasetAccessTierLookup,
  filterPrivateSharedDatasetRows
} from "@skytruth/shared-datasets";

const getAccessTier = createCatalogSharedDatasetAccessTierLookup();

const { rows, tierLookupFailed } = await filterPrivateSharedDatasetRows(
  candidateRows, // each row carries an `assetSlug` field by default
  { getAccessTier }
);
```

Pass `getAssetSlug` when rows store the dataset slug under a different field.
`tierLookupFailed` reports that at least one row was dropped because its tier
could not be resolved: the result is safe to serve but over-filtered, so skip
long-lived caching of it (otherwise a transient catalog outage pins a degraded
payload until the cache expires).

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `npm install @skytruth/shared-datasets` returns 404 | The registry, scope, or package name is wrong, or npm has a transient registry issue. | Verify `npm view @skytruth/shared-datasets version` and use the public npm registry. |
| Browser bundle includes `node:crypto` | The server entrypoint was imported into client code. | Move signing helpers behind a backend route and import browser helpers from the main entrypoint only. |
| Private PMTiles session succeeds but tiles fail | The PMTiles library's internal range requests are missing credentials. | Configure or wrap its fetch implementation so all PMTiles requests use `credentials: "include"`. |
| Private PMTiles return `401` or `403` | User is unauthenticated, unauthorized, or the signed cookie is missing/expired. | Re-call the session endpoint and verify the backend authorization path. |
| Public PMTiles fail with a cookie/session error | Public layers are unnecessarily using the private session path. | Skip `ensurePmtilesCdnSession` for known public layers or pass the catalog `accessTier` accurately. |

## Maintainer Validation And Releases

The initial `0.1.0` release was published manually because npm trusted-publisher
configuration requires an existing package. An npm maintainer bootstraps a new
package with `npm publish --access public`; ordinary releases use the
`Publish TypeScript SDK` GitHub Actions workflow.
Trusted Publishing is configured; do not create a long-lived `NPM_TOKEN`.

Prepare each release in a reviewed PR. Changes to SDK source, this packed README,
either package manifest, or `tsconfig.json` require a higher stable
`major.minor.patch` version. Choose the appropriate semver level and, from this
directory, run:

```bash
npm ci
npm version --no-git-tag-version patch  # or minor / major for the reviewed change
npm test
npm run test:pack
```

Commit `package.json` and `package-lock.json` with the package changes.
Test/workflow-only changes outside the packed package do not require a release.
Prerelease versions are not supported by the stable-release workflow.

The packed-consumer smoke check builds a real tarball, installs it into a
separate consumer, and checks root/server runtime imports and TypeScript
declarations. Its retained artifacts live in a named directory under
`${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}/_scratch/`;
the command prints the exact path. CI checks the PR's version increase and runs
the SDK and package checks on Node 22 and 24; `ci-ready` requires both selected
matrix results.

Merging a versioned package change to `main` triggers release detection from the
main-push CI completion. The listener verifies that exact `ci-ready` result,
checks the reviewed version, and publishes the retained Node 24 tarball bytes
through npm's GitHub Actions OIDC handshake. Validation failures, cancellations,
and obsolete completion events are no-ops; an unrelated deployment failure after
passing validation keeps the tested package eligible. The read-only release
workflow never edits versions, commits, or pushes. Commit-message bump trailers
have no effect.

The publisher compares the reviewed version with all published stable versions.
A new version must be higher; retrying an existing version succeeds without
publishing only when the packed bytes match its registry integrity. Changed
bytes require another reviewed version. Missing/malformed registry metadata and
network/authentication failures stop release. Workflow-only pushes without a
package/version change skip publication. Manual dispatch from `main` requires
the exact tested executor SHA and successful source CI run and attempt; it
retries that version with the same integrity rules and never invents a bump.
An older retry cannot move npm's latest tag backwards.

Release uses Node 24 and npm trusted publishing (npm 11.5.1 or later). Node 24
validation should be required by branch protection; adding a workflow does not
change repository protection settings.

Keep npm trusted-publisher settings as follows:

- Publisher: GitHub Actions
- Organization or user: `SkyTruth`
- Repository: `shared-datasets-1`
- Workflow filename: `publish-typescript-sdk.yml`
- Environment name: blank unless the workflow gains a GitHub environment
- Allowed actions: `npm publish`

After success, verify the registry version with the command in
[Package Status And Installation](#package-status-and-installation).

## Show a dataset on a map

The optional `@skytruth/shared-datasets/maplibre` entrypoint handles protocol
registration, vector styling, extent fitting, and matching metadata on click:

```typescript
import {showDataset} from '@skytruth/shared-datasets/maplibre';
const dataset = await showDataset('map', {
  tiles: 'gs://example-bucket/category/subcategory/example-layer/releases/2026-06-26/example-layer.pmtiles#1782497508702243',
  metadata: 'gs://example-bucket/category/subcategory/example-layer/releases/2026-06-26/example-layer.metadata.ndjson.gz#1782497515428207',
}, {bucket: 'example-bucket'});
```

Install `maplibre-gl@5.9.0` and `pmtiles@4.3.0` alongside the SDK, import
`maplibre-gl/dist/maplibre-gl.css` once, and give the container an explicit
height. The SDK root stays framework-neutral; these optional peers are only
needed for this entrypoint. `dataset` contains `map`, `records` keyed by
`feature_id`, and the resolved `layer`. Metadata is optional (`null`);
the helper requires vector PMTiles and ignores archive attribution HTML.
Popup properties are inserted as text, never HTML.

Both references require `#generation`. Tiles and metadata must belong to the
same asset root and release. Availability and identity are checked before a map
is created; no catalog lookup or release substitution occurs. The shorthand
records verified object identity but does not carry published checksums/sizes.
Use the full snapshot API below when published integrity expectations matter.
`resolveDatasetMap(reference, options)` performs resolution and metadata loading
without creating a map.

Options include `bucket`, `artifactBaseUrl`, `access`, `mapOptions`,
`probeArtifact`, and `authorizeArtifact`. For private/internal data, provide
`access` and either an `authorizeArtifact` callback or `authorizationUrl` for your
app-owned authenticated `POST` route. The route receives the generated snapshot,
`asset_slug`, `role`, and `locale`; implement it with `authorizeSnapshotArtifact`
from the server entrypoint below. Current catalog entitlement and exact indexed
identity are authoritative. Session tokens, cookies, and signed URLs never appear
in portable references.

## Exact portable snapshots

These additive APIs consume v1 dataset lockfiles and workspace JSON. See the [authoritative portable contract](../../docs/standards/workspace-snapshot-v1.md).
The browser validator is compiled from the same module used by the viewer.

```ts
import {
  validateWorkspaceSnapshot, resolveSnapshotLayers, fetchSnapshotMetadata
} from "@skytruth/shared-datasets";

const response = await fetch("/dataset.lock.json");
const lock = validateWorkspaceSnapshot(await response.text());
// Validate and preflight all required artifacts before mounting any layers.
const layers = await resolveSnapshotLayers(lock);
const records = await fetchSnapshotMetadata(layers[0]);
const attributes = records.get("1")?.properties;
```

`resolveSnapshotLayer(lock, slug, options)` prepares one PMTiles layer;
`resolveSnapshotLayers` prepares all datasets as one successful result. These
helpers use the captured URIs/generations rather than resolving latest or a
current index. They return `tileUrl`, exact metadata selection, required
artifact URLs, and captured dataset provenance. Canonical and localized
metadata stay with their captured tiles. `fetchSnapshotMetadata` verifies
published size/SHA-256 and record identity, then joins records by `feature_id`.
Range-rendering PMTiles does not verify an entire archive checksum.

Use `options.bucket` only for an explicitly trusted alternative bucket; never
copy this option from untrusted JSON. Public layers use generation-qualified
artifact URLs. Restricted layers require `authorizeArtifact(dataset, artifact)`
which calls an application-owned authenticated route and returns exactly:

```ts
{ gs_uri, generation, resolved_release, url }
```

The client rejects responses that disagree with the captured identity.
`probeArtifact(url, artifact)` can be supplied for a consumer-owned transport;
the default HEAD probe checks availability and exposed generation/size.
Access URLs are ephemeral runtime values and must not be included in exports.

On the **server-only** entrypoint, `authorizeSnapshotArtifact(snapshot, slug,
role, resolvedLocale, options)` is the route primitive. Authenticate the request
using your app's existing session and reject bodies over 1 MiB before parsing.
Pass a trusted bucket, the verified viewer, your existing policy, `getAsset`,
`getReleaseIndex`, `getSigningKey`, and optional `signingConfig`. The helper
rechecks the **current** catalog tier/root and the indexed URI/generation,
uses the existing artifact signing helper, and clamps credentials to grant
expiry. Imported access tiers never grant entitlement. Replaced older
generations which the index no longer authorizes fail without signing them.
The application owns HTTP status translation and session implementation.

```ts
import {authorizeSnapshotArtifact} from "@skytruth/shared-datasets/server";

// Inside an authenticated application route, with bounded validated request data:
const access = await authorizeSnapshotArtifact(
  body.snapshot, body.asset_slug, body.role, body.locale,
  {viewer, getAsset, getReleaseIndex, getSigningKey}
);
// Return access as JSON to the authenticated caller.
```

The catalog action generates a complete public MapLibre/PMTiles example with
matching metadata, citations, and access tier. Restricted examples explicitly
require the route above. Install from the same reviewed repository revision
shown by the catalog for these new APIs until their npm release is available.
No browser credentials or signing keys belong in generated code.

A snapshot is a reference, not an archive. Missing generations, denied access,
and integrity failures reject the restore; callers must not substitute latest.
The dataset API also supports non-PMTiles canonical artifacts through Python.

Because `web/catalog/workspace-contract.js` is compiled into the package, its
changes and changes to `scripts/copy-snapshot-contract.mjs` participate in the
same reviewed version-increase check and publish trigger as package sources.
