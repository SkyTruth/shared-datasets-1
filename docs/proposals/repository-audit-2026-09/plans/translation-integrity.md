> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# G — local translation integrity plan

Status: PLAN ONLY; awaiting explicit supervisor approval. Worktree: `worktrees/translation-integrity`, branch `codex/audit-translation-integrity`, base `1bf095d861d921e2378203495bd9a0da0bdf650c`. No tracked edits. Governing scope: [charter](../CHARTER.md), [G assignment](../prompts/translation-integrity.md), [board](../EXECUTION-BOARD.md). Read AGENTS and invariant-first-engineering, update-feature-metadata-translations, sync-docs-with-code, and local-temp-workspaces. E is frozen and accepted; this plan does not reopen it.

## Baseline and evidence

All three defects remain on the assigned base. Disposable fixtures, fake translators, and results are retained in reproduce.py (historical artifact: `../evidence/translation-integrity-baseline/reproduce.py`) and results.json (historical artifact: `../evidence/translation-integrity-baseline/results.json`). The recorded module path proves imports came from this worktree. No provider/network calls occurred.

| Defect | Executed result | Owner today |
|---|---|---|
| C4: canonical/output alias | One-record canonical became an empty gzip; returned `valid=true`, `feature_count=0`. | `feature_metadata_localization.materialize_locale_sidecar` writes the final path before its lazy canonical iterator reads. |
| C4: stale refusal after write | Existing final bytes changed before `fail_on_stale=True` raised. | Same function validates/stale-checks after final output replacement. |
| C6: intact reordered workbook | Beta's hash/text then Alpha's hash/text were accepted; feature 1 got `Beta ES`, feature 2 got `Alfa`; report valid, mismatch count 2. | `feature_metadata_document_translate.translated_values_for_locale` zips files/shards and rows/entries positionally. Extra rows are ignored. |
| C5: transient provider error | First run saved original `Alpha` as `source_provided` with failure notes; next working provider received zero calls and zero tasks. | Machine producer's error mode and both machine/document skip-key builders treat every current CSV key as completed. Localization applies the failure row. |

Existing local suites passed **22 tests / 12 subtests**: localization, document translate, machine translate, translation pipeline, agent translation skills. In particular the document test currently asserts acceptance of damaged/blank hash cells; that test must be replaced, not preserved as compatibility authority.

Inspected consumers include the pipeline's materialization/report path, publishing-concierge command rendering, both translation CLIs, release sidecar read/write/validation, and translation skill/standards. The source tree does not contain PR145's proposed translation product specification; its spec-only status and broader scope are recorded by the review/board. No implementation of its roadmap is inferred.

## Invariants and boundaries

1. Inputs are read-only. A failed per-file preparation/validation/replacement leaves that file's previous final bytes intact (or leaves it absent). A candidate cannot alias canonical metadata, CSV input, schema, another output, or a report. Only the intentional translation-CSV update may replace its own previously read CSV.
2. A localized candidate preserves canonical record count/order and asset/release/feature/geometry/properties identity. It changes only allowlisted current-hash successful translated property values. Validate actual canonical input before deriving; compare candidate against that source, not solely a counter updated by the output generator. A separately validated empty canonical source remains a valid empty source.
3. A workbook text value belongs to the hash in its own row. All manifest tasks and shard entries must have complete, unambiguous identity. No positional recovery, truncated zip, excess-row ignoring, or filename/argument-order authority.
4. Failed translation work cannot become a completed skip key or an applied translation. CSV file validity is distinct from task completion. Successful/human/source-provided work survives default retries and imports.
5. Input files, CSVs and XLSX/JSON documents are boundary data. Shared normalization/validation owns these decisions once; machine, document and localization callers consume the same decisions. Report output is also a write boundary.

## Smallest complete change

### A. Validated candidates and local file preservation

Add a small translation-local I/O helper used by the three scripts. It owns only protected-path/alias checks, sibling temporary candidate creation, cleanup on ordinary failure, destination snapshot checks, and atomic `os.replace` after caller validation. It is not a workflow engine and does not modify the shared release-feature-model writer.

