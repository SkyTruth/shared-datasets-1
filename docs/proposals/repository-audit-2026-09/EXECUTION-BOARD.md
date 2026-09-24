> Preserved planning/review record. See the [archive index](README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Supervised remediation board

> Latest PR checkpoint: PR #154 CI correction `362c260` is committed/pushed; GitHub CI passed, including all 94 native geospatial tests. The PR is open/draft/mergeable into main `da2f61b`, with migration and publication-ownership holds. PR #153 was merged externally as `da2f61b`; its operational prerequisites were not verified here. #148–#153 are merged externally. Publication recovery still awaits the user's PR decision. No merge or deployment was performed by this agent; local main remains untouched.

All branches start at `1bf095d861d921e2378203495bd9a0da0bdf650c`. They are independent worktrees under this directory's `worktrees/`. The user authorized commits on 2026-09-23; all reviewed source is now committed locally. Exact SHAs and history are in [commits/README.md](commits/README.md). Nothing will be merged or deployed by this assignment.

The governing prompt and completion gates are in [CHARTER.md](CHARTER.md). Every assignment starts with a plan-only turn and requires the supervisor's explicit `PLAN APPROVED` message before implementation. A passing test suite does not bypass supervisor review.

| Package | Branch | Initial scope | Status |
|---|---|---|---|
| A | `codex/audit-scratch-cleanup` | Remove content-based deletion authority | Supervisor accepted; awaiting user review |
| B | `codex/audit-feature-id-highwater` | Persist trustworthy monotonic generated-ID state | Source accepted after revisions; merge held for migration/ownership |
| C | `codex/audit-python-release-cache` | Resolve and cache exact release artifacts | Supervisor accepted after companion-format revision; awaiting user review |
| D | `codex/audit-historical-consumers` | Keep browser/TypeScript tiles and metadata on one release | Supervisor source accepted after revisions and real browser QA; rollout compatibility documented |
| E | `codex/audit-publish-approval` | Bind execution to immutable reviewed plan bytes | Supervisor source accepted after revisions; rollout/F integration still required |
| F1 | `codex/audit-publish-preflight` | Verified local snapshots, original-generation CAS, mandatory native checks | Supervisor accepted; standalone worktree |
| F | `codex/audit-publication-recovery` | Publication preflight, exact receipts, safe recovery | F1/F2 core source accepted; F3b/c NOT APPROVED; merge/deploy held |
| F3a | `codex/audit-publication-rollout-gate` | Block premature automatic ingestion deployments | Supervisor source accepted; explicitly blocks all 3 deployments if merged |
| G | `codex/audit-translation-integrity` | Prevent local translation corruption and false success | Supervisor source accepted after independent regression and packaging review |

## Approval record

- A: approved after reading the full plan and source classifier. Approved removal of obsolete `--catalog` and historical scans. Required baseline-failing behavior tests and marker race/incomplete-state tests. Explicitly retained existing age abandonment policy; this package cannot claim active-PR protection or prefix-atomic deletion.
- B: initial plan not approved. Correct strict allocation design, but it excluded every usable legacy seed interface. Required revision to supply a deterministic offline audit/seed preparation and validation path, exact generation binding, reuse detection, history-completeness limitations, and integer exhaustion semantics before implementation.
- B revision: approved after checking the added audit/manifest preparation path. Keep the evidence format bounded; no general log-ingestion framework or automatic history certification. Legacy migration and stale-baseline publication enforcement remain deployment prerequisites. Approved snapshot interface: frozen `GeneratedIdentitySnapshot(path: str, generation: int, sha256: str)` carried by `GeneratedIdentityBaseline(records, next_feature_id, release, snapshot)` and generated build outputs. An explicit genesis has no snapshot only after absence checks. F must enforce ownership before newly allocated IDs become visible; checking a generation and then writing is not atomic ownership.
- C: approved after reading full plan and SDK source. Approved exact fetched identity/cache and intentional unpinned alias resolver compatibility. Required ambiguous-artifact rejection, no generation switching on checksum disagreement, minimal cache metadata, baseline-failing freshness/corruption tests, and explicit public/ADC pinning evidence.
- F1: approved only private verified local snapshots, original-generation finalizer CAS, and failure when mandatory native validation cannot run. F2/F3 not approved: require protected per-asset catalog targets, replay from original expectations instead of replanning live state, explicit state/receipt crash reconciliation, honest source-checkpoint recoverability, fixed preconditions for delayed side effects, and restrictive multi-mode examples. No publication layout/state/receipt changes authorized yet.

- E: approved after full plan review, including automatic localization and planned-alert authority consumers. Run/attempt provenance must not change semantic transaction identity. Executor/derivation version changes must refuse an existing proposal transaction unless replay uses its original pinned executor; no refreshed expectations or new transaction to bypass old progress. F owns persisted replay and must share a tested interface.

- F2 revision: approved the single proposal-key receipt, receipt-before-claim order, exact fixed expectations, bounded post-claim checkpoints, explicit nonrecoverable-source state and retained reservation, protected per-asset catalog target classification, and conservative executor replay refusal. Core/layout implementation only; F3 adapters still require review. Managed deletes must refuse explicitly in v1; no implicit deletion recovery claim. New paths are bounded operational layout using existing approved formats, with no IAM widening.

- D: approved complete plan after inspecting browser/TS/signer and actual lookup backends. Required correction: lookup response identity must come from a backend that enforces it; never echo resolver-requested generation as proof when Firestore ignores that parameter. Approved only a minimal actual-result identity contract, with unverified legacy/Firestore results rejected for exact joins. Accepted layer-specific restricted TS URL nullability and explicit legacy capability limits; alias resolver remains compatible.

- G: approved after reading the complete plan and actual v1 workbook manifest/importer. V2 is justified by missing exported task/options identity, not merely reordered hashes. Requires strict shard/hash closure, human-edit-preserving legacy failure classification, one shared completion decision, and per-file atomic output. Materialized sidecar identity checks must stream or use compact fingerprints; task-pipeline scale remains deferred. Changes stay in translation helpers/tests and translation docs, with F owning remote mutation.

## Supervisor baseline checks

- Main remained clean at `1bf095d861d921e2378203495bd9a0da0bdf650c`.
- `UV_CACHE_DIR=.uv-cache uv run --no-sync python -m pytest -q`: 739 passed, 4 native-tool tests skipped, 427 subtests passed (6.24 seconds).
- Independent authorization reproduction: an old APPROVED review plus a later CHANGES_REQUESTED review is accepted; dispatch event conversion accepts an open PR with `allow_merged=True`. Evidence saved under `evidence/approval-baseline-results.json` and `evidence/baseline-stale-reviews.json`.

## Live configuration prerequisites

Read-only recheck on 2026-09-22: main ruleset 18755592 still has required approving reviews=0, CODEOWNER review=false and stale dismissal=false; required contexts are lint/tests/geospatial-changes. Production environment protection_rules contains only branch_policy, with no required reviewer. No settings were changed. Strict CODEOWNER approval would conflict with the repository’s explicit self-authored merge exception unless that policy is deliberately reconciled; do not silently claim the code changes solve repository-wide authorization.

## Combined review checkout

`codex/audit-integration-review` / `worktrees/integration-review` contains a committed review-only snapshot of accepted A, B, C, D, E, G and frozen F1/F2/F3a changes. All applied without conflicts; each original work-package branch is separately committed and clean. Initial input hashes are in `evidence/integration-inputs.json`; final file hashes, branches and clean-index evidence for all worktrees are in `evidence/final-worktree-snapshot.json`. Final combined Python suite: **1007 passed, 4 native skips, 1 explicitly deselected location-sensitive Terraform fixture, 827 subtests passed**. Ruff across scripts/tests/ingestion/Python SDK and Git diff --check passed. Main remains clean at the original baseline. Log: `evidence/integration-final-tests.txt`. The deselected unchanged fixture passes on root main; its private-executable premise conflicts with a /private/tmp checkout. This integration check is not merge/deploy approval.

## Queued agent prompt template

Read CHARTER.md, AGENTS.md, matching skill bodies, and the named source-review findings. Work only in the assigned worktree at the stated baseline. This turn is PLAN ONLY: no tracked edits or installs. Verify the defect still exists, inspect actual producer and consumer call sites, and reproduce with external disposable fixtures. Write `plans/{package}.md` containing every Gate 1 requirement. Identify exact file ownership and overlaps with other packages. Send the supervisor a concise summary and STOP. Only a later message beginning `PLAN APPROVED` permits implementation. Final implementation must satisfy Gates 2 and 3 and deliver a handoff under `reviews/`.

## Cross-package boundaries

- A removes heuristic completion now; F owns any future producer-created transaction receipt. A must not create a competing receipt format.
- B owns sequence construction, baseline reads, and manifest sequence metadata. F owns publication concurrency/activation. B cannot claim safety against concurrent stale-baseline publications without F or an explicit deployment constraint.
- C owns Python SDK files. D owns browser, TypeScript SDK, and restricted signer. Both use existing release index contracts rather than inventing separate release schemas.
- E owns review authorization, immutable plan extraction, and workflow handoff. F owns mutation execution, recovery, and finalization. Shared helper/workflow edits require explicit coordination and combined validation before a merge recommendation.
- G addresses the narrowly defined integrity defects. Release-wide translation debt/product design in spec-only PR145 is not implicitly part of this change.

## Final evidence required from supervisor

Read every final diff; run independent targeted checks from each worktree; challenge adverse cases and compatibility; return concrete revisions for any unresolved invariant. Record accepted and rejected findings with reasons. Explain the final behavior, branch, tests, migration needs, remaining limits, and merge order to the user before any merge. Do not equate local test acceptance with production readiness.

## F3 scope decision

The broad F3 adapter plan was not approved as one implementation package. Approved only F3a in its independent main-based worktree: strict hold-only registry/gate, three deployment workflow ordering, behavioral/structure tests and focused deployment docs. No bypass, future active/install_guard state, cloud probes, runtime mutation guard, adapter or adoption schema in F3a. Subsequent adapter plan must define durable adoption evidence/seed authority and replay identity before upstream discovery before approval. Core/layout and generated-ID branches remain held.

## Final supervisor decisions

- G: exact 14-file patch source accepted after independent 123 affected tests/2 subtests, four baseline regressions and refusal of the crossreview's 2/02 coordinate reproducer. Full reasoning: reviews/translation-integrity-supervisor.md. Agent full suite: 838 passes/429 subtests, four native skips, known one deselection. Combined final checks pass as above.
- E: additional independent authorization crossreview found no new P1/P2 bypass in the accepted boundary; 17 adversarial cases and 45 tests/53 subtests passed. See reviews/publish-approval-crossreview.md. Live configuration prerequisites remain unresolved.
- F3b/c: not approved for implementation. Revised plan has a bounded module/removal budget and request lookup before upstream discovery, but seed execution must stay unreachable until a concrete protected readiness contract covers old/running/scheduled/manual/historical-workflow writers throughout adoption. Same-date source changes are refused in v1. Five future packages must each obtain explicit plan approval. See plans/publication-recovery-f3-contracts.md, final decision section.
- Every worker is stopped with frozen source. All worktree indexes and working trees are clean following the user-authorized commits. Recovery contains the exact standalone F1 commit as its parent plus a separate F2 commit. Integration is a committed review-only composition, not a proposed single mega-change.

## Commit checkpoint

User authorization on 2026-09-23 supersedes the initial no-commit limit only for staging and committing reviewed work. No push, PR, merge, deployment or remote operation was performed. All holds remain. See [commit record](commits/README.md).

## PR154 merge and rollout plan

The user requested a concrete plan to clear the remaining blockers. See `plans/pr154-merge-and-rollout.md`: P1 source isolation/prewrite validation; P2 historical and writer feasibility; P3 enforced old-writer fence; P4 actual publisher ownership; P5 adoption/rehearsal; P6 separately approved protected cutover. Two independent read-only reviews are incorporated. No package implementation is authorized by this planning turn; #154 remains draft.
