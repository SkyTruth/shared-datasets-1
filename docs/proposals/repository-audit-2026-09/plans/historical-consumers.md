> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Package D — coherent historical browser and TypeScript consumers

Status: approved scope implemented and frozen for Gate 3 review; see `../reviews/historical-consumers-handoff.md` and frozen patch/file hashes. Package B remains frozen and untouched. No installs, Git index/history, or remote dataset mutations occurred.

Worktree: `${REMEDIATION_WORKDIR}/worktrees/historical-consumers`
Branch: `codex/audit-historical-consumers`
Clean verified base: `1bf095d861d921e2378203495bd9a0da0bdf650c`

Read CHARTER, EXECUTION-BOARD, AGENTS, invariant-first-engineering, static-catalog-web-preview, sync-docs-with-code, local-temp-workspaces, catalog preview/consumer/TypeScript/feature API docs, and consumer-review C2/C3. This is a bounded consumer correction using the existing release-index schema and existing endpoints. It ends at an uncommitted review handoff, never deployment.

## 1. Baseline, source evidence, and independent reproduction

The defects remain in this baseline:

- `web/catalog/app.js:795` merges a dated latest release but explicitly retains the mutable top-level PMTiles URL. `selectedVersionValue` changes a missing saved date to latest; `assetReferenceForRelease` returns the current asset for an unknown old release.
- `web/catalog/map-preview.js:560` sends only the slug to the restricted signer. Map feature events carry a date but no captured artifact identity.
- `services/catalog_viewer/run.py:429` signs the top-level PMTiles path regardless of requested date or generation. Download helpers select metadata/schema by date but discard the indexed file generation. GCS and CDN signers likewise omit generation.
- `api/typescript/src/metadata-records.ts:126` gets tiles from the catalog alias and applies `version` only to metadata. Missing releases and legitimate old releases without sidecars both become the latest `resolvedRelease`.
- `artifact-url.ts` returns null for both absent release and absent sidecar and discards generation in artifact URL construction. Browser metadata/schema caches use slug/date/locale, allowing same-date replacement reuse. Browser bounded lookup re-resolves the date; its response includes index generation but no actual sidecar identity.
- `scripts/catalog_site.py` preserves `files[].generation` but emits generation-less derived version URLs. Both producer and browser choose the first same-format file when their preferred basename is absent, which is ambiguous for multiple PMTiles variants.

The existing producer `ingestion/common/release_index.py` already records schema_version 1, asset_slug, latest_release/date, releases[].date/files[], and per-file path/format/generation/size/sha256. No new release schema is needed.

Independent external fixtures are retained under `evidence/historical-consumers/`:

| Evidence | Result at baseline |
|---|---|
| `reproduce.mjs`, `javascript-baseline.json` | Eight expected invariant failures: TS historical tiles, missing version, old no-sidecar date, metadata generation; browser latest tiles, missing selection, unknown old feature reference, signer version propagation. |
| `reproduce_signer.py`, `signer-baseline.json` | Historical request signs `/latest/`; metadata request with expected generation 999 signs indexed generation 102 and returns 200. Imported service path proves the assigned worktree was used. |
| `typescript-baseline.tap` | All 28 existing TypeScript tests pass, compiled from this worktree. |
| Focused Python baseline | `test_catalog_viewer.py`, `test_catalog_site.py`, `test_catalog_web_pmtiles_js.py`: 56 passed, 176 subtests. |

Fixtures use synthetic two-release data, not production objects. A preliminary isolated TS runner failed because it lacked the package self-reference metadata/source files; copying the unchanged package.json, README, and source snapshot into the external runtime fixed that harness, without dependency installs or symlinks.

## 2. Invariant, owners, and trust boundaries

Invariant: a successful indexed layer selection resolves one concrete release entry once, then carries its exact selected PMTiles/metadata/schema identities through rendering, signing, cache lookup, downloads and feature inspection. An artifact identity is bucket/object path plus generation, with optional checksum/size from the same release entry. A date alone is not an immutable identity. Missing optional metadata does not change the selected release.

Impossible states:

