> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Supervisor acceptance: A — scratch cleanup

Accepted for user review, not merged or deployed.

I reviewed all five changed files, including the source and tests, and read the retained baseline failures. The fresh and old-unwarned examples failed with `matching-release` deletion before the change. No content-matching helper, canonical listing dependency, or matching-completion input survives.

I independently ran the focused cleanup, publish-workflow, and catalog-drift suites from this worktree: **62 passed, 136 subtests passed**. `git diff --check` passed. Status showed exactly the five intended unstaged files on `codex/audit-scratch-cleanup`.

Reviewed adverse cases cover malformed/non-object/coerced warning data, invalid generations, read/write/delete races, actual-vs-candidate reporting, and the original partial/unchanged-data proposal failures. The retained exact-generation deletion is correctly narrower than a prefix transaction. Documentation states this without promising active-PR protection or a new grace period.

Scope accepted: remove unsafe content-based eligibility and obsolete scans/CLI input; keep the existing age-based abandonment contract. The source is smaller (net 66 lines removed). Existing valid warning format and JSON audit outputs remain compatible.

Residual policy: an old unchanged proposal can still age out despite an open PR; new objects arriving after listing do not make the prefix atomic. These are explicit existing-policy limits, not claimed fixed. This patch is independently useful and should precede any future receipt-based cleanup design.

No revisions were required after final handoff. No Git index/history or remote mutation was performed by this review. User review and any requested commit/merge are still outstanding.
