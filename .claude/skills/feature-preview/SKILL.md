---
name: feature-preview
description: Use when deploying, destroying, uploading test data to, or loading data into the feature preview environment, including preview release bundles in gs://skytruth-shared-datasets-1-preview/, catalog refreshes, and distinguishing preview uploads from production _scratch/pending-publishes promotion.
---

# Feature Preview

Use this skill for the disposable feature branch preview environment and its
test data. The preview path is separate from production dataset publishing.

## Load only the relevant context

Read `AGENTS.md` and `gcp-shared-datasets` before remote object work.
For a first release with metadata sidecars, load
`update-feature-metadata-translations` for the maintainer locale/field decision.
For deploy/destroy, read [deploy and viewer QA](references/deploy-and-viewer-qa.md)
and [environment contract](references/environment-contract.md), and load
`protected-terraform-apply` before Terraform/IAM changes.
For viewer/PMTiles code or cache QA, load `static-catalog-web-preview`.
CLI interfaces come from `--help`; before deploying, inspect the workflow and
service contract at the exact remote ref that will execute.

## Start and evidence loop

Classify the request before inspecting/transforming/uploading dataset objects:

```bash
uv run python scripts/publishing_concierge.py start --help
uv run python scripts/publishing_concierge.py start "$SOURCE" --category "$CATEGORY" --subcategory "$SUBCATEGORY" --request-classification preview-only --proposal-id "$PROPOSAL_ID" --release-date "$RELEASE_DATE" --preview-ref "$PREVIEW_REF" --access-tier private
uv run python scripts/publishing_concierge.py next --state-file "$STATE_FILE" --json
uv run python scripts/publishing_concierge.py confirm --state-file "$STATE_FILE" --step "$STEP_ID" --evidence-json "$EVIDENCE_JSON"
```

Set `STATE_FILE` from start's `state_file`; repeat next/work/confirm.
Read saved `plan` for `curator_field_options`, destinations and blocking questions.
`next` returns `commands`, `evidence_schema` and `blockers`. Use its planned
roles and metadata requirements rather than a hand-asserted bundle checklist.
At artifact preparation, use the publish skill's
[artifact contract](../publish-shared-dataset/references/artifacts-and-metadata.md).
Its validators provide `valid`/`errors`, PMTiles verification/decode evidence and
metadata bundle counts; successful validation does not grant curator approvals.

## Critical Distinction

Preview data loading does not use the production `_scratch/pending-publishes/`
promotion path, `shared-datasets-publish-plan`, or the `Approved dataset
mutation` workflow.

- Production canonical dataset publish: use `publish-shared-dataset`, stage
  under `gs://skytruth-shared-datasets-1/_scratch/pending-publishes/...`, open a
  reviewed PR with a fenced publish plan, and let the approved workflow promote.
- Feature preview test data: write a disposable release bundle directly
  under `gs://skytruth-shared-datasets-1-preview/...`, stat exact generations,
  record the URIs and generations in concierge upload evidence, then refresh the
  preview catalog with a preserve-mode deploy. Current lookups read GCS sidecars;
  Firestore index loaders and the standalone metadata endpoint are retired.

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

## Preview Concierge Gate

For any preview request phrased as "upload this," "add this," "load this,"
"put this in preview," "put this in sidecar preview," or equivalent, run the
repo publishing concierge intake or a preview-specific concierge wrapper before
inspecting, transforming, or uploading dataset objects.

Use the concierge to classify the source, confirm the catalog asset slug,
category/subcategory, release date, required artifact roles, content types,
identity strategy, feature metadata sidecar requirements, PMTiles requirements,
release-index requirements, run-record requirements, and validation evidence.

Do not skip this gate because the asset slug already exists in
`catalog/shared-datasets-catalog.csv`, because the supplied file is already an
approved format such as FGB, or because the preview destination seems obvious.
Existing catalog metadata may prefill decisions, but it does not replace the
structured preview-bundle checklist.

For vector preview releases, the expected bundle normally includes:

- Canonical FGB or approved vector source transformed for preview use.
- Lightweight PMTiles for map display, with geometry and `feature_id` only.
- Feature metadata sidecar using the standard `.metadata.ndjson.gz` suffix
  expected by the selected preview service ref.
- For first preview loads of release-oriented vector metadata, either
  maintainer-approved localized metadata sidecars and their translation source
  CSV, or an explicit maintainer decision that no autogenerated translations
  should be produced for this preview bundle.
- Schema JSON ending `.schema.json`.
- Manifest JSON ending `.manifest.json`.
- Release index at `_catalog/releases/{asset-slug}.json`.
- Run record under the asset `runs/` prefix when a release attempt succeeds,
  fails, or is repaired.