- A feature ID from tiles A is enriched with sidecar B after latest advances, a same-date replacement, a locale change, or a delayed response.
- Missing explicit date, missing selected PMTiles, malformed index, or ambiguous file selection silently uses latest or another file.
- A requested generation authorizes an arbitrary URI, another asset/root, another format, or an unreferenced generation.
- A cache hit selected only by date replaces a newer artifact's values, or a signed URL refresh changes the layer identity.
- Unpinned legacy aliases claim exact dated lineage or perform an apparently verified cross-artifact join.

Owners: release-index producer owns published file identities; each runtime's boundary parser creates a detached selected-release value; the catalog viewer owns authorization and exact URL signing; application state owns the selected value's lifetime. Browser events carry the captured selection instead of consulting a newly mutated global asset. External boundaries are generated catalog/index JSON, endpoint requests/responses, transport errors, and optional legacy fields. These are consumer-construction/boundary defects, not a reason to change IDs or the release-index writer.

## 3. Smallest complete fix and removal pass

### A. Select a release and files once

Add a small dependency-free browser module, `web/catalog/release-reference.js`, owning selected-release construction and artifact identity/URL helpers. Keep browser layout, map rendering and fetch orchestration in their current files. In TypeScript, place equivalent release/file selection ownership in `artifact-url.ts`, reused by the layer and standalone metadata helpers; no generalized contracts framework.

At the JSON boundary validate supported schema, matching slug, real ISO dates, unique release dates, a latest pointer referencing an actual release entry, files shape and selected artifact paths. Require the asset's catalog bucket and release root; never derive ownership from the client's requested URI. Preserve complete `files` in the producer and use them as authority, not stale top-level feature_metadata or URL values.

Choose primary PMTiles/FGB by the catalog-selected basename when present; otherwise accept only a sole matching candidate. Multiple remaining candidates or duplicate primary matches fail. Canonical metadata and each requested locale are separately unambiguous; absent requested locale may fall back to the canonical sidecar within the same selected release. Never use another release's sidecar. A schema/sidecar advertised without usable identity is not the same as an absent optional artifact.

Generation handling accepts positive exact integers or canonical decimal strings; JavaScript rejects unsafe numeric integers instead of rounding them and normalizes accepted values to decimal strings for equality/URLs. Reject bool, zero, negative, fractional, malformed or out-of-64-bit values. Validate present checksum/size fields without converting null or empty size to zero. Missing legacy generation is represented as unsupported for an exact selection, as described below.

`scripts/catalog_site.py` retains the existing top-level convenience aliases and versions/files contract. Correct version URL generation and ambiguous variant selection there; do not add duplicated persisted snapshot fields. Package a new browser module in the existing static copy list and viewer static allowlist.

### B. Carry the captured browser selection

`selectedReference` uses release-owned fields for both latest and dated selections, never a top-level URL override. A frozen in-memory selection holds the concrete date and detached normalized artifact references. Resolve latest once when mounting a layer. Selecting/reloading a new snapshot can replace the complete layer; an in-flight request cannot mutate an already mounted layer's identity.

Pass the captured selection into map feature events and metadata lookup groups. Replace slug/date re-lookups and date-only cache keys with path/generation identity keys, including the chosen locale artifact and schema identity as appropriate. Metadata language changes select another file from the captured release entry. Existing render/request serial checks discard responses for obsolete UI selections. Do not add another global cache or an eviction project.

Public indexed PMTiles, sidecars and schema use the existing `/artifacts/{object}` route with `generation=...`; public FGB/download/copy references likewise retain selected path/generation. Preserve configurable bucket/base URL rules and preview bucket behavior. Add generation before any signature; never append parameters to a completed signed URL. A SHA-based `v=` parameter alone is not a generation constraint.

The repo already uses a Cloud CDN backend bucket for `/artifacts/*`. Google's [cache-key documentation](https://docs.cloud.google.com/cdn/docs/caching#cache_keys) says backend-bucket keys include the recognized generation parameter, and the [GCS XML API parameter reference](https://docs.cloud.google.com/storage/docs/xml-api/reference-headers#generation) defines generation as selecting an object version. This supports using the existing route without Terraform changes; it does not constitute a live deployment test.

