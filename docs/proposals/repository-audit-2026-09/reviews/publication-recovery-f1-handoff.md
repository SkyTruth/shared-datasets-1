> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# F1 handoff — publication input/finalizer validation

F1 only is implemented on `codex/audit-publication-recovery` in `worktrees/publication-recovery`, base/HEAD `1bf095d861d921e2378203495bd9a0da0bdf650c`. F2/F3 remain unimplemented and unapproved; their revised plan is `plans/publication-recovery.md`.

## Behavior and changed files

- `scripts/publish_release.py`: capture all artifacts/metadata into invocation-private copies; verify all planned sizes/digests before any mutation; rerun vector/COG and schema checks against those copies; use them for uploads, manifest template, schema update and notification sampling. Preserve original source paths in run records. Temporary copies are removed on normal or exceptional exit, under the standard work root.
- `scripts/finalize_promoted_release_metadata.py`: generation-pinned JSON reads return payload plus original object info; replacement CAS uses that original generation. Result metadata comes from the upload response, avoiding a later replacement being reported as this write.
- `scripts/vector_asset.py`: absent mandatory ogrinfo/PMTiles checks or unavailable representative decode is invalid. Existing lookup geometry exception is retained.
- Three matching test files exercise the defects and failure paths. `scripts/README.md` and `docs/gcp-asset-operations.md` document disk cost, cleanup/recovery limits, CAS scope, and native readiness requirements.

## Proof

Before source edits, newly added regressions produced **14 failures**: same-size changes to seven different inputs, originals replaced at first upload, finalizer pre-read/pre-write/post-write races, and three missing native-check cases. Evidence: `evidence/publication-recovery/f1-baseline-regressions.txt`. Independent earlier reproductions remain in `baseline.py` / `baseline-results.json` there.

Final command (from this worktree with `UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv UV_CACHE_DIR=${REPO_ROOT}/.uv-cache`):

```text
uv run --no-sync python -m pytest tests/test_publish_release.py tests/test_finalize_promoted_release_metadata.py tests/test_vector_asset.py tests/test_publish_workflow.py tests/test_ingestion_common.py tests/test_wdpa_monthly.py tests/test_sea_ice_daily.py tests/test_eamlis_monthly.py tests/test_gcs_asset.py tests/test_repo_guardrails.py -q
```

**185 passed, 3 skipped, 117 subtests passed.** Output: `evidence/publication-recovery/f1-final-tests.txt`. WDPA/sea-ice native integrations skipped on their runnable-tool checks; EAMLIS requires its integration opt-in and runnable GDAL. Executables resolve under `/usr/local/bin`, which does not establish runnable native integration in this environment. No native artifacts were built and no dependencies installed.

Ruff on all six changed Python files passed; `catalog_docs.py check` passed for 24 assets with existing source-description placeholder warnings; `git diff --check` passed. Imported module paths were verified to this worktree. Supervisor separately reports 68 tests/84 subtests passed for publish/finalizer/vector/guardrails and no F1 blocker.

## Immutable review snapshot

`reviews/publication-recovery-f1.patch` contains the complete eight-file unstaged diff, made read-only before any F2 source work (none authorized). Size 36,335 bytes; SHA-256:

```text
2deeb2050d2d8b4be056000d7d40201899afa79f7584851e19d35e0920e3e3c4
```

Review this patch, the failure evidence, and the final test output. Apply only in a separate review checkout if desired; do not stage/merge this branch as part of acceptance. Source working tree still contains exactly these eight unstaged files; index/history remain untouched.

## Invariant/removal and compatibility

Invariant enforced: uploaded bytes are verified frozen inputs; stale finalizer payloads cannot replace newer JSON; missing validation evidence cannot mean valid. Boundary changed: local plan-to-execution, JSON read-to-replacement, native validation readiness.

Removed: finalizer replacement-time and post-write reloads, original-file reads during upload/finalization, and native-tool-absent success. Existing COG boundary normalization was extracted and reused, without a new fallback. Internal handling removed: implicit adoption of a fresh generation for a stale payload. Fallbacks added: none. Rejected: checksum-only check followed by original reopen, fresh generation adoption, and success when tools are absent.

Preserved: public CLI flags, plan/result/persisted schemas, paths, per-object upload preconditions, original run-record source paths, and geometry exception. Internal `load_json_object`/`replace_json_object` contract changed and every caller was updated. Temporary disk needs the full input set and native checks now also run at execution. Deletion candidates retained pending F2/F3: independent multi-object loops, stat-based artifact facts, schema/index/notification side-effect ordering and partial-release refusal.

## Explicit remaining limits

F1 is **not** durable recovery, multi-object atomicity, stale/backfill protection, an allocation lock, or receipt-bound promotion. Private copies disappear on exit; partial canonical publication can still need reviewed repair. Finalizer artifact facts still come from current stats; this patch only closes the read/replacement lost-update window. B allocation rollout remains blocked on F2/F3 enforcement/adoption and quiesced legacy writers; automatic ingestion deploy triggers make a docs-only deployment warning insufficient.

No staging, commits, pushes, PRs, merges, installations, remote calls/writes, production dispatches, or settings changes were performed. Retained local evidence/patch/plan are under this named remediation directory. No unrelated prior temporary directories were removed.
