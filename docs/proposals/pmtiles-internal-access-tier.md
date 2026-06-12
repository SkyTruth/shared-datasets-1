# Proposal: `internal` PMTiles Access Tier

Status: implemented (2026-06-11/12) — shared-datasets-1 side merged in PR
#83 and released as SDK 0.6.0; cerulean-ui adoption built (Section 6).
Guest/funder access deferred by decision (Open Question 4): internal tier
is allowed-domain only for now. Pending: the first internal-tier asset.
Owner: jonaraphael
Date: 2026-06-11

## Problem

The PMTiles CDN currently knows two access tiers: `public` (anonymous) and
`private` (any authenticated consumer session). The ratified policy decision is
that `private` means exactly that: any self-registered, signed-in user.

Some datasets need a stronger barrier: visible only to users in the dataset
owner's email domain (initially `@skytruth.org`). In Cerulean this maps to
`aoi_type.read_perm = 2` with a SkyTruth-owned `aoi_type.owner`. Today the
stack cannot express this:

- `SharedDatasetAccessTier` is hard-coded to `'public' | 'private'`
  (`api/typescript/src/catalog.ts`).
- One signed cookie unlocks the entire `/pmtiles/private/` prefix. Hiding a
  dataset in the UI does not stop a signed-in user from `curl`ing its tiles.

Filtering at the API layer alone would be security theater. Enforcement must
live at the CDN data plane.

## Goals

1. **Secure**: a user who is not authorized for an internal dataset cannot
   fetch its tiles, even with `curl` and a valid private-tier cookie.
2. **Reliable**: fail closed at every layer; one authorization policy shared by
   cookie issuance and API payload filtering so they cannot drift.
3. **Magic for consumers**: a downstream app (cerulean-ui, 30x30) should get
   correct tiered behavior from the `@skytruth/shared-datasets` SDK with one
   ~5-line session route and the existing `ensurePmtilesCdnSession` call. No
   consumer should hand-roll cookies, prefixes, or policy.

## Design Overview

Add a third tier, `internal`, with its own CDN URL prefix and its own signed
cookie. Tier classification lives in the shared-datasets catalog (data plane
truth); the authorization rule (who qualifies for `internal`) lives in SDK
code with a single exported policy function.

```text
tier      URL prefix                                       who gets a cookie
--------  -----------------------------------------------  -------------------------------
public    https://tiles.skytruth.org/pmtiles/public/...    no cookie needed
private   https://tiles.skytruth.org/pmtiles/private/...   any authenticated session
internal  https://tiles.skytruth.org/pmtiles/internal/...  session w/ verified email whose
                                                           domain is in the allowed list
                                                           (default: skytruth.org)
```

Why this is sound at the CDN layer:

- Cloud CDN signed-cookie validation checks the `URLPrefix` embedded in the
  cookie policy against the request URL **before** URL rewrite. A private
  cookie (`URLPrefix=.../pmtiles/private/`) presented against
  `/pmtiles/internal/x.pmtiles` fails validation; the request falls through to
  GCS unauthenticated and 403s because internal objects are never public.
- The two cookies are both named `Cloud-CDN-Cookie` but carry different
  `Path` attributes (`/pmtiles/private` vs `/pmtiles/internal`), so browsers
  send the right one per request automatically.
- The existing signing key (`shared-datasets-pmtiles-v1`, attached to the
  backend bucket) validates any prefix policy. **No new key or secret.**

## Component Changes

### 1. Catalog & schema (shared-datasets-1)

- Allow `access_tier = internal` in `catalog/categories`/CSV validation,
  asset-doc frontmatter validation, and catalog regeneration.
- `docs/standards/asset-layout-and-formats.md`: document the three tiers and
  what `internal` means (owner-domain restricted).
