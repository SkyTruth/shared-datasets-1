> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

## Summary

The periodic scratch cleaner could delete a fresh pending proposal when just one staged data file matched an older release. For example, unchanged data plus an unpublished corrected README was treated as already published, and both staged files became deletion candidates.

Remove content matching as deletion authority, along with the catalog/historical-release scans and obsolete `--catalog` option. Keep the existing age-and-warning policy, validate warning records and object generations, and distinguish proposed deletions from successful deletions in reports.

## Behavior and tradeoffs

- Warn once the newest staged object is at least 60 days old; delete at 90 days only when an earlier valid warning still matches that observed state.
- A first warning after day 90 can become eligible on the next audit; this does not introduce a new 30-day grace period.
- Matching published bytes no longer permits early deletion. Published leftovers may therefore remain until producer cleanup or normal age-based cleanup.
- Open PRs are not protected by this policy. Exact-generation checks protect individual objects; deleting a proposal is not atomic across its files.
- No repository caller uses the removed `--catalog` option. External scripts that pass it must remove it.

Explicit publication receipts would provide stronger completion-based cleanup, but require separate producer/recovery work. This fix removes the unsafe inference independently.

## Validation

- [x] Original fresh mixed-proposal and old-unwarned regressions failed on the baseline and pass with this fix.
- [x] Independent focused suites: **62 tests and 136 subtests passed** (`test_scratch_cleanup.py`, `test_catalog_drift_guard.py`, `test_publish_workflow.py`).
- [x] Ruff, `git diff --check`, CLI help, and catalog-doc checks passed.
- [x] Generation conflicts, malformed warnings, replacement races, dry runs, and age boundaries are covered.
- Live bucket compliance/cleanup was not run: this is a code-only policy fix tested with a generation-enforcing fake client. No GCS objects were inspected or changed.

## Dataset admission and bucket hygiene

Not applicable: no dataset, ingestion pipeline, catalog metadata, bucket layout, IAM, or canonical object changes. No remote object paths changed.

## Review

The PR is self-authored by `jonaraphael`, the repository's sole CODEOWNER; GitHub does not permit requesting a self-review.
