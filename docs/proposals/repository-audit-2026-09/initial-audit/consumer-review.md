> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

**Final snapshot reconciliation by the primary reviewer:** PR #146 merged during this review as `e2a29ea0a5ecd7d7688158a67f25e88400d4224f`; the final checkout is clean main. Terraform queue and wrapper-detection findings in the initial snapshot are resolved in code by that merge. Final equivalent-tree verification: 738 Python tests passed, 4 skipped, 427 subtests passed; Ruff passed. The detailed observations below retain their original snapshot context. No implementation changes were made by the review agents.

# Consumer-surface review

Reviewed 2026-09-22 against the local checkout (parent reports clean c69850b). Read-only source review; no source or Git state changed. `npm test` rebuilt ignored TypeScript `dist` output and passed all 28 tests. Local reproduction snippets imported existing modules, mocked GCS/network, and used a temporary child under this review workspace; the cache reproduction child was automatically removed.

## Coverage and strengths

Inspected Python SDK source and docs; every TypeScript source module and package configuration; all four service entry points and their containers; catalog generation/release-selection paths; web app metadata, download, bootstrap, signer, and cache paths; relevant tests/docs. Followed invariant-first-engineering and static-catalog-web-preview skills. No live browser session, remote GCS reads, deployed endpoint checks, package publishing, infrastructure actions, or mutations were performed. The parent independently ran the broader Python test suite.

Useful existing foundations: immutable Python reference objects, duplicate detection in CSV and sidecar parsing, atomic local download replacement, explicit access tiers with verified-email internal grants, grant-expiry-clamped cookies, separate browser/server package exports, generation checks for sidecar reads, limited lookup ID/response counts, dedicated restricted signing endpoints, source citations/lifecycle metadata, and no private fallback on access-tier lookup failures. These should be retained while simplifying the duplicate implementations.

## Ranked confirmed findings

### C1 — P1: Python latest downloads can remain stale indefinitely and report false lineage

Evidence: `api/python/src/skytruth_shared_datasets/catalog.py:381-398` resolves latest from static catalog canonical_path and `asset.last_updated` without consulting release history. `:422-425` returns a cache hit solely because the destination exists; `_cache_path` at `:600-604` uses the static date. The actual downloader at `:638-642` does not pin an object generation. `api/python/README.md:111-116` recommends `resolved_id` as lineage for latest requests.

The operations reviewer confirmed that ordinary ingestion updates per-asset release indexes, not catalog CSV (`ingestion/common/gcs.py:407-423`). The repository explicitly allows cron dates to advance without tracked catalog-date edits. Consequently normal daily/monthly refreshes need not change the Python cache key.

Reproduced locally with a fake downloader: first fetch remote `January`, change the remote to `September`, fetch again with the same normal catalog. Result: returned `January`; only one network download; `resolved_id == example@2026-01-01`. Even force=True would label new bytes with the stale date. Same-date corrective replacements are also invisible to the current key.

Recommendation: resolve latest once through the release index into a concrete release, artifact URI, generation, checksum and size. Keep a latest alias only as convenience; download and cache by pinned identity, and expose that identity in DatasetRef. Regression test scheduled refresh with unchanged CSV plus same-date replacement.

### C2 — P1: Historical restricted catalog maps silently show latest geometry with historical attributes

Evidence: `web/catalog/app.js:794-802` merges the selected dated release into the map reference. `web/catalog/map-preview.js:560-580` requests a signed URL using only `slug`, with no selected version. `services/catalog_viewer/run.py:429-450` signs the top-level catalog's `pmtiles_path` (latest). Sidecar requests independently use the selected release (`web/catalog/app.js:1797-1805`).

Confirmed by invoking handle_signed_url with `?slug=example&version=2026-01-01`: response signed `gs://bucket/example/latest/example.pmtiles`. The handler ignores the requested historical version. This can display wrong historical footprints, fail feature joins, or attach old metadata to current geometry.

Recommendation: make signer resolve exact selected release and generation from the same validated bundle as the metadata reference; propagate version in the browser. Test a restricted asset with deliberately different IDs/geometries in two releases.

### C3 — P1/P2: TypeScript layer helper mixes latest PMTiles with pinned metadata and silently substitutes latest for missing versions

Evidence: `api/typescript/src/metadata-records.ts:130-131` always resolves the catalog latest PMTiles ref; `:156-168` pins only the metadata sidecar and falls back to latest when sidecar resolution returns null. `api/typescript/README.md:210-240` recommends mounting `layer.ref.url` and joining `layer.sidecar` because they belong to the same release, but also exposes a version option that only pins the sidecar.

