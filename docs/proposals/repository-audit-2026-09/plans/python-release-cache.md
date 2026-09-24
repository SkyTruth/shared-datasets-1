> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Package C plan: exact Python fetch identity and verified cache

Status: PLAN ONLY. Awaiting an explicit supervisor message beginning `PLAN APPROVED`. No tracked source files, dependencies, Git index/history, or remote state have been changed.

Worktree: `${REMEDIATION_WORKDIR}/worktrees/python-release-cache`
Branch: `codex/audit-python-release-cache`
Verified clean base: `1bf095d861d921e2378203495bd9a0da0bdf650c`

Read the complete remediation CHARTER.md, AGENTS.md, matching skill frontmatter, invariant-first-engineering, sync-docs-with-code, and local temp standard. This plan covers package C and all three gates in the charter. Implementation, if approved, ends at a reviewable uncommitted diff and handoff; no commits, push, PR, merge, deployment or remote mutation.

## 1. Baseline and independent failure evidence

The defect remains in this baseline. `api/python/src/skytruth_shared_datasets/catalog.py` resolves latest using `asset.path_for_format(...)` and `asset.last_updated`; `_cache_path` uses that date (or literal latest); `fetch` trusts an existing cache file without validating it. Only dated resolve reads the release index. The normal checked-in catalog currently omits last_updated entirely, making the stable `latest` cache directory the common case. Legacy/custom catalogs can still supply a static date.

Independent baseline reproduction, imported from this assigned worktree under `uv run --no-sync`:

1. Create an in-memory catalog with last_updated 2026-01-01 and a mocked mutable latest object.
2. Fetch `January` bytes once.
3. Change remote bytes to `September`, leaving catalog unchanged; fetch again.
4. Modify the cached file to `corrupt`; fetch a third time.

Observed:

```text
module: .../worktrees/python-release-cache/api/python/src/skytruth_shared_datasets/catalog.py
{'remote': 'September', 'returned': 'January', 'downloads': 1, 'lineage': 'example@2026-01-01'}
{'tampered_cache_returned': 'corrupt', 'downloads': 1}
```

Baseline SDK suite: 32 tests and 26 subtests passed. Thus existing green tests do not exercise the broken freshness/integrity invariant. Temporary reproduction files were under the named remediation directory and automatically removed.

`ingestion/common/release_index.py` already supplies the relevant schema: schema_version 1; latest_release/date; releases[].date/files; files[].path/format/generation/size/sha256 (metadata fields optional for older data). This package will consume that contract and will not add a release schema or change the writer.

## 2. Invariant, bad states, owners and trust boundaries

Invariant: every successful `Catalog.fetch` / `fetch_dataset` result identifies the exact GCS artifact generation downloaded or verified in cache. For indexed latest, the release date, URI and expected metadata must all come from a single validated release-index response. Cached bytes must match the resolved identity and their recorded/expected checksum before reuse. Unavailable information is represented explicitly; a CSV date never claims to be verified release lineage.

Impossible states to prevent:

- Returning yesterday's cached bytes after latest advances while CSV is unchanged.
- Reusing a cache entry after the same dated artifact is replaced with another generation.
- Returning truncated/tampered cached bytes or exposing a partial failed download as a success.
- Resolving an explicit missing version to latest.
- Treating authentication failure, timeout, malformed index or incomplete publication as proof of a legacy asset.
- Assigning a historical date to a latest-only object without release-index evidence.
- Combining a release date/path from one index fetch with file metadata from another.

Owners: SDK boundary parsing owns a validated selected artifact; GCS owns immutable generation identity; the SDK download/cache boundary owns byte-integrity verification and atomic local installation. Trust boundaries are CSV/index JSON, anonymous HTTP or ADC storage responses, caller-provided compatible GCS clients, and persisted local cache files. Failures are external-dependency/boundary failures, not reasons to repair producer metadata silently.

## 3. Smallest complete change and intentional API compatibility

### 3.1 Preserve explicit browser alias resolution; make fetched identity exact

`Catalog.resolve(version='latest')` currently means a cheap local catalog-to-URL mapping. It is used by the PMTiles redirector and tests, supports custom browser URL bases, and intentionally emits the latest CDN URL. Preserve that documented convenience behavior and its lack of network I/O. It must return an honestly *unpinned latest alias*: no artifact generation or invented exact lineage from static CSV last_updated. Dated resolve continues to use the index and selects the same validated artifact helper as fetch.

