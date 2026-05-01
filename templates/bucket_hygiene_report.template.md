# Bucket Hygiene Report

**Audit date:** YYYY-MM-DD
**Bucket:** `gs://skytruth-shared-datasets-1/`
**Audit command:** `UV_CACHE_DIR=.uv-cache GOOGLE_CLOUD_PROJECT=shared-datasets-1 SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1 uv run python .claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py --format json`

## Summary

- Objects inspected:
- Errors:
- Warnings:
- Info:
- Final status:

## Findings

| Severity | Check | Path | Generation | Uploader hint | Owner confirmation | Disposition |
|---|---|---|---:|---|---|---|
| ERROR | `path-taxonomy` | `gs://...` | 0 | unknown | pending | pending |

## Remote Changes

| Action | Path | Generation precondition | Result generation | Notes |
|---|---|---:|---:|---|
| upload | `gs://.../README.md` | 0 | 0 | Added missing README. |

## Verification

- [ ] Re-ran full bucket audit.
- [ ] Confirmed bucket `_catalog/shared-datasets-catalog.csv` matches repo catalog.
- [ ] Verified every changed remote path with `scripts/gcs_asset.py stat`.
- [ ] Listed destructive deletions separately with explicit approval evidence.

## Follow-Up

- Owner/source/license questions:
- Deferred moves or renames:
- Automation gaps:
