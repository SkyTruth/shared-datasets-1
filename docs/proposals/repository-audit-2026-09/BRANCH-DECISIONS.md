> Preserved planning/review record. See the [archive index](README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Branch walkthrough decisions

User requested plain-language discussion of each branch before deciding whether to create a PR. Discuss one package at a time: problem, motivation, proposed solution, pros, cons, strongest alternative and recommendation. User authorized scratch cleanup, Python release/cache, publication-preflight and translation-integrity PRs as written, plus draft historical-consumers, publication-authorization and feature-ID high-water PRs, on 2026-09-23. No other package has PR approval. A decision to create a PR does not authorize merging or deploying it.

| Order | Branch | User decision |
|---|---|---|
| 1 | codex/audit-scratch-cleanup | Approved as written; PR #148 merged externally as 234ffbc7 |
| 2 | codex/audit-python-release-cache | Approved as written; PR #149 merged externally as 676cc748 |
| 3 | codex/audit-publish-preflight | Approved as written; PR #150 merged externally as e9343589 |
| 4 | codex/audit-historical-consumers | Draft PR approved; #151 subsequently marked ready and merged externally as e834f13f; deployment prerequisites not verified here |
| 5 | codex/audit-translation-integrity | Approved as written; PR #152 merged externally as 5f525173 |
| 6 | codex/audit-publish-approval | #153 CI repair pushed as 1d276ba; checks passed; merged externally as da2f61b; operational prerequisites not verified |
| 7 | codex/audit-feature-id-highwater | Draft PR approved; #154 CI repair 362c260 passes all checks and 94 native tests; migration/ownership hold |
| 8 | codex/audit-publication-recovery | Walkthrough presented next; awaiting decision; adapters/adoption hold |
| 9 | codex/audit-publication-rollout-gate | Not yet presented; deliberate deployment-stop consequence |
| 10 | codex/audit-integration-review | Explain review-only role; not a merge proposal |

Scratch cleanup: https://github.com/SkyTruth/shared-datasets-1/pull/148 (head cc2a7977, 5 files, 1 commit). GitHub PR metadata verified open, non-draft, mergeable, base main. Attached to this task. No merge or deployment performed.

Python release/cache: https://github.com/SkyTruth/shared-datasets-1/pull/149 (head 856cc80f, 5 files, 2 commits). Follow-up commit only clarifies README empty-index provenance; no SDK behavior change. Open/mergeable/non-draft into main verified; attached to task. No merge performed by this agent.

Publication preflight: https://github.com/SkyTruth/shared-datasets-1/pull/150 (head d5173df0, 8 files, 1 commit). Verified open/mergeable/non-draft into current main 676cc748, attached to task. No merge or deployment performed by this agent. Earlier open-state records for #148/#149 are historical; both were subsequently merged externally.

Historical consumers: https://github.com/SkyTruth/shared-datasets-1/pull/151 (head d44d8c7b, 21 files, 1 commit). Open/draft/mergeable into e9343589 verified, attached to task. Kept draft for restricted TypeScript migration and coordinated browser/service rollout, missing generation evidence, and live access/retention validation. No merge/deployment performed by this agent.

Translation integrity: https://github.com/SkyTruth/shared-datasets-1/pull/152 (head f51818fc, 14 files, 1 commit). Verified open/non-draft/mergeable into e834f13f, attached to task. No merge/deployment performed by this agent. Authorization walkthrough uses a fresh read-only ruleset/environment check; existing zero-review/no-environment-reviewer configuration unchanged.

Publication authorization: https://github.com/SkyTruth/shared-datasets-1/pull/153 (head ff1b72be, 21 files, 1 commit). Verified open/draft/unmerged into 5f525173, no merge conflicts but behind current main; attached to task. Independent pre-PR check confirmed clean branch and all 21 accepted file hashes. Kept draft for protection-policy reconciliation, pending-plan migration, historical-workflow controls, and live handoff/retention validation. No merge, deployment, settings mutation, or GCS write performed.

Feature-ID allocation: https://github.com/SkyTruth/shared-datasets-1/pull/154 (head 169ee75a, 19 files, 1 commit). Verified open/draft/unmerged into 5f525173, no merge conflicts but behind current main; attached to task. Independent pre-PR check confirmed clean branch and all 19 accepted file hashes. Draft body explicitly prohibits merge until per-asset historical migration and publication ownership are satisfied. No migration, deployment, settings mutation, GCS write, or merge performed.

PR #153 CI repair: user explicitly approved local fast-forward to existing remote merge 1ef87d2. Commit 1d276ba repairs scoped secret-scanner false positive, read-only mutation-intent routing, and guarded immutable-main bootstrap recognition. Root full suite 919 passed/669 subtests, four native skips and one known temporary-path deselection; lint/guardrails/admission/pinned scanner passed. GitHub tests/lint/routing/catalog/hygiene pass on exact pushed head, intentional alert/geospatial skips. Worktree clean; no merge. Details: evidence/pr153-ci-fix/README.md, commits/publish-approval-ci-fix.json, prs/publish-approval-ci-fix.json.

PR #154 CI repair: commit `362c2604944ab188fb27506b58b881f8059717f6` changes only the WDPA native test fixture to supply explicit genesis and assert IDs/counters. Pushed to the existing branch; all active checks passed on that head, including 94 native integration tests. Verified open/draft/mergeable into main `da2f61b`; no production migration or ownership integration occurred. Details: evidence/pr154-ci-fix/README.md and commits/feature-id-ci-fix.json. The current merge-blocker planning task does not authorize implementation or production operations.
