> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

## Summary

Selecting a historical map release could still load latest tiles, metadata, or signed downloads, and delayed responses from an earlier selection could overwrite the current view. This could present an apparently coherent map assembled from different releases.

Capture one release selection and exact per-file generation, then carry that identity through browser maps, schema/metadata caches, inspectors, downloads, TypeScript layers, and catalog-authorized signing. Missing explicit releases no longer fall back to latest. Selection changes invalidate delayed map mounts and inspector responses.

The metadata lookup backend must report the pinned file it actually read. Requested provenance is not accepted as proof: legacy/Firestore results without enforced identity cannot enrich an exact indexed layer. Exact tiles remain usable when optional metadata is unavailable, with a capability notice. Canonical bounded lookup values are identified as source-language values when needed.

## Why this is a draft

Source review passed, but caller migration and deployment coordination remain prerequisites to merging and release:

- [ ] Identify restricted TypeScript consumers and migrate coherent layer callers from assuming `ref.url` is always available to the exact descriptor/authorized signer flow (`ref.url` can be null).
- [ ] Coordinate browser, catalog viewer and feature lookup response contracts; verify which deployed backend can provide enforced provenance.
- [ ] Inspect release-index generation coverage and plan any separately reviewed repairs. This PR does not invent missing historical evidence or migrate remote indexes.
- [ ] Verify live IAP/signing, CDN generation behavior and retained historical objects before claiming production readiness.
- [ ] Confirm the deployment order and rollback behavior before marking ready for review/merge.

Access tiers remain unchanged. The signer selects catalog-owned asset/release/files and checks expected generations; it does not grant arbitrary-URI signing. Catalog-only alias resolvers stay compatible. Genuine HTTP404 index absence preserves explicitly limited map-only latest compatibility; permission, network and malformed-index failures do not enable it.

## Tradeoffs and alternatives

This spans 21 browser/SDK/service/test/doc files and deliberately refuses unverified historical joins. Older indexes or backends may lose rich metadata capability until repaired or upgraded. It cannot restore deleted historical bytes, repair source data, or make publication atomic.

A centralized API returning the complete selected bundle could reduce client selection logic, but would add a service dependency for direct-file/static-catalog consumers. This change reuses the existing release index and access services, removes duplicated resolvers and unpinned fallbacks, and preserves direct artifact access.

## Validation

- [x] Independent targeted Python suites: **106 passed, 174 subtests passed**.
- [x] TypeScript compilation and **31 SDK tests** passed.
- [x] Baseline browser/signer regressions now select historical files and reject mismatched generations; tests cover variants, missing metadata, cancellation and unverified backends.
- [x] Real browser QA with native-verified synthetic PMTiles exercised current/historical internal/public views, restricted errors, and stale-map clearing. Transport/authentication were simulated; this is not live cloud proof.
- [x] Ruff, JavaScript syntax, catalog build/docs, guardrails and diff checks passed. Full native ingestion integration remains unrun.

## Dataset admission and bucket hygiene

Not applicable: no new dataset/pipeline, release schema, permission change, Terraform mutation, remote repair or deployment. No remote object paths changed. Live compliance/retention and access checks remain explicit draft prerequisites above.

## Review

Self-authored by `jonaraphael`, the sole CODEOWNER; GitHub does not permit requesting self-review.
