> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# D handoff — coherent historical consumers

Status: implementation FROZEN for Gate 3 review. No merge/deploy permission implied.

- Worktree: `${REMEDIATION_WORKDIR}/worktrees/historical-consumers`
- Branch: `codex/audit-historical-consumers`
- Base/HEAD: `1bf095d861d921e2378203495bd9a0da0bdf650c`
- Approved plan: `../plans/historical-consumers.md`, including supervisor's backend-enforced lookup identity correction.
- Evidence root: `../evidence/historical-consumers/`
- Frozen patch (includes all three new files): `historical-consumers.patch`
- Patch SHA256: `0989bb47f969e92ccb849a1ff87220ee861f246cc98e49f9d7255fca41a19569`
- Per-file SHA256 and exact 21-file inventory: `frozen-files.json`; source status: `final-status.txt`.

## Result and ownership

Indexed browser and TS layer selections bind PMTiles and optional metadata to one concrete release and exact per-file generation. Latest resolves once. Missing date, changed generation, ambiguous primary variant, invalid identity, and unavailable restricted signer fail explicitly. No new release schema/backend/endpoint is introduced.

The browser carries a detached frozen selection into map events, colorizer/schema/sidecar caches, bounded lookup, and downloads. Cache keys contain path/generation. Empty/error/new selection invalidates map mounts (including delayed module import and library/signing awaits) and inspector completions. Missing optional sidecar differs from missing release; valid exact tiles still render with compact properties when legacy metadata/schema lacks generations.

Viewer signer/download resolution authorizes only the catalog-owned asset/release/file. Caller generation is an expectation, never arbitrary URI authority. GCS generation is inside canonical signed query; CDN generation is inside HMAC input. Auth tier/domain/expiry/no-store contracts remain. Historical retained sidecar reads construct `blob(..., generation=...)` and keep the read precondition; unavailable old bytes fail without an unpinned retry.

Lookup provenance is **backend-enforced**. `LookupResult` lets GCS return the pinned/cache sidecar identity actually used. Firestore/legacy dict results return absent identity, even if a resolver requested a valid generation. An exact browser layer refuses these unverified results. Bounded API values are canonical; the inspector labels source-language values if a display locale was selected.

TypeScript catalog-only alias resolvers stay compatible. Only coherent indexed restricted layers return `ref.url: null` plus exact `pmtiles` descriptor. The existing server signing helper accepts generation; docs show authorized backend reselection, expected-generation comparison, signing and caller identity verification. Explicit HTTP404 index absence preserves only map-only latest alias; 403/network/malformed-index errors cannot trigger legacy mode.

## Files and removal pass

Production: `web/catalog/{release-reference.js,app.js,map-preview.js}`, `scripts/catalog_site.py`, `services/{catalog_viewer,feature_preview_service}/run.py`, `api/typescript/src/{catalog,artifact-url,artifact-url-server,metadata-records}.ts`.

Tests: `tests/fixtures/historical-consumers.json`, `tests/test_catalog_release_reference_js.py`, `tests/test_{catalog_site,catalog_viewer,catalog_web_pmtiles_js,feature_preview_service}.py`, `api/typescript/tests/shared-datasets.test.mjs`.

Docs: `api/typescript/README.md`, `docs/{catalog-web-preview,consumer-guide,feature-metadata-api}.md`.

Removed the indexed-latest top-level tile override; missing-version-to-latest recovery; metadata-null-to-latest date inference; first-candidate ambiguity fallbacks; duplicated download file resolvers and unused query helper; date-only metadata/schema cache identity; feature slug/date rediscovery; unpinned sidecar retry on unsupported SDK kwargs. Replaced brittle source-string assertions with behavioral orchestration tests (kept limited wiring smoke tests). Producer/hydrator no longer filter malformed file records into optional absence. Kept catalog aliases explicitly for legacy map-only callers and existing tiered-session APIs.

## Evidence and validation

Baseline independent evidence is retained unchanged: `javascript-baseline.json` has eight invariant failures; `signer-baseline.json` signs latest for a historical request and accepts wrong generation999. Final `verify.mjs`/`javascript-final.json` checks all eight corrected outcomes using actual current modules/compiled SDK. `verify_signer.py`/`signer-final.json` signs the exact historical path and refuses wrong generation with409. These do not require live data.

- Focused Python suites: **81 passed, 106 subtests** (`python-focused-final.log`). Includes shared fixture matrix, producer/browser/TS primary variants, same-date replacement, malformed identity/hydration, canonical-locale notice, real Firestore unverified results, GCS retained/unavailable historical generation and no unpinned retry, signing query/HMAC, app delayed import and map library cancellation, delayed inspector clearing.
- Full Python suite: **745 passed, 4 skipped, 346 subtests; one unchanged environment failure** (`python-full.log`). `TerraformProdApplyTests.test_binary_resolver_accepts_private_executable` creates its fake binary under this `/private/tmp` worktree; executable safety guard correctly refuses shared-temp paths. No Terraform code/test/guard changed. Supervisor baseline outside shared temp passed that test. Skips are existing GDAL/native ingestion checks (EAMLIS, raster, sea ice, WDPA).
- Existing readonly TypeScript compiler: build passed; **31 tests passed** (`typescript-final.tap`), including existing auth grant/expiry/export portability tests.
- Ruff targeted files, all three browser JS syntax checks, `git diff --check`, and static repo guardrails passed. Guardrails initially hit system Git's Xcode-license error; rerun with the documented fallback Git PATH passed.
- `catalog_docs.py check`: current for24 assets, with six pre-existing source-confirmation description warnings. Real catalog build passed; `final-catalog-build/` includes the new browser module and24 asset docs (`catalog-build.json`).

