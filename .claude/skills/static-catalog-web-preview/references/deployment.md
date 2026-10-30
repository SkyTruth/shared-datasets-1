# Catalog deployment

UI-only PRs deploy automatically within their `main` CI run after `ci-ready`
passes. The existing delivery-selection job checks the exact push range and
requires merged same-repository PRs covering every changed catalog input:
`web/catalog/`, asset docs, catalog CSV/taxonomy, the catalog builder and its
publication/selection helpers. It calls the reusable catalog publisher only
when selected, avoiding a separate workflow and completion event for each main
CI run. Selected PMTiles CDN route/access updates must succeed first. Unrelated
changes do not allocate publisher credentials. If the push contains an immutable
dataset mutation plan, the existing approved publication path owns the refresh
and ordinary catalog selection is skipped.

The protected publisher independently verifies the exact current CI run and
attempt, passing `ci-ready`, and tested executor. Ordinary refreshes require the
trusted main CI push; mutation refreshes retain complete immutable authorization
verification. Required reusable inputs, serialized signed receipts,
generation-preconditioned writes, live verification and no-cache metadata remain
unchanged. A catalog selection failure fails delivery selection; publisher
failures remain visible through the parent CI completion and the same catalog
incident target. Successful wrapper jobs cannot establish recovery.

An hourly read-only probe at minute 17 compares a generation-pinned release-index
snapshot and repo metadata with the published CSV and catalog JSON, using the
same comparison as live publication acceptance. It ignores `generated_at` alone.
Only changed catalog data selects the protected deployment job; idle probes skip
its tests, signing, deployment records and publication. Repo changes and reviewed
dataset mutations retain their existing event-driven refresh paths.

The probe uses the existing protected publisher identity solely for GCS reads;
its GitHub permissions allow only contents reads and OIDC authentication. It has
no deployment or attestation write permissions and adds no IAM grants. Failed
reads or malformed snapshots fail the probe without producing a selection.
When this scheduled probe is the only attempted work and the publisher was
skipped, its result stays in Actions and creates no Slack deployment incident.
Failures of an allocated publisher job still alert. This does not resolve an
existing incident or prove a successful deployment.

Selected deployments still require passing `ci-ready` for the scheduled main
revision. The existing bundle digest skips publication when the same revision
and release-index snapshot are already deployed; changed indexes can refresh the
same revision. Pending differences are checked again on the next hourly probe;
this gate is not a retry budget or a replacement for failed-deployment recovery.
No ingestion IAM or dataset writes are added.

After trusted PRs merge to `main`, the post-merge dataset mutation workflow
runs first, including finalization of prepared translation bundles. When it
succeeds, `.github/workflows/catalog-web-deploy.yml` downloads existing
`_catalog/releases/*.json` release indexes, rebuilds the catalog web bundle with
release-index-backed latest/version fields, and publishes both `_catalog/web/`
and the root `_catalog/shared-datasets-catalog.csv` contract with the approved
publisher identity, generation preconditions, and
`no-cache, max-age=0, must-revalidate`. This workflow is the normal PR-backed
promotion path for repo-generated catalog web changes and the automatic refresh
path after approved canonical data mutations. The protected `Catalog / PMTiles CDN sync`
workflow starts after catalog web deploy completes when the merged change
touches catalog or asset documentation inputs that can affect PMTiles routes or
public managed-folder IAM grants.
The protected
`.github/workflows/catalog-viewer-deploy.yml` workflow also starts after a
successful `Data / Approved mutation` workflow so the IAP-protected viewer is
rebuilt and reapplied through the production environment. Do not also add a
`shared-datasets-publish-plan` for `_catalog/web/catalog.json` when the catalog
web deploy workflow will run from the same PR; that duplicates the promotion and
can race the automatic deploy. The publish job runs the live catalog drift
guard using its exact release-index snapshot before recording success. The
standalone guard retains manual live checks on trusted `main`. Local catalog
consistency and site builds run without cloud credentials in the existing CI
`tests` suite, including documentation-only changes. Skipped deployments do not
launch a second live comparison against newer indexes.

Use `scripts/gcs_asset.py` for every object write so generation preconditions are
explicit. Manual catalog web deployments should stage files under
`_scratch/pending-publishes/catalog-web/{proposal-id}/` and promote approved
objects only through an explicit PR with a fenced publish plan. After that PR
merges, the `Data / Approved mutation` GitHub workflow applies the plan. Do not
use standalone workflow dispatch or single-object fallback inputs for catalog
refreshes. New files should use no-clobber promotion. Existing files should be
replaced only after reading the current generation. Set the publish-plan
`cache_control` field for `catalog.json` and any other cache-sensitive
replacement that needs no-cache metadata.

Publisher-identity CLI reference only:

```bash
WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"
OUT="$WORK_ROOT/catalog-web"
DEST=gs://skytruth-shared-datasets-1/_catalog/web

UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload "$OUT/index.html" "$DEST/index.html"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload "$OUT/styles.css" "$DEST/styles.css"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload "$OUT/app.js" "$DEST/app.js"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload "$OUT/map-preview.js" "$DEST/map-preview.js"
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py upload "$OUT/catalog.json" "$DEST/catalog.json"
```

Humans and agents should stage these files under `_scratch/pending-publishes/`
and use the approved workflow instead of running the publisher-identity commands
locally. For replacements, stat each object first and use the returned
generation as the workflow `destination_generation` precondition:

```bash
UV_CACHE_DIR=.uv-cache uv run python scripts/gcs_asset.py stat "$DEST/catalog.json"
```

Repeat for changed static files and copied docs. Do not use unsafe overwrites for
catalog deployment.

Keep the live web shell and runtime contract revalidating on every request. The
catalog app also appends cache-busting query strings to `catalog.json`, docs
Markdown, and PMTiles URLs, but the object metadata should not invite a browser
or CDN to hold stale catalog or tile bytes after a same-path replacement. Pass
the publish-plan `cache_control` field with
`no-cache, max-age=0, must-revalidate` for `catalog.json`, web shell/runtime
objects, and same-path PMTiles replacements that need no-cache metadata.

Corrective PMTiles rebuilds should replace both `latest/*.pmtiles` and the
matching dated release PMTiles object for the canonical dataset release. Do not
create PMTiles-only dated release directories for display repairs unless
PMTiles is the asset's canonical format; release indexes and catalog versions
should represent release dates that include the canonical format.

## Pre-launch hygiene

Run the read-only compliance audit before publishing the catalog and after
deployment:

```bash
UV_CACHE_DIR=.uv-cache GOOGLE_CLOUD_PROJECT=shared-datasets-1 SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1 \
  uv run python .claude/skills/shared-datasets-compliance-audit/scripts/audit_shared_datasets.py
```

Resolve visible hygiene issues before announcing the catalog, especially stray
desktop files, missing asset READMEs, stale bucket-side catalog objects, and
README sections required by `docs/standards/asset-layout-and-formats.md`.
