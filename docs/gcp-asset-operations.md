# GCP asset operations

This document summarizes how this repo expects maintainers and AI agents to inspect, upload, edit, and publish files in the shared datasets bucket.

For the full operational procedure, read `skills/gcp-shared-datasets/SKILL.md`.

## Chosen approach

Use a small repo-owned Python CLI/library built on `google-cloud-storage` for data object operations.

Use `uv` for local Python dependency management and command execution.

Use Terraform for GCP infrastructure.

Use `gcloud storage` only for manual diagnostics or emergency one-off operations.

Do not use Terraform or Pulumi to manage frequently changing dataset files.

Do not use Cloud Storage FUSE for canonical writes.

## Why

Remote dataset files need safe, repeatable object mutations. GCS generation preconditions let us prevent accidental overwrites. A Python CLI can make those safety checks the default and can also enforce SkyTruth naming/path conventions.

## Standard commands

```bash
uv run python scripts/gcs_asset.py list gs://$SHARED_DATASETS_BUCKET/
uv run python scripts/gcs_asset.py stat gs://$SHARED_DATASETS_BUCKET/path/to/object
uv run python scripts/gcs_asset.py download gs://$SHARED_DATASETS_BUCKET/path/to/object /tmp/object
uv run python scripts/gcs_asset.py upload ./local-file gs://$SHARED_DATASETS_BUCKET/path/to/new-object
uv run python scripts/gcs_asset.py upload ./local-file gs://$SHARED_DATASETS_BUCKET/path/to/existing-object --replace-generation <generation>
```

## Safe update pattern

1. `stat` destination object.
2. Download it if editing.
3. Make local changes.
4. Upload with `--replace-generation`.
5. Verify with `stat`.
6. Update README/catalog when relevant.

## New object pattern

By default, `upload` uses no-clobber behavior and fails if a live object already exists.

For dataset roots, create or update the adjacent `README.md` with enough schema detail for a consumer to inspect the asset without opening the full data file. Where possible, include a properties/columns table with field names, types, and short explanations. If field meanings are not available, list names/types and mark definitions as needing source confirmation.

## Unsafe overwrites

Only use `--unsafe-overwrite` when the user explicitly requests it or when operating under `_scratch/`.

Record unsafe overwrites in the PR or final response.