Exact environment for all Python commands, from the assigned worktree:

```sh
export UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv
export UV_CACHE_DIR=${REPO_ROOT}/.uv-cache
export PYTHONDONTWRITEBYTECODE=1
uv run --no-sync python -m pytest -q tests/test_catalog_site.py tests/test_catalog_viewer.py tests/test_feature_preview_service.py tests/test_catalog_web_pmtiles_js.py tests/test_catalog_release_reference_js.py -p no:cacheprovider
uv run --no-sync python -m pytest -q -p no:cacheprovider
uv run --no-sync ruff check scripts/catalog_site.py services/catalog_viewer/run.py services/feature_preview_service/run.py tests/test_catalog_site.py tests/test_catalog_viewer.py tests/test_feature_preview_service.py tests/test_catalog_release_reference_js.py tests/test_catalog_web_pmtiles_js.py
uv run --no-sync python scripts/catalog_docs.py check
PATH=${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback:$PATH uv run --no-sync python scripts/repo_guardrails.py check-static
uv run --no-sync python scripts/catalog_site.py --out ${REMEDIATION_WORKDIR}/evidence/historical-consumers/final-catalog-build
```

Exact no-install TypeScript runtime strategy:

```sh
node ${REPO_ROOT}/api/typescript/node_modules/typescript/bin/tsc -p api/typescript/tsconfig.json --outDir ${REMEDIATION_WORKDIR}/evidence/historical-consumers/runtime/api/typescript/dist --typeRoots ${REPO_ROOT}/api/typescript/node_modules/@types
node --test ${REMEDIATION_WORKDIR}/evidence/historical-consumers/runtime/api/typescript/tests/*.test.mjs
```

The external runtime contains copies of this worktree's package.json/README/src/tests and the shared fixture under `runtime/tests/fixtures/`, needed for package self-reference and portability checks. Compiler/types/dependencies were read-only; no install, prepare, symlink, sync, or shared-environment change occurred. Node20.20.0.

## Real browser/native QA

`browser-qa/` retains the fixture server, request log, synthetic GeoJSON/MBTiles/PMTiles, catalog bundle and native-validation.log. Resolved tools: `/usr/local/bin/tippecanoe`2.79.0 and `/usr/local/bin/pmtiles` reports dev/commit none/build unknown; decoder `/usr/local/bin/tippecanoe-decode`. Both two-point archives pass PMTiles magic, `pmtiles verify`, `pmtiles show`, and tile decode with IDs a1/a2 (old) versus b1/b2 (new). Initial one-point conversion failed zero-area bounds; retained as evidence, never used as successful PMTiles proof.

This subagent's CUA had no browser provider and native app elicitation required root. The supervisor independently used IAB against the actual local app and native archives and reported:

- Internal latest b2/New footprint2/2026-09-22, then historical a2/Old footprint2/2026-01-01.
- Public historical a2/Old footprint2/2026-01-01, generation-pinned100/101 links and no stale inspector.
- Private403 and historical409 both visibly fail and remove the prior map/inspector.
- Restored private success renders b2/New footprint2/2026-09-22.

Root preserves independent browser evidence in its supervisor review. The fixture redirects only artifact transport to a generation-aware local store, uses real pinned map libraries, and simulates signer responses. This is actual rendered-map evidence, **not live IAP/CDN authorization or remote retention proof**. Locale, missing metadata/schema, stale async responses, variant ambiguity and malformed index matrix are automated behavioral tests; no claim of a complete visual/narrow-layout/locale QA pass. Dummy FGB bytes test only the control, not FGB format. Fixture controls are restored false; loopback server session35829 remains for supervisor and may be stopped after review.

## Review and deployment limits

1. Review patch and `frozen-files.json`; ensure file hashes match before independent tests. Challenge selected snapshot propagation, loader pinning, Firestore no-proof behavior, strict signer allowlist, and async cancellation.
2. Inspect TypeScript nullable restricted-layer transition and all user-visible legacy limits. Existing assets with missing generations need separately reviewed index repair from trustworthy publication evidence; this package performs no inventory or migration.
3. Browser/viewer/feature API rollout must account for new identity responses. Old or Firestore unverified lookup responses intentionally cannot enrich an exact browser layer. Restricted TS applications must adopt an authorized exact-descriptor URL flow before mounting coherent layers.
4. Source acceptance is distinct from deployed bucket retention, CDN generation forwarding/cache behavior, live IAP and catalog stale-cache authorization behavior. The latter cache issue remains outside scope. No multi-object publication atomicity claim: F owns activation/ownership. D cannot fix B's historical ID reuse.
5. Only C overlap is `docs/consumer-guide.md`; preserve C's Python text when integrating. B remains frozen and untouched. No source/hash algorithm, release schema, dependency, access policy, Terraform, deployment, remote-object, credential/settings, Git index/history, commit, PR, or merge mutation occurred.

Stop here for supervisor review. No further source edits unless Gate3 requests a bounded revision.
