---
name: gcp-shared-datasets
description: Use before inspecting, downloading, uploading, editing, replacing, deleting, publishing, or validating objects in the shared SkyTruth GCS dataset bucket. Focuses on safe Cloud Storage object operations and generation preconditions.
---

# GCP Shared Datasets

Use this skill for remote object safety in
`gs://skytruth-shared-datasets-1/`. For manual dataset add/update workflows,
also load `publish-shared-dataset`. For scheduled ingestion deployment, also
load `deploy-scheduled-ingestion`.

## Decision

Use the repo-owned Python CLI/library built on `google-cloud-storage` for
dataset object operations.

Use Terraform for infrastructure, but route production Terraform mutations
through reviewed PRs merged to `main` and protected GitHub Actions workflows.
Load `.claude/skills/protected-terraform-apply/SKILL.md` before suggesting,
planning, documenting, or running any production Terraform apply.

Use `gcloud storage` only for human diagnostics, emergency downloads, and
documented break-glass operations.

Do not use Terraform, Pulumi, or Cloud Storage FUSE for routine canonical
dataset uploads/edits. Do not perform canonical writes from a local human or
agent terminal; stage manual publish bytes under `_scratch/pending-publishes/`
and promote approved objects only through an explicit PR with a fenced publish
or delete plan. After that PR merges, the GitHub `Approved dataset mutation`
workflow runs under the `shared-datasets-production` environment. Do not use
standalone workflow dispatch or single-object fallback inputs to bypass a PR.

## Required Environment

Expected project:

```bash
export GOOGLE_CLOUD_PROJECT=shared-datasets-1
```

Expected bucket:

```bash
export SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
```

Use `uv run` for repo-owned Python commands. Do not create ad hoc pip virtualenvs
or mamba environments for routine GCS object operations.

Local downloads and edit copies must follow
`docs/standards/local-temp-workspaces.md`. Use the repo temp root
`${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}` and a named
child such as `downloads/{asset-slug}/`; do not scatter files directly under
`/tmp`.

Local authentication:

```bash
gcloud auth application-default login
gcloud config set project shared-datasets-1
```

CI/runtime authentication:

- Prefer Workload Identity Federation, Cloud Run service accounts, or other
  managed identity.
- Never commit service account JSON keys.
- Never print access tokens or signed URLs into logs unless explicitly required.

## Safe Object Model

Cloud Storage objects have immutable `generation` values. To safely replace an
object:

1. Read object metadata.
2. Capture its current `generation`.
3. Upload the replacement with `if_generation_match=<that_generation>`, exposed
   by `scripts/gcs_asset.py` as `--replace-generation`.
4. Verify the new object and generation.

For new objects, use `if_generation_match=0`, exposed by the CLI's default
no-clobber upload behavior.

Never perform a blind overwrite unless the user explicitly asks for an unsafe
overwrite and you are operating in `_scratch/`.

Canonical `latest/`, `releases/`, dataset README, and `_catalog/` writes must
use the approved publisher identity or a scheduled ingestion service account
scoped to its owned asset root. `_scratch/` is noncanonical staging space and
must not be cited as a stable shared dataset contract.

## Core Commands

List remote prefix:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py list \
  gs://$SHARED_DATASETS_BUCKET/100-geographic-reference/
```

Inspect object metadata:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat \
  gs://$SHARED_DATASETS_BUCKET/README.md
```

Download object:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py download \
  gs://$SHARED_DATASETS_BUCKET/README.md \
  "$TMPDIR/shared-datasets-1/downloads/root-README.md"
```

Upload a new object without clobbering:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload \
  ./asset.fgb gs://$SHARED_DATASETS_BUCKET/_scratch/pending-publishes/example-asset/123/asset.fgb
```

Replace an existing object safely:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat \
  gs://$SHARED_DATASETS_BUCKET/path/to/README.md
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload \
  ./README.md gs://$SHARED_DATASETS_BUCKET/path/to/README.md \
  --replace-generation 123456789
```

The replacement example is for the approved publisher identity or documented
break-glass use. Local agents should stage under `_scratch/pending-publishes/`
instead.

Copy a remote object without clobbering the destination:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py copy \
  gs://$SHARED_DATASETS_BUCKET/src/file.fgb \
  gs://$SHARED_DATASETS_BUCKET/dst/file.fgb
```

Unsafe overwrite, only when explicitly approved:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload \
  ./README.md gs://$SHARED_DATASETS_BUCKET/path/to/README.md \
  --unsafe-overwrite
