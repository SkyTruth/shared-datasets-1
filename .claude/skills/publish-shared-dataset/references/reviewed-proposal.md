# Reviewed production proposal

Read for canonical first uploads, updates, and replacement recovery proposals.
Set `WORK_ROOT="${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}"`;
keep artifacts, reports, and the PR body in a named task child `WORK_DIR` there.
`SLUG`, `PROPOSAL_ID`, `RELEASE_DATE`, `PUBLISH_DIR`, and destination URIs come
from the concierge state or the verified existing asset contract, never guesses.

## Prepare and validate

Create or update `docs/assets/{asset-slug}.md`; use the dataset README template
and [artifact/metadata contract](artifacts-and-metadata.md) when changing it.
The asset doc owns catalog metadata and bucket README content. Generate outputs:

```bash
uv run python scripts/catalog_docs.py generate
uv run python scripts/catalog_docs.py check
uv run python scripts/catalog_docs.py export-readmes --output-dir "$WORK_DIR/readmes"
uv run python scripts/catalog_site.py --out "$WORK_DIR/catalog-web"
```

Successful `check` exits zero; exported README paths mirror bucket paths. Inspect
`catalog.json.assets[]` for the affected slug and its `versions[].files`.
For canonical vectors/tables, check schema compatibility before staging:

```bash
uv run python scripts/dataset_alerts.py check-schema-compatibility --help
uv run python scripts/dataset_alerts.py check-schema-compatibility --asset-slug "$SLUG" --dataset-path "$CANONICAL_FILE"
```

The JSON report contains `compatible`, `diffs`, `blocked_diffs`, and `waiver`.
A failure needs review; do not assert compatibility or silently invent a waiver.
Do not edit `catalog/shared-datasets-catalog.csv` directly for normal asset
metadata changes.

## Stage and record

Load `gcp-shared-datasets` before remote inspection or staging. For each candidate:

```bash
uv run python scripts/gcs_asset.py validate-path "$DESTINATION_URI"
uv run python scripts/gcs_asset.py upload "$LOCAL_FILE" "$SCRATCH_URI"
uv run python scripts/gcs_asset.py stat "$SCRATCH_URI"
uv run python scripts/gcs_asset.py stat "$DESTINATION_URI"
```

`SCRATCH_URI` is under `_scratch/pending-publishes/{asset-slug}/{proposal-id}/`.
Upload defaults to no-clobber. Use upload `--help` for content/cache metadata;
PMTiles need `application/vnd.pmtiles` and no-cache revalidation. Record `uri`,
`generation`, `size`, and `crc32c` from stat, with local validation evidence.
A destination generation is the observed numeric string; use an empty string
only for a confirmed absent destination. Authentication or network errors do not
prove absence. Order release objects before latest copies, then run/index
metadata. Include companions, exported READMEs and any reviewed index repairs.

For repo-generated catalog changes, the protected `catalog-web-deploy.yml`
refreshes `_catalog/web/` after the approved mutation. Do not also promote
`_catalog/web/catalog.json` from the same PR: duplicate writers can race.
Build it locally for concierge/runtime validation, and record the automatic
refresh path. For a manual catalog-only proposal that will not trigger that
workflow, load `static-catalog-web-preview` and include the staged catalog with
`cache_control: no-cache, max-age=0, must-revalidate` and observed generation.
Corrective PMTiles-only repairs replace the matching dated/latest PMTiles;
never create a PMTiles-only release unless PMTiles is the canonical format.

First uploads use concierge `render-pr`. Existing/manual/combined plans use:

```bash
uv run python scripts/reviewed_dataset_plan.py prepare --publish "$PUBLISH_PLAN" > "$WORK_DIR/pr-body.md"
```

The command prints a body with matching fences and writes
`.github/dataset-plans/{asset}/{proposal}/{sha256}.json`. Add exactly one
immutable document and its matching fence. See the [plan contract](../../../../.github/dataset-plans/README.md)
only for specialized payloads, migration or executor/receipt questions.
Include files changed, source/destination URIs and generations, source version,
validation, admission for new assets, consumer impact and unresolved assumptions.

## Git and review

Create a focused branch and stage only related files. Load
`repo-alert-commit-messages` before committing. With the final head committed,
read `docs/ci-preflight.md` and `docs/agent-integration.md`, freeze the clean
checkout, then run:

```bash
git fetch origin main
uv run python scripts/ci_preflight.py --base "$MAIN_SHA" --head "$HEAD_SHA"
git push -u origin "$BRANCH"
gh pr create --base main --head "$BRANCH" --title "$TITLE" --body-file "$WORK_DIR/pr-body.md"
```

Resolve `MAIN_SHA=git rev-parse origin/main` and `HEAD_SHA=git rev-parse HEAD`
after any required integration, before preflight. Passing evidence must match
those exact revisions. Request `jonaraphael` review when the author differs;
when the author is `jonaraphael`, record GitHub's self-review block and restricted
self-acceptance in the body instead of issuing a known-failing reviewer request.

## Protected authorization

Do not promote canonical objects from the local terminal. After a same-repo
PR containing the publish-plan block is merged to `main`, the GitHub
`Approved dataset mutation` workflow verifies `jonaraphael` acceptance and
promotes the listed staged objects under the `shared-datasets-production`
environment. If GitHub blocks self-review because `jonaraphael` authored the
PR, the merged PR is treated as restricted self-acceptance. Promotion still
requires that explicit PR; do not use standalone workflow dispatch or
single-object fallback inputs to bypass the PR.
If the asset doc changes `access_tier`, rely on the protected
`catalog-web-deploy` and `pmtiles-cdn-sync` workflows after merge to update
`_catalog/web`, PMTiles CDN routes, and public managed-folder IAM. Do not
run local production Terraform apply for access-tier changes.

## After merge

Observe the protected workflow to terminal success, stat each changed object,
verify generations/metadata, release-index `latest_release`/`latest_run`, and
catalog freshness. A reviewable proposal is not a completed remote publication.
Report local work directories, paths, validation and any unverified state.
Dataset upload announcements are operational notifications, not Git commit gates.
Do not infer announcement state solely from commit history, and do not block a
requested commit to send or verify an announcement. Report sent/skipped/uncertain;
do not send duplicate Slack notifications unless the human explicitly asks.