`Catalog.fetch` always uses one internal artifact-resolution path for both latest and dated requests. It does not download the mutable URL returned by the browser alias mapper. It resolves latest to the matching dated release entry when available, then pins that exact artifact. Returned fetched `gs_uri` and `url` describe that artifact, not an alias. This distinction is intentional existing API compatibility, documented explicitly, not an unchecked fallback. `resolve_dataset` remains the convenience/browser resolver; users needing bytes plus lineage use fetch once. No new public resolver API or flag is required for this fix.

Extend DatasetRef additively with `generation`, `sha256`, `size`, and `release_index_generation` (optional before a concrete artifact is observed). `last_updated` on fetched indexed data is the actual selected release date; on no-index latest-only data it stays empty. `resolved_id` on a generation-pinned result becomes `{slug}@{release-or-latest}#generation={generation}`; on an unpinned alias it stays `{slug}@latest`. Include `gs_uri` when recording artifact lineage across formats. The docs will explicitly stop describing an unpinned browser alias as reproducible. This is a documented change to newly returned fetched lineage strings; existing stored IDs are untouched and are not retrospectively upgraded.

### 3.2 Resolve and validate once

Introduce a small internal immutable selected-artifact structure/helper in the SDK module. Reuse it from dated resolve and fetch instead of maintaining separate date/path branches.

- Fetch the existing release index once via the selected access mode; capture the index object generation when available (GCS Blob generation or HTTP x-goog-generation). `versions()` keeps returning the public JSON object rather than adding a new schema.
- Validate object shape, supported schema_version when present (legacy missing version remains documented), matching asset_slug when supplied, real ISO dates, releases/files lists, unique relevant dates, and unambiguous selected format/file. Prefer the canonical basename for multiple files of the same format; exclude localized metadata sidecars from default `format='metadata'`. This follows current catalog artifact selection and avoids selecting an arbitrary point companion or locale.
- For latest, require latest_release.date to select an actual release entry when releases exist. Do not infer latest from maximum date, merge metadata from separate releases, or use alias files when the selected release lacks the requested format.
- Validate the selected URI stays in the asset's catalog bucket and correct asset release root. Validate generation as a positive non-bool integer (including canonical decimal strings from GCS where needed), size as nonnegative non-bool integer, and sha256 as 64 hex characters. Missing legacy optional metadata differs from malformed present metadata.
- If the file generation is absent in an older index, observe the exact selected object's current GCS metadata and obtain a generation, without pretending it is the original historically published generation. Validate any index checksum/size against that object/download. This yields an honest observation of the indexed path.

### 3.3 Generation-pinned download and integrity

- ADC: use a Blob for the selected generation and generation precondition on download; compatible clients must support the documented modern adapter methods. Do not TypeError-retry without preconditions.
- Public: use an exact GCS object HTTPS URL with the selected generation parameter, and verify response generation metadata when supplied. For latest-only or generation-less indexes, obtain object metadata through a bounded HEAD request first; no credentials are obtained implicitly. Preserve access='public' vs access='gcs', and do not sign URLs or broaden tier authorization.
- Stream bytes to a process-unique temporary file, computing SHA-256 and size. Check against supplied release metadata, and enforce GCS generation identity. Download mismatch, lost generation, malformed metadata or transport failure raises FetchError/CatalogLoadError with clear context and never publishes success.
- No automatic stale fallback, alias retry, generation refresh mid-download or hidden retry against a newer artifact. A caller retry can re-resolve latest cleanly after a publish race.

### 3.4 Cache by artifact identity, verify before reuse, install atomically

Use a versioned local cache namespace, keyed by a digest of the full GCS URI plus generation, with the release or explicit latest-only label retained for human navigation. The format/slug/filename are retained. This isolates buckets, assets, generations and old unverified caches.

A small local verification record stores the exact URI/generation, byte size and computed SHA-256, plus selected release/index metadata for diagnostics. This is a local cache record, not another canonical release schema. Validate the record identity and recompute SHA-256/size before every reuse. When upstream sha256 is present, the cached digest must also match that independently supplied checksum.

