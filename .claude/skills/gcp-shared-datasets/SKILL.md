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

Use `gcloud storage` for human diagnostics, emergency downloads, documented
break-glass operations, or the narrow scratch-transfer recovery below.

Do not use Terraform, Pulumi, or Cloud Storage FUSE for routine canonical
dataset uploads/edits. Do not perform canonical writes from a local human or
agent terminal; stage manual publish bytes under `_scratch/pending-publishes/`
and promote approved objects only through an explicit PR with a checked-in
immutable plan document and matching readable publish/delete fences. Use
`publishing_concierge.py render-pr` or `reviewed_dataset_plan.py prepare`; see
`.github/dataset-plans/README.md`. PR-body edits never change execution authority. After that PR merges, the GitHub `Data / Approved mutation`
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

Application Default Credentials and the CLI's saved login are separate. Check
which credential source the failing tool uses before asking for reauthentication.
Reuse an authorized working identity without changing global accounts or
permissions. If a supported temporary access-token file is needed, never print
or commit it, restrict it to mode `0600`, and remove it after use.

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

## Preview Bucket Writes

For `gs://skytruth-shared-datasets-1-preview/`, follow the same generation-safe
object model in this skill, but route dataset-intake decisions through
`.claude/skills/feature-preview/SKILL.md`.

If a preview upload request involves dataset bytes, do not proceed directly from
path validation to `scripts/gcs_asset.py upload`. First confirm the
feature-preview Preview Concierge Gate has produced the planned object list,
artifact roles, content types, validations, and destination URIs. Raw
single-object preview uploads are allowed only when the human explicitly asks
for scratch-only or diagnostic staging and no preview release/load is being
claimed.

Do not skip the preview concierge gate because the asset slug already exists in
`catalog/shared-datasets-catalog.csv`, because the supplied file is already an
approved format such as FGB, or because the preview destination seems obvious.
Existing catalog metadata may prefill decisions, but it does not replace the
structured preview-bundle checklist.

## Procedure and reports

For ordinary staging, use only these commands; flags live in their help:

```bash
uv run python scripts/gcs_asset.py validate-path "$DESTINATION_URI"
uv run python scripts/gcs_asset.py upload "$LOCAL_FILE" "$SCRATCH_URI"
uv run python scripts/gcs_asset.py stat "$SCRATCH_URI"
uv run python scripts/gcs_asset.py stat "$DESTINATION_URI"
```

`validate-path` reports `valid`/`errors`; a failed exit blocks staging.
`upload` prints the resulting URI, generation and size. `stat` reports `uri`,
`generation`, `size`, `crc32c`, `content_type`, and object metadata. Compare
remote size/CRC32C to validated local bytes; a stored checksum label alone is
not independent integrity proof. Not-found is distinct from network/auth errors.
Record the observed generation, or confirmed absence for no-clobber creation.

For text editing, large-transfer recovery, diagnostics, FUSE limitations or a
412 failure, read [object operations](references/object-operations.md).
For standing scratch abandonment policy, read [scratch cleanup](references/scratch-cleanup.md).
For partial approved publication, use the publish skill's recovery route.
Vector artifacts and identity decisions belong to the publish skill's
[artifact contract](../publish-shared-dataset/references/artifacts-and-metadata.md).
