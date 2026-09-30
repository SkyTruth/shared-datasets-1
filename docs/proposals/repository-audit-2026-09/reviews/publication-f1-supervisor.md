> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# Supervisor review — publication F1

Status: source accepted; F2/F3 remain unapproved and unresolved. User review is required before merge.

Standalone review branch: `codex/audit-publish-preflight` in `worktrees/publish-preflight` (exact frozen F1 patch copied from `codex/audit-publication-recovery`), based on `1bf095d861d921e2378203495bd9a0da0bdf650c`.

I read all eight F1 file diffs (source, tests and documentation). The changes bind every local planned input to a verified private copy before validation or upload; retain the originally read JSON generation as the finalizer replacement precondition; take the resulting generation from the write response; and reject missing mandatory native validators. Tests exercise same-size byte drift, mutations during capture and upload, cleanup on failure, validators/notifier using frozen paths, read/write races and native validator failure.

Independent validation: `uv run --no-sync python -m pytest -q tests/test_publish_release.py tests/test_finalize_promoted_release_metadata.py tests/test_vector_asset.py tests/test_repo_guardrails.py` — **68 passed, 84 subtests passed**. Run from the assigned worktree using the existing shared runtime. No live remote write or native binary execution was performed.

Limits: invocation-private copies are not durable checkpoints; partially published bundles still need recovery. Finalizer artifact stats are not yet bound to a transaction receipt. Per-object CAS does not make a multi-object release atomic. F2/F3 own those remaining problems. No production-readiness claim follows from F1. Keep a separate F1 patch snapshot before any protocol edits.

Invariant enforced: uploaded original inputs equal planned bytes, and a JSON rewrite cannot adopt a later generation as permission to replace it. Boundary changed: capture and finalizer read/write. Code removed: finalizer pre-write reload and post-write unconstrained reload; silent native-validation omission. Fallbacks added: none. Existing post-publication warning paths remain for the separately reviewed recovery phase.