Write bytes through a temp file then atomic replace, and verification JSON via its own atomic write. An interruption without a complete matching verification record is a cache miss. Concurrent downloads for the same immutable generation may duplicate I/O but must never mix identities or return incomplete bytes; no lock infrastructure is necessary for this bounded patch. Tests exercise failure between installations and concurrent callers. Do not delete old cache directories or migrate old bytes silently.

A tampered entry is not returned; a normal online fetch can re-download the same pinned artifact and atomically repair that entry. If repair fails, report failure, leaving pre-existing unrelated entries untouched. `force=True` bypasses cache reuse while preserving all identity/integrity checks.

Simpler alternatives fail: force=True as a workaround still falsifies CSV-date lineage; date-only cache keys miss same-day repairs; keying by URI alone misses replacement; TTLs are not identity; downloading latest then attaching a release date permits publication races; mere cache-file existence cannot detect corruption.

Removal pass: remove date-only cache identity and unconditional exists-success; replace duplicated dated/latest download path selection with one owner; remove reliance on CSV freshness for claimed exact lineage. Keep alias URL construction only as intentional public API compatibility.

## 4. Explicit existing-data, legacy and offline strategy

| Situation | Intended behavior |
|---|---|
| Current release index with file generation/hash/size | Resolve one indexed dated artifact, pin generation, verify supplied integrity metadata, cache by identity. |
| Older valid release index missing generation/hash/size | Preserve explicit release/path, observe its current GCS generation, verify any supplied hash/size, compute local SHA-256; document observed identity rather than claiming unknown original bytes. |
| Definitive index NotFound/HTTP 404 and version=latest | Supported latest-only path: resolve catalog alias, observe its current generation, pin it, keep release date unknown and identify by generation. |
| Valid empty index with latest_release=null and releases=[] | Same explicit latest-only behavior, if the catalog latest object exists. |
| Missing index for explicit YYYY-MM-DD | UnsupportedVersionError / clear not-found; no guessed dated path and no fallback to latest. |
| Nonempty history missing/broken latest pointer; selected file missing; malformed index | Fail explicitly; do not silently use mutable latest. |
| 403/401, timeout, transport or unexpected index errors | Fail; do not reinterpret as absence or reuse a stale latest cache. |
| Existing old date/latest cache paths | Leave untouched and ignore for trusted reuse; download/verify into the new namespace. No automatic deletion or claim that old bytes were correct. |
| Valid exact-identity cache and index currently reachable | Revalidate cached size/hash and return it without re-downloading dataset bytes. |
| Offline/error before current identity can be resolved | Fail closed. This patch adds no offline mode and does not return cached data claiming to be current. Callers can retain and use an earlier explicit cache_path at their own application level. |
| Object generation changed/disappeared after resolution | Exact download fails; no switch to another generation under the same returned identity. |

The no-index path is required by repo design: dated releases are optional, and `Catalog.resolve` supports custom/legacy catalogs. No live bucket inventory is necessary or authorized to establish that compatibility requirement. The implementation will use representative executable fixtures rather than assuming which current assets have indexes.

## 5. Exact intended files

Production:
- `api/python/src/skytruth_shared_datasets/catalog.py`: selected artifact parsing, exact fetch resolution, added reference identity, transport pinning, integrity/cache handling.
- `api/python/src/skytruth_shared_datasets/cli.py`: only if help wording needs to distinguish alias url output from verified fetch; no new flags planned.

Tests:
- `tests/test_shared_dataset_sdk.py`: regressions and realistic fake storage/HTTP generations; adjust old cache tests that asserted unsafe reuse.

Docs:
- `api/python/README.md`: latest alias vs exact fetch, lineage, compatibility, new cache layout, failures, examples.
- `docs/consumer-guide.md`: focused Python lineage wording where it currently calls resolve aliases durable.
- `docs/shared-datasets-consumer-skill/SKILL.md`: focused Python lineage instruction so future LLM callers do not persist unpinned resolve results as exact artifacts.

No release producer, canonical metadata, catalog CSV, TS/web/service source, IAM, workflow or infrastructure edits. No new runtime dependency. If implementation reveals a needed file/contract change beyond this list, return to Gate 1.