Validate PMTiles with the same standard as canonical publish artifacts before
uploading or marking the preview bundle successful. Do not trust a `.pmtiles`
filename, a successful tile-builder exit code, or a still-running long tile build.
For metadata-lookup vector previews, use the validated PMTiles build path:
export a WGS84 GeoJSONSeq tile source from the FGB with GDAL, build an MBTiles
intermediate with Tippecanoe, then convert that archive with `pmtiles convert`.
When a tool writes an output named `.pmtiles`, check the first bytes as soon as
the file exists; stop immediately if the header is `SQLite format 3`. For the
final `.pmtiles`, confirm the PMTiles magic bytes, run `pmtiles verify`, inspect
`pmtiles show` metadata, and decode representative tiles to verify expected
layers and `feature_id` as the only feature property.

Do not report the preview publication, release bundle, or upload as successful
while a required companion artifact failed to generate or validate. If PMTiles
is expected for the preview and PMTiles generation fails, keep or replace remote
manifest, release-index, and run records with an explicit failed status; retain
local artifacts in the standard temp workspace for diagnosis; and report the
blocking failure instead of calling the preview release complete. Use scratch or
preview-bucket failed run records for evidence, but do not cite them as a
published preview release.

## Preview Artifact QA Guardrails

Use these checks before upload for hand-built vector preview bundles:

- If the source is newline-delimited GeoJSON or GeoJSONSeq, force the GDAL
  `GeoJSONSeq:` driver instead of relying on filename guessing. Confirm feature
  counts after every transformation.
- Validate the final FGB with `ogrinfo -al -so`, an explicit feature count,
  required identity fields `feature_id`, `geometry_hash`, and
  `properties_hash`, and a geometry validity query. Preserve source fields in
  the FGB and metadata sidecar. If invalid geometries are found, repair them
  before final sidecar, schema, manifest, hash, and PMTiles generation; do not
  treat after-the-fact repair as equivalent to validating the uploaded bytes.
- Compare source, FGB, sidecar, and decoded PMTiles representative feature
  counts or samples. A one-feature FGB, missing IDs, or mismatched compact
  properties is a blocking error.
- Keep PMTiles preview detail proportional to the preview purpose. If a
  no-simplification or high-maxzoom build grows unexpectedly or runs too long,
  stop and rebuild with a preview-appropriate maxzoom, then document that choice
  in the run record or final report.
- Before workflow dispatch, inspect the deploy workflow and preview service from
  the ref that will execute. Align sidecar paths, generations, manifest files,
  and release-index `files[]` entries with that remote contract.
- For first preview loads of release-oriented feature metadata, stop for the
  translation decision point from `update-feature-metadata-translations`: ask
  which locales and which metadata fields should be autogenerated, or record
  that the maintainer chose no autogenerated translations for the preview.## Preview Data Runbook

1. Confirm every remote destination URI starts with:

```text
gs://skytruth-shared-datasets-1-preview/
```

2. Run the preview request through the Preview Concierge Gate. If the source is
   an existing catalog asset, use the catalog row only to prefill destination
   root and known metadata. Continue through the required preview-bundle
   artifacts and validation decisions before upload.

3. Build or collect the complete preview release bundle outside the repo tree.
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

4. Validate each planned preview URI before upload:

```bash
SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1-preview \
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py validate-path \
  gs://skytruth-shared-datasets-1-preview/path/to/object
```

5. Upload each preview object no-clobber with the repo GCS helper. Only set
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

6. Stat every uploaded object and record exact generations:

```bash
SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1-preview \
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat \
  gs://skytruth-shared-datasets-1-preview/path/to/local-artifact.json
```

7. Record every preview URI and generation in concierge `preview-upload`
   evidence, then follow `preview-catalog-refresh`. Deploy the selected preview
   ref with `preview_data_mode=preserve`:

```bash
gh workflow run feature-preview-deploy.yml --ref "$PREVIEW_REF" \
  -f preview_data_mode=preserve
```

   Wait for the catalog collection, build, and publication steps to finish.
   Firestore loaders are retired. The preview catalog publisher identity remains
   required; deployment preserves historical Firestore database bytes.

8. Confirm that the preserve-mode deploy refreshed the preview catalog viewer.
   The viewer includes only preview-bucket release-index assets, materializes
   top-level "latest" from those release indexes, and treats every preview asset
   as private so the authenticated viewer signs short-lived preview-bucket URLs.
   The generated catalog must preserve every release-index `files` entry in
   `versions[].files`, including metadata sidecars, schemas, manifests, and any
   other new sidecar datafiles in the preview release bundle.

9. Complete concierge `preview-viewer-verify` evidence. Report the preview
   bucket paths, generations, workflow run, preview catalog viewer refresh and
   lookup verification, and any retained local temp work directory.

## Safety Rules

- Do not cite preview-bucket objects as canonical shared dataset contracts.
- Do not upload irreplaceable data or production-only credentials to the
  preview bucket.
- Do not use production bucket URIs as preview workflow inputs.
- Do not create a production publish PR merely to load preview test data.
## Completion evidence

```bash
uv run python scripts/publishing_concierge.py validate --state-file "$STATE_FILE"
uv run python scripts/publishing_concierge.py render-report --state-file "$STATE_FILE"
```

Require `ready_for_preview: true` and `errors: []`; preserve real upload stats,
workflow outcome and viewer observations. Never render a production publish PR
for preview data. A validator cannot establish live UI success without that QA.