Explicit missing/changed release errors are shown in the map/detail status, leaving search/docs usable. Missing optional sidecar leaves compact feature inspection available with an explicit metadata-unavailable message. Snapshot conflict says to reload the catalog/reselect; expiry says authorization expired. No automatic re-resolution to latest or another generation. Restricted signer absence must not cause a pinned historical layer to fall back to a mutable cookie URL.

### C. Restrict signer and download resolution to the selected catalog-owned artifact

Use one small internal selected-file result in `services/catalog_viewer/run.py` for PMTiles and existing FGB/metadata/schema download resolution. Read one release-index payload per request. The slug selects the trusted catalog asset, the concrete version selects its indexed release, and the format/primary basename/locale selects its artifact. Requests do not accept an arbitrary GCS URI or path.

The existing PMTiles signer gains `version` and optional expected `generation` query parameters; the existing download endpoint gains expected `generation`. New indexed browser calls always send the concrete date and selected file generation. Reject malformed/duplicate control parameters (400), missing explicit release/file (404), and a supplied generation differing from the authorized selected file (409). Invalid catalog/index identity is a boundary error, never a fallback. A supplied generation cannot expand the allowlist. Unknown URI-like request parameters never influence selection.

Return additive `resolved_release`, `gs_uri`, and `generation` identity fields with the URL/expiry. Browser verifies these against the captured selection before using the URL. Old callers without expected generation can still request the currently indexed file; this does not promise snapshot coherence for old clients. No-index slug-only latest calls retain only explicitly documented legacy alias semantics.

Extend the existing signer interfaces with an optional exact generation. GCS signed GET URLs include that generation in the canonical signed query. Metadata CDN URLs include it before HMAC signing. Public responses include generation in their artifact URL. Preserve same-origin credentials for requesting URLs and credential-free use of signed GCS URLs. Preserve current IAP/domain rules for both private/internal assets; do not replace them with TS consumer-specific grant rules. Keep TTLs, HEAD/OPTIONS behavior and no-store responses. TS cookie/grant issuance and expiry-clamping remain unchanged and receive regression coverage.

If the old generation is still in the selected index but no longer retained by storage, an exact read fails; never retry unpinned. If the generation remains retained but is no longer referenced by the current authorized release index, a refreshed signer request refuses it with conflict. Public already-resolved URLs can continue only while their exact object exists. Neither case swaps metadata into old tiles.

### D. Verify bounded lookup's actual identity

The browser may choose `POST /v1/assets/{slug}/releases/{date}:lookup` instead of loading a large sidecar. Add a minimal serving-result contract carrying documents and optional enforced sidecar identity. Only GCS results may report the exact pinned/cache identity they actually used. Firestore and legacy dictionary backends report absent identity: they do not enforce the resolver sidecar arguments and must never echo them as proof. Expose that optional identity in the existing response. Test stale same-date Firestore records cannot enrich an exact browser layer. No new endpoint, request protocol, storage backend, or Firestore provenance scheme.

Before enriching, compare response asset/date and canonical sidecar URI/generation against the captured selection. A missing or different identity cannot enrich an exact layer. Existing public streaming fallback may read the exact pinned sidecar; otherwise show compact properties plus metadata-unavailable/conflict. Canonical lookup keeps its existing locale limitation, which must be explicit rather than claiming a localized response. No speculative second API call that re-resolves latest. This also protects consumers while viewer/service rollout versions differ: identity-less older responses are not trusted as exact.

### E. TypeScript API compatibility decision for Gate 1

Preserve `resolveSharedDatasetPmtilesRef(s)` and catalog-only helpers as cheap unpinned alias resolvers, just as C preserves Python alias resolution. Extend catalog metadata additively with the catalog primary PMTiles path needed for deterministic selection.

`resolveSharedDatasetLayer` becomes the coherent resolver: fetch catalog and one index, select release independently of sidecar presence, select PMTiles from that release, and return the selected artifact descriptor plus optional sidecar. Public `ref.url` is the generation-pinned selected PMTiles URL. `resolvedRelease` is the selected date even when sidecar is null. Missing explicit release or absent selected PMTiles throws `SharedDatasetCatalogResolutionError`.

