> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# G bounded independent crossreview

Read-only review of `worktrees/translation-integrity` against `plans/translation-integrity.md`, focused on workbook v2 hash/shard/task closure, human-row preservation, the shared failure classifier, and CLI completion semantics. No source edits in G, D, or B. No provider/network/install operations. The root-known initial destination snapshot and duplicate-report issues were deliberately not duplicated.

## Open finding

**P2 — reject numeric aliases for workbook row/cell coordinates.**

`scripts/feature_metadata_document_translate.py:144-155` stores raw `row_ref` strings in `seen_rows`, while validity uses `int(row_ref)`. An XLSX with row references `1`, `2`, and `02`, and cells `A2/B2` plus `A02/B02`, therefore passes the duplicate-coordinate check even though both data rows name logical row2. The import accepts both tasks and reports `complete: true`, `generated_row_count: 2`. This bypasses the approved requirement that ambiguous/duplicate worksheet coordinates fail before output changes. Hash-to-value pairing remains intact in this synthetic case; the finding is acceptance of ambiguous workbook structure, not demonstrated cross-feature reassignment.

Evidence: `evidence/translation-crossreview/coordinate_case.py`, `duplicate-coordinates/results.json`, and the mutated workbook under `duplicate-coordinates/workbooks/`. The script produces two canonical values, exports normal v2 evidence, then changes the second data row from `3/A3/B3` to `02/A02/B02` without changing its hashes or translated values. Actual importer writes two rows successfully.

Smallest fix: require canonical positive ASCII decimal row references (`[1-9][0-9]*`), or normalize row identity to an integer before duplicate detection and validate matching cell references consistently. Add a `2` versus `02` regression asserting refusal and unchanged/absent destination CSV. Reuse the existing two-column reader boundary; no workbook framework is needed.

## Finding fixed during review

The initial machine CLI omitted `csv.Error` from validation handling. An existing CSV field of131073 bytes triggered `_csv.Error: field larger than field limit (131072)` and exited1 with no report/commit, conflicting with the new convention that exit1 means a valid written partial result. Actual external subprocess evidence is preserved in `results-before-cli-fix.json` and `csv-cli-stderr-before-fix.txt`.

G added the catch while this review was running. Independent rerun of the same command now exits2, emits the descriptive validation error, preserves the exact input CSV bytes and writes no success/partial report. `results.json` and `csv-cli-stderr.txt` verify the correction. No additional source change requested for this resolved case.

## Positive observations and checks

- Manifest parsing rejects duplicate JSON keys and unsupported v1. Typed pending flags, exact eligible task keys, pending hash/entry closure, unique shard membership, deterministic shard IDs and counts are checked before output.
- Import recomputes tasks from the pinned canonical/schema hashes and options, verifies source text, and rejects excluded tasks that are no longer completed. Manifest hints/argument order do not determine row assignment.
- Default import preserves a human row completed after export; independent fixture returned generated_row_count0 and byte-identical CSV despite different workbook text. Existing test coverage also exercises reordered rows/shards, malformed/foreign/missing/duplicate members, formerly completed exclusions, and idempotence.
- The legacy classifier requires the old exact producer note, source_provided state, current canonical hash, and exact current source text. Independent tests confirmed untouched fallback recognition and preservation after value/provenance edits. Explicit failure rows stay outside completed keys and localized application.
- CLI success/partial/fail semantics otherwise follow the approved scoped completion contract; report-write failure honestly warns that earlier per-file commits may remain. No claim of batch atomicity or distributed freshness.
- Focused current source suites: **84 passed** in2.68s:

```sh
UV_PROJECT_ENVIRONMENT=${REPO_ROOT}/.venv \
UV_CACHE_DIR=${REPO_ROOT}/.uv-cache \
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python -m pytest -q \
  tests/test_feature_metadata_document_translate.py \
  tests/test_feature_metadata_machine_translate.py \
  tests/test_feature_metadata_localization.py -p no:cacheprovider
```

External evidence scripts run from the G worktree with the same environment and `uv run --no-sync python <absolute-script-path>`. Source hashes observed near review completion are in `evidence/translation-crossreview/reviewed-source-hashes.json`; G remains active, so these identify a reviewed state rather than freezing its patch. The worksheet finding must be checked against G's eventual frozen handoff.

The approved review scope did not include provider quality, remote race handling, or full translation pipeline scalability. D's disposable browser server session35829 was stopped at the supervisor's request; D/B source remains frozen.
