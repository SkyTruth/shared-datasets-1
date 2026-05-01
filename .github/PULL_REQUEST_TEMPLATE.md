## Summary

Describe the asset, infrastructure, ingestion, tooling, or documentation change.

## Validation

- [ ] Ran relevant tests or documented why they were not run.
- [ ] For bucket-facing changes, ran the compliance audit or explained remaining findings.
- [ ] For remote object changes, listed every changed `gs://` path and generation precondition.

## Bucket Hygiene

- [ ] No root-level bucket objects are introduced except the intentional bucket `README.md`.
- [ ] Every asset root has a `README.md`.
- [ ] Catalog rows are current when asset ownership, source, license, cadence, format, or canonical path changed.
- [ ] Remote `_catalog/shared-datasets-catalog.csv` is current when catalog contents changed.
- [ ] Any audit findings are either fixed or explicitly explained.