For indexed private/internal layers, return the exact PMTiles descriptor (file/gsUri/generation) and `ref.url: null`, as the current sidecar already does. Change only the layer-specific ref type to `Omit<SharedDatasetCatalogRef, 'url'> & {url: string | null}`; the catalog ref type remains unchanged. This is an intentional compile-visible correction for restricted layer callers: obtain an authorized URL for the selected descriptor before mounting. Keeping a latest cookie URL would preserve the defect; inventing browser signing would widen access. No new callback/backend is required. Document the exact restricted call flow and preserve all authorization helpers.

Add optional generation to `SharedDatasetArtifactUrlOptions` and the existing server artifact signing config so app-owned backends can sign the selected descriptor after their existing tier authorization. Signatures cover generation and expiry. The low-level signer stays a low-level server helper; it does not become a public URI-signing endpoint. Do not import signing code into browser exports or add dependencies.

Removal candidates: delete top-level PMTiles override on indexed latest; implicit missing-date-to-latest conversions; metadata-null-to-latest resolution; first-file ambiguity fallback; date-only metadata/schema identity; asset re-fetch by slug/date for captured map features; generation-less selected-artifact URLs. Retain legacy catalog aliases and low-level unpinned URL helpers only for their documented API compatibility. Existing catches remain only at external/UI boundaries and must not label unresolved selection as latest success.

## 4. Existing data and compatibility strategy

| Existing state | Intended result |
|---|---|
| Valid index and selected PMTiles plus metadata with generations | Exact dated/generation layer and joins; latest is captured once. |
| Selected release has PMTiles but no sidecar | Correct selected tiles/date; null optional sidecar; compact inspection. |
| Sidecar locale absent | Canonical same-release fallback with existing fallback flag. |
| Explicit release missing, release missing PMTiles, ambiguous variants | Fail explicitly; do not use latest, another format, or an arbitrary variant. |
| Same-date generation replacement after selection | Existing URLs/cache stay bound to old identity; signer mismatch or missing old object fails until whole selection is refreshed. |
| No release index (definitive absence), version=latest | Preserve map-only catalog alias compatibility; resolvedRelease=null, no cross-artifact metadata/schema join or claim of exact lineage. |
| No index, explicit dated version | Fail; never construct a guessed path. |
| Old indexed PMTiles lacks generation | Exact indexed layer unavailable with actionable message. Discovery/docs and separate legacy alias resolver remain available. No current HEAD/read is certified as original history. |
| Indexed sidecar/schema exists but lacks generation | Exact metadata/schema capability unavailable; do not silently borrow latest or label it simply absent. Already exact PMTiles may still render compact properties. TS metadata helper reports the unsupported identity rather than returning guessed identity. |
| First-upload/local catalogs with only top-level metadata declarations | Discovery/legacy map preview remains; coherent metadata joins wait for a usable index. Update the old docs promise explicitly. |
| 401/403/expiry, timeout, invalid JSON or inconsistent latest pointer | Visible error; never interpret as authoritative legacy absence or switch identities. A caller may retry the same snapshot. |

No production inventory, seed, migration, index rewrite, IAM grant or remote read is part of this package. Assets lacking generations need separately reviewed index repair from trustworthy publication evidence; this plan cannot promise all currently published assets satisfy the stronger consumer boundary. No historic generations are fabricated. Dataset IDs/hash algorithms are unchanged.

## 5. Exact intended files

Production:

- New `web/catalog/release-reference.js`: bounded pure release/artifact construction and identity helpers.
- `web/catalog/app.js`: release selection, identity-aware downloads/sidecar/schema/cache/lookup, UI failure handling.
- `web/catalog/map-preview.js`: signer request/response binding and captured identity in click events.
- `scripts/catalog_site.py`: preserve/provide selected artifact generation URLs, ambiguity handling, copy new browser module.
- `services/catalog_viewer/run.py`: shared selected-file resolution, constrained expected generations, signer identity/URLs, static module allowlist.
- `services/feature_preview_service/run.py`: additive actual sidecar URI/generation response fields only.
- `api/typescript/src/catalog.ts`, `artifact-url.ts`, `artifact-url-server.ts`, `metadata-records.ts`: primary artifact metadata, one release selector, generation URL/signing, coherent layer types/results. Existing index/server wildcard exports suffice; no export routing or package dependency changes expected.

Tests:

- `tests/test_catalog_site.py`, `tests/test_catalog_viewer.py`, `tests/test_feature_preview_service.py`, `tests/test_catalog_web_pmtiles_js.py`.
- New `tests/test_catalog_release_reference_js.py` invoking Node behavioral checks against the actual browser module and request integration, not string-presence correctness claims.
- New tiny `tests/fixtures/historical-consumers.json`: shared two-release / variant / generation fixture data, no large artifacts.
- `api/typescript/tests/shared-datasets.test.mjs`; existing session-handler suite remains unchanged regression coverage unless a focused assertion is needed.

Docs: `docs/catalog-web-preview.md`, `api/typescript/README.md`, `docs/consumer-guide.md`, `docs/feature-metadata-api.md` (only added response identity and snapshot semantics). No catalog CSV/asset docs or generated tracked catalog output changes. Any further persisted/public contract or ownership change returns to Gate 1.

## 6. Meaningful regression/adverse checks

First preserve baseline-failing tests using deliberately different feature IDs/geometries/metadata in A and B. Run final independent fixtures as well as implementation tests; do not merely invert source-string assertions.

1. Browser/TS latest and historical selections pair the matching dated PMTiles and sidecar. Latest advances between catalog fetch, index fetch, signing and metadata requests; every successful join stays with the captured index selection.
2. Same date/path changes generation; URL/cache identity changes on a new selection. A delayed old metadata/schema response cannot overwrite a new selection. Old retained generation can be read exactly when still authorized; unretained or no-longer-referenced generation fails without fallback.
3. Missing explicit release fails; selected old release with no metadata stays old. Missing locale falls back only to canonical within that selected release. Missing schema has a clear capability result.
4. PMTiles primary variant matches preferred basename; sole nonstandard variant works; multiple nonprimary or duplicate primary entries fail. Producer/browser/TS/signer make the same decision with the shared fixture.
5. Unsupported/malformed index, duplicate dates/files, wrong slug/bucket/asset root, outside-release paths, invalid generation/size/checksum, unsafe numeric generation, and inconsistent latest pointer fail at the relevant boundary. Generation omission is tested separately from malformed presence.
6. Signer rejects arbitrary URI/path influence and mismatched version/generation. A captured old date remains usable when latest advances to a different date. Same-date replaced file cannot be signed under an old expected generation. Test localized metadata, schema and FGB as well as tiles.
7. Anonymous public success; anonymous/wrong-domain private/internal refusal; authorized IAP private/internal success; expiry and reauthorization preserve snapshot. No signing call on denied requests, no widened cookie prefixes or grants. Existing TS grant-expiry and public/server export safety tests remain green.
8. GCS signing and CDN HMAC vectors assert the exact generation is inside signed content. Public artifact and range URL checks preserve generation. No post-signature cache-busting mutation.
9. Bounded lookup echoes the actual sidecar identity; mismatched/missing echo never enriches captured tiles. Public exact streaming may recover from an unavailable bounded endpoint; restricted unavailable identity shows compact properties and reason.
10. Legacy no-index latest remains explicitly unpinned map-only; explicit historical fails; present index without generations is not automatically trusted. 403/timeout/malformed index never activates legacy mode.
11. New module is emitted by catalog builder and served by viewer. Current repo catalog builds without native dataset bytes or remote calls. Existing source-only checks remain smoke tests, not the proof of join correctness.

Browser QA after approval: build an external fixture bundle and serve on loopback. Drive the real UI with the available browser tool; use a fixture-only fetch transport shim to map catalog/artifact requests to a local generation-aware fake store, without changing production selection algorithms. Existing local `tippecanoe` and `pmtiles` can create two tiny different point archives outside the repo; validate magic, `pmtiles verify/show`, and decoded IDs before calling them real map evidence. Use the real pinned map libraries when available. Check public/private/internal historical map plus click attributes, A/B IDs, latest advancing, locale/colorizer, missing release/sidecar, expiry, signer failure, and stale async response. Also smoke-test search/filter/detail/docs/copy/FGB controls and narrow layout. Preserve screenshot/request-log evidence. If browser/dependency/native execution is unavailable, report that exact gap; Node fixtures alone are not visual QA. No production datasets or deployed endpoints are required.

