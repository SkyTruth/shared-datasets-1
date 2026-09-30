> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Supervisor review — E immutable publication approval

Source accepted after revisions for user review. Branch `codex/audit-publish-approval` remains uncommitted and unstaged. This is not approval to deploy or merge.

I read all final source, workflow, test, fixture and documentation changes. Independent final validation ran eight affected suites from the E worktree: **174 passed, 140 subtests passed**. Diff checks passed. I also applied the frozen E patch plus four new files to the separate combined review checkout containing A/B/C/F1. The full combined Python suite passed **860 tests and 704 subtests**, with four native-tool skips and the previously documented unchanged /private/tmp-sensitive Terraform fixture deselected. Original branches and main were untouched.

I required corrections for real closed-PR run metadata (source head rather than assumed merge/main), typed no-mutation handoffs, dot proposal identifiers, escaping producer symlinks before directory creation, and concierge evidence integrity after rendering. Regression tests cover the actual corrected behaviors. The handoff retains four baseline-failing authorization/parser regressions and read-only GitHub event evidence.

The accepted boundary is a canonical checked-in plan added at the exact reviewed head and identical merge revision. Effective acceptance must match that head, or satisfy the existing self-authored merged-PR exception. A captured artifact binds plan bytes, executor SHA and source run; downstream mutation and automatic localization revalidate that artifact and current acceptance. Mutable PR prose cannot alter an accepted transaction. Ordinary merged code PRs have an explicit no-mutation envelope; missing evidence is an error.

Removed unsafe historical-approval, body-extraction, moving-main executor, heuristic PR lookup and catalog fallback paths. Preserved local fence parsing only for preparation/rendering and display comparison. Changed source/destination expectations require a fresh reviewed proposal. Missing or expired authorization artifacts have no unsafe fallback.

Operational limits remain material: live rulesets/environment reviewers have not changed; historical workflow reruns still need rollout control; artifact retention is finite; GitHub revocation and GCS writes are not atomic. F owns mutation receipts, exact replay, finalization, all-writer adoption and localization output concurrency. E/F combined execution validation remains required. No live workflow, canonical write, dependency install or settings mutation was performed.

Frozen tracked patch: `reviews/publish-approval.patch`; patch and new-file hashes: `evidence/publish-approval-final-inputs.json`. No source revisions remain requested for E at this gate.
