---
name: gcp-shared-datasets
description: "Use this skill whenever an agent needs to inspect, download, upload, edit, replace, publish, or validate files in the shared GCP Cloud Storage bucket for SkyTruth shared datasets. Also use it when designing ingestion jobs that write to the bucket."
---

# GCP Shared Datasets Skill

This skill defines the standard way for agents and maintainers to work with remote files in the SkyTruth shared datasets GCP project.

## Decision

Use a **repo-owned Python CLI/library built on `google-cloud-storage`** for dataset object operations.

Use **Terraform** for infrastructure.

Use **`gcloud storage`** for human diagnostics and emergency one-off operations.

Do **not** use Terraform, Pulumi, or Cloud Storage FUSE for routine canonical dataset uploads/edits.

## Why this is the standard

Cloud Storage object operations need safe read-modify-write behavior. The Python Cloud Storage client and GCS APIs expose generation and metageneration preconditions, which prevent accidental overwrites and race conditions. A repo-owned Python CLI can encode SkyTruth-specific path rules, approved formats, catalog updates, dry-run behavior, and validation checks.

Terraform is excellent for infrastructure state but poor for frequently changing data objects. Cloud Storage FUSE is useful for some read-heavy exploration, but its filesystem semantics differ from POSIX, it does not preserve all object metadata, and it should not be used for canonical writes.

## Tool responsibilities

| Layer | Standard tool | Allowed use |
|---|---|---|
| Buckets, IAM, service accounts, schedulers, Cloud Run jobs, APIs, monitoring | Terraform | Required for reviewed infrastructure changes |
| Dataset object upload/download/edit/copy/stat/list | Python CLI in `scripts/gcs_asset.py` | Required default for agents |
| Manual diagnostics | `gcloud storage` | Allowed for inspection and emergency copies |
| Mounted exploration | Cloud Storage FUSE | Read-heavy exploration only; avoid writes |
| Routine data object management | Terraform/Pulumi | Do not use |

## Required environment

Expected project:

```bash
export GOOGLE_CLOUD_PROJECT=shared-datasets-1
```

Expected bucket:

```bash
export SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
```

Local authentication:

```bash
gcloud auth application-default login
gcloud config set project shared-datasets-1
```

CI/runtime authentication:

- Prefer Workload Identity Federation, Cloud Run service accounts, or other managed identity.
- Never commit service account JSON keys.
- Never print access tokens or signed URLs into logs unless explicitly required.

## Install local dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Safe object operation model

Cloud Storage objects have immutable `generation` values. To safely replace an object:

1. Read object metadata.
2. Capture its current `generation`.
3. Upload the replacement with `if_generation_match=<that_generation>`.
4. Verify the new object and generation.

For new objects, use `if_generation_match=0`, which succeeds only if no live object exists at that path.

Never perform a blind overwrite unless the user explicitly asks for an unsafe overwrite or you are operating in a scratch path.

## Core commands

List remote prefix:

```bash
python scripts/gcs_asset.py list gs://$SHARED_DATASETS_BUCKET/100-geographic-reference/
```

Inspect object metadata:

```bash
python scripts/gcs_asset.py stat gs://$SHARED_DATASETS_BUCKET/README.md
```

Download object:

```bash
python scripts/gcs_asset.py download gs://$SHARED_DATASETS_BUCKET/README.md /tmp/shared-datasets-README.md
```

Upload new object without clobbering:

```bash
python scripts/gcs_asset.py upload ./wdpa.fgb gs://$SHARED_DATASETS_BUCKET/100-geographic-reference/130-protected-areas/wdpa/latest/wdpa.fgb
```

Replace existing object safely:

```bash
python scripts/gcs_asset.py stat gs://$SHARED_DATASETS_BUCKET/path/to/README.md
python scripts/gcs_asset.py upload ./README.md gs://$SHARED_DATASETS_BUCKET/path/to/README.md --replace-generation 123456789
```

Unsafe overwrite, only when explicitly approved:

```bash
python scripts/gcs_asset.py upload ./README.md gs://$SHARED_DATASETS_BUCKET/path/to/README.md --unsafe-overwrite
```

Copy remote object without clobbering destination:

```bash
python scripts/gcs_asset.py copy gs://$SHARED_DATASETS_BUCKET/src/file.fgb gs://$SHARED_DATASETS_BUCKET/dst/file.fgb
```

## Editing a remote README

Use this workflow:

```bash
URI=gs://$SHARED_DATASETS_BUCKET/100-geographic-reference/130-protected-areas/wdpa/README.md
python scripts/gcs_asset.py stat "$URI"
python scripts/gcs_asset.py download "$URI" /tmp/wdpa.README.md
# edit /tmp/wdpa.README.md
python scripts/gcs_asset.py upload /tmp/wdpa.README.md "$URI" --replace-generation <generation-from-stat>
python scripts/gcs_asset.py stat "$URI"
```

After editing, ensure the repo-side catalog is updated if owner/source/license/cadence/canonical path changed.

## Adding a new dataset

1. Pick category/subcategory using `AGENTS.md`.
2. Pick an asset slug in lowercase kebab-case.
3. Create local asset files using approved formats.
4. Create `README.md` from `templates/dataset_README.template.md` or the minimal template.
5. Upload to `latest/` with no-clobber behavior.
6. If versioned, upload to `releases/YYYY-MM-DD/` with no-clobber behavior.
7. Update `catalog/shared-datasets-catalog.csv`.
8. Verify remote paths.
9. In the PR/final response, list all remote paths changed.