```

Delete only with an explicit generation and confirmation. For canonical objects,
the normal path is a reviewed PR with a fenced `shared-datasets-delete-plan`;
the command below is for the approved workflow, approved publisher identity, or
documented break-glass use:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py delete \
  gs://$SHARED_DATASETS_BUCKET/path/to/object --generation 123456789 \
  --confirm DELETE
```

## Editing Remote Text Files

Use this read-modify-write pattern for remote README or metadata edits only
under the approved publisher identity or documented break-glass path. Local
agents should download/read for inspection, stage edited bytes under
`_scratch/pending-publishes/`, and promote through an explicit PR:

```bash
URI=gs://$SHARED_DATASETS_BUCKET/path/to/README.md
EDIT_DIR="$TMPDIR/shared-datasets-1/downloads/example-asset"
mkdir -p "$EDIT_DIR"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat "$URI"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py download "$URI" "$EDIT_DIR/README.md"
# edit "$EDIT_DIR/README.md"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload \
  "$EDIT_DIR/README.md" "$URI" --replace-generation <generation-from-stat>
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat "$URI"
```

If the edit changes owner, source, license, citation, cadence, canonical path,
available formats, schema, or update notes, update
`docs/assets/{asset-slug}.md` and run:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py generate
UV_CACHE_DIR=.uv-cache uv run python scripts/catalog_docs.py check
```

Do not edit `catalog/shared-datasets-catalog.csv` directly for normal asset
metadata changes.

## `gcloud storage`

`gcloud storage` is acceptable for:

- Quick listing.
- Manual inspection.
- Emergency download.
- Debugging authentication.
- Comparing behavior with the Python CLI.

Examples:

```bash
gcloud storage ls gs://$SHARED_DATASETS_BUCKET/
gcloud storage cp gs://$SHARED_DATASETS_BUCKET/README.md \
  "$TMPDIR/shared-datasets-1/downloads/root-README.md"
```

Do not use ad hoc `gcloud storage cp` commands as hidden production automation
when a repo script/job should exist. Do not use local `gcloud storage cp` for
canonical writes; stage manual bytes under `_scratch/pending-publishes/` and
promote them only through an explicit PR and the approved publisher workflow
after merge. For documented break-glass or approved publisher-identity work,
prefer `scripts/gcs_asset.py` so generation preconditions and metadata are
explicit.

## Cloud Storage FUSE

Cloud Storage FUSE is acceptable only for read-heavy local exploration when an
application expects filesystem paths.

Do not use it for:

- Canonical writes.
- Concurrent writes.
- Metadata-sensitive uploads.
- Git repositories.
- Database-like workloads.
- Anything that needs atomic patching or POSIX locking.

## Validation

Before uploading, check:

- Path follows the taxonomy and asset layout.
- Asset slug is lowercase kebab-case.
- Format is approved by `docs/standards/asset-layout-and-formats.md`.
- CSV files do not contain canonical geometry.
- README exists for dataset roots.
- Release path is dated if cron/versioned.
- Existing destination object is not overwritten blindly.

After uploading, verify:

- Object exists.
- Object size is nonzero.
- Generation changed or was created.
- Content type is reasonable.
- README/catalog references correct remote path when metadata changed.
- For releases, `latest/` points to or contains the intended latest data.

## Failure Handling

If an upload fails due to `412 Precondition Failed`:

1. Stop.
2. Re-run `stat` on the destination object.
3. Compare the current remote generation with the expected generation.
4. Determine whether another process updated the object.
5. Do not retry as unsafe overwrite unless explicitly approved.

If a cron job fails after writing a release but before updating `latest/`:

- Keep the release if it is valid.
- Write or update run status as failed/partial if possible.
- Let the next retry detect existing files and continue safely.

## Never Do This

```bash
# Bad: blind overwrite of canonical object.
gcloud storage cp ./new.fgb gs://$SHARED_DATASETS_BUCKET/path/latest/asset.fgb

# Bad: mount bucket and edit canonical README in-place.
gcsfuse skytruth-shared-datasets-1 ./mnt
vim ./mnt/path/README.md

# Bad: manage frequently updated data file as Terraform resource.
resource "google_storage_bucket_object" "latest_dataset" { ... }
```

## Official References

- Cloud Storage request preconditions: https://cloud.google.com/storage/docs/request-preconditions
- Google Cloud Python authentication / ADC: https://cloud.google.com/docs/authentication/application-default-credentials
- `gcloud storage cp`: https://cloud.google.com/sdk/gcloud/reference/storage/cp
- Cloud Storage FUSE overview and limitations: https://cloud.google.com/storage/docs/cloud-storage-fuse/overview
- Cloud Storage soft delete: https://cloud.google.com/storage/docs/soft-delete
- Cloud Storage object versioning: https://cloud.google.com/storage/docs/object-versioning
