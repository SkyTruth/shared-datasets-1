> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

## Problem and resulting behavior

Generated feature IDs can be reused accidentally after a feature disappears. With A=1/B=2, deleting B can lower the inferred next ID to 2 and give an unrelated C that same ID. The defect was reproduced in both the allocator and the actual WDPA builder.

This PR allocates new generated IDs from a validated, persisted next-ID counter instead of the maximum surviving ID. Deletions and empty releases cannot lower that counter. Existing matching and explicitly reviewed identity decisions preserve IDs where appropriate; a returning absent key receives a new allocation rather than automatically reclaiming its retired ID. Source-field IDs and hash canonicalization are unchanged.

## Why draft — merge/deployment blockers

**Do not merge until both prerequisites below are satisfied.** These ingestion/common changes can trigger ingestion deployments on merge. Draft status alone does not implement a deployment fence.

- [ ] Review complete historical allocation evidence and perform an approved migration for each existing generated-ID asset. No production asset was audited or migrated in this work. Current rows, current listings, and release indexes alone cannot establish that an old ID was never allocated or overwritten. Insufficient history requires an explicit remediation decision, not a guessed starting number.
- [ ] Integrate publication ownership enforcement before newly allocated IDs become visible. Two jobs must not publish against the same captured baseline. A generation preflight alone is not a lock. The separate publication-recovery core is not sufficient until actual writer adapters, adoption, and old-writer controls are implemented and accepted.

## Implementation

- Define one compact, validated identity baseline containing prior identity records, next-ID, release, and exact manifest snapshot (`path`, `generation`, `sha256`). Load referenced metadata at its recorded generation and verify release, hash, count, and path.
- Carry before/after sequence state through the streaming writer and real WDPA/sea-ice builders. Expose the baseline snapshot for the separate publication-ownership contract. Keep the counter through deletions, reviewed reuse, and empty output; enforce numeric limits without wraparound.
- Add offline `feature_id_sequence_audit.py audit` and `prepare-manifest` commands. These validate supplied history, exact generations/hashes, partial allocations, reviewed reuse evidence, manifest references, and current anchors, then prepare a review candidate. They make no network calls or remote writes.
- Keep the migration boundary explicit: a successful result proves consistency of supplied evidence, not authenticity or globally complete history. The result is `prepared_for_review`, not runtime allocation authority; jobs accept no seed file or integer override.
- Remove maximum-live-ID initialization, collision-skipping/output-max tracking, and broad automatic WDPA legacy reinterpretation. Retain explicit historical compatibility readers and evidence conversion where still needed.

Changed files (19 total) are the shared feature model, common metadata/GCS ingestion helpers, WDPA/sea-ice builders and READMEs, the new offline audit and migration guide, consumer/layout documentation, and focused tests/helpers. No publication lock, automatic migration, remote dataset repair, or arbitrary numeric seed is added.

## Validation

Previously completed against the accepted source, with identical committed bytes independently verified before this PR:

- Independent supervisor run across eight affected suites: **206 passed, 129 subtests passed, 2 native-tool skips**.
- Baseline regression evidence: both the allocator and actual WDPA builder reused ID 2 after deletion.
- Six-release streaming-writer reproduction: `A1/B2 -> A1 -> A1/C3 -> A1/B4/C3 -> empty -> D5`, with next-ID values **3, 3, 4, 5, 5, 6**.
- Regression coverage includes exact old-generation reads, unavailable/invalid generations, malformed and duplicate JSON, empty/exhausted sequences, incomplete history, partial artifact mappings, altered evidence, and compact baseline memory behavior.
- Ruff and catalog documentation checks passed; the catalog check reported six existing source-confirmation description warnings. All seven existing WDPA identity decisions passed the offline check. Committed diff whitespace check passed.
- Full combined remediation checkout: **1007 tests and 827 subtests passed**, four native-tool skips and one documented unchanged temporary-path-sensitive Terraform fixture deselected. This is combined-source evidence, not a claim that this standalone branch was tested against today's main.

Checks used the existing repo environment with `uv run --no-sync`; no dependencies were installed. Native geospatial integrations and live GCS were not exercised. Builder tests mocked native conversion boundaries. No test rerun was needed for the pre-PR verification because all 19 committed file hashes matched the accepted evidence.

## Scope and review

This is a code PR, not a request to publish migration candidates. Remote GCS paths changed: **none**. No live migration, deployment, workflow dispatch, repository-settings change, or merge was performed while preparing it.

This PR is authored by `jonaraphael`, the sole configured CODEOWNER. GitHub does not allow requesting a review from the author, so no self-review request is made. The explicit migration and ownership blockers above remain regardless of authorship.
