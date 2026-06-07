# P0 IHO World Seas Release Index Repair

This note documents the reviewed mutation proposal for repairing the
`iho-world-seas` release index after the 2026-06-05 metadata-contract publish
promoted canonical artifacts but failed during the post-promotion release-index
rebuild step.

The repair does not change dataset artifacts. It stages a rebuilt
`_catalog/releases/iho-world-seas.json` under:

```text
gs://skytruth-shared-datasets-1/_scratch/pending-publishes/iho-world-seas/iho-world-seas-release-index-repair-20260607/
```

Promotion must occur through the approved dataset mutation workflow after this
PR is reviewed and merged.

## Object To Promote

| Destination | Operation |
|---|---|
| `_catalog/releases/iho-world-seas.json` | Replace existing release index with generation precondition |

## Release Entries

| Release date | Rows | Files | Notes |
|---|---:|---:|---|
| `2026-06-05` | 101 | 12 | Metadata-contract release with canonical metadata, schema, manifest, PMTiles, translation CSV, and localized metadata sidecars |
| `2026-04-29` | 101 | 2 | Preserved historical release |

## Validation

- Rebuilt the release index from current live bucket releases and run records
  using `scripts/gcs_asset.py release-index rebuild --asset-slug iho-world-seas
  --dry-run`.
- Confirmed the rebuilt latest release is `2026-06-05`, contains 12 files, has
  101 rows, and includes `index_load_status`.
- Fixed release-index rebuild checksum normalization so localized metadata
  sidecars and the translation CSV keep their exact artifact-path SHA-256
  values instead of inheriting the canonical metadata checksum.
- Ran `UV_CACHE_DIR=.uv-cache uv run python -m pytest
  tests/test_ingestion_common.py -q`.
- Validated JSON syntax with `jq empty`.
- Staged the candidate JSON to scratch with content type `application/json` and
  cache control `no-cache, max-age=0, must-revalidate`.
- Captured the current destination generation before staging:
  `1780636087002059`.