## 7. Cross-package boundaries and non-goals

- C owns Python SDK. Its alias-versus-exact-fetch distinction is preserved here. Only overlap is TypeScript portions of `docs/consumer-guide.md`; retain C's independently reviewed Python wording when integrating. No C source changes.
- B stays frozen; no ID/hash/manifest sequence changes. D cannot repair historical identifier reuse.
- F owns publication activation/ownership. D consumes existing indexed file identities and fails on inconsistent/unavailable selections; it does not claim multi-object publication atomicity or introduce a receipt/pointer protocol.
- No E workflow changes, G translation integrity changes, source data writes, new metadata backend, generalized schema package, broad cache/LRU work, access-tier policy redesign, catalog hydration performance redesign, deployment, npm publishing, or production migration.
- Existing indefinite catalog-cache fallback during authorization outages is a separately identified service concern, not silently claimed fixed here. New generation/path authority remains constrained to the same server-owned catalog and indexed asset root; no caller-selected asset access is added.

## 8. Validation commands, limitations, and gates

Every source command uses the assigned workdir. Python runs with the existing environment and no sync:

```sh
UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv \
UV_CACHE_DIR=${REPO_ROOT}/.uv-cache \
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q \
  tests/test_catalog_site.py tests/test_catalog_viewer.py \
  tests/test_feature_preview_service.py tests/test_catalog_web_pmtiles_js.py \
  tests/test_catalog_release_reference_js.py -p no:cacheprovider
```

Practical TS strategy, already verified without installs/shared dependency mutations:

```sh
node ${REPO_ROOT}/api/typescript/node_modules/typescript/bin/tsc \
  -p api/typescript/tsconfig.json \
  --outDir ${REMEDIATION_WORKDIR}/evidence/historical-consumers/ts-runtime/dist \
  --typeRoots ${REPO_ROOT}/api/typescript/node_modules/@types
```

Copy the assigned worktree's package.json, README, tests and source snapshot into that external `ts-runtime/` directory; copy the shared fixture at the same relative location if a test imports it. This supports package self-reference exports and source/docs scans. Run `node --test .../ts-runtime/tests/*.test.mjs`. Installed Node is v20.20.0; compiler and types are read-only existing dependencies. Do not run npm install, npm prepare, uv sync, or create node_modules links in any worktree. The first compile points directly at assigned source, not the copied baseline snapshot.

Also run relevant Python Ruff checks, JS syntax checks, exact selection regressions, local catalog build/inspection, current catalog docs check, and read-only `git diff --check`/status via bundled fallback Git. Run the full Python suite once after focused passes if relevant; preserve the known shared-temp Terraform executable-guard limitation if it appears, never relax the guard.

Limitations: no live cloud/IAP/CDN request verified; offline signing/transport fixtures prove requested identities and refusal behavior, not deployed IAM/CORS/retention. Local source acceptance differs from readiness to roll out the stricter legacy behavior. Browser, viewer and TS callers must adopt response/type changes together; missing response identities fail visibly during mixed-version rollout. No automatic deployment, merge or SDK version publication is authorized.

Completion checklist:

- [x] Explicit supervisor PLAN APPROVED before tracked edits.
- [x] Baseline failures preserved; final behavioral and adverse tests pass.
- [x] All map/inspector/colorizer/download/cache/signing call sites carry selected identity.
- [x] No missing version fallback, arbitrary URI signing, unpinned restricted alias in coherent layer, or fabricated legacy history.
- [x] Public/private/internal and expiry semantics preserved and tested.
- [x] TypeScript layer nullability compatibility change and legacy restrictions documented honestly.
- [x] Browser QA executed or exact limitation reported; source/test/build/type/lint checks complete.
- [x] Removal pass records deleted fallbacks and intentional alias compatibility.
- [x] `reviews/historical-consumers-handoff.md` gives files, commands/results, residual deployment/migration limits, and exact supervisor review instructions.
- [x] No prohibited Git/remote/deployment actions. Stop for Gate 3 review.

Invariant-first recommendation: create one selected artifact identity at the boundary and remove the later independent date/alias resolutions. Do not repair mismatched metadata with another fallback.