## 6. Regression and adverse-path tests

Tests must fail on the baseline for the motivating defect and pass with the fix:

1. Latest release moves January -> September with identical CSV, including a CSV without last_updated. The second fetch downloads September, exposes the correct date/path/generation and distinct cache identity.
2. Same release date/path replaced with generation B and new checksum; second fetch uses B and distinguishes its lineage/cache from A.
3. Index pointer changes after it is read; selected snapshot's artifact remains the one fetched. If that generation becomes unavailable, fail without switching.
4. Matching-size cache tampering, truncated bytes, missing/malformed/mismatched verification record: never return unverified bytes; online repair succeeds; failed repair returns an error.
5. Wrong upstream SHA-256/size/generation and bool/zero/malformed metadata are refused; partial downloads leave no usable entry. A subsequent valid retry succeeds.
6. ADC/private and anonymous/public tests assert exact generation/precondition or URL, and assert public code never creates an ADC client or obtains signing credentials.
7. Explicit historical release remains selectable despite a newer latest; missing explicit version/format fails. Multiple same-format companion files select canonical basename and canonical metadata locale deterministically or fail ambiguity.
8. No index 404 / valid empty index: latest-only object generation refreshes cache, date remains unknown, no historic inference; historical request fails. 403/timeouts/malformed indexes never invoke that path.
9. Legacy index file lacking metadata: current object metadata supplies generation; supplied checksum is still enforced. Malformed metadata is never treated as missing.
10. Old date/latest cache ignored, not deleted; fresh verified namespace used.
11. Concurrent same-generation fetches and injected interruption before verification-record publication never return mixed/partial bytes.
12. Cache hit may avoid dataset download only after the current artifact identity is established and local bytes verified. Explicit offline/error behavior tested without network.
13. Existing local catalog search, custom URL base, public-GCS inspection, latest CDN alias resolution and PMTiles redirector tests remain compatible and make no new network calls.

## 7. Dependencies, conflicts and non-goals

Independent of package D (historical browser/TS bundle) because no shared source files are changed. Coordinate only the common meaning of release identity; do not introduce a second schema. Package F may improve publication activation/receipts, but this SDK fix depends only on existing release-index fields and fails coherently when an in-progress publisher exposes incomplete metadata. It does not claim publisher multi-object atomicity.

Non-goals: new release pointer protocol, production backfill, old-cache bulk cleanup, new metadata lookup backend, browser changes, SDK publishing/version release, user auth/IAM changes, caching performance tuning beyond correctness, generalized retry framework, and live GCS validation. The no-network alias resolver remains intentional; callers who require exact identity without bytes may warrant a separate future API design, not an opportunistic new flag here.

## 8. Validation and completion checklist

All commands run FROM the assigned worktree with:

```sh
UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv \
UV_CACHE_DIR=${REPO_ROOT}/.uv-cache \
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync ...
```

Planned checks:
- Verify SDK import path points at this worktree.
- Baseline/final independent reproducer for unchanged CSV and tampered cache.
- `pytest tests/test_shared_dataset_sdk.py tests/test_pmtiles_redirector.py -q -p no:cacheprovider`.
- Appropriate focused doc/guardrail tests affected by Python consumer docs, without running remote or deployment tests.
- `ruff check api/python/src/skytruth_shared_datasets tests/test_shared_dataset_sdk.py`.
- `git diff --check` and source status using bundled git (read-only commands only).

Completion:
- [ ] Explicit `PLAN APPROVED` received before edits.
- [ ] All exact identity, no-index compatibility and failure cases exercised behaviorally.
- [ ] No downloaded/cache bytes labeled with guessed historic correctness.
- [ ] Public/private access mode behavior preserved without weaker preconditions.
- [ ] Old cache ignored without destructive migration.
- [ ] Focused docs match code, including intentional alias resolver limitation.
- [ ] Tests/lint/diff checks pass; network/native checks not run are stated.
- [ ] Removal pass records what was removed/retained and why.
- [ ] `reviews/python-release-cache-handoff.md` contains final files, results, compatibility limitations and demanding review instructions.
- [ ] No stage/commit/push/PR/merge/deploy/remote actions.
- [ ] Stop for Gate 3 supervisor review; supervisor approval is not user merge approval.
