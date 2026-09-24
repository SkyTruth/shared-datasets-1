> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

# G supervisor decision — source accepted

Accepted the exact 14-file snapshot in evidence/translation-integrity-final/source-snapshot.json and patch SHA-256 `507ad18054bd7198cc32d86cc0940f785e55f779fb6aaf541aabda7761e198c8`. All source hashes were independently verified. The plan and four-file packaging addendum were approved before implementation. No merge, deployment, provider call or remote write is approved by this decision.

Read final production, docs and packaging changes, the tests and independent crossreview. Required revisions before acceptance covered original input/report/later-output fingerprints, duplicate CLI JSON, oversized CSV error exit status, ambiguous XLSX row coordinates and WDPA Docker import closure. The helper's OSError subtype preserves the existing pipeline failure boundary without editing its remote mutation code.

Independent validation: 123 affected tests and 2 subtests passed; all four original regressions passed against the frozen source. The second reviewer's unchanged coordinate reproducer now refuses `2`/`02` before CSV creation; evidence/translation-integrity-supervisor contains this result. The agent also demonstrated four failures on exact-base source, 838 full-suite passes with 429 subtests, four native skips and the known unrelated location-sensitive fixture deselection. Final combined validation is recorded in EXECUTION-BOARD.md.

The crossreview's coordinate finding is resolved in the frozen source by canonical positive ASCII decimal row references. Its original report deliberately retains the earlier observation; this decision records closure.

Invariant enforced: no alias or failed preparation may destructively replace protected local bytes; document translations join through verified task/hash membership; failed provider work stays retryable.
Boundary changed: shared translation CSV parsing/completion classification, candidate/replace I/O, v2 manifest/XLSX validation and WDPA packaging.
Code removed: positional translation recovery, duplicate CSV parsers and output-key validation, fabricated successful fallback rows, direct final-path writes, duplicate report emission and conflicting CSV docs.
Internal handling removed: consumers no longer accept damaged hashes or infer completion from a source-value fallback.
Fallbacks added: no silent repair. Failed rows explicitly preserve canonical source values during materialization and report unresolved work.
Fallbacks rejected: positional workbook recovery, successful labeling of outages, implicit non-string translation, upload freshness inferred from local success.
Deletion candidates left in place: documented refresh-current behavior, narrow proven legacy-failure recognition, existing canonical metadata identity sets and full task/workbook collections.
Remaining uncertainty: per-file atomicity only; arbitrary editors can race the final check/rename; v1 workbooks require re-export; older helpers reject new failed rows; pipeline memory remains proportional to total task count; distributed publication and locale retirement are separate held work. No native image build or full GDAL ingestion integration was performed.