- Before creating/writing candidates, resolve all declared input/output/report paths and reject equal resolved paths and existing same-inode aliases (`samefile`, including hardlinks). Reject writable symlink aliases. Check every batch output/report together before the first commit. Validate schema paths at the CLI boundary where they are available; propagate optional protected-input paths into the local materialization call for callers that own a schema.
- Materialization validates canonical source and obtains its independent count/identity; writes a sibling candidate using the existing gzip writer; validates the candidate and source/candidate identity; evaluates `fail_on_stale`; only then replaces the destination. Do not reject zero records merely because they are zero. Malformed/duplicate source records, malformed output, truncation, stale refusal and write/replace exceptions preserve existing final bytes.
- CSV writers use the same candidate/validate/replace mechanism. Machine translation may deliberately update its previously read CSV in place; this is an explicit allowed target, not an exemption for aliases to canonical/schema/workbooks/manifests/reports. The duplicate-key and row-state parser validates the complete candidate before replacement.
- Snapshot read inputs and any pre-existing writable CSV when work begins; compare before committing to catch source edits and human CSV edits during translation. Changed input/CSV refuses with instructions to rerun against current files, never refreshes the saved expectation. This is detection at the local commit boundary, not a claim of locking out arbitrary editors or remote actors. No advisory-lock protocol or distributed CAS is introduced.
- Reports and generated workbook/manifest outputs are also written through sibling candidates and checked for aliases to all known inputs/outputs. Preserve an existing report on its own write failure. Preflight all paths before output writes.
- Atomicity is **per file**, not per locale batch, not sidecar-plus-report, and not an XLSX export transaction. Preparation failure for one file never leaves its partially written final path. Earlier successfully committed files can remain if a later file/report replacement fails; CLI errors/reporting must say this explicitly. Prefer preparing candidates before committing where already practical; do not add rollback or claim multi-file atomicity. Files left by a process kill are private candidates, not final outputs; do not delete unrelated historical temp files.

A same-path string check alone misses symlink/hardlink aliases. Writing then validating leaves destruction behind. Merely requiring positive counts wrongly rejects legitimate empty releases. A shared replace helper is justified because gzip, CSV and report paths need the same preserve/replace semantics.

### B. Hash-bound workbook import with a complete manifest

Keep the small standard-library two-column XLSX format (`hash,text`) and existing locale reuse feature. Export a **v2 document manifest**, validated strictly at load, with:

- asset/release, canonical sidecar SHA-256 and optional schema SHA-256;
- normalized fields/locales/target mappings and collection options (`stringify_non_string`, `skip_numeric_strings`);
- the exact eligible task keys `(feature_id, field, locale, source_value_hash)` and which were pending at export; this records completed exclusions without retaining a second CSV snapshot;
- unique hash/source-text entries for precisely the pending tasks;
- each shard's deterministic ID from its sorted hash set, exact entry count, and exact hash membership. Existing row numbers/names may remain display hints but never identity authority.

The importer first validates manifest JSON shape/duplicate keys/types, unique task keys, task/entry/shard closure and counts. Recompute eligible tasks from the exact canonical snapshot/options; they must equal the manifest's eligible task keys. Every pending key must be among those tasks. A formerly completed excluded key must still have a completed current row, otherwise request a fresh export. Newly completed/human-edited rows within the exported pending set are preserved, not replaced by workbook results. Current canonical/schema changes or inconsistent explicit asset/release overrides refuse before CSV writes.

For each locale, validate every workbook and all rows before producing output: exact two-column header; exact row widths; well-formed nonblank hashes; nonblank translated values; no duplicate row hashes/cell coordinates, missing/extra/foreign hashes, or extra/missing workbooks. Hash membership identifies each shard regardless of file argument/name order, and hashes identify row values regardless of row order. Each expected shard is present exactly once. A file containing fragments from two shards fails even if the union across files happens to match. Empty task export has exactly one explicitly empty shard and header-only workbook, with no ambiguous inference.

Resolve locale reuse only after its source locale has passed all validation; reject duplicate normalized locales, duplicate/redundant files, cyclic/conflicting reuse and direct-plus-reused authority for one destination. Completed current CSV rows remain authoritative. Replace only failed current rows or intentionally requested refresh rows, with one result per key. Preserve stale/historical rows unless the existing explicit refresh policy requests replacement of that exact current key.

**Legacy v1** lacks complete task/source identity. Refuse v1 import with actionable instructions: keep translated originals, rerun export with current canonical/schema/CSV and the same approved locale/field choices, then populate the new workbook's `text` by intact hash (not row position) and import the new manifest. No automatic v1 upgrade, positional acceptance flag, or silent source-identity invention. Damaged hashes require restoration from an independently verifiable original or retranslation; do not guess correspondence. Existing completed CSV translations remain usable and need no migration.

The v1 manifest already records hash/shard entries; that is enough to diagnose row swaps but does not record exported per-task identity, completed-task exclusions, or collection options. Those missing facts justify v2. This version bump is preferable to a growing compatibility parser that cannot prove historical task identity. Hash matching alone fixes swapped rows but cannot detect a stale manifest/task set or incomplete shards; exact task/shard closure is required.

### C. One failure/completion decision across producers and consumers

Keep the existing CSV columns. Reserve `review_state=translation_failed` for a failed task with **empty `value`** and producer failure notes. Empty value is allowed only for this failure state; successful rows still require nonempty values. A failed row is a durable task result, not a translated value. New failures never use `source_provided` and never copy source text into the translation value. This is an additive state semantic but older readers will reject such rows; deploy the local helpers together and document the compatibility limit.

