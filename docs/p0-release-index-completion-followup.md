# P0 Release Index Completion Follow-Up

This note documents the reviewed mutation proposal for completing the remaining
sparse release-index records after the P0.3 canonical-file repair restored
missing FGB objects for `cerulean-s1-envelope`, `global-coral-reefs`, and
`gogi`.

The repair does not change dataset bytes. It stages corrected release-index JSON
and three new backfill run records under:

```text
gs://skytruth-shared-datasets-1/_scratch/pending-publishes/catalog-release-index-repair/p0-release-index-completion-20260605/
```

Promotion must occur through the approved dataset mutation workflow after the
PR is reviewed and merged.

## Objects To Promote

| Destination | Operation |
|---|---|
| `_catalog/releases/cerulean-s1-envelope.json` | Replace existing release index with generation precondition |
| `_catalog/releases/global-coral-reefs.json` | Replace existing release index with generation precondition |
| `_catalog/releases/gogi.json` | Replace existing release index with generation precondition |
| `200-imagery-derived/210-satellite-indexes/cerulean-s1-envelope/runs/2026-05-04.json` | Create new backfill run record with no-clobber precondition |
| `500-conservation-ecosystems/530-habitat-condition/global-coral-reefs/runs/2026-05-04.json` | Create new backfill run record with no-clobber precondition |
| `300-infrastructure-industrial/310-energy/gogi/runs/2026-05-04.json` | Create new backfill run record with no-clobber precondition |

## Release Entries Completed

| Asset | Release date | Rows | Notes |
|---|---:|---:|---|
| `cerulean-s1-envelope` | 2026-05-01 | 1 | Points release index at existing run record and fills checksums |
| `cerulean-s1-envelope` | 2026-05-04 | 1 | Adds checksums, rows, and a new backfill run record |
| `global-coral-reefs` | 2026-05-04 | 17,504 | Adds FGB checksum, rows, and a new backfill run record |
| `gogi` | 2026-05-04 | 411,521 | Adds FGB checksum, rows, and a new backfill run record |

## Validation

- Corrected JSON was generated from the current live release indexes downloaded
  on 2026-06-05.
- JSON candidates parse with `jq empty`.
- Release-index missing-field checks return no rows for the affected entries.
- P0.3 validation evidence supplied the row counts and SHA-256 checksums for
  the restored FGB files.
- Publish-plan JSON validates with `scripts/reviewed_dataset_plan.py`.
- Destination paths validate with `scripts/gcs_asset.py validate-path`.