- `docs/pmtiles-cdn.md` consumer contract:
  - Update line ~271 to ratify the decision already made: `private` = any
    authenticated user (replacing "SkyTruth or admin users for the first
    rollout").
  - Add the `internal` tier contract: cookie `Path=/pmtiles/internal`, signed
    prefix `https://tiles.skytruth.org/pmtiles/internal/`, issued only to
    sessions with a **verified** email in the allowed domain list or with an
    unexpired app-level guest grant.
  - Update the cookie TTL language from 24 hours to the new 30-day default,
    clamped to grant expiry for guest-granted access.

### 2. Terraform (shared-datasets-1, protected workflow)

`terraform/envs/prod/pmtiles_cdn.tf` is already driven by
`lower(row.access_tier)`, so `/pmtiles/internal/{slug}.pmtiles` path rules and
URL-map tests appear automatically when the first internal asset lands in the
catalog CSV. Required edits:

- CORS conditions currently hardcode private:
  `allow_credentials = split("/", path_rule.key)[0] == "private"` →
  `!= "public"`, and the matching `allow_origins` ternary. (Two lines.)
- `shared_bucket_public.tf` already grants `allUsers` managed-folder IAM only
  when `access_tier == "public"`, so internal assets stay non-public with no
  change. Verify with `terraform plan` that an internal row creates a managed
  folder with **no** public binding.
- No new signing key, address, certificate, or backend.

Tier *reclassification* runbook (e.g. an asset moving `private` → `internal`):
update the asset doc + catalog, run protected Terraform apply (URL map paths
move from `/pmtiles/private/x` to `/pmtiles/internal/x`), then invalidate the
CDN cache for the old path so stale cached entries cannot serve under the old
prefix. Consumers pick up the new URL from the catalog automatically.

### 3. TypeScript SDK (`@skytruth/shared-datasets`)

All additive; existing consumers keep compiling.

**Types** (`pmtiles-cdn-session.ts`, `catalog.ts`):

```ts
export type PmtilesTier = 'public' | 'private' | 'internal';
export type SharedDatasetAccessTier = PmtilesTier; // unify the two enums
```

`getPmtilesTier` / `getSharedDatasetAccessTier` accept `internal`. Unknown
values still resolve to `null` (fail closed).

**One policy function, exported and shared** (`private-access.ts`):

```ts
export type PmtilesViewer = {
  email?: string | null;
  emailVerified?: boolean | null;
  // App-level guest grants, resolved by the consumer's getViewer from its own
  // user store. A grant is time-boxed; expired grants must not be returned.
  tierGrants?: Array<{ tier: PmtilesTier; expiresAt?: Date | null }>;
};

export type PmtilesAccessPolicy = {
  internalAllowedDomains: string[]; // default ['skytruth.org']
};

export const isViewerAuthorizedForTier = (
  tier: PmtilesTier,
  viewer: PmtilesViewer | null,
  policy?: Partial<PmtilesAccessPolicy>
): boolean;
// public  -> true
// private -> !!viewer
// internal-> viewer?.emailVerified === true AND
//            (email domain in allowed list OR an unexpired tierGrant for
//             'internal')
```

This same function backs cookie issuance **and** consumer API payload
filtering, so the two can never disagree.

**Server config** (`pmtiles-cdn-session-server.ts`): replace the single
`privatePath`/`privateUrlPrefix` pair with a tier table (keeping the old
fields as deprecated aliases):

```ts
tierPaths: {
  private:  { path: '/pmtiles/private',  urlPrefix: 'https://tiles.skytruth.org/pmtiles/private/'  },
  internal: { path: '/pmtiles/internal', urlPrefix: 'https://tiles.skytruth.org/pmtiles/internal/' }
}
```

The default cookie TTL moves from 24 hours to **30 days** (decision
2026-06-11): browser sessions should not need re-arming mid-engagement, and
guest cookies are already clamped to their grant expiry regardless of the
default. Consumers can still override via `ttlSeconds` /
`PMTILES_CDN_COOKIE_TTL_SECONDS`.

- `getExpiredPmtilesCookies()` returns one expired cookie per restricted tier.
- New `getPmtilesSessionCookiesForTiers(signingKey, tiers, config)` signs one
  cookie per granted tier.

**The magic piece — a full session-route handler** so consumers stop
hand-writing the contract:

```ts
import { createPmtilesSessionHandler } from '@skytruth/shared-datasets/server';

export const handler = createPmtilesSessionHandler({
  getViewer,      // async (req) => PmtilesViewer | null  (from the app's auth)
  getSigningKey,  // async () => Buffer                   (from the app's secret store)
  policy,         // optional PmtilesAccessPolicy override
  config          // optional PmtilesCdnSessionConfig override
});
```

The handler implements the entire contract once, correctly:

- `Cache-Control: no-store` on every response.
- `DELETE` → expire all restricted-tier cookies, 204.
- `GET ?tier=public` → 204, no cookie.
- `GET ?tier=private|internal` → 401 if no viewer; 403 if the viewer fails
  `isViewerAuthorizedForTier` (so clients can distinguish "log in" from "not
  allowed"); otherwise sign and set the cookie(s) for **every** tier the
  viewer qualifies for (a viewer authorized for internal also gets private,
  so one round-trip arms the whole map), 204.
- When authorization comes from a `tierGrant` with an `expiresAt`, the cookie
  TTL is clamped to `min(configured TTL, time remaining on the grant)` so a
  guest's CDN access cannot outlive their grant by more than the request that
  issued it.
- `GET ?tier=grants` → 200 `{ "tiers": ["public", "private", ...] }` without
  setting cookies, so UIs can decide which layers/datasets to even offer.
- Invalid tier → 400. Never logs or echoes the key or cookie value.

Ship thin adapters: `createNextPmtilesSessionHandler` (pages router, what
cerulean-ui uses) and a WHATWG `Request`/`Response` variant (app router,
30x30, anything else).

**Client** (`pmtiles-cdn-session-client.ts`):

- `ensurePmtilesCdnSession` accepts `accessTier: 'internal'` (it already takes
  the tier straight from the resolved catalog ref, so consumer call sites do
  not change).
- A 403 returns `{ ok: false, status: 403, denied: true }` so UIs hide the
  layer instead of retrying.
- New `getPmtilesCdnGrants({ endpoint })` wrapping `?tier=grants`.

### 4. Consumer integration (what "magic" looks like)

A brand-new consumer (e.g. 30x30) needs exactly three things:

```ts
// 1. One route: pages/api/pmtiles/session.ts
export default createNextPmtilesSessionHandler({
  getViewer: async req => {
    const { data } = await getAPISession(req);
    return data?.user ?? null;
  },
  getSigningKey: getPMTilesCDNSigningKey
});

// 2. Resolve datasets from the catalog (tier comes along for free)
const refs = await resolveSharedDatasetPmtilesRefs(slugs);

// 3. Arm the session before mounting any non-public layer
const result = await ensurePmtilesCdnSession({
  accessTier: refs[slug].accessTier,
  endpoint: '/api/pmtiles/session'
});
if (result.ok) mountLayer(refs[slug].url);
else if (result.denied) hideLayer(slug); // not authorized — don't retry
```

No prefixes, no cookies, no policy, no tier names in consumer code. When a
fourth tier ever appears, consumers pick it up by bumping the SDK.

### 5. Guest access (funders and other passers-through)

Specific outside individuals — funders, partners, reviewers — sometimes need
to see internal datasets without a `@skytruth.org` email. The design supports
this without weakening the tier model:

- **The grant is identity-based, not link-based.** The guest signs in like any
  other user (magic link to their own email takes ~30 seconds, and magic-link
  delivery itself proves inbox ownership). A bare "share link" that arms a
  cookie without sign-in is deliberately excluded: links get forwarded, and a
  forwarded link is an unauditable skeleton key.
- **The grant is resolved by the consumer's `getViewer`.** The SDK never
  maintains a guest list; it only evaluates `viewer.tierGrants`. How a
  consumer stores and looks up grants is its own decision (deliberately not
  prescribed here — see Open Questions); the only contract is that
  `getViewer` returns unexpired grants for the signed-in user and nothing
  else.

- **Grants are time-boxed by default.** A funder demo grant might be 30 days;
  expiry is enforced twice — `getViewer` stops returning expired grants, and
  the cookie TTL was already clamped to the grant remainder at issuance — so
  guest access ends at grant expiry even though the default cookie TTL is a
  month.
- **Granting is an admin action** by a SkyTruth admin, recorded against a
  real account with an expiry, so "who could see this dataset in March" is
  answerable after the fact — something a shared link can never give.
- **Same policy function everywhere** means a granted funder automatically
  gets the full experience: tiles render, the dataset appears in
  `/api/config`, metadata sidecars resolve — no per-endpoint special-casing.

If guests later need access across multiple consumers (cerulean *and* 30x30),
promote the grant store to a small shared service or a non-public GCS object
read server-side by each consumer's `getViewer`. The SDK interface
(`tierGrants` on the viewer) does not change — only where the consumer looks
them up. Per-consumer storage is the right starting point: a funder is
usually being shown one product.

### 6. cerulean-ui specifics (`read_perm` mapping)

- Replace the body of `pages/api/pmtiles/session.ts` with the SDK handler.
- **Mapping**: `aoi_type.read_perm = 3` ('any') ⇒ asset tier `public` or
  `private`; `read_perm = 2` + owner email `@skytruth.org` ⇒ asset tier
  `internal`. `read_perm` remains the Cerulean-side consumer policy for API
  payload filtering; `access_tier` remains the data-plane truth.
- **Consistency check** (the two sources of truth must agree): at config-cache
  load, join `aoi_type.read_perm`/owner-domain against the catalog tier per
  `asset_slug`. On mismatch, fail closed — treat the dataset as the *more
  restrictive* of the two, drop it from anonymous/unauthorized payloads, and
  log loudly. Optionally also a CI check in cerulean-cloud migrations.
- `utils/api/private-shared-aoi-auth.ts`: extend the gates from
  "has session?" to `isViewerAuthorizedForTier(tier, viewer)` using the same
  SDK policy function — covers `/api/config`, AOI search, and the metadata-url
  endpoint with one rule.
- `utils/shared-aoi.ts` `getSharedAoiTypes` currently hard-filters
  `readPerm === 3`, which would hide `read_perm = 2` datasets even from
  authorized SkyTruth users. Accept `readPerm === 2` as well; the server has
  already filtered unauthorized viewers, so the client just trusts the
  payload.
- `owner` is `users(id)`; "SkyTruth-owned" means the owner user's email domain
  is `skytruth.org`. Resolve it in the consistency check, not per request.

### 7. Metadata sidecars and other artifact routes

The `/private/{bucket-object-path}` signed-URL route (used by
`/api/shared-datasets/metadata-url`) serves both private- and internal-tier
sidecars; the GCS objects are non-public either way and the exact-URL signing
already scopes access per object. The authorization decision in the
metadata-url endpoint must use `isViewerAuthorizedForTier` with the asset's
tier — same policy function, no second rule.

## Security Analysis

- **Data-plane enforcement**: tiles are protected by Cloud CDN signature
  validation + non-public GCS objects, not by UI filtering. `curl` with a
  private cookie against an internal path fails `URLPrefix` validation → 403.
- **Single policy function**: cookie issuance, API filtering, and metadata
  URLs all call `isViewerAuthorizedForTier`. No drift.
- **Verified email only**: the internal check requires `emailVerified`. Magic
  link proves inbox ownership; OAuth providers report verification. Any auth
  provider that cannot attest verification must not satisfy the internal tier.
- **Fail closed**: unknown tier → 400/null; catalog resolution failure →
  dataset dropped; `read_perm`/tier mismatch → most-restrictive wins.
- **Revocation latency**: with the 30-day default TTL, a cookie issued to a
  domain-authorized user remains valid up to a month after that user loses
  authorization (e.g. leaves SkyTruth). This is an accepted trade-off
  (decision 2026-06-11) in exchange for sessions that never need re-arming;
  the mitigations are (a) guest cookies are clamped to grant expiry, so the
  long tail applies only to domain-based holders, and (b) rotating the
  signing key (`shared-datasets-pmtiles-v1`) immediately invalidates every
  outstanding cookie — the documented break-glass for offboarding concerns.
- **Guest grants are auditable**: every grant is tied to a real account with
  an expiry, set by an admin action — answerable after the fact in a way a
  forwarded share link never is.
- **Catalog trust**: the catalog classifies *which* datasets are internal; it
  never decides *who* qualifies — that rule ships in SDK code. A hypothetical
  catalog mutation could mislabel a tier, but object ACLs and URL-map routing
  are applied from the same CSV via the protected Terraform workflow, so a
  catalog-only tamper cannot make non-public bytes readable.
- **Cache**: signed-content cache entries still require a valid cookie per
  request (`signed_url_cache_max_age_sec` governs cache fill, not auth
  bypass). Tier moves require explicit cache invalidation (runbook above).

## Rollout

1. **SDK release** (additive): tier types, policy function, tier-table config,
   session handler, client updates. Unit tests: issuance matrix
   (anonymous→401, wrong-domain no-grant→403 internal + private cookie still
   granted, skytruth.org→204 with two cookies, guest grant→204 with cookie
   TTL clamped to grant expiry, expired grant→403), 30-day default TTL,
   DELETE expiry of both paths, grants endpoint, no-store everywhere.
2. **Docs + schema PR**: `docs/pmtiles-cdn.md` contract (including the
   ratified `private` = any-authenticated-user language), standards doc,
   catalog validation accepting `internal`.
3. **Terraform PR** (protected workflow): the two `== "private"` → `!= "public"`
   CORS conditions. No-op until an internal asset exists.
4. **cerulean-ui PR**: adopt the SDK handler, policy-based filtering,
   `readPerm === 2` UI support, consistency check.
5. **First internal asset**: classify (new or reclassified) asset as
   `internal` in its asset doc, regenerate the catalog, protected apply, cache
   invalidation if reclassified.
6. **End-to-end verification**: anonymous, non-SkyTruth-account, and
   SkyTruth-account `curl` checks against public/private/internal tile URLs;
   private cookie replayed against internal path must 403.

## Out of Scope / Future

- Per-organization tiers (a non-SkyTruth `owner` domain with `read_perm = 2`)
  would need a tier registry generating one prefix + cookie per org. The
  `tierPaths` table and policy callback leave room for this; not built now.
- Per-dataset (rather than per-tier) cookies: Cloud CDN supports it via
  longer URLPrefixes, but cookie-count and UX cost is not justified yet.
- IUCN range data stays `private` per the ratified decision unless the data
  steward asks for `internal`.

## Open Questions

1. Tier name: `internal` vs `restricted` vs `skytruth`? (`internal` proposed —
   short, and the allowed-domain list is configurable.)
2. Should the cerulean read_perm↔tier consistency check also run in CI against
   the production catalog, or only at config-cache load?
3. Default guest-grant duration when an admin doesn't specify one (proposed:
   30 days)?
4. ~~Where guest grants are stored and how admins manage them.~~ Deferred by
   decision (2026-06-12): guest/funder access is not being built for now.
   Internal-tier access is allowed-domain only; consumers return no
   `tierGrants` from `getViewer`. The SDK grant machinery (`tierGrants`,
   TTL clamping) stays dormant and unchanged, so this can be picked up later
   without SDK or gate changes. A column on the auth `user` table was
   proposed and rejected (2026-06-11); candidates if revisited: a dedicated
   grants table in the consumer DB, better-auth's admin-plugin roles, or a
   shared SkyTruth-managed grant source.

Resolved 2026-06-11: cookie TTL is ~30 days for all tiers (was an open
question about shortening below 24h — decided the opposite; see Security
Analysis for the revocation trade-off and break-glass).
