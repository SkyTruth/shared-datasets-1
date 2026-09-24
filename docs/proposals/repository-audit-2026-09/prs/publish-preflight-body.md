> Preserved planning/review record. See the [archive index](../README.md). Statuses, approvals, commands and test results describe their recorded checkpoint; this document is not a new execution authorization.

## Summary

Publication could upload different local bytes from those recorded in its plan, overwrite newer metadata using work calculated from an older read, or report successful vector validation when mandatory native tools were missing.

- Copy every planned artifact and metadata input into invocation-private temporary files, verify their planned size/SHA-256 before canonical mutation, validate the frozen copies, and use those copies throughout execution.
- Bind finalizer JSON reads to the observed generation and use that original generation as the replacement precondition. Use the upload response for resulting metadata rather than reloading a potentially newer object.
- Refuse vector validation when mandatory FGB/PMTiles checks or representative tile decoding cannot run.

For example, replacing an original file after plan creation no longer changes what is uploaded, and a finalizer that read generation 10 cannot overwrite another writer's generation 11.

## Scope and tradeoffs

This PR contains only the standalone preflight fixes. It does not include publication receipts, adoption, writer adapters, or the held recovery framework.

Temporary storage needs the full input set, and copying/hashing/revalidation adds I/O and execution time. Environments without the required native validators now fail explicitly. Existing public CLI flags, output formats, stored schemas and source-path provenance are preserved.

The temporary copies are not durable recovery checkpoints. A later upload can still leave a partially published bundle; per-object generation checks do not make a multi-file release atomic. Finalizer artifact facts still use current stats, and source-backfill/ownership safeguards remain separate work. Durable immutable staging and receipt-driven recovery are a stronger future design, but are not prerequisites for closing these three defects.

## Validation

- [x] New regressions produced 14 baseline failures covering same-size input drift, originals replaced during execution, finalizer read/write races, and missing validators.
- [x] Independent supervisor run: **68 tests and 84 subtests passed** across publish, finalizer, vector and repository guardrails.
- [x] Broader affected suite: **185 passed, 117 subtests passed, 3 native integration skips**.
- [x] Ruff, `git diff --check`, and catalog-doc validation passed.
- Native geospatial integration and live remote writes were not performed. The checks above exercise validation and storage behavior with local fixtures/fakes; they do not prove a deployed toolchain or transaction recovery.

## Dataset admission and bucket hygiene

Not applicable: no new dataset or ingestion pipeline, catalog metadata, bucket layout, IAM, infrastructure deployment, or remote object mutation. No `gs://` paths changed and no live bucket compliance/cleanup was run for this code-only change.

## Review

Self-authored by `jonaraphael`, the repository's sole CODEOWNER; GitHub does not permit requesting self-review.
