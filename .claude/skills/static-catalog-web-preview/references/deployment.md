# Catalog deployment

UI-only PRs also deploy automatically after their complete `main` CI run succeeds.
The catalog workflow verifies the exact CI revision and retained comparison plan,
requires merged same-repository PRs covering the changed catalog inputs, and
selects `web/catalog/`, asset docs, catalog CSV/taxonomy, the catalog builder and
its publication/selection helpers. Unrelated changes do not allocate publisher
credentials. If the same push includes an immutable dataset mutation plan, its
publication workflow owns the refresh instead, so there is no duplicate deploy.
Automatic refreshes retain the protected publisher, serialized signed receipts,
generation-preconditioned writes, live verification and no-cache metadata.

After trusted PRs merge to `main`, the post-merge dataset mutation workflow
runs first, including finalization of prepared translation bundles. When it
succeeds, `.github/workflows/catalog-web-deploy.yml` downloads existing
`_catalog/releases/*.json` release indexes, rebuilds the catalog web bundle with
release-index-backed latest/version fields, and publishes both `_catalog/web/`
and the root `_catalog/shared-datasets-catalog.csv` contract with the approved
publisher identity, generation preconditions, and
`no-cache, max-age=0, must-revalidate`. This workflow is the normal PR-backed
promotion path for repo-generated catalog web changes and the automatic refresh
path after approved canonical data mutations. The protected PMTiles CDN sync
workflow starts after catalog web deploy completes when the merged change
touches catalog or asset documentation inputs that can affect PMTiles routes or
public managed-folder IAM grants.
The protected
`.github/workflows/catalog-viewer-deploy.yml` workflow also starts after a
successful `Approved dataset mutation` workflow so the IAP-protected viewer is
rebuilt and reapplied through the production environment. Do not also add a
`shared-datasets-publish-plan` for `_catalog/web/catalog.json` when the catalog
web deploy workflow will run from the same PR; that duplicates the promotion and
can race the automatic deploy. The live catalog drift guard runs after that
deploy workflow completes, using the same release-index hydration, so it does
not race the object promotion and still detects stale release history.

Use `scripts/gcs_asset.py` for every object write so generation preconditions are
explicit. Manual catalog web deployments should stage files under
`_scratch/pending-publishes/catalog-web/{proposal-id}/` and promote approved
objects only through an explicit PR with a fenced publish plan. After that PR
merges, the `Approved dataset mutation` GitHub workflow applies the plan. Do not
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
