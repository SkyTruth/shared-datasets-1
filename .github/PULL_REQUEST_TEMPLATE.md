## Summary

Describe the asset, infrastructure, ingestion, tooling, or documentation change.

## Validation

- [ ] Ran relevant tests or documented why they were not run.
- [ ] For bucket-facing changes, ran the compliance audit or explained remaining findings.
- [ ] For remote object changes, listed every changed `gs://` path and generation precondition.

## Dataset Admission

Complete this section for any new canonical dataset or new ingestion pipeline.
Existing assets are grandfathered unless this PR changes the dataset contract.

- Intended consumer(s):
- Why this belongs in shared-datasets instead of project storage, scratch storage, or direct upstream access:
- Source, license, and citation status:
- Named steward:
- Update expectations:
- Estimated published footprint, including canonical files, companion artifacts, and expected release copies:
- Large-data exception, required when the proposed published footprint is >= 10 GB:
- Alternatives considered:
- Deprecation or exit policy:

## Bucket Hygiene

- [ ] No root-level bucket objects are introduced except the intentional bucket `README.md`.
- [ ] Every asset root has a `README.md`.
- [ ] Catalog rows are current when asset ownership, source, license, cadence, format, or canonical path changed.
- [ ] Remote `_catalog/shared-datasets-catalog.csv` is current when catalog contents changed.
- [ ] Any audit findings are either fixed or explicitly explained.
