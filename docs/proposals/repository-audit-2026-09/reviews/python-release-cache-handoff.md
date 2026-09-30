> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Package C handoff: Python release cache

Complete, awaiting Gate 3 review. [Approved plan](../plans/python-release-cache.md); [charter](../CHARTER.md).

Worktree: `${REMEDIATION_WORKDIR}/worktrees/python-release-cache`
Branch: `codex/audit-python-release-cache`; base `1bf095d861d921e2378203495bd9a0da0bdf650c`.

## Behavior and files

Fetch resolves one release-index snapshot to an exact dated artifact and generation, verifies downloaded/cached bytes, and caches by full URI+generation. Same-date corrections and unchanged CSV dates no longer hide updates. Latest-only assets get an observed generation without an invented release date. Explicit missing versions, checksum disagreement, malformed/forbidden/unavailable indexes, or an index disappearing after stat fail without alias/stale fallback.

Five modified files, all unstaged:
- `api/python/src/skytruth_shared_datasets/catalog.py`: reference identity, strict release selection, pinned public/ADC downloads, verified v2 cache.
- `tests/test_shared_dataset_sdk.py`: realistic storage/HTTP fakes and regression/adverse cases (`ExactArtifactFetchTests`, line 711 onward).
- `api/python/README.md`: identity, cache, legacy/error contracts.
- `docs/consumer-guide.md`: focused Python lineage corrections.
- `docs/shared-datasets-consumer-skill/SKILL.md`: fetched identity versus unpinned alias guidance.

## Evidence and checks

Independent baseline repro in the assigned worktree: remote January -> September returned January with one download and stale `example@2026-01-01` lineage; changing the 7-byte cache from January to corrupt returned corrupt without downloading. Baseline suite nevertheless passed 32 tests +26 subtests.

Final tests cover those failures, same-date replacements, bad receipts, partial download/record writes, concurrent writes, old caches, upstream size/hash/generation mismatch, duplicate JSON/date/artifact ambiguity, historical/missing versions, main/points companions, canonical/localized metadata, no-index/empty-index behavior, public/ADC generation binding, and index/public-permission races. The supervisor independently verified an interim 66 tests +68 subtests; two subsequent focused tests cover post-stat index disappearance and public 403 refusal.

All commands ran from this worktree with the existing runtime, without sync/install:

```sh
UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv \
UV_CACHE_DIR=${REPO_ROOT}/.uv-cache \
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync pytest \
  tests/test_shared_dataset_sdk.py tests/test_pmtiles_redirector.py \
  tests/test_repo_guardrails.py -q -p no:cacheprovider
# 93 passed, 138 subtests passed

UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv \
UV_CACHE_DIR=${REPO_ROOT}/.uv-cache \
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync ruff check \
  api/python/src/skytruth_shared_datasets tests/test_shared_dataset_sdk.py
# All checks passed

<bundled-git> diff --check
# clean
```

## Compatibility, removal pass and limits

- Approved compatibility: `Catalog.resolve(latest)` stays local and retains CDN/custom URL mapping, but is honestly unpinned (`last_updated=''`, `resolved_id='{slug}@latest'`). `resolve_dataset` loads its catalog with ADC then does that mapping. Dated resolve may expose index generation metadata without verifying bytes.
- Fetch adds optional DatasetRef generation/sha256/size/release_index_generation fields and returns `resolved_id` with `#generation=...`. Persist URI alongside it to distinguish artifacts/formats. Existing historical IDs are untouched.
- The v2 cache record has only URI, generation, SHA-256 and size. Old date-only caches are ignored and retained, not deleted/migrated. Every reuse hashes the file; no offline mode is added.
- Downloads stream, then SHA-256 uses a second bounded file pass. Concurrent callers may duplicate I/O; no global locking framework. No claim of one-pass hashing or publisher multi-object atomicity.
- Removed: date-only cache identity, unconditional exists-success, arbitrary first-format selection, unpinned downloader, and static CSV-date lineage. Dated resolve and fetch share selected-artifact resolution.
- Narrow handling retained/added: documented absent/empty-index latest-only compatibility; observed generation for older index entries; re-download of invalid disposable cache entries. Rejected: authentication/outage/race as legacy absence, unchecked precondition retries, stale success, and silently switching artifacts after mismatch.
- No live GCS/network integration, native geospatial checks, package publication or production migration. No dependency/environment changes, Git mutations, commits, PRs, remote changes or merge. Main checkout source was not edited.

Review focus: `_read_release_index` absence versus post-stat race; `_release_ref` canonical/companion ambiguity; transport generation binding; `_verified_cache` and interrupted/concurrent installation; docs distinction between alias resolve and exact fetch. Gate 3 approval is still required; stop here.
