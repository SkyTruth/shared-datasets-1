---
name: feature-preview
description: Use when deploying, destroying, uploading test data to, or loading data into the feature preview environment, including preview test datasets, preview release bundles in gs://skytruth-shared-datasets-1-preview/, Feature preview index load dispatches, and distinguishing preview data loads from production _scratch/pending-publishes promotion.
---

# Feature Preview

Use this skill for the disposable feature branch preview environment and its
test data. The preview path is separate from production dataset publishing.

## Required Context

Read these before preview work:

- `AGENTS.md`
- `docs/feature-preview.md`
- `.github/workflows/feature-preview-deploy.yml` for deploys
- `.github/workflows/feature-preview-destroy.yml` for destroys
- `.github/workflows/feature-preview-index-load.yml` for data loads
- `.claude/skills/gcp-shared-datasets/SKILL.md` before any GCS object
  inspection or write; use it for helper semantics and generation safety, but
  follow this skill for the preview-bucket destination policy
- `.claude/skills/protected-terraform-apply/SKILL.md` before changing preview
  Terraform or IAM

## Critical Distinction

Preview data loading does not use the production `_scratch/pending-publishes/`
promotion path, `shared-datasets-publish-plan`, or the `Approved dataset
mutation` workflow.

- Production canonical dataset publish: use `publish-shared-dataset`, stage
  under `gs://skytruth-shared-datasets-1/_scratch/pending-publishes/...`, open a
  reviewed PR with a fenced publish plan, and let the approved workflow promote.
- Feature preview test data: write a disposable release bundle directly
  under `gs://skytruth-shared-datasets-1-preview/...`, stat exact generations,
  then run the preview index-load workflow with explicit URIs and generations.

If the user asks to add, update, upload, or publish a canonical shared dataset,
use `publish-shared-dataset` instead. If the user asks to test a dataset in the
feature preview slot, use this skill.

## Deploy Or Destroy Preview

- Deploy or replace the preview with the GitHub Actions workflow named
  `Deploy Feature Branch to Preview`.
- Select the feature branch or tag to deploy from the GitHub **Run workflow**
  branch dropdown.
- Destroy the preview with `Destroy Preview Environment`.
- Do not run local preview Terraform applies unless the user explicitly requests
  a break-glass path and the protected Terraform skill permits it.

## Preview Data Runbook

1. Confirm every remote destination URI starts with:

```text
gs://skytruth-shared-datasets-1-preview/
```

2. Build or collect the preview release bundle outside the repo tree. The
   preview service expects release artifacts under:

```text
gs://skytruth-shared-datasets-1-preview/{category}/{subcategory}/{asset-slug}/releases/YYYY-MM-DD/
```

   The release index must be:

```text
gs://skytruth-shared-datasets-1-preview/_catalog/releases/{asset-slug}.json
```

   Any schema, manifest, sidecar, or release-index paths inside those files must
   point to the preview bucket, not the production bucket.

3. Validate each planned preview URI before upload:

```bash
SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1-preview \
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py validate-path \
  gs://skytruth-shared-datasets-1-preview/path/to/object
```

4. Upload each preview object no-clobber with the repo GCS helper. Only set
   `SHARED_DATASETS_ALLOW_CANONICAL_MUTATION=1` after confirming every
   destination is in `gs://skytruth-shared-datasets-1-preview/`:

```bash
GOOGLE_CLOUD_PROJECT=shared-datasets-1 \
SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1-preview \
SHARED_DATASETS_ALLOW_CANONICAL_MUTATION=1 \
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload \
  ./local-artifact.json \
  gs://skytruth-shared-datasets-1-preview/path/to/local-artifact.json \
  --content-type application/json \
  --cache-control "no-cache, max-age=0, must-revalidate"
```

5. Stat every uploaded object and record exact generations:

```bash
SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1-preview \
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat \
  gs://skytruth-shared-datasets-1-preview/path/to/local-artifact.json
```

6. Run `Feature preview index load` from `main` with:
   - `ref`
   - `asset_slug`
   - `release`
   - `sidecar_uri`, `schema_uri`, `manifest_uri`
   - `sidecar_generation`, `schema_generation`, `manifest_generation`
   - optional `load_id`

7. Report the preview bucket paths, generations, workflow run, and any retained
   local temp work directory.

## Safety Rules

- Do not cite preview-bucket objects as canonical shared dataset contracts.
- Do not upload irreplaceable data or production-only credentials to the
  preview bucket.
- Do not use production bucket URIs as preview workflow inputs.
- Do not create a production publish PR merely to load preview test data.