Localization owns normalized row validation and a shared failure classifier; machine/document callers reuse it instead of maintaining a second CSV parser and contradictory skip policy. Existing successful/unknown nonfailure review states retain prior nonempty-value behavior; do not rewrite historical provenance. The document producer's existing `document_translated` state is explicitly documented alongside machine/human/source-provided states.

Narrow legacy recognition requires **all** of: state exactly `source_provided`; notes exactly matching the old producer template `machine translation failed; source value retained; provider=google; target={target}` with a valid nonempty provider target; current source hash equals the row's stored source hash; and saved value equals the producer's source text for that canonical value. For canonical strings, additionally require hashing the saved fallback value to reproduce the recorded source hash. For non-string canonical values, the old optional stringification was `str(raw_value)`, whose text hash intentionally differs from the raw canonical JSON hash: verify the actual raw canonical hash and exact `str(raw_value)` together, and never enable non-string task collection unless the caller explicitly selected its existing stringification option. Tests cover both policies. Other source-provided rows remain successful. If someone edited the value or provenance, preserve it rather than infer it is still a failed machine result. Report recognized legacy failures; only normalize/replace them when that current key is actually processed. Stale legacy rows remain stale, not invented current failures.

- Default machine retries only incomplete/failed current keys, in addition to new keys. Existing successful/human/current source-provided rows remain skipped. A failed row is replaced by exactly one successful or failed result on rerun; it is not appended beside itself. Source hash changes create a distinct current task while retaining historical rows.
- Document export includes these same failed current keys. Import can complete them and leaves any newly completed human row intact. No disagreement between skip-key construction and materialization.
- Localization records failed current rows as unresolved, applies no replacement, and keeps canonical metadata value. It continues to detect stale/orphan/missing-field rows. A canonical fallback does not increment applied/successful translation counters.
- Preserve CLI error-mode spellings with explicit corrected semantics: default existing `source` becomes “keep canonical fallback in materialization; persist failed task without a translation”; `skip` writes successful work and reports omitted failed task keys (still retryable because absent); `fail` preserves the previous CSV and returns an error. No new flags or compatibility retries.
- Machine reports retain `valid` for parseable/validated CSV; add `complete`, successful task count, failed task count/keys, and outstanding current task count. Count provider failures independently from many tasks sharing one provider value. `translated_unique_value_count` includes successes only. CLI returns 0 for completed requested work, 1 for a written valid partial CSV with outstanding provider failures, 2 for validation/provider fail-mode errors with no CSV commit. Write the partial report before returning 1 so agents can checkpoint and rerun default safely.
- Document import reports completion for the manifest's requested tasks after honoring current successful rows. Localization reports validity plus failed/stale/unresolved source-row counts and completion **within the requested translation rows**, not invented coverage for every field/locale in an asset. Its successful canonical fallback can remain exit 0, with `--fail-on-stale` retaining its explicit stricter policy. It must not present failed rows as applied translations.

A retry-all switch would overwrite human work; deleting all source-provided rows would corrupt valid provenance. A new general job database or provider service is unnecessary for these local defects.

## Exact intended source files and removal pass

| File | Planned change |
|---|---|
| `scripts/translation_local_io.py` (new) | Bounded shared alias/snapshot/candidate/replace semantics. |
| `scripts/feature_metadata_localization.py` | Single CSV row/failure boundary, validated materialization before replace, protected paths, honest reports/CLI. |
| `scripts/feature_metadata_machine_translate.py` | Reuse row boundary; selective retry; explicit failed rows; atomic CSV/report writes; deliberate exit statuses. |
| `scripts/feature_metadata_document_translate.py` | Strict v2 producer/parser; hash-set shard/row matching; exact tasks; safe CSV/workbook/manifest/report outputs. |
| `tests/test_feature_metadata_localization.py` | Baseline and adversarial alias/atomicity/identity/failed-row cases. |
| `tests/test_feature_metadata_machine_translate.py` | Failure lifecycle, preservation, CLI and interruption cases. |
| `tests/test_feature_metadata_document_translate.py` | Complete manifest and reorder/refusal/human-preservation matrix; replace unsafe positional test. |
| `tests/test_translation_local_io.py` (new, only shared behavior) | Real symlink/hardlink, destination-change, interrupted write/replace tests. |
| `.claude/skills/update-feature-metadata-translations/SKILL.md` | Hash-based export/import repair instructions, failure/retry/checkpoint/exit semantics. |
| `docs/standards/asset-layout-and-formats.md` | One authoritative existing CSV column contract and explicit failure/provenance states; local output/compatibility limits. The later contradictory `source_value,translated_value` schema paragraph is replaced by a reference to the existing `value` schema. |

