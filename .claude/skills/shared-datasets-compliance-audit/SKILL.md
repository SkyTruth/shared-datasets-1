---
name: shared-datasets-compliance-audit
description: Perform a read-only compliance walkthrough of the shared datasets repository and GCS bucket. Use when Codex needs to audit bucket directories, asset folder structure, README completeness, properties/columns documentation, approved formats, release/latest layout, catalog freshness, or newly added non-compliant files; report findings and offer fixes without moving, deleting, rewriting, or reorganizing assets.
---

# Shared Datasets Compliance Audit

## Overview

Use this skill to inspect the shared datasets control plane and data plane for compliance with `AGENTS.md`. The audit is read-only: flag problems, identify likely owners/uploader hints where available, and offer a repair plan before making any changes.

## Rules

- Do not fix, move, delete, rename, or rewrite assets during the audit.
- Do not move an asset just because it appears misclassified. Flag it, explain why, and recommend a human conversation first.
- If object metadata exposes uploader-like fields (`uploaded_by`, `created_by`, `owner`, `author`), include them in the finding.
- If uploader identity is not visible, say so and suggest checking Cloud Audit Logs before moving or reclassifying an asset.
- Treat missing README/catalog/schema documentation as fixable documentation issues, not permission to reorganize data.

## Audit Workflow

1. Read `AGENTS.md`, `catalog/categories.yaml`, `docs/standards/dataset-taxonomy.md`, `docs/standards/asset-layout-and-formats.md`, `catalog/shared-datasets-catalog.csv`, and `.claude/skills/gcp-shared-datasets/SKILL.md`.
2. Run the read-only audit script:

```bash
UV_CACHE_DIR=.uv-cache GOOGLE_CLOUD_PROJECT=shared-datasets-1 SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1 \
  uv run python .claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py
```

3. Review findings by severity:
   - `ERROR`: likely broken contract, missing required metadata, unknown top-level location, bad object layout, catalog path missing.
   - `WARN`: incomplete README fields, missing properties/columns descriptions, nonstandard but not necessarily broken layout.
   - `INFO`: useful context such as uploader unknown or manual follow-up suggested.
4. Summarize findings without applying fixes.
5. Offer a concrete fix plan grouped by:
   - documentation/catalog fixes that are safe to make after approval,
   - asset moves/renames requiring human conversation first,
   - source/license/schema questions requiring owner confirmation.

## Script Notes

The bundled script walks remote GCS objects with `google-cloud-storage`, reads only small text metadata/README/catalog/manifest files, compares asset roots against local catalog/category rules, checks raster layout rules for COG and Zarr assets, and checks during full-bucket audits whether the bucket-side `_catalog/shared-datasets-catalog.csv` matches the repo catalog. It does not download large data files.

Useful options:

```bash
uv run python .claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py --help
uv run python .claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py --prefix 100-geographic-reference/
uv run python .claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py --format json
uv run python .claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py --fail-on-findings
```

## Completion Criteria

Report:
- command(s) run and whether the audit was full-bucket or prefix-limited,
- counts by severity,
- every non-compliant asset/object path,
- catalog rows missing or stale,
- README requirements missing, especially properties/columns tables,
- raster README metadata, COG/Zarr layout, and Zarr latest manifest problems,
- uploader/owner hints if available, or an explicit note that uploader was not visible,
- which fixes you can make after approval and which require a human conversation first.