Local Node repro: `version:'2026-01-01'` returned `resolvedRelease:'2026-01-01'`, a January sidecar URL, and `https://tiles.skytruth.org/pmtiles/public/example.pmtiles` (latest). An absent requested version `2025-01-01` returned latest resolvedRelease `2026-09-22` instead of a not-found error. A valid old release without metadata suffers the same fallback.

Recommendation: explicit `ResolvedReleaseBundle` with matched tiles+sidecar/schema identity; distinguish absent release from absent optional metadata. Either actually pin the entire layer or expose a clearly separate metadata-only resolver; fail a requested missing version. Latest catalog and release-index fetches should also have a coherent snapshot rule across publishing races.

### C4 — P2: A cold or missing metadata ID blocks every lookup across all datasets

Evidence: `services/feature_preview_service/run.py:153-154,194-209` holds one global RLock while doing `_load_sidecar_records`; `:220-235,319-353` reads/decompresses from the beginning until requested IDs are found or EOF. The same index instance backs production catalog lookup (`services/catalog_viewer/run.py:1074`) and preview service (`services/feature_preview_service/run.py:806`).

Reproduced using two threads and a blocking fake sidecar loader: an already-cached hit for asset `fast` remained blocked by a cold miss for unrelated asset `slow`. Every new click near the end of a large sidecar can rescan it. Max 500 IDs / 10 MiB response does not bound upstream bytes or scan latency.

Recommendation: establish a queryable per-release artifact (e.g. sharded IDs or a local rebuildable SQLite index with exact-generation source), or at minimum use per-bundle in-flight work and release the lock before I/O. Bound I/O/time and report scanned bytes and latency. Do not simply reactivate dormant Firestore without a reviewed operational decision.

### C5 — P2: Sidecar lookup cache grows without eviction, including absent IDs and obsolete generations

Evidence: `services/feature_preview_service/run.py:153,194-209,221` caches every requested ID, including None for absence, under each `(asset,release,URI,generation)` forever. There is no maximum entry count, byte limit, TTL or old-generation removal.

Local repro inserted 1,000 absent IDs; all remained in cache. Authenticated requests can fill memory with arbitrary valid nonexistent IDs without adding useful data. Routine release updates also retain past generations. The browser likewise retains full parsed sidecars in `state.featureMetadataCache` with no aggregate budget (`web/catalog/app.js:1587-1602`); its autoload threshold caps compressed artifact size, not decompressed object memory.

Recommendation: bounded generation-aware LRU/cache accounting; cap negative caching separately; evict old generations. Prefer one bounded index over duplicate full-text/Map representations.

### C6 — P2: TypeScript cold access-tier filtering fans out one catalog load per row

Evidence: `api/typescript/src/private-access.ts:146-163` stores a result only after load completes and has no pending Promise; `:228-244` uses Promise.all with a tier lookup for each row. Thus simultaneous calls all see an empty/expired cache.

Local Node repro: filtering 100 rows of the same slug caused exactly 100 loadAccessTiers calls. This repeats at TTL expiry and can amplify outages. Existing test covers sequential cache hits.

Recommendation: one shared in-flight Promise per loader, cleared on failure; deduplicate slugs before the filter pass. Test concurrent startup, expiry and failed retry.

### C7 — P2: One stalled release-index fetch prevents the entire catalog from becoming interactive

Evidence: `web/catalog/app.js:174-180` awaits all release indexes before filters/events/rendering. `:186-194` starts one unbounded fetch per asset; there is no timeout/AbortSignal. Promise.allSettled tolerates rejections but does not resolve a stalled request.

Recommendation: render catalog metadata immediately; hydrate selected/currently-visible assets incrementally with bounded concurrency, timeouts and per-asset freshness state. Make release selection unavailable only for the affected asset while history loads.

### C8 — P2: HTTP request bodies are read without byte bounds or safe Content-Length validation

Evidence: `services/catalog_viewer/run.py:1027-1029`, `services/feature_preview_service/run.py:779-780`, `services/metadata_service/run.py:768-770` convert Content-Length directly and read that many bytes before application validation. Malformed values raise outside the JSON error boundary; negative values invoke an unbounded read, and large declared bodies consume service resources before max_ids or max_response_bytes matters.

Recommendation: one shared HTTP boundary helper for strict nonnegative bounded Content-Length, request deadline and body-byte limit; return clear 400/413 responses. Configure proxy/runtime limits as defense in depth. Current IAP protection reduces exposure but does not remove the reliability defect for legitimate/corrupt callers.

### C9 — P2: Two lookup APIs implement different request contracts and error behavior

