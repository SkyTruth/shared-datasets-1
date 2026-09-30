> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# G handoff — frozen for supervisor review

Worktree `worktrees/translation-integrity`, branch `codex/audit-translation-integrity`, base `1bf095d861d921e2378203495bd9a0da0bdf650c`. Unstaged changes only; no commits, index changes, remote access/writes, notifications, installs, image builds or deployments. Scope approved in [plan](../plans/translation-integrity.md) and [packaging addendum](../plans/translation-integrity-packaging-addendum.md), governed by [charter](../CHARTER.md).

## Result

Local sidecars, CSVs, workbooks, manifests and reports use per-file candidates/replacement. Canonical metadata is validated independently; exact derived-record comparison streams before sidecar replacement. Initial input fingerprints precede schema/locale-selection reads, and each output/report retains its own original destination expectation through long work. Alias, stale, malformed, interrupted and changed-file failures preserve that file's previous final bytes.

V2 document manifests bind source/schema, all eligible task keys, pending/completed exclusions and collection options. Import matches intact hashes and exact shard membership; it rejects ambiguity and preserves newly completed human work. Provider failures persist as empty `translation_failed` rows, remain retryable, and leave canonical values during materialization. Machine/document/materialization share the row/failure decision. The narrow legacy rule requires exact old default-Google failure notes and a matching canonical fallback value/hash; human-edited values survive.

WDPA ships the new helper; its trigger/import smoke and geospatial CI selection include it. Existing pipeline error handling catches the helper's `OSError` subclass. No pipeline mutation/authentication logic changed.

## Exact 14 changed/new files

- `scripts/translation_local_io.py` (new): alias checks, original snapshots, sibling candidates and per-file replace.
- `scripts/feature_metadata_localization.py`: shared CSV/state validation, streaming derivation check, precommit stale checks and protected inputs/reports.
- `scripts/feature_metadata_machine_translate.py`: shared completion keys, selective retry, failed task state, safe CSV/report writes, CLI 0/1/2 semantics.
- `scripts/feature_metadata_document_translate.py`: v2 export/strict import, hash/shard/task identity and safe output/report writes.
- `tests/test_translation_local_io.py` (new).
- `tests/test_feature_metadata_localization.py`.
- `tests/test_feature_metadata_machine_translate.py`.
- `tests/test_feature_metadata_document_translate.py`.
- `.claude/skills/update-feature-metadata-translations/SKILL.md`.
- `docs/standards/asset-layout-and-formats.md` (translation paragraphs only).
- `ingestion/wdpa_monthly/Dockerfile` (one helper COPY).
- `.github/workflows/wdpa-monthly-deploy.yml` (helper path/import smoke only).
- `.github/workflows/ci.yml` (helper/test detector and geospatial test invocation only).
- `tests/test_wdpa_monthly_deploy_workflow.py` (declared-COPY isolated imports and actual trigger behavior).

Review patch: translation-integrity.patch (historical artifact: `../evidence/translation-integrity-final/translation-integrity.patch`).

Exact final file hashes: source-snapshot.json (historical artifact: `../evidence/translation-integrity-final/source-snapshot.json`).

## Evidence and validation

All commands ran from the assigned worktree using the charter's existing `UV_PROJECT_ENVIRONMENT`/`UV_CACHE_DIR`, `PYTHONDONTWRITEBYTECODE=1` and `uv run --no-sync`.

