> Preserved planning/review record. See the [archive index](README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Supervised remediation — review dossier

> Latest PR checkpoint: PR #154 CI correction `362c260` is committed/pushed; GitHub CI passed, including all 94 native geospatial tests. The PR is open/draft/mergeable into main `da2f61b`, with migration and publication-ownership holds. PR #153 was merged externally as `da2f61b`; its operational prerequisites were not verified here. #148–#153 are merged externally. Publication recovery still awaits the user's PR decision. No merge or deployment was performed by this agent; local main remains untouched.

This is the review record for the highest-priority findings in the exhaustive repository audit. Supervision for this bounded pass is complete; EXECUTION-BOARD.md records decisions and evidence. Source acceptance is separate from permission to merge or readiness to deploy. Every branch is based on main commit `1bf095d861d921e2378203495bd9a0da0bdf650c`. Following user authorization on 2026-09-23, reviewed source is committed in its worktree; all are clean. [Commit SHAs and history](commits/README.md). No push, PR, merge, deployment, settings change or remote-object mutation has been performed.

## How assignments are controlled

The exact prompt contract and acceptance requirements are in CHARTER.md. Each implementation began only after its written plan was read and explicitly approved. Plans identify the invariant, trust boundaries, actual producer/consumer call sites, compatibility, existing-data strategy, baseline-failing regressions, removals and completion checks. Scope changes that add persisted contracts or ownership return to plan review. The supervisor independently reads the final diff, verifies final file hashes, runs targeted checks and records revisions and acceptance in reviews/. Test counts alone do not confer acceptance.

## Changes and consequences

| Package / branch suffix | Concrete user benefit | Acceptance limits |
|---|---|---|
| A — audit-scratch-cleanup | An unchanged file can no longer cause an entire fresh pending proposal to be classified as already published and deleted. Removes the misleading historical-content heuristic. | Existing age-based abandonment remains; no active-PR/prefix-atomic cleanup guarantee. |
| C — audit-python-release-cache | Fetching latest follows the exact indexed release/generation and verifies cache bytes, so static catalog dates cannot hide updates. | Catalog-only alias resolution remains intentionally unpinned; no offline-cache mode. |
| D — audit-historical-consumers | Browser maps, feature inspection, downloads and TypeScript layers use one selected release with exact object generations. | Coordinated service/browser rollout and restricted TypeScript caller migration; legacy missing-generation indexes need reviewed repair. |
| E — audit-publish-approval | Mutation bytes come from a checked-in digest-named plan at the reviewed commit, passed through an immutable artifact envelope and revalidated. | Does not change live review/environment settings, historical workflows or publication crash recovery. |
| F1 — audit-publish-preflight | Local bytes are frozen/verified before mutation, finalizer writes protect the originally read generation, missing required native validators refuse. | Still per-object publication; standalone immediate improvement, not durable transaction recovery. |
| B — audit-feature-id-highwater | New generated IDs advance a persisted sequence through deletions and empty releases; legacy history is not guessed from current rows. | HOLD merge/deploy pending reviewed historical seed evidence, coordinated publication ownership and rollout. No live migration performed. |
| F2 — audit-publication-recovery | Durable exact intents/receipts, ownership, allocation reservation, fixed CAS recovery and independent notification journal. | Core/layout only; existing writer adapters are not migrated. HOLD until adapters/adoption accepted. |
| F3a — audit-publication-rollout-gate | New shared changes cannot automatically deploy before publication adoption is ready. | Merging blocks all three ingestion deployment workflows. Existing running jobs/schedules remain active. No permitting override exists. |
| G — audit-translation-integrity | Candidate writes, exact workbook identity and retryable failure rows protect canonical metadata and human translations. | Supervisor source accepted after revisions. Per-file atomicity only; workbook v1 needs documented re-export, existing translation CSVs remain usable. |

All branch names are prefixed `codex/`; matching worktree directories and exact status appear in EXECUTION-BOARD.md. F1 has a standalone worktree so its immediate fixes need not carry the transaction framework. Accepted changes are also committed as a review-only snapshot on `codex/audit-integration-review` for cross-package validation; source branches remain independently reviewable.

## Material holds and merge order

1. Review small immediate fixes A, C and F1 independently. D and G require their documented compatibility/packaging changes; no automatic deployment is approved here.
2. E changes how mutation proposals are represented. Existing body-only pending plans must be regenerated into the checked-in document contract and reviewed; do not treat old approval as approval of new bytes. Repository/environment review enforcement must be reconciled with the explicit self-authored exception.
3. Do not merge B or the F2 runtime/core as independently deployment-ready. Shared ingestion/model changes trigger automatic deployments. F3a must precede or accompany that rollout, with the deliberate deployment-hold consequence understood.
4. Full publication recovery requires approved writer adapters, concrete adoption authority/evidence, migration validation and old-writer quiescence. Direct multi-object latest aliases remain non-atomic. F3b/c implementation was explicitly NOT APPROVED: protected readiness must demonstrate old-writer exclusion throughout adoption before a seed step can become callable. Its five bounded follow-on packages remain plan-only.

This is not a promise that every finding in the original exhaustive audit has been implemented. The audit retains the remaining product, performance, workflow and lower-priority opportunities; the charter explicitly limits this remediation pass to the major integrity boundaries.

## Evidence

Every accepted package has an agent handoff and a separate supervisor decision under reviews/. Final combined validation: **1007 Python tests and 827 subtests passed**, with four native integration skips and one explicitly deselected location-sensitive Terraform fixture that passes on main. Ruff and diff --check passed. D separately passed TypeScript compilation and 31 SDK tests; actual browser QA exercised latest/historical/internal/public maps and private/expired failures using tiny native PMTiles fixtures. The board contains detailed baseline and package totals. Evidence contains baseline-failing reproductions, exact input/patch hashes, test logs, synthetic native artifacts and real-browser fixture records. Native ingestion integration requiring GDAL and live GCS/IAP/CDN behavior is not represented as tested. No translation provider calls or operational notification sends were made.

Retained working directory: `${REMEDIATION_WORKDIR}/`. Original audit: `${INITIAL_AUDIT_WORKDIR}/`. Retain these worktrees and evidence while the branches are under review.

## Review map

The following branches now have local reviewable commits; their SHAs are in commits/README.md. Source acceptance means they satisfied this local review contract; the compatibility and rollout limits above still apply. Full paths and hashes are in evidence/final-worktree-snapshot.json. The publication-recovery branch contains the exact standalone F1 commit plus a separate F2 commit, so preflight can be reviewed independently.

| Full branch | Plan | Supervisor decision |
|---|---|---|
| `codex/audit-scratch-cleanup` | [Plan](plans/scratch-cleanup.md) | [Decision](reviews/scratch-cleanup-supervisor.md) |
| `codex/audit-python-release-cache` | [Plan](plans/python-release-cache.md) | [Decision](reviews/python-release-cache-supervisor.md) |
| `codex/audit-historical-consumers` | [Plan](plans/historical-consumers.md) | [Decision](reviews/historical-consumers-supervisor.md) |
| `codex/audit-publish-approval` | [Plan](plans/publish-approval.md) | [Decision](reviews/publish-approval-supervisor.md) |
| `codex/audit-publish-preflight` | [Plan](plans/publication-recovery.md) | [Decision](reviews/publication-f1-supervisor.md) |
| `codex/audit-feature-id-highwater` | [Plan](plans/feature-id-highwater.md) | [Decision](reviews/feature-id-highwater-supervisor.md) |
| `codex/audit-publication-recovery` | [Plan](plans/publication-recovery.md) | [Decision](reviews/publication-recovery-f2-supervisor.md) |
| `codex/audit-publication-rollout-gate` | [Plan](plans/publication-rollout-gate.md) | [Decision](reviews/publication-rollout-gate-supervisor.md) |
| `codex/audit-translation-integrity` | [Plan](plans/translation-integrity.md) | [Decision](reviews/translation-integrity-supervisor.md) |

## Simplification and limits

Invariants are established at the input parser, release selection, output writer and authorization boundary. Removed behavior includes content-match cleanup authority, mutable-body execution authority, positional translation joining, duplicate translation CSV parsing and unverified cache reuse. Internal consumers can rely on those stronger boundaries. Retained fallbacks are explicit compatibility behavior: legacy latest-only assets, canonical values for unresolved translations and unmanaged publication paths. Silent historical high-water guesses, arbitrary unpinned historical fallback and takeover of incomplete publication claims are rejected. The new recovery state/receipt model adds material complexity and remains held until real writer integration can justify and validate it; passing isolated state-machine tests is insufficient.
