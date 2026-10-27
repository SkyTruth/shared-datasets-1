# Object operations

Read for text edits, transfer recovery, diagnostics or failure handling.

## Core commands

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
- The documented scratch-transfer recovery below.

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

## Large Scratch Transfers

Preserve validated artifacts when transport fails. Measure transferred bytes and
throughput separately from local file reads; a file offset at EOF is not evidence
that an upload is complete. After an unknown outcome, inspect the exact remote
destination and reuse it only after verification, recording its generation.

For user-authorized manual staging of validated bytes under
`_scratch/pending-publishes/`, a one-off retry may use supported chunk/timeout
controls or `gcloud storage`. This is transport recovery, not canonical write
authority or an alternative production publishing path. For compressible text,
consider `--gzip-in-flight` after verifying current support; retain the original
stored bytes, object name, content type and expected SHA-256.

Use generation zero for a new object and the exact reviewed generation for an
authorized replacement. Disable parallel composite uploads unless their extra
objects and cleanup are explicitly planned. Verify remote size and CRC32C
against local bytes, plus content type, content encoding, metadata and generation.
Supplied SHA metadata alone is not independent integrity verification.

Resume the normal staging/publication workflow after verification. Retry only
the transfer; do not rebuild unchanged validated artifacts for network or
authentication failures.

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

## Failure Handling

If an upload fails due to `412 Precondition Failed`:

1. Stop.
2. Re-run `stat` on the destination object.
3. Compare the current remote generation with the expected generation.
4. Determine whether another process updated the object.
5. Do not retry as unsafe overwrite unless explicitly approved.

If the GitHub `Data / Approved mutation` workflow fails mid-promotion:

- Preserve the checked-in plan and captured authorization artifact.
- Stat every source at its recorded generation and every destination at its
  current generation; treat the bucket as changed state.
- Do not edit a merged PR body or refresh generation expectations during replay.
- `publishing_concierge.py refresh-retry-plan` may prepare an **unapproved**
  replacement payload. Changed expectations/waivers require a new immutable
  document and reviewed PR. CRC mismatch remains a reason for human review.
- Body-only merged plans cannot be retroactively authorized. Open legacy PRs
  need a checked-in document and approval on the resulting new head.
- An unchanged-plan retry keeps original expectations. Durable receipt-based
  recovery and executor compatibility must follow the publisher contract; never
  create another transaction to evade an existing proposal's expectations.

GCS and GitHub network diagnostics often need network access outside the local
sandbox. If a prior command in the same task has already failed or hung on
DNS/network access, rerun important `gcs_asset.py`, `gh`, or `gcloud` diagnostic
commands with the required approved network path instead of first repeating a
known-bad sandbox attempt.

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