Evidence: active sidecar service `services/feature_preview_service/run.py:567-601` catches JSON syntax but not invalid UTF-8, coerces every ID and field to string, and coerces include_provenance with bool(). Dormant metadata service `services/metadata_service/run.py:450-496` validates strings and boolean explicitly and catches UTF-8 errors. Both use the same `/v1/assets/...:lookup` shape.

Repro: active parser accepts `{"ids":[true,null],"include_provenance":"false"}` as IDs `['True','None']` and provenance=True. Invalid UTF-8 falls through to a generic 500 in the active API. Schema field validation also differs: active service silently projects unknown fields to null, inactive one returns invalid_field.

Recommendation: a single strict request/response schema and parser at the boundary, with shared conformance fixtures in both languages and services. Keep only one owner of normalization and feature-ID validity.

### C10 — P2/P3: Unknown-size browser sidecars can bypass the autoload budget

Evidence: `web/catalog/app.js:1855-1857` uses Number(sidecarFile?.size). `Number(null)` and `Number('')` are both zero, contrary to the documented rule that unknown size disables autoload. Local extraction/repro returned `[0,0,null]` for `[null,'',undefined]`.

Recommendation: reject null/empty/noninteger values before numeric conversion and validate release-file size once in the schema. Add decompressed/aggregate memory limits rather than trusting compressed size alone. Producer paths normally emit integer sizes; this is a boundary defect, not proof that current artifacts exceed the budget.

### C11 — P3: TypeScript object maps can resolve inherited property names as valid assets

Evidence: `api/typescript/src/catalog.ts:256-280` uses `{}` as refs and tests `!refs[slug]`; the catalog parser does not reject inherited-property names. Local repro: `resolveSharedDatasetPmtilesRef('constructor',{fetchJson:async()=>({assets:[]})})` returned Object (a function) rather than throwing. `private-access.ts:172-181` similarly uses a plain object lookup for arbitrary slugs.

Recommendation: Map or null-prototype dictionaries plus Object.hasOwn; validate normalized slug syntax and uniqueness when parsing. This is confirmed type/lookup correctness, not evidence of a deployed security exploit.

### C12 — P2 operational gap: Expected service failures are silent or escape the response contract

Evidence: active lookup `services/feature_preview_service/run.py:502-505` discards every unexpected exception and returns an undifferentiated 500 without a structured log. Catalog signer calls (`services/catalog_viewer/run.py:450,531`) lack a top-level exception boundary, so GCS/IAM signing failures close the stdlib request handler without the documented JSON response. TypeScript session handler wraps only signing (`pmtiles-session-handler.ts:193`), leaving getViewer/authorize exceptions outside its documented no-store error result.

Recommendation: one transport exception boundary, typed external-dependency failures, structured non-sensitive error context, request correlation and measurements of source bytes/caches. Keep invalid persisted data explicit; do not silently fabricate empty responses.

## Complexity reductions with concrete ownership boundaries

1. **One release-bundle contract.** Version resolution, metadata locale matching, file-role detection and path validation are independently implemented in Python SDK, TS artifact helpers, catalog_viewer, feature_preview_service, catalog_site and app.js. Encode a validated bundle with slug/date/access tier and artifact URI+generation+size+checksum once; use that to drive download, map, inspector and cache behavior. C1-C3 are direct symptoms of parallel state.
2. **One active lookup implementation.** `services/metadata_service/run.py` is 834 lines, while `_resolve_release_metadata` always raises inactive at :185-188. It retains unreachable historical-load scans, FirestoreFeatureIndex and the full handler pipeline; active lookup uses the 819-line feature_preview service, which also retains a FirestoreFeatureIndex even though both mains instantiate GcsSidecarFeatureIndex. Keep a minimal documented inactive facade if needed for compatibility, remove/defer dormant internals/dependencies after confirming external callers, and separate the active lookup domain from the feature-preview deployment name. Do not delete an externally consumed endpoint without a migration.
3. **Remove optional-client compatibility fallbacks that weaken guarantees.** `feature_preview_service/run.py:286-310` retries blob.open/download without if_generation_match on TypeError and falls back from streaming to full download on AttributeError. Docker installs modern google-cloud-storage. Prefer an explicit supported adapter Protocol and modern production adapter; put fake compatibility into tests rather than weakening production preconditions. This is a deletion candidate pending dependency/caller verification, not a demonstrated current GCS generation race (reload can pin modern Blob generation).
4. **Reuse browser-safe SDK primitives in catalog code.** The web app repeats locale/path/signed-tier/metadata parsing logic. The SDK already handles CDN-decompressed vs raw gzip bytes whereas app.js always unconditionally decompresses. Align through a tiny browser bundle or shared executable contract fixtures; do not create a second unchecked implementation.
5. **Replace source-text assertions with behavior tests.** `tests/test_catalog_web_pmtiles_js.py:35-250` mostly checks literal marker strings, including the very branching/duplicate code that should be simplified. Retain a small smoke test for exports/HTML wiring, move pure helpers into importable modules, and test outcomes and meaningful browser flows.
6. **Separate discovery metadata and serving authorization freshness.** Catalog caches in catalog_viewer (:108-111) and redirector (:60-63) serve prior catalogs indefinitely during loader failure. Such stale fallback may be acceptable for titles but needs explicit bounded behavior where tiers or paths authorize signing. Add a maximum staleness rule and observability; no stale-tier exploit was established here.

