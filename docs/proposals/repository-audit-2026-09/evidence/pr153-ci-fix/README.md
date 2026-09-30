> Preserved planning/review record. See the [archive index](../../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# PR #153 CI correction

Observed PR head: `1ef87d293f75370b04b13cf399f275414d9b898a` (remote main merged into original reviewed E commit). No remote changes are to be overwritten.

Failures:

- CI run `35945239711`: Gitleaks 8.24.3 stopped the tests job before tests ran. Three findings are the same deterministic `proposal_key` digest in `tests/fixtures/dataset-mutation-authorization-v1.json`, introduced by `ff1b72be`. `identity_digests` recomputes it from public synthetic repository/PR/revision/plan fields; it is not a credential.
- Alert run `35945241559`: workflow checks out the trusted base commit, then unconditionally calls the new `dataset_mutation_authorization.py preview` script which is absent from that base. The introducing code PR has no dataset mutation plan.

Secret scan correction: inherit default rules and add one rule-scoped allowlist requiring BOTH exact fixture path AND exact known synthetic digest. No broad file/rule exclusion or fixture/history rewrite. Same revision scan failed before and passed after. Gitleaks 8.24.3 (downloaded pinned release, checksum verified) also passed; positive controls still flag a different digest in that path and the same digest elsewhere. Existing fixture replay-contract test passed.

Workflow correction plan approved for subagent: read-only intent-routing job without checkout or credentials beyond GitHub read access; complete fresh exact-head PR metadata/files enumeration; route JSON mutation documents (including previous paths) and plan fence markers to the existing privileged trusted-base validator. Ordinary code PRs skip that job. Incomplete/error/stale input fails, and potential mutation never becomes execution authority. No missing-script or legacy-body execution fallback.

Git synchronization: automatic review initially rejected `git merge --ff-only origin/codex/audit-publish-approval`, citing AGENTS.md's explicit Git-operation authorization rule. The user explicitly approved the local fast-forward, which completed to `1ef87d2` while preserving the focused fix. No alternate index/history operation was used to bypass the rejection.

Previously unreached static guardrails then exposed three failures: the main-ref/trusted-checkout marker checks do not recognize E's captured executor contract. Correction is being planned independently; no arbitrary checkout expression or loss of main-ref dispatch protection is acceptable. Root independently passed 139 authorization/parser/publisher/concierge/localization tests and 72 subtests before this guardrail repair.

This correction does not approve merging/deploying E or remove its rollout prerequisites. No GCS objects or repository settings changed.

## Completion

Committed and pushed `1d276ba7f14b2636c099b1d97fc28ee8cfeaa7cf` (eight files), retaining the remote merge update. Worktree clean. No repo-alert block: routine CI repair, not a new product capability.

Final root verification: 919 tests and 669 subtests passed, four unavailable-native-tool skips, one unchanged location-sensitive Terraform fixture deselected. Ruff, check-static, check-diff, admission and committed diff checks passed. Pinned Gitleaks 8.24.3 passed the complete PR history including the correction. Existing fixture tests recompute the allowed synthetic digest; scanner controls detect other values/paths.

GitHub CI run 35947077420 passed tests, lint and geospatial-change detection. Catalog drift/local hygiene passed. Alert route passed both on push and after the PR description update (run 35947141009); privileged alert appropriately skipped for this code-only PR. Geospatial integration was skipped by its unchanged path filter. Verified all latest checks are success or intentional skip on the exact pushed head; no PR merge performed.

Invariant enforced: only possible mutation proposals enter privileged preview; main-workflow bootstrap remains pinned and guarded; secret scanning retains all default rules with one exact synthetic-data exception. Boundary changed: read-only intent routing and parsed recognition of guarded bootstrap checkout. Removed: unconditional preview for ordinary code PRs and the superseded manual-only localization ref guard. No execution fallback or exception-swallowing was added. Legacy literal-main and preview guardrail handling remains unchanged. Remaining uncertainty: the PR's previously documented live rollout prerequisites still apply.
