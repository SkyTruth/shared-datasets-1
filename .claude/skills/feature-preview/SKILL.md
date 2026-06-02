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

## Preview Upload Intake Rule

Treat preview requests phrased as "add this," "upload this," "load this," or
"put this in sidecar preview" as requests to build a complete disposable
preview release bundle, not as permission to upload only the supplied file.
Use the supplied file as source material when needed, infer or confirm the asset
slug, category, subcategory, release date, source version, and required
companion artifacts, then build and validate the preview bundle before any
success report.

For vector preview releases, the expected bundle normally includes:

- Canonical FGB or approved vector source transformed for preview use.
- Lightweight PMTiles for map display, with compact feature properties such as
  `feature_id`.
- Feature-index sidecar ending `.features.ndjson.gz`.
- Schema JSON ending `.schema.json`.
- Manifest JSON ending `.manifest.json`.
- Release index at `_catalog/releases/{asset-slug}.json`.
- Run record under the asset `runs/` prefix when a release attempt succeeds,
  fails, or is repaired.

Validate PMTiles with the same standard as canonical publish artifacts before
uploading or marking the preview bundle successful. Do not trust a `.pmtiles`
filename or a successful Tippecanoe exit code. Confirm the PMTiles magic bytes,
run `pmtiles verify`, inspect `pmtiles show` metadata, and decode representative
tiles to verify expected layers and compact feature properties. If the local
tool produced MBTiles/SQLite, convert it with `pmtiles convert` and validate
the converted archive before upload.

Do not report the preview publication, release bundle, or upload as successful
while a required companion artifact failed to generate or validate. If PMTiles
is expected for the preview and PMTiles generation fails, keep or replace remote
manifest, release-index, and run records with an explicit failed status; retain
local artifacts in the standard temp workspace for diagnosis; and report the
blocking failure instead of calling the preview release complete. Use scratch or
preview-bucket failed run records for evidence, but do not cite them as a
published preview release.

## Deploy Or Destroy Preview

- Deploy or replace the preview with the GitHub Actions workflow named
  `Deploy Feature Branch to Preview`.
- Select the feature branch or tag to deploy from the GitHub **Run workflow**
  branch dropdown.
- The deploy publishes an initial empty preview catalog web bundle and reports
  both the preview API URL and the preview catalog viewer URL.
- Destroy the preview with `Destroy Preview Environment`.
- Do not run local preview Terraform applies unless the user explicitly requests
  a break-glass path and the protected Terraform skill permits it.

## Preview Data Runbook

1. Confirm every remote destination URI starts with:

```text
gs://skytruth-shared-datasets-1-preview/
```

2. Build or collect the complete preview release bundle outside the repo tree.
   Do not upload the supplied source file by itself unless the user explicitly
   asked for a scratch-only diagnostic upload. The preview service expects
   release artifacts under:

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

7. Confirm that the index-load workflow refreshed the preview catalog viewer.
   The viewer includes only preview-bucket release-index assets, materializes
   top-level "latest" from those release indexes, and treats every preview asset
   as private so the authenticated viewer signs short-lived preview-bucket URLs.
   The generated catalog must preserve every release-index `files` entry in
   `versions[].files`, including feature-index sidecars, metadata sidecars,
   schemas, manifests, and any other new sidecar datafiles in the preview
   release bundle.

8. Report the preview bucket paths, generations, workflow run, preview catalog
   viewer refresh status, and any retained local temp work directory.

## Safety Rules

- Do not cite preview-bucket objects as canonical shared dataset contracts.
- Do not upload irreplaceable data or production-only credentials to the
  preview bucket.
- Do not use production bucket URIs as preview workflow inputs.
- Do not create a production publish PR merely to load preview test data.
