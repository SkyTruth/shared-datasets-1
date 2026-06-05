# P0 IMS Sea Ice Release Index Repair

This note documents the reviewed mutation proposal for repairing the
`ims-sea-ice-extent` release index after successful historical runs for
`2026-05-05` and `2026-05-06` were omitted from
`_catalog/releases/ims-sea-ice-extent.json`.

The repair does not change dataset bytes. It stages corrected run records and a
rebuilt release-index JSON under:

```text
gs://skytruth-shared-datasets-1/_scratch/pending-publishes/ims-sea-ice-extent/p0-ims-release-index-20260605/
```

Promotion must occur through the approved dataset mutation workflow after the
PR is reviewed and merged.

## Objects To Promote

| Destination | Operation |
|---|---|
| `_catalog/releases/ims-sea-ice-extent.json` | Replace existing release index with generation precondition |
| `200-imagery-derived/250-weather-climate/ims-sea-ice-extent/runs/2026-05-05.json` | Replace existing run record with generation precondition |
| `200-imagery-derived/250-weather-climate/ims-sea-ice-extent/runs/2026-05-06.json` | Replace existing run record with generation precondition |

## Release Entries Added

| Release date | Rows | Source version |
|---|---:|---|
| `2026-05-05` | 2,632 | `ims2026125_4km_GIS_v1.3.tif.gz` |
| `2026-05-06` | 2,578 | `ims2026126_4km_GIS_v1.3.tif.gz` |

## Validation

- Full bucket audit on 2026-06-05 still reported the two IMS missing-release
  warnings before this repair.
- Corrected JSON was generated from the current live release index, not from a
  stale earlier candidate.
- JSON candidates parse with `jq empty`.
- Release-index missing-field checks return no rows for the added entries.
- Publish-plan JSON validates with `scripts/reviewed_dataset_plan.py`.
- Destination paths validate with `scripts/gcs_asset.py validate-path`.