No changes planned to release_feature_model, generated catalog/data, asset docs, dependency files, publishing concierge, GitHub workflows, or `feature_metadata_translation_pipeline.py`. Existing integration/skill tests will be run; change those only if an asserted public report contract requires it, with supervisor notification. Remove positional mismatch acceptance/counters, ignored-row behavior, duplicate CSV parsing, fake source-provided error writes, and obsolete skill/CLI wording. Retain explicit refresh only as user-selected replacement behavior; default retry does not require it.

## Tests required before handoff

- Baseline failures: canonical equals output; stale rejection preserving previous output; reversed intact hashes must map correctly; failed provider then success must call provider and replace one key. Preserve baseline fixtures/results separately; tests must exercise helpers and CLI, not assert source strings.
- Output protection: direct/resolved/symlink/hardlink aliases among canonical/CSV/schema/output/report; all-locales/report-dir aliases; absent and pre-existing outputs; malformed canonical, duplicate IDs, midstream writer failure, invalid candidate identity/count, replace failure and simulated interruption. Verify source hashes and previous final bytes unchanged. Empty valid canonical plus explicit locale produces validated empty output; malformed/truncated gzip does not.
- Workbook: reordered rows and shards succeed by hash; extra/missing/duplicate files or rows, foreign/blank/damaged hashes, bad/duplicate headers/cells, invalid JSON/duplicate keys, duplicate tasks/shards, missing entries, incorrect counts, stale source/schema, changed task options, unsupported v1 and wrong asset/release all refuse before output changes. Zero pending tasks, reused locales and legitimate repeated source text across features work. New human completion after export survives; a formerly completed task now missing requires fresh export.
- Failure lifecycle: first failure → retry success; repeated failures produce one failed key; genuine source-provided and human rows preserved; exact legacy producer failure retried; approximate notes or edited value not reinterpreted; current source-hash change schedules new work; failed rows not applied; shared provider value yields accurate unique/task counts; machine and document select the same current pending tasks.
- Concurrent/interrupted local writes: edit CSV while fake provider is blocked, then release it; original expectation refuses and preserves human edit. Repeat for canonical input changes. Inject exception during CSV serialization and before replace. Rerun produces no duplicate keys. State the remaining external-editor race after final check explicitly.
- CLI: actual export/import/localization commands and patched local fake-provider machine main test 0/1/2 statuses, parseable partial reports, report alias refusal, and helpful recovery messages. No provider or GCS network access.

## Boundaries, validation and completion checklist

F owns managed remote claims/uploads, exact source/schema/translation generations and destination preconditions in the translation pipeline. G sends that owner the local report/protected-schema call contract, but does not edit its remote logic. E owns immutable authorization and automatic workflow handoff; no E files change. G cannot resolve C7's distributed stale-output race or C8 locale retirement. A future extension, if separately approved, must bind the three input generations and pre-derivation destination generation to F's execution/claim and refuse stale activation; a fresh destination reload immediately before upload is insufficient. Do not add a second receipt or claim a fix here.

Docs overlap: E changes a separate reviewed-plan section of asset-layout-and-formats. G edits translation schema/state paragraphs only; combine against E's accepted bytes and rerun doc/authorization tests in supervisor integration. No schema/ID canonicalization changes, PR145 roadmap, provider additions, translator dependency changes, deployment, publication or historical data migration.

Commands will run from this worktree with the charter's `UV_PROJECT_ENVIRONMENT`/`UV_CACHE_DIR`, `PYTHONDONTWRITEBYTECODE=1`, and `uv run --no-sync`. Planned validation:

1. Focused pytest: the three translation suites, new I/O suite, translation pipeline and agent translation skill tests, then publishing-concierge and metadata-localization workflow tests for caller compatibility.
2. `uv run --no-sync ruff check scripts tests`; three CLI `--help` and disposable CLI smoke workflows.
3. One complete Python suite after focused tests pass; record native/infrastructure fixture limitations rather than changing unrelated code. No repeated full-suite runs without new evidence.
4. Bundled Git `diff --check`, diff/source status review and exact changed-file list. Inspect every persisted/CLI change; delete obsolete tests/branches that encoded the bug.
5. Retain baseline and final regression logs under `evidence/translation-integrity-*`; write `reviews/translation-integrity-handoff.md` with exact tests, compatibility breaks, per-file atomicity/local concurrency limits and F/E prerequisites.
6. Confirm only approved files changed, no prohibited actions or shared-runtime mutations occurred; freeze and STOP for supervisor review. Tests passing is not acceptance.

Plan-phase retained directory: `evidence/translation-integrity-baseline/`. Existing source tests passed but do not cover the demonstrated defects. No claim of production correctness, historical translation correctness, batch atomicity or remote freshness is made.