## Material user/maintainer improvements

- **Exact release bundles and lockfiles:** a consumer command/API returns and optionally persists slug, release, artifact generation/checksum, schema version and citation. Reproducible jobs become deterministic and easily auditable instead of treating `latest` as lineage.
- **Bounded feature lookup:** indexed artifacts/queryable service for large datasets; release-pinned batch lookup and optional projected fields/locale. A small bounded response should imply bounded work, not repeated full gzip scans.
- **Generic downloads:** UI and signing API currently only offer FGB, metadata and schema (`catalog_viewer/run.py:566-573`, `app.js:1107+`), leaving CSV/COG and other allowed assets with copied paths. Use validated artifact roles and current access policy to offer the actual files, sizes, hashes, licenses and exact selected version.
- **Release comparisons and schema changes:** display row/field changes, update outcomes, lifecycle warnings and matching successor guidance; provide a machine-readable diff between two releases for downstream maintainers.
- **Shareable state:** URL-encoded asset IDs, release, locale, filter/map selections and a citation copy action would make analysis/support reproducible. Ensure URLs never contain signed credentials.
- **Capability discovery:** a small runtime config should state whether signer/lookup/download endpoints exist. `catalogViewerApiAvailable` (:1904-1910) currently infers capability from hostname, treating any localhost or alternate static host as an API server and making failing speculative calls.
- **Explicit locale fallback:** API fallback for large sidecars sends no locale (`app.js:1450`), while sidecar autoload honors locale. Expose requested/resolved locale and fallback reason consistently; either support locale in bounded lookup or display why source text is returned.
- **SDK package parity:** Python exposes catalog/format resolution; TypeScript catalog resolver mainly returns PMTiles. A small shared release-manifest schema can give both consistent asset discovery and exact-artifact resolution without copying all server dependencies.
- **Freshness/diagnostics:** show release date separately from actual check-in timestamp and catalog build timestamp, plus stale/index-inactive reasons. Operations reviewer separately found last-check-in using source release date.

## Operationalize the interactions

1. Build a consumer conformance fixture set: two releases of each access tier, changed geometry/IDs, no sidecar, missing version, localized fallback, large/unknown sidecar size, malformed JSON and replaced generations. Feed the same fixtures to Python SDK, TS SDK, services and browser tests.
2. Add one deterministic `consumer-check` command that builds the catalog from fixtures, starts local fake signer/lookup endpoints, runs real browser flows, then emits structured check results plus screenshots/trace only on failure. CI should exercise search/filters, dated maps, downloads, expired credentials, click hydration and a slow/unavailable index.
3. Add lifecycle/load checks: concurrent SDK cold start should issue one fetch; cached feature reads must not wait for unrelated assets; cache limits enforced; sidecar lookup records bytes/scanned rows/time; max HTTP body rejected before reading it.
4. Generate types/docs from the versioned release/lookup contract; produce a machine-readable SDK capability and compatibility report so an LLM does not infer behavior from multiple prose sources.
5. Add dry-run consumer migration output before access-tier/bucket-publicness changes, enumerating direct GCS URLs still emitted. Public historical PMTiles/FGB and Python public fetch currently use storage.googleapis.com; availability after bucket-publicness removal was not verified here.

## Invariant-first conclusion

Recommended invariant: a displayed/fetched layer, its metadata and its lineage always reference one validated immutable artifact bundle; bounded requests have bounded work, and access-tier decisions are explicit. Establish this at release-index parsing/resolution, then consume validated types. No implementation changed and no fallbacks were added. Deletion candidates include duplicate resolvers/parsers, marker tests, unreachable Firestore paths, and fake-client production fallback branches. Public API compatibility and deployed consumers must be checked before removal.

Infrastructure clarification from operations review: per-public-asset managed folders intentionally retain allUsers objectViewer (`terraform/envs/prod/shared_bucket_public.tf:47-56,96-114`), even though bucket-wide public access is disabled. Therefore direct public GCS URLs are not a confirmed present failure; CDN-only migration remains a future compatibility check. No deployed IAM was inspected.
