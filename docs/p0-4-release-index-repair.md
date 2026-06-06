# P0.4 Release Index Metadata Repair

This note documents the reviewed repair plan for historical release-index
entries that were missing row counts, run-record provenance, or SHA-256
checksums.

The repair does not change dataset artifacts. It stages backfilled run records
and corrected `_catalog/releases/*.json` files under
`_scratch/pending-publishes/catalog-release-index-repair/p0-4-release-index-repair-20260605/`
for promotion by the approved dataset mutation workflow.

## Repaired Entries

The following entries have staged run records and corrected release indexes with
non-null `rows`, `run_record_path`, and per-file `sha256` values:

| Asset | Release date | Rows |
|---|---:|---:|
| `eamlis-abandoned-mine-land-inventory` | 2026-04-30 | 62,220 |
| `gfw-anchorages` | 2026-02-02 | 166,496 |
| `gfw-fixed-infrastructure` | 2026-04-30 | 57,681 |
| `global-coral-reefs` | 2026-04-29 | 18,429 |
| `gogi` | 2026-05-02 | 1,624,200 |
| `iho-world-seas` | 2026-04-29 | 101 |
| `natural-earth-10m-land` | 2026-04-30 | 11 |
| `petrodata` | 2026-04-29 | 1,273 |
| `wdpa-marine` | 2026-04-29 | 17,528 |

## Blocked Entries

Three sparse entries are PMTiles-only releases:

| Asset | Release date | Reason |
|---|---:|---|
| `cerulean-s1-envelope` | 2026-05-04 | No canonical FGB/CSV release file exists for a defensible row count. |
| `global-coral-reefs` | 2026-05-04 | No canonical FGB/CSV release file exists for a defensible row count. |
| `gogi` | 2026-05-04 | No canonical FGB/CSV release file exists for a defensible row count. |

Those entries remain blocked by the separate P0.3 canonical-file repair.

## Validation

- Downloaded existing repairable release artifacts from the main bucket.
- Computed SHA-256 from the existing object bytes.
- Counted rows from FlatGeobuf artifacts with `ogrinfo -ro -al -so`.
- Counted rows from CSV artifacts by header-aware line counting.
- Generated schema-versioned run records for the 10 repaired releases.
- Generated corrected release-index JSON for the 10 affected assets.
- Validated JSON syntax for generated run records and release indexes.
- Validated that all repaired entries have `rows`, `run_record_path`, and
  `sha256` values.
- Validated planned canonical destination paths with `scripts/gcs_asset.py
  validate-path`.
