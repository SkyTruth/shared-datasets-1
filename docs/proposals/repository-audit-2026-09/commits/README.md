> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Remediation commits — 2026-09-23

> Latest PR checkpoint: PR #154 CI correction `362c260` is committed/pushed; GitHub CI passed, including all 94 native geospatial tests. The PR is open/draft/mergeable into main `da2f61b`, with migration and publication-ownership holds. PR #153 was merged externally as `da2f61b`; its operational prerequisites were not verified here. #148–#153 are merged externally. Publication recovery still awaits the user's PR decision. No merge or deployment was performed by this agent; local main remains untouched.

The user requested commits after the initial source-review handoff. All reviewed source changes are now committed locally. No source bytes changed during this operation. Nothing was pushed, merged, deployed or written remotely. Previous handoffs accurately describe their precommit review state; this record supersedes their unstaged/uncommitted status.

All nine work-package branches and the combined review-only snapshot are clean. Main is unchanged at `1bf095d861d921e2378203495bd9a0da0bdf650c`.

| Branch | Commit | Review status |
|---|---|---|
| `codex/audit-scratch-cleanup` | `cc2a7977c1bdd9a97e72a7668349c749afb29c54` | Source accepted; documented rollout/compatibility limits still apply |
| `codex/audit-python-release-cache` | `ef8f490d35b18b48115816169dc64f0fa29d047e` | Source accepted; documented rollout/compatibility limits still apply |
| `codex/audit-historical-consumers` | `d44d8c7b21415349c699d7f85c057de771b55465` | Source accepted; documented rollout/compatibility limits still apply |
| `codex/audit-publish-approval` | `ff1b72be849d06aa84269016c80454db858ee29c` | Source accepted; documented rollout/compatibility limits still apply |
| `codex/audit-publish-preflight` | `d5173df0c680fee43734b32a9c53e2b251627a64` | Source accepted; documented rollout/compatibility limits still apply |
| `codex/audit-feature-id-highwater` | `169ee75a28c4e9acf70b0e9fd215fd2306eef7df` | HOLD merge/deploy: migration and ownership |
| `codex/audit-publication-recovery` | `9bb3e4b803177a4569be45af67c9d1956ced0494` | HOLD merge/deploy: adapters and adoption incomplete |
| `codex/audit-publication-rollout-gate` | `f63c26f4970d0084f19e1ba584810218a464014e` | Merging deliberately blocks all 3 deployment workflows |
| `codex/audit-translation-integrity` | `f51818fc98ef6d87c627c8e5a2c2781b153ec0be` | Source accepted; documented rollout/compatibility limits still apply |
| `codex/audit-integration-review` | `9222fe667e8f58a3b0207c3f42357a43953a33af` | Review-only snapshot; do not merge |

The recovery branch starts with the exact preflight commit `d5173df0c680fee43734b32a9c53e2b251627a64`, followed by the eight-file core commit `9bb3e4b803177a4569be45af67c9d1956ced0494`. Preflight is therefore shared ancestry, not a second divergent implementation. Each other branch has one commit on the reviewed baseline. Integration is a frozen composition for test evidence, not a proposed combined merge.

Verification: empty indexes before staging; exact changed/untracked file sets and all 218 reviewed file occurrences matched frozen SHA-256 values. Staged blobs and diffs were checked before each commit, committed blobs and messages were checked afterward, and `git show --no-patch --format=full HEAD` output was retained for each commit. No tests were repeated because committed content is identical to the tested snapshots. Existing final integration result remains 1007 passed/827 subtests, four native skips, one documented unrelated deselection; lint and diff checks passed.

No repo-alert blocks: these are repairs, safety holds, or unintegrated recovery scaffolding rather than a released new capability. No operational notifications were sent. See alert-decisions.md for the package decisions, results.json for exact files/SHAs, and the staged patches/commit outputs in this directory.

Planning, review notes and evidence remain in the external remediation directory; generated evidence is intentionally not added to product branches. No source edits or staged files remain in the worktrees.
