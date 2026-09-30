> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

## Summary

Python `fetch(..., version="latest")` could reuse stale cached data when scheduled publication advanced without changing the repository catalog date. Existing cache files were also accepted without verifying their contents, including same-size corruption.

Resolve downloads through one validated release index, pin the selected storage generation, and verify the downloaded bytes. Cache identity now includes object URI and generation; reuse verifies contents, and interrupted downloads are kept out of trusted cache entries. Main versus companion artifacts are selected explicitly rather than taking the first matching format.

## Behavior and compatibility

- Explicit missing historical releases fail instead of falling back to latest.
- A definitive initial index 404 or valid empty index retains latest-only compatibility: observe and pin the current object without inventing a release date. Permission errors, outages, malformed indexes and broken nonempty history fail.
- `Catalog.resolve(..., version="latest")` remains an unpinned URL mapper for an already loaded catalog; it no longer claims a verified release date. `fetch` provides verified local bytes and generation-bearing identity.
- Consumers parsing fetched identifiers or treating alias-resolution `last_updated` as verified lineage must adjust. Record the object URI together with the fetched identity.
- Old cache entries remain on disk but are ignored. Each fetch resolves remote identity and hashes cache bytes; there is no offline/stale-cache fallback. Downloads use bounded memory and a second disk pass for hashing.
- This verifies individual artifacts, not an atomic bundle across separate fetch calls. No dependency or access-policy change.

Checking only the mutable latest object's generation would be simpler, but would not provide the same dated-release provenance and published artifact/checksum evidence. The narrow latest-only compatibility path uses that approach where an index is absent.

## Validation

- [x] Baseline-failing regressions cover unchanged catalog dates and same-size cache corruption.
- [x] Independent final SDK, redirector and guardrail suites: **93 tests and 138 subtests passed**.
- [x] Coverage includes same-date replacement, historical selection, malformed/ambiguous indexes, main/companion formats, interrupted/concurrent cache writes, legacy assets, public HTTP and authenticated GCS generation pinning.
- [x] Ruff and `git diff --check` passed.
- Live GCS integration and package publication were not performed; transport behavior is exercised with local fixtures.

## Dataset admission and bucket hygiene

Not applicable: SDK code/tests/docs only. No dataset contract, catalog contents, bucket layout, IAM or remote objects changed. No bucket compliance audit was needed for these local consumer changes.

## Review

Self-authored by `jonaraphael`, the sole CODEOWNER; GitHub does not permit a self-review request.