- Four independent original regressions (canonical alias, stale output preservation, reordered intact hashes, default failed-provider retry): **4 failed on extracted exact-base source; 4 passed on G**, using the same external tests (historical artifact: `../evidence/translation-integrity-final/test_original_regressions.py`). Baseline failures (historical artifact: `../evidence/translation-integrity-baseline/regression-failures.txt`), fixed results (historical artifact: `../evidence/translation-integrity-final/original-regressions-passed.txt`). Original observed results (historical artifact: `../evidence/translation-integrity-baseline/results.json`) retained.
- Packaging regression before its fix: isolated declared-file imports failed on missing helper; CI regex rejected helper/test paths. Before results (historical artifact: `../evidence/translation-integrity-baseline/packaging-failures.txt`). Both pass in final focused/full runs.
- Focused pytest across translation/local-I/O/pipeline/skills, WDPA ingestion/deploy, geospatial CI, localization workflow and concierge: **215 passed, 45 subtests, 1 native skip**. Log (historical artifact: `../evidence/translation-integrity-final/focused-tests.txt`).
- Full pytest: `pytest -q -p no:cacheprovider -k 'not test_binary_resolver_accepts_private_executable'`: **838 passed, 429 subtests, 4 native skips, 1 explicitly deselected unrelated fixture**. Log (historical artifact: `../evidence/translation-integrity-final/full-tests.txt`). The unchanged Terraform fixture assumes a private executable is outside `/private/tmp`; it is known location-sensitive in these remediation worktrees. Native GDAL/Tippecanoe/PMTiles integration remains unrun.
- Actual subprocess CLI export → reordered workbook import → localization → already-complete machine run: each stdout parses as one JSON document, preserves exact feature mapping, and performs no provider call. Three CLI `--help` commands pass. Script (historical artifact: `../evidence/translation-integrity-final/cli_smoke.py`), results (historical artifact: `../evidence/translation-integrity-final/cli-results.json`).
- Fake-provider CLI tests cover success/partial/fail exit codes, report edits during work, repeated failure→success, shared provider-value/task counts, and human preservation. An actual oversized-CSV subprocess returns **2**, with no traceback, provider invocation or replacement.
- Adverse tests include same/resolved/symlink/hardlink aliases, schema-read/CSV-selection races, later locale/shard/report edits, failed replacement/interrupted CSV/gzip, empty source, output identity/count corruption, strict manifest/shard closure, blank/foreign/duplicate/extra/missing hashes, malformed headers/cells, and logical row-coordinate ambiguity (`2` versus `02`, non-ASCII and invalid coordinates).
- `ruff check scripts tests` and bundled Git `diff --check`: pass. Source status contains exactly the 14 files above.

## Removal/compatibility pass

Removed positional hash mismatch acceptance and ignored excess rows, duplicated CSV parsing/column ownership, duplicate-output-key checks now owned by the candidate parser, fabricated successful source fallbacks, direct final-path writes in the three tools, duplicate CLI report emission, and obsolete row-order skill/help instructions. The contradictory standards CSV schema now references the existing canonical column contract.

Retained intentional `--refresh-current` replacement behavior; default retries do not require it. Retained `source/skip/fail` spellings with corrected semantics: default `source` records an empty failed task; `skip` omits failed tasks; both return CLI 1 for partial work, while `fail` returns 2 without CSV replacement. Genuine source-provided/current human rows remain complete. Non-string legacy recognition verifies the raw canonical hash and old `str(raw)` fallback together; it never enables stringification automatically.

V1 document manifests fail with re-export/verified-hash recovery instructions. Old completed CSV work remains valid. New failure rows require the updated helpers together; older readers reject the empty value. Document import now preserves an existing output CSV automatically when no explicit input CSV is supplied. Validity and completion are distinct: localization's `requested_rows_complete` covers only supplied translation rows, not asset-wide coverage.

## Limits and integration seams

- Atomicity is per file. An earlier locale, CSV, workbook or report may already be committed when a later file fails. There is no batch rollback or cross-file durability claim.
- Fingerprints detect changes observed before replacement, not arbitrary-editor locking; the final check/rename window remains. Shared metadata validation uses its existing identity sets; localization adds no second full metadata-record copy.
- V2 manifests and the current task/workbook pipeline grow with total task count and remain in memory. Workbook sharding is not a bound on total memory.
- F retains remote claims, downloaded generation binding, destination expectations and stale-output activation. E retains immutable authorization. This package does not resolve distributed C7 or locale retirement C8.
- Additive local adapter seam: `protected_inputs=[schema]`; `input_snapshots` carries snapshots taken before schema parsing; single-file `expected_output` can carry an earlier destination observation. Reports retain `valid` and add failed/unresolved counts plus `requested_rows_complete`. F was notified; its source remains untouched.
- Preserve F3a's HOLD gate when combining the WDPA workflow path. G changes only top-level dependency paths and the existing import-smoke command. Standards overlap with F/B is confined to separate translation paragraphs. No rollout/merge/deploy acceptance is implied.

Retained scratch: `evidence/translation-integrity-baseline/` (source extraction, fixtures, logs) and `evidence/translation-integrity-final/` (tests, CLI fixtures/reports, logs, source hashes). Worktree is frozen pending supervisor review.