Example:

```bash
ASSET_ROOT=gs://$SHARED_DATASETS_BUCKET/300-infrastructure-industrial/330-offshore-platforms/offshore-platforms
python scripts/gcs_asset.py upload ./offshore-platforms.fgb     $ASSET_ROOT/latest/offshore-platforms.fgb
python scripts/gcs_asset.py upload ./offshore-platforms.pmtiles $ASSET_ROOT/latest/offshore-platforms.pmtiles
python scripts/gcs_asset.py upload ./README.md                 $ASSET_ROOT/README.md
```

## Updating a versioned dataset

Preferred order:

```bash
ASSET_ROOT=gs://$SHARED_DATASETS_BUCKET/400-events-observations/420-flaring-thermal-events/viirs-flares
RELEASE=2026-04-29

python scripts/gcs_asset.py upload ./viirs-flares.csv     $ASSET_ROOT/releases/$RELEASE/viirs-flares.csv
python scripts/gcs_asset.py upload ./viirs-flares.geojson $ASSET_ROOT/releases/$RELEASE/viirs-flares.geojson

# After validation, replace latest safely.
python scripts/gcs_asset.py stat $ASSET_ROOT/latest/viirs-flares.csv
python scripts/gcs_asset.py upload ./viirs-flares.csv $ASSET_ROOT/latest/viirs-flares.csv --replace-generation <generation>
```

If `latest/` does not exist yet, upload without `--replace-generation`; the CLI will use no-clobber behavior by default.

## Designing cron jobs that write assets

Cron jobs must be idempotent.

Required behavior:

1. Determine deterministic release path.
2. If that release already exists and is valid, exit success.
3. Generate outputs in local temp or work prefix.
4. Validate outputs.
5. Upload `releases/YYYY-MM-DD/` with no-clobber behavior.
6. Update `latest/` after release upload succeeds.
7. Write `runs/YYYY-MM-DD.json`.
8. Leave previous `latest/` untouched on failure.

Do not let a retry corrupt or delete a successful previous release.

## When `gcloud storage` is acceptable

Use `gcloud storage` for:

- Quick listing.
- Manual inspection.
- Emergency copy/download.
- Debugging authentication.
- Comparing behavior with the Python CLI.

Examples:

```bash
gcloud storage ls gs://$SHARED_DATASETS_BUCKET/
gcloud storage cp gs://$SHARED_DATASETS_BUCKET/README.md /tmp/README.md
gcloud storage cp ./README.md gs://$SHARED_DATASETS_BUCKET/README.md --if-generation-match=<generation>
```

Do not use ad hoc `gcloud storage cp` commands as hidden production automation when a repo script/job should exist.

## When Cloud Storage FUSE is acceptable

Cloud Storage FUSE is acceptable for read-heavy local exploration when an application expects filesystem paths.

Do not use it for:

- Canonical writes.
- Concurrent writes.
- Metadata-sensitive uploads.
- Git repositories.
- Database-like workloads.
- Anything that needs atomic patching or POSIX locking.

## Validation before upload

Before uploading, check:

- Path follows the bucket taxonomy.
- Asset slug is lowercase kebab-case.
- Format is approved.
- CSV has no geometry.
- README exists for dataset roots.
- File is not accidentally huge for `.geojson` previews.
- Release path is dated if cron/versioned.
- Existing destination object is not overwritten blindly.

## Validation after upload

After uploading, verify:

- Object exists.
- Object size is nonzero.
- Generation changed or was created.
- Content type is reasonable.
- README/catalog references correct remote path.
- For releases, `latest/` points to or contains the intended latest data.

## Failure handling

If an upload fails due to `412 Precondition Failed`:

1. Stop.
2. Re-run `stat` on the destination object.
3. Compare the current remote generation with the expected generation.
4. Determine whether another process updated the object.
5. Do not retry as unsafe overwrite unless explicitly approved.

If a cron job fails after writing a release but before updating `latest/`:

- Keep the release if it is valid.
- Write or update run status as failed/partial if possible.
- Next retry should detect existing files and continue safely.

## Never do this

```bash
# Bad: blind overwrite of canonical object.
gcloud storage cp ./new.fgb gs://$SHARED_DATASETS_BUCKET/path/latest/asset.fgb

# Bad: mount bucket and edit canonical README in-place.
gcsfuse skytruth-shared-datasets-1 ./mnt
vim ./mnt/path/README.md

# Bad: manage frequently updated data file as Terraform resource.
resource "google_storage_bucket_object" "latest_dataset" { ... }
```

## Official references

- Cloud Storage request preconditions: https://cloud.google.com/storage/docs/request-preconditions
- Google Cloud Python authentication / ADC: https://cloud.google.com/docs/authentication/application-default-credentials
- `gcloud storage cp`: https://cloud.google.com/sdk/gcloud/reference/storage/cp
- Cloud Storage FUSE overview and limitations: https://cloud.google.com/storage/docs/cloud-storage-fuse/overview
- Cloud Storage soft delete: https://cloud.google.com/storage/docs/soft-delete
- Cloud Storage object versioning: https://cloud.google.com/storage/docs/object-versioning
