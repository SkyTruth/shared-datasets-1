> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

## Summary

Translation tools could overwrite canonical input or a previously good output before failing, assign returned spreadsheet text by row position despite reordered hashes, and mark failed provider work as completed source-value fallback so reruns never retried it.

Use validated sibling candidates and per-file replacement with protected input/output/report paths and original fingerprints. Share strict CSV parsing and completion classification across machine, document and localization tools. Workbook v2 manifests bind canonical/schema snapshots, task identities, options and exact hash/shard membership; import permits reordering but rejects ambiguous or damaged identities and preserves newly completed human work.

Provider failures become explicit empty `translation_failed` rows, remain retryable, and retain canonical values during materialization without claiming completed translation. Untouched legacy Google fallback rows are recognized only through exact producer notes plus matching canonical value/hash. WDPA packaging and CI/import checks include the new shared I/O helper.

## Compatibility and operating changes

- V1 document manifests require re-export. Keep returned workbooks and transfer text only by verified intact hashes; never use row position to reconstruct identity.
- Existing completed CSV translations remain valid. New failed rows require the updated helpers together; older readers reject their empty values.
- Machine CLI exit codes: **0** complete; **1** valid partial CSV committed after provider failures; **2** validation or fail-policy errors. Rerun partial work normally to preserve successful/human rows; `--refresh-current` explicitly replaces successful current work too.
- Default document import preserves an existing output CSV when no explicit source CSV is supplied.
- Localization reports file validity separately from completion of supplied translation rows; this is not whole-asset language coverage.
- Atomicity is per file, not across a batch. A later failure can leave earlier validated files committed. Fingerprints detect observed edits but do not lock arbitrary editors or establish remote-generation freshness.
- Current workbook/task memory use still grows with total task count; sharding is not a bound on total memory.

## Validation

- [x] Four independent original regressions fail on exact-base source and pass on this branch: input alias, stale-output preservation, reordered hashes and failed-provider retry.
- [x] Supervisor: **123 affected tests/2 subtests plus the four regression checks passed**.
- [x] Broader focused suite: **215 passed, 45 subtests, 1 native skip**. Full branch suite: **838 passed, 429 subtests, 4 native skips, 1 documented location-sensitive Terraform fixture deselected**; that unchanged fixture passes on main outside the shared-temp worktree.
- [x] Actual local CLI subprocess checks produce one JSON report and correct output mapping; provider behavior is faked, with no external translation calls.
- [x] Tests cover aliasing, concurrent edits, interruptions, malformed/ambiguous XLSX coordinates, strict task/shard closure, human preservation and partial/failure exits.
- [x] Isolated declared-Docker-COPY imports and CI dependency detection pass. Ruff and diff checks pass.
- Native image builds and full GDAL ingestion integration were not run. Remote publication concurrency and locale retirement remain separate work.

## Dataset admission and bucket hygiene

Not applicable: no new dataset/pipeline or remote translation publication. No provider, GCS, IAM or Terraform operations occurred; no remote object paths changed. Local source format/CLI compatibility is documented above.

## Review

Self-authored by `jonaraphael`, the sole CODEOWNER; GitHub does not permit requesting self-review.
