# shared-datasets-1

`shared-datasets-1` is the control-plane repository for SkyTruth's shared datasets bucket.

The bucket stores reusable data assets. This repository stores the code, infrastructure, protocols, templates, and automation that keep those assets organized, documented, refreshed, and easy to consume.

## What this repo is for

Use this repo for:

- Infrastructure-as-code for the shared datasets GCP project.
- Scheduled ingestion and refresh jobs.
- Lightweight APIs, access helpers, and client protocols.
- Dataset README templates and catalog conventions.
- Automation that uploads, validates, indexes, or republishes assets in the shared bucket.
- Operational guidance for humans and AI agents.

Do **not** use this repo for large data files. Large assets belong in Cloud Storage under the shared bucket.

## Source of truth

| Concern | Source of truth |
|---|---|
| Repo purpose and quick start | `README.md` |
| Agent/maintainer routing and safety rules | `AGENTS.md` |
| Claude Code shim | `CLAUDE.md` |
| Repo-local skill catalog | `.claude/skills/`, mirrored at `.agents/skills` |
| Manual dataset add/update/publish workflow | `.claude/skills/publish-shared-dataset/SKILL.md` |
| Remote GCS object safety and commands | `.claude/skills/gcp-shared-datasets/SKILL.md` |
| Scheduled ingestion deployment procedure | `.claude/skills/deploy-scheduled-ingestion/SKILL.md` |
| Feature preview deploys and test-data loads | `.claude/skills/feature-preview/SKILL.md`, `docs/feature-preview.md` |
| Python environment/tooling alignment | `.claude/skills/align-virtual-environment/SKILL.md` |
| Bucket/repo compliance walkthroughs | `.claude/skills/shared-datasets-compliance-audit/SKILL.md` |
| Invariant-first code/design simplification | `.claude/skills/invariant-first-engineering/SKILL.md` |
| Production Terraform apply safety | `.claude/skills/protected-terraform-apply/SKILL.md` |
| Repo-alert commit messages | `.claude/skills/repo-alert-commit-messages/SKILL.md` |
| Release feature metadata translations | `.claude/skills/update-feature-metadata-translations/SKILL.md` |
| Temporary local file/workspace hygiene | `docs/standards/local-temp-workspaces.md` |
| Dataset categories and bucket paths | `catalog/categories.yaml`, `docs/standards/dataset-taxonomy.md` |
| Asset layout, formats, and README requirements | `docs/standards/asset-layout-and-formats.md` |
| Dataset README templates | `templates/dataset_README.template.md`, `templates/dataset_README.minimal.template.md` |
| Static catalog web preview | `.claude/skills/static-catalog-web-preview/SKILL.md`, `docs/catalog-web-preview.md` |
| Code/docs alignment workflow | `.claude/skills/sync-docs-with-code/SKILL.md` |
| Consumer integration guide | `docs/consumer-guide.md` |
| Feature metadata lookup API | `docs/feature-metadata-api.md`, `services/catalog_viewer/`, `services/feature_preview_service/` |
| Feature branch preview | `.claude/skills/feature-preview/SKILL.md`, `docs/feature-preview.md`, `terraform/envs/preview/`, `Deploy Feature Branch to Preview`, `Destroy Preview Environment`, `Preview Terraform IAM sync` |
| Python SDK usage | `api/python/README.md` |
| TypeScript SDK usage and npm package contents | `api/typescript/README.md` |
| TypeScript SDK npm release workflow | `.github/workflows/publish-typescript-sdk.yml` |
| Tiered PMTiles browser access | `docs/pmtiles-cdn.md` |
| Infrastructure | `terraform/` |
| Ingestion jobs | `ingestion/` or `scripts/` |
| Access protocols / APIs | `api/python/`, `api/typescript/`, and `docs/` |

When instructions conflict, follow the **Authority Order** defined in
`AGENTS.md`; it is the single owner of that ordering.

## Core principles

1. **The bucket is a product.** Treat every shared dataset as something another SkyTruth project may depend on.
2. **Low overhead wins.** Prefer predictable paths, simple READMEs, and generated catalogs over heavy metadata processes.
3. **Stable paths beat clever names.** Someone should be able to guess where a dataset belongs before searching.
4. **Canonical data is boring.** Approved data formats are `.fgb`, COG `.tif`, `.zarr/`, `.pmtiles`, `.geojson`, `.ndgeojson`, and geometry-free `.csv`; release vector metadata sidecars use `.metadata.ndjson.gz`, `.schema.json`, and `.manifest.json`.
5. **Cron jobs must be safe to retry.** Scheduled jobs should be idempotent, skip unchanged assets without writing new dataset artifacts, and never destroy previous releases.
6. **Infrastructure and data are managed differently.** Terraform manages cloud resources. The Python GCS asset tooling manages data objects.
7. **Agents must leave things clearer than they found them.** Any remote asset change should update the relevant README/catalog when appropriate.

## Repository layout

```text
.
├── README.md
├── AGENTS.md
├── CLAUDE.md
├── .agents/
│   └── skills -> ../.claude/skills
├── .claude/
│   └── skills/
│       ├── align-virtual-environment/
│       │   └── SKILL.md
│       ├── deploy-scheduled-ingestion/
│       │   └── SKILL.md
│       ├── feature-preview/
│       │   └── SKILL.md
│       ├── gcp-shared-datasets/
│       │   └── SKILL.md
│       ├── invariant-first-engineering/
│       │   └── SKILL.md
│       ├── publish-shared-dataset/
│       │   └── SKILL.md
│       ├── protected-terraform-apply/
│       │   └── SKILL.md
│       ├── repo-alert-commit-messages/
│       │   └── SKILL.md
│       ├── shared-datasets-compliance-audit/
│       │   └── SKILL.md
│       ├── static-catalog-web-preview/
│       │   └── SKILL.md
│       ├── sync-docs-with-code/
│       │   └── SKILL.md
│       └── update-feature-metadata-translations/
│           └── SKILL.md
├── catalog/
│   ├── categories.yaml
│   └── shared-datasets-catalog.csv
├── docs/
│   ├── consumer-guide.md
│   ├── gcp-asset-operations.md
│   ├── pmtiles-cdn.md
│   ├── standards/
│   │   ├── asset-layout-and-formats.md
│   │   └── dataset-taxonomy.md
│   └── tooling-decision-record.md
├── scripts/
│   ├── README.md
│   └── gcs_asset.py
├── ingestion/
│   ├── README.md
│   └── common/
├── templates/
│   ├── dataset_README.template.md
│   ├── dataset_README.minimal.template.md
│   └── cron_run.template.json
├── terraform/
├── api/
│   ├── python/
│   └── typescript/
└── .github/
    └── PULL_REQUEST_TEMPLATE.md
```

Some directories may start empty. Keep them because they communicate where future work belongs.

## Shared bucket layout

The expected home bucket is:

```text
gs://skytruth-shared-datasets-1/
```

The bucket uses this top-level structure:

```text
README.md
_catalog/
_templates/
_scratch/
_deprecated/
000-system/
100-geographic-reference/
200-imagery-derived/
300-infrastructure-industrial/
400-events-observations/
500-conservation-ecosystems/
600-maritime-ocean/
700-non-geographic-reference/
800-derived-ml-products/
```

`README.md` is the human landing page for someone browsing the bucket directly.
The category data lives in `catalog/categories.yaml`; classification guidance is
in `docs/standards/dataset-taxonomy.md`.

## Approved dataset formats

| Format | Use for |
|---|---|
| `.fgb` | Canonical geographic vector data |
| COG `.tif` | Canonical raster data |
| `.zarr/` | Canonical multidimensional/chunked raster or array products |
| `.pmtiles` | Map tiles / web visualization artifacts |
| `.geojson` | Small previews, interchange, debugging |
| `.ndgeojson` | Newline-delimited GeoJSON features for streamable vector interchange/debugging |
| `.csv` | Non-geometry tables only |
| `.metadata.ndjson.gz` | Canonical feature metadata sidecar for release-oriented vector assets |
| `.schema.json` | Release feature schema |
| `.manifest.json` | Release manifest with source, artifact, checksum, identity, validation, and index-load policy metadata |

COGs must be internally tiled, internally overviewed, georeferenced, and self-contained. Zarr assets publish immutable release prefixes and expose `latest/manifest.json`; do not mirror chunk objects directly under `latest/`. PNG/JPEG/WebP files are previews only, and raw source rasters such as NetCDF, GRIB, HDF, or non-COG GeoTIFF require a documented source/archive exception.

See `docs/standards/asset-layout-and-formats.md` for full layout, naming, README, COG, and Zarr rules. Do not add new canonical file formats without updating that standards doc, the templates, the catalog schema/validation, and the review checklist.

Release-oriented vector assets use a normalized release feature model as the
source of truth. FGB remains the canonical vector artifact for consumers,
PMTiles are intentionally lightweight geometry-plus-`feature_id` lookup tiles,
and the full feature metadata lives in a durable GCS sidecar. Current catalog
and feature-preview viewers read that sidecar directly; the rebuildable
Firestore serving stack is retired; viewers use generation-pinned GCS sidecars.
Canonical FGBs and metadata sidecars
must carry `feature_id`, `geometry_hash`, and `properties_hash`; consumers may use
`geometry_hash` from the sidecar as the stable geometry-equivalence key for
grouping or de-duplicating footprints.

## Quick start for contributors

### Dataset admission

New canonical datasets and new ingestion pipelines need admission evidence in
the PR before reviewed publish promotion. A single-consumer asset is allowed,
but multi-project reuse is preferred. Every admitted asset must have a citable
source, confirmed license or terms, preferred citation, named steward, and clear
update expectations.

The PR should explain the intended consumer(s), why shared-datasets is the right
home, alternatives considered, expected maintenance path, and deprecation or
exit policy. If the proposed published footprint is **>= 10 GB**, including
canonical files, companion artifacts, and expected release copies, the PR must
include an explicit large-data exception explaining why project storage, scratch
storage, or direct upstream access is not the better answer.

The optional `admission` frontmatter block in dataset asset docs can preserve
this decision record beside the catalog metadata, but the PR discussion remains
the public review record. Existing assets are grandfathered unless a PR changes
their dataset contract.

### Dataset lifecycle states

Catalog `status` values are a consumer contract, not a deletion pathway:

- `active`: recommended for new use and maintained according to the documented cadence.
- `deprecated`: still readable and citable, but discouraged for new work.
- `superseded`: still readable and citable, with a required successor asset.
- `retired`: historical only, with no expected updates or endorsement for new analysis.

Non-active assets must keep their catalog row, README, citation, and existing
release paths. They also need `lifecycle_reason`, `lifecycle_date`, and
`consumer_guidance`; `superseded` assets additionally need
`successor_asset_slug`. Use lifecycle metadata to steer consumers without
breaking existing paths.

### Add or update a dataset

1. Read `AGENTS.md`.
2. Load `.claude/skills/publish-shared-dataset/SKILL.md`.
3. Pick the correct bucket category/subcategory using `catalog/categories.yaml` and `docs/standards/dataset-taxonomy.md`.
4. Convert supplied source files into approved canonical formats before staging
   promotion candidates. Source files such as `.xlsx`, `.zip`, raw GeoTIFF,
   NetCDF, or other noncanonical exports are inputs, not shared-datasets
   contracts. Use FGB plus PMTiles for geographic vector data, COG/Zarr for
   raster or array data, and CSV only for non-geometry tables.
5. For vector/table assets, use the publishing concierge output and canonical
   artifact profile to identify source field ID candidates and high-value
   `search_fields`. Prefer a real unique non-null source field when its values
   already satisfy `feature_id` rules. If no source field is suitable, generate
   monotonic decimal `feature_id` values from an approved assignment key or the
   stored geometry/properties hash pair. If the asset publishes localized
   feature metadata, keep PMTiles lightweight with `feature_id` only, maintain
   editable rows in `{asset-slug}.metadata-translations.csv`, and materialize
   generated `{asset-slug}.metadata.{locale}.ndjson.gz` sidecar views from the
   canonical `{asset-slug}.metadata.ndjson.gz`. Treat `geometry_hash` as the
   sidecar key for geometry-equivalent footprint grouping/de-duplication, not
   as a URL lookup handle.
6. Create or edit `docs/assets/{asset_slug}.md`; this asset doc is the local source of truth for catalog metadata and bucket README content.
7. For generated vector assets, use `uv run python scripts/vector_asset.py build ... --metadata-lookup` so FGB and PMTiles are created outside the repo under the standard temp work directory. Ensure the canonical FGB and canonical metadata sidecar contain `feature_id`, `geometry_hash`, and `properties_hash`, and that PMTiles expose only `feature_id` for metadata lookup.
   For localized feature metadata, generate locale views with
   `scripts/feature_metadata_localization.py` after the canonical metadata
   sidecar and schema are ready. Do not put translated full metadata back into
   PMTiles.
8. Run `uv run python scripts/catalog_docs.py generate` to refresh managed asset-doc blocks, `catalog/shared-datasets-catalog.csv`, and `docs/assets/index.md`.
9. Review the generated diff, then run `uv run python scripts/catalog_docs.py check`.
10. Stage any manual publish bytes under
   `_scratch/pending-publishes/{asset-slug}/{proposal-id}/`; use an issue/PR
   number when available, otherwise use a stable branch or timestamped proposal
   ID and record it in the PR. Scratch-only staging of the supplied source file
   is not complete unless the request explicitly says to stage only.
11. Open a PR that requests review from `jonaraphael`, unless `jonaraphael` is
   also the PR author and GitHub blocks the reviewer request. Include staged
   source URIs/generations, intended canonical destination URIs,
   destination-generation expectations, validation commands, and any needed
   `content_type` or `cache_control` publish-plan fields. Use the concierge or
   `reviewed_dataset_plan.py prepare` to add an immutable plan document and render
   its matching readable fence; see [plan preparation](.github/dataset-plans/README.md).
12. After a same-repo PR with a checked-in plan and exact-head approval merges, the
    `Approved dataset mutation` GitHub workflow promotes the listed staged
    objects under the `shared-datasets-production` environment. Manual workflow
    dispatch by PR number is restricted to `jonaraphael`. After a successful
    approved mutation, the catalog web deploy workflow automatically rebuilds
    `_catalog/web/` and `_catalog/shared-datasets-catalog.csv` from the current
    release indexes, and the catalog viewer deploy workflow refreshes the
    IAP-protected viewer through the production environment.

### Upload a new version of an existing dataset

1. Read `AGENTS.md`, load `.claude/skills/publish-shared-dataset/SKILL.md`, and
   load `.claude/skills/gcp-shared-datasets/SKILL.md`.
2. Identify the existing `asset-slug`; open `docs/assets/{asset-slug}.md`, the
   generated catalog row, and current bucket layout before choosing any paths.
3. Preserve the existing slug, taxonomy, canonical path, and approved formats
   unless the request explicitly asks for a reviewed asset-contract change.
4. Check whether a scheduled ingestion job owns the asset. If it does, use the
   scheduled-ingestion path unless the user explicitly requests a corrective
   manual publish.
5. Inspect current remote state read-only: existing `releases/`, `latest/`
   generations, run records, and `_catalog/releases/{asset-slug}.json` when
   present.
6. Build replacement artifacts outside the repo tree, matching catalog-listed
   formats. For supported single-object assets, validate the plan with
   `uv run python scripts/gcs_asset.py publish-release --dry-run ...`.
7. Update `docs/assets/{asset-slug}.md` only for durable contract changes such
   as source/license/citation, schema, file table, format, cadence, or consumer
   notes. Do not hand-edit `catalog/shared-datasets-catalog.csv`.
8. Run `uv run python scripts/catalog_docs.py generate`, `check`, and
   `export-readmes`. Rebuild catalog web output when public catalog metadata or
   previews must change.
9. Stage the new artifacts, changed README, catalog web files, and any reviewed
   metadata-repair JSON under
   `_scratch/pending-publishes/{asset-slug}/{proposal-id}/` with no-clobber
   uploads. Record every staged source URI and generation.
10. Stat canonical destinations. Dated release objects should be absent;
    replacements such as `latest/`, README, catalog web, and release-index
    objects need current destination generations.
11. Open a focused PR requesting review from `jonaraphael`, unless
    `jonaraphael` is also the PR author and GitHub blocks the reviewer request.
    Include release date/source version, staged source URIs/generations,
    intended canonical destination URIs, destination-generation expectations,
    validation commands, stale companion formats if any, consumer impact, and a
    checked-in plan document plus its matching `shared-datasets-publish-plan` fence.
12. After a same-repo PR with a checked-in plan and exact-head approval merges, the
    `Approved dataset mutation` GitHub workflow promotes the listed staged
    objects. Order the plan so dated release objects come before `latest/`, and
    write run records and `_catalog/releases/{asset-slug}.json` only from actual
    promoted object metadata. After a successful approved mutation, the catalog
    web deploy workflow automatically rebuilds `_catalog/web/` and
    `_catalog/shared-datasets-catalog.csv` from the current release indexes,
    and the catalog viewer deploy workflow refreshes the IAP-protected viewer
    through the production environment.

### Delete canonical dataset objects

1. Require an explicit deletion request and reason. Do not infer deletion from
   vague cleanup language, and prefer deprecation, catalog removal, or replacement
   when bytes can safely remain.
2. Enumerate exact object URIs. Prefix deletes, wildcards, and generation-less
   deletes are not valid.
3. Read current generations with `uv run python scripts/gcs_asset.py stat ...`
   and confirm the catalog, README, release index, run records, and consumer
   references will not point at deleted objects.
4. If deletion is part of a replacement, stage and approve the replacement
   objects first. In a combined PR plan, publish actions run before deletes.
5. Open a focused PR requesting review from `jonaraphael`, unless `jonaraphael`
   is also the PR author and GitHub blocks the reviewer request. Include consumer
   impact, replacement/deprecation state, exact object URIs, generations, and a
   checked-in plan document plus its matching `shared-datasets-delete-plan` fence.
6. After a same-repo PR with a checked-in delete plan and exact-head approval merges, the
   `Approved dataset mutation` workflow deletes the listed objects with
   generation preconditions under the publisher identity, then verifies the live
   object is absent.

### Controlled canonical publishing

Canonical shared-dataset writes are intentionally not a local-terminal workflow.
Humans and general-purpose agents can prepare artifacts, update metadata, and
stage bytes under `_scratch/pending-publishes/`, but `latest/`, `releases/`,
dataset README, and `_catalog/` mutations must use the approved publisher
identity behind the `shared-datasets-production` GitHub environment.

`.github/CODEOWNERS` routes all repository changes to `@jonaraphael` as the
sole owner. That routing becomes an enforced review gate only when GitHub branch
protection or rulesets require CODEOWNER review before merge.

Terraform grants Workload Identity access only to the OIDC subject for this
repository and environment. PR approval by `jonaraphael` is the normal human
approval gate for automatic promotion. When GitHub blocks self-review because
`jonaraphael` authored the PR, the `Approved dataset mutation` workflow can be
dispatched with the merged PR number by `jonaraphael` only. Both entry points
verify identical checked-in plan bytes at the reviewed head and merge revisions.
They recheck effective acceptance before mutation and pass one digest-bound
artifact across jobs using an exact executor SHA. Open PRs, stale/revoked reviews
and body-only plans cannot authorize mutations. See the
[plan format, migration and retry contract](.github/dataset-plans/README.md). Manual workflow dispatch by PR
number remains restricted to `jonaraphael`. If the GitHub environment is
also configured with required deployment reviewers, GitHub will pause the publish
job for that separate environment approval instead of completing from PR approval
alone. The workflow must use source and destination generation preconditions.
After applying the Workload Identity resources, set the GitHub environment
variable `GCP_WORKLOAD_IDENTITY_PROVIDER` to the Terraform output
`github_workload_identity_provider`.
For read-only catalog drift and bucket hygiene workflows, set repository
variables `GCP_READONLY_WORKLOAD_IDENTITY_PROVIDER` and
`GCP_READONLY_SERVICE_ACCOUNT` to the Terraform outputs
`github_readonly_workload_identity_provider` and
`github_readonly_service_account`.
The publisher also has read access to `_scratch/pending-publishes/` so the
approved workflow can copy reviewed staged bytes into canonical prefixes. After
successful promotion, the same workflow deletes the promoted scratch source
objects with generation preconditions. A separate `Scratch cleanup audit`
workflow runs weekly in the protected production environment: it writes a warning
marker when the newest object in a pending-publish prefix is 60 days old and
selects the prefix for deletion at 90 days if that object's name, generation,
and update time still match the warning. This is an age-based abandonment
policy; it does not inspect PR status or infer completed publication from
historical content matches. A first warning after day 90 can become eligible on
the next audit. Each deletion uses the listed object's exact generation, but
cleanup is not atomic across a prefix. Dry-run reports identify candidates and
perform no writes.
The Terraform `scratch_writer_members` variable preserves the current
scratch-only writer and should be overridden with the approved scratch-only
group or service account when that identity is ready, before removing any
remaining broad human write grants.
The project-level IAM deny policy is declared in Terraform as optional
hardening, but it is disabled by default because creating or updating IAM deny
policies requires `roles/iam.denyAdmin` at the organization scope. A project
owner alone cannot grant or use that role. The standing project-scope control is
therefore conditional IAM plus alerting: remove broad writer grants, grant
canonical writes only to publisher and scheduled-job identities, keep `_scratch/`
separate, and alert on unexpected canonical writes or deletes. An approved
organization-level IAM administrator or controlled IaC identity can enable the
extra deny backstop with `canonical_mutation_deny_policy_enabled=true`.

`_scratch/` is noncanonical. Do not cite scratch objects as durable shared
dataset paths, and do not treat scratch staging as approval to publish. Emergency
break-glass mutation is reserved for the approved break-glass identity and must
record changed paths, generations, and rationale.

### Generated catalog and asset docs

`docs/assets/{asset_slug}.md` files own machine-readable asset metadata in YAML
frontmatter and human-readable prose in Markdown. Do not hand-edit
`catalog/shared-datasets-catalog.csv` for normal asset changes; it is generated
from those asset docs for downstream compatibility.

Use:

```bash
uv run python scripts/catalog_docs.py generate
uv run python scripts/catalog_docs.py check
uv run python scripts/catalog_docs.py export-readmes --output-dir /tmp/shared-dataset-readmes
```

`generate` rewrites managed `asset-summary` and `files-table` blocks inside each
asset doc, refreshes the CSV catalog, and refreshes `docs/assets/index.md`.
`check` is the CI-safe drift detector for generated catalog and index outputs;
it does not fail merely because hand-authored asset-doc YAML was wrapped
differently. `export-readmes` writes upload-ready
bucket README files under category/subcategory/asset paths without touching GCS.

Asset docs must include `citation` so CSV and JSON catalog consumers can cite
the original source publication or authoritative dataset release. Optional
discovery frontmatter such as `bounds`, `geometry_type`, `row_count`,
`source_url`, and `license_flags` is preserved by the docs generator and exposed
in the static catalog JSON/site when present. The CSV catalog is a static asset
registry; cron freshness belongs in bucket release indexes, not tracked catalog
dates.

### Add a cron or ingestion job

1. Create a distinct package under `ingestion/<job_slug>/` with a README, `run.py`, and a Dockerfile when containerized.
2. Put shared runtime or publishing helpers in `ingestion/common/`; do not import from another job package just to reuse behavior.
3. Add focused tests under `tests/test_<job_slug>.py` and run tests for any job touched by shared helper changes.
4. Use a distinct Terraform file such as `terraform/envs/prod/<job_slug>.tf` for Cloud Scheduler, Cloud Run, service accounts, IAM, secrets references, and monitoring.
5. Make the job idempotent, write to a dated release before updating `latest/`, and include a run record using `templates/cron_run.template.json` when applicable. By default, cron jobs should write a skipped run record and leave release and `latest/` dataset artifacts unchanged when the source or generated output has not changed.
6. For source-availability windows or repeated retries for one upstream period, keep the release date stable for that source period and record the actual attempt date in the skipped run/check-in payload.
7. Keep `_catalog/releases/{asset-slug}.json` current on every success and meaningful skip. Verify `latest_run`, `latest_release`, and the custom metadata on `latest/` objects after deployment.
8. Normal cron runs must not require Git commits or tracked catalog date edits. The data browser reads latest-release and last-check-in state from the bucket release index.

### Change infrastructure

1. Use Terraform in `terraform/`.
2. Do not use Terraform to manage changing dataset objects.
3. Keep service accounts narrowly scoped.
4. Prefer folder/prefix-level roles only when broad bucket access is inappropriate.

### Configure cron failure Slack alerts

Production Terraform defines log-based Cloud Monitoring alerts for scheduled
ingestion failures. The alerts cover two cases:

- Cloud Scheduler cannot start a configured ingestion job.
- An unattended Cloud Run Job execution exits failed, including a detached canary.

The Cloud Run filter covers future jobs throughout the configured project and
region. A synchronous GitHub deployment test is quiet only when the execution
creator is the configured Terraform deployment identity and its execution env
contains `SHARED_DATASETS_GITHUB_ACTIONS_RUN_URL`. The workflow passes its run URL
with `gcloud run jobs execute --update-env-vars`, waits with `--wait`, and fails
when Cloud Run fails. The marker must never be installed on a persistent job
template or added to an asynchronous run. Missing metadata remains alertable;
Scheduler executions, manual unmarked runs, and detached WDPA runs retain Slack
coverage. PR CI and supervised build failures remain visible in GitHub.

`unattended-workflow-alert.yml` reports failed scheduled maintenance and automatic
catalog/CDN follow-ups through the existing GitHub Slack webhook secret. Manual
dispatches and PR/push checks do not send failure messages. A regression test
requires every scheduled workflow to be included in its subscriptions.

Routine WDPA deployment no longer deliberately fails a job to produce an alert.
Use `cron-alert-delivery-test.yml` from reviewed `main` when testing monitoring:
it refuses active executions and uses the existing pre-write failure override.
Verify actual Slack delivery for the reported execution; a failed job alone is
not delivery evidence. See [alert routing verification](docs/alert-routing.md).

The WDPA observer may write only its expected status object without an unapproved
writer alert. Other writes by that identity remain alertable. The protected
`cron-alert-policy-sync.yml` covers this policy and watches its identity inputs.
Configure monitoring changes in a reviewed PR and let that workflow apply them
after merge. A local review plan can pass the existing Cloud Monitoring Slack
notification channel:

```bash
terraform -chdir=terraform/envs/prod plan \
  -var='cron_alert_notification_channels=["projects/shared-datasets-1/notificationChannels/CHANNEL_ID"]'
```

The protected alert-policy deployer needs both
`logging.notificationRules.create` and `logging.notificationRules.delete`:
updating a log-based policy replaces its internal notification rule. If the
sync reports a missing permission, repair the custom role through the existing
bootstrap and policy-sync workflow before treating the new filter as deployed.

Alternatively, Terraform can create a Slack channel when both
`cron_alert_slack_channel_name` and sensitive `cron_alert_slack_auth_token` are
provided, but using an existing Google Cloud Slack notification channel is
preferred because it avoids putting Slack OAuth material in Terraform inputs.

### Send lightweight dataset notifications

The `repo-functionality-alert` GitHub Actions workflow runs after pushes to
`main` and posts any fenced `repo-alert` blocks found in pushed commit messages.
The committing agent uses a high bar: only exceptional, broad-use new repository
functionality should get a block. Routine fixes, maintenance, alert tuning,
docs, and ordinary dataset refreshes should not.

Configure the workflow with the GitHub secret
`SHARED_DATASETS_SLACK_WEBHOOK_URL`. Alert blocks use this format:

````text
```repo-alert
emoji: 🗺️
headline: Vector publishing helper added
summary: A new command builds FlatGeobuf and PMTiles artifacts from source vectors.
why_excited: Manual publishes are faster, more repeatable, and easier to review.
```
````

To preview alerts from a saved GitHub push event JSON:

```bash
uv run python scripts/repo_alerts.py send-from-github-event \
  --event-path /path/to/push-event.json \
  --dry-run
```

Use the dataset alert helper after a manual dataset upload or meaningful update:

```bash
uv run python scripts/dataset_alerts.py upload-summary \
  --asset-slug gfw-fixed-infrastructure \
  --changed-path gs://skytruth-shared-datasets-1/path/to/object.fgb \
  --release-path gs://skytruth-shared-datasets-1/path/to/releases/YYYY-MM-DD/ \
  --row-count 123 \
  --dataset-path ./gfw-fixed-infrastructure.fgb \
  --new-dataset
```

The helper only posts to Slack when `--new-dataset` is set, which should be used
only when the asset's canonical `latest/` object did not exist before the
publish. Existing asset refreshes print a local skip message instead of posting
`Dataset updated` to Slack. The approved GitHub promotion workflow derives the
new-dataset flag from the publish plan `destination_generation`.

For canonical vector/table assets, `publish-release` and the approved GitHub
promotion workflow run schema validation before canonical objects are written.
The validation reports added, removed, renamed, reordered, and type-changed
fields so reviewers can confirm the new release schema is intentional.

```bash
uv run python scripts/dataset_alerts.py check-schema-compatibility \
  --asset-slug gfw-fixed-infrastructure \
  --dataset-path ./gfw-fixed-infrastructure.fgb
```

Schema snapshots are stored under
`gs://skytruth-shared-datasets-1/_catalog/schema-snapshots/`. After a successful
compatible or waived publish, the approved publisher workflow runs
`check-schema --upload-snapshot` to update the snapshot and, when a schema delta
exists, emit a structured Cloud Logging diagnostic. Schema-change Slack
monitoring is intentionally quiet; consumer-impacting schema changes should use
the reviewed breaking-change alert path instead. Local `check-schema` runs are
read-only by default: they print the schema-change payload and skip the snapshot
upload unless `--upload-snapshot` is passed from a runtime with
`SHARED_DATASETS_ALLOW_CANONICAL_MUTATION=1`.

FYI Slack summaries use the Secret Manager secret
`shared-datasets-slack-webhook-url` by default. To set or rotate the webhook:

```bash
gcloud secrets versions add shared-datasets-slack-webhook-url \
  --project=shared-datasets-1 \
  --data-file=/path/to/webhook-url.txt
```

### Production Terraform

Production Terraform mutations must land through reviewed PRs and protected
GitHub Actions workflows in the `shared-datasets-production` environment. Local
`terraform plan`, `terraform validate`, and saved-plan review commands are OK;
local `terraform apply` and `scripts/terraform_prod_apply.py` are reserved for
explicitly approved break-glass emergencies.

The shared hierarchical-namespace data bucket retains deleted or overwritten
objects through Cloud Storage soft delete for 30 days.

### Feature Branch Preview

The feature branch preview is a single replaceable test slot in
`shared-datasets-1` for deploying and testing selected feature branches before
merge. It uses preview-named GCP resources and a separate Terraform state
prefix under `terraform/envs/preview/`; see
`.claude/skills/feature-preview/SKILL.md` and
`docs/feature-preview.md` for the GitHub Actions workflow steps. Run
the preview deploy workflow by selecting the feature branch or tag in the
GitHub **Run workflow** branch dropdown. The deploy prints both the preview API
URL and an IAP-protected preview catalog viewer URL.
Stable preview IAM bootstrap is managed by the protected
`Preview Terraform IAM sync` workflow.
Preview test data is not production publishing: upload disposable release
bundles directly to `gs://skytruth-shared-datasets-1-preview/` with safe
preconditions and record exact URIs and generations in concierge upload
evidence. Refresh the preview catalog by deploying with
`preview_data_mode=preserve`. The catalog is built from preview-bucket release
indexes, shows only preview assets, and preserves the full release `files` list
for sidecar datafiles. Current lookups read GCS sidecars; the Firestore index
loaders are retired. Canonical dataset adds and
updates still use the reviewed `_scratch/pending-publishes/` promotion path in
`publish-shared-dataset`.

## Standard local setup

```bash
UV_CACHE_DIR=.uv-cache uv sync --locked --all-groups

export GOOGLE_CLOUD_PROJECT=shared-datasets-1
export SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
```

For local development, authenticate with Application Default Credentials:

```bash
gcloud auth application-default login
gcloud config set project shared-datasets-1
```

For CI, use Workload Identity Federation or a CI-provided service account. Do not commit service account JSON keys.

### Local tests

The default test suite is network-free: remote services, GCS, Slack, and source
downloads are mocked or represented by local fixtures.

```bash
UV_CACHE_DIR=.uv-cache uv run pytest
```

The locked dev group includes GeoPandas and Rasterio to execute generated
FGB/COG consumer examples against tiny synthetic fixtures. They are test-only
dependencies; the Python SDK does not require them for exact byte fetching.

Geospatial integration tests run only when the required native binaries are
installed locally: GDAL CLI tools, PMTiles, and the Tippecanoe decoder used for
PMTiles sampling. Enable the explicit GDAL integration flag for the tests that
require it:

```bash
RUN_GDAL_INTEGRATION_TESTS=1 UV_CACHE_DIR=.uv-cache uv run pytest \
  tests/test_raster_standards.py \
  tests/test_wdpa_monthly.py \
  tests/test_sea_ice_daily.py \
  tests/test_eamlis_monthly.py
```

### Local lint and Terraform validation

CI also gates merges on Python lint and Terraform validation. Run the same
checks locally before opening a PR:

```bash
UV_CACHE_DIR=.uv-cache uv run ruff check .
terraform fmt -check -recursive terraform/
terraform -chdir=terraform/envs/prod init -backend=false -input=false && terraform -chdir=terraform/envs/prod validate
terraform -chdir=terraform/envs/preview init -backend=false -input=false && terraform -chdir=terraform/envs/preview validate
```

Terraform validation requires the CI-pinned Terraform version (see
`TERRAFORM_VERSION` in `.github/workflows/ci.yml`); older binaries reject the
`optional(type, default)` variable syntax used by the prod environment.

The catalog's inline **Use this dataset** section provides 3–5 line integration
examples and a small source credit. See the
[consumer workflow](docs/consumer-guide.md#use-this-dataset) and
[portable v1 contract](docs/standards/workspace-snapshot-v1.md).

## TypeScript SDK release

The TypeScript helpers live under `api/typescript/` and publish as
`@skytruth/shared-datasets`. The initial `0.1.0` release has been published to
npm.

The initial release was published manually by an npm maintainer because npm
trusted-publisher configuration requires the package to already exist. From
`api/typescript/`, use an authenticated npm session and publish the scoped
package as public only for a new package bootstrap:

```bash
npm publish --access public
```

Trusted Publishing is configured for follow-up releases; do not create a
long-lived `NPM_TOKEN`. Prepare each release in a reviewed PR. Changes to SDK
source, the packed README, either package manifest, or `tsconfig.json` require a
higher stable `major.minor.patch` version. Choose the appropriate semver level
and update both manifests from `api/typescript/`, for example:

```bash
npm version --no-git-tag-version minor
npm test
npm run test:pack
```

Commit `package.json` and `package-lock.json` with the package changes. The
`TypeScript SDK validation` workflow checks the PR's version increase, runs the
SDK tests on Node 24, and installs the actual tarball into a separate consumer
that exercises the root/server runtime exports and TypeScript declarations.
Ordinary CI also retains Node 22 coverage. Test/workflow-only changes outside
the packed package do not require a release. Prerelease versions are not
supported by this stable-release workflow.

Merging a versioned package change to `main` triggers `Publish TypeScript SDK`.
It checks the merged version, repeats the tests, packs and validates the package,
and publishes that exact tarball through npm's GitHub Actions OIDC handshake.
The workflow has read-only repository access: it never edits versions, commits,
or pushes. Commit-message bump trailers have no effect.

The publisher compares the reviewed version with all published stable versions.
A new version must be higher; retrying an existing version succeeds without
publishing only when the packed bytes match its registry integrity. Changed
bytes require another reviewed version. Missing or malformed registry metadata
and network/authentication failures stop the release. A workflow-only push with
no package/version change skips publication. Manual dispatch from `main` retries
the checked-out version with the same integrity rules; it never invents a bump.
An older workflow retry cannot move the npm latest tag backwards.

The release uses Node 24 and npm trusted publishing (npm 11.5.1 or later). No
local `npm publish` is needed for a normal release. The Node 24 check should be
required by branch protection; adding a workflow does not change repository
protection settings.

The npm trusted-publisher settings should remain:

- Publisher: GitHub Actions
- Organization or user: `SkyTruth`
- Repository: `shared-datasets-1`
- Workflow filename: `publish-typescript-sdk.yml`
- Environment name: leave blank unless the workflow gains a GitHub environment
- Allowed actions: `npm publish`

After the workflow succeeds, verify the registry version:

```bash
npm view @skytruth/shared-datasets version
```

TypeScript consumers may use a local path only for development and integration
testing against unreleased local changes:

```bash
npm install ../shared-datasets-1/api/typescript
```

Do not commit local-path installs to production consumer repos.

## GCS asset CLI

This repo includes a small Python CLI intended for AI agents and maintainers:

```bash
uv run python scripts/gcs_asset.py list gs://skytruth-shared-datasets-1/100-geographic-reference/
uv run python scripts/gcs_asset.py stat gs://skytruth-shared-datasets-1/README.md
uv run python scripts/gcs_asset.py download gs://skytruth-shared-datasets-1/README.md /tmp/README.md
uv run python scripts/gcs_asset.py upload ./asset.fgb gs://skytruth-shared-datasets-1/_scratch/pending-publishes/example-asset/123/asset.fgb
```

For prepared release artifacts belonging to an existing catalog asset, prefer
`publish-release` over individual uploads. It validates local files, verifies
that release objects do not already exist, captures current `latest/`
generations, uploads `releases/YYYY-MM-DD/` first, then updates `latest/` and
writes a run record:

```bash
uv run python scripts/gcs_asset.py publish-release \
  --asset-slug gfw-fixed-infrastructure \
  --release-date 2026-05-01 \
  --publish-dir "$TMPDIR/shared-datasets-1/vector-assets/gfw-fixed-infrastructure/publish" \
  --source-version "GFW public-fixed-infrastructure-filtered:latest" \
  --dry-run
```

Run canonical publish commands only under the approved publisher identity, not
from a local human or agent terminal. Read
`.claude/skills/gcp-shared-datasets/SKILL.md` before using the CLI for any
remote operation. For manual dataset add/update work, read
`.claude/skills/publish-shared-dataset/SKILL.md` first.

## Vector artifact builds

Use `scripts/vector_asset.py` to create upload-ready FGB and PMTiles artifacts
without writing generated data into git. The helper uses GDAL for the canonical
FGB and WGS84 GeoJSONSeq tile source, builds the MBTiles intermediate with
Tippecanoe, then converts it with `pmtiles convert`. The failed projection path is
not used because metadata-lookup SQL projections can drop geometry and produce
empty or bad MBTiles output:

```bash
uv run python scripts/vector_asset.py build ./source.shp \
  --asset-slug example-asset \
  --layer-name example_asset \
  --title "Example Asset" \
  --description "Example vector tiles" \
  --metadata-lookup \
  --tile-simplify 0.001
```

By default, outputs go under
`$TMPDIR/shared-datasets-1/vector-assets/{asset-slug}/publish/` and temporary
MBTiles intermediates go under `build/`. Set `SHARED_DATASETS_WORKDIR` or pass
`--work-dir` for a different temp root. Use `--tile-simplify` only for dense
display tiles; the canonical FGB is still generated without simplification.
Shared PMTiles should use auto maxzoom from FGB profiling and source metadata.
The policy biases toward detailed display, caps at zoom 12 by default, and only
uses zooms below 8 when source/profile evidence or a documented override proves
the asset is intentionally coarse.

Generated IDs are opt-in. When no source field is suitable, assign monotonic
decimal `feature_id` values from an approved assignment key or the pair of
stored `geometry_hash` and `properties_hash` values. The manifest `identity`
block records the strategy, source fields or assignment key, hash algorithm,
canonicalization version, previous release, and next generated ID.

For localized feature metadata, keep the canonical FGB faithful to the source
geometry and stable identifier fields, with `feature_id`, `geometry_hash`, and
`properties_hash` preserved in both the FGB and release metadata sidecar.
`geometry_hash` is the stable geometry-equivalence key consumers can use after
loading the sidecar to group or de-duplicate footprints. Store editable translations in
`{asset-slug}.metadata-translations.csv` keyed by `feature_id`, field, locale,
and source-value hash, then materialize one sidecar per locale:

```bash
uv run python scripts/feature_metadata_localization.py \
  --canonical-sidecar "$TMPDIR/shared-datasets-1/vector-assets/example-asset/publish/example-asset.metadata.ndjson.gz" \
  --translation-source "$TMPDIR/shared-datasets-1/vector-assets/example-asset/publish/example-asset.metadata-translations.csv" \
  --schema "$TMPDIR/shared-datasets-1/vector-assets/example-asset/publish/example-asset.schema.json" \
  --translatable-field name \
  --locale es --locale fr \
  --manifest "$TMPDIR/shared-datasets-1/vector-assets/example-asset/publish/example-asset.manifest.json" \
  --asset-slug example-asset \
  --release 2026-05-01 \
  --output-dir "$TMPDIR/shared-datasets-1/vector-assets/example-asset/publish" \
  --report-dir "$TMPDIR/shared-datasets-1/vector-assets/example-asset/reports"
```

Translation-only edits prepare one reviewed bundle containing the CSV, every
maintained locale sidecar, and the manifest with coverage and file hashes.
Pass explicit `--locale` and `--translatable-field` arguments from the asset's
catalog configuration and `--manifest` to the local materializer. Stage both
release and `latest/` copies. The approved mutation workflow rejects incomplete
language bundles and finalizes their generations before refreshing the catalog.
WDPA edits target its current release through the existing publication owner;
feature IDs, geometry, source metadata, and the generated-ID counter stay fixed.
There is no separate after-publish localization workflow.

Missing or stale translations retain source text and do not block publication.
When any maintained locale has at least 1% unresolved values, the completed
release can produce one combined Slack notice and per-locale debt CSVs under
`_scratch/translation-debt/{asset}/{release}/`. Private/internal notices contain
counts and file references only. See [translation maintenance](docs/feature-metadata-api.md)
for coverage and delivery semantics.

## Catalog web preview

Build the zero-backend catalog and PMTiles preview site from repo metadata:

```bash
uv run python scripts/catalog_site.py --out /tmp/shared-datasets-1/catalog-web
python3 -m http.server 4173 --directory /tmp/shared-datasets-1/catalog-web
```

The deploy target is:

```text
gs://skytruth-shared-datasets-1/_catalog/web/
```

See `docs/catalog-web-preview.md` for deployment and verification steps.

## PR expectations

A PR that changes remote asset organization, ingestion jobs, or access behavior should state:

- What asset or path is affected.
- Request review from `jonaraphael`, or record the GitHub self-review block when
  `jonaraphael` is the PR author.
- Whether the change is docs-only, infrastructure, ingestion code, API/access protocol, or remote data mutation.
- For new canonical datasets or ingestion pipelines, the Dataset Admission
  section from the PR template.
- Whether `latest/` or `releases/` paths are changed.
- Staged `_scratch/pending-publishes/` source URIs and source generations.
- Intended canonical destination URIs and destination-generation expectations.
- A checked-in `.github/dataset-plans/` document and matching readable
  `shared-datasets-publish-plan` fence if approval should trigger
  automatic promotion. For intentional release-schema changes, describe the
  schema change, rationale, reviewer, PR reference, and consumer impact in the
  publish plan.
- For changes that may break consumers of `{asset-slug}@latest`, include a
  top-level `breaking_changes` array in the fenced publish or delete plan. Each
  entry must include `category`, `summary`, `consumer_action`, and
  `affected_surfaces`. Allowed categories are `path`, `format`, `artifact_set`,
  `schema`, `feature_identity`, `pmtiles_lookup`, `metadata_sidecar`, `access`,
  `catalog`, `lifecycle_delete`, and `other`. The planned-alert workflow posts a
  slug-scoped Slack heads-up for same-repo, non-draft PRs; the approved mutation
  workflow posts a live Slack alert after successful promotion or deletion.
  Schema diffs, catalog contract removals/restrictions, and `latest/` deletion
  targets are detected automatically where the workflow has enough context;
  semantic contract changes such as feature identity policy or PMTiles lookup
  semantics must be declared explicitly in `breaking_changes`.
- A checked-in `.github/dataset-plans/` document and matching readable
  `shared-datasets-delete-plan` fence if approval should trigger
  reviewed deletion; every deletion must include exact URI, generation, and
  reason.
- How the change was validated.
- Any consumer impact.

## Non-goals

This repo should not become:

- A dumping ground for one-off notebooks.
- A mirror of large data files.
- A second uncontrolled copy of the shared bucket.
- A place where agents invent new bucket conventions without updating the docs.

The portable workspace validator (`web/catalog/workspace-contract.js`) is an
SDK build input. Changes to it or the contract-copy build script require the
same reviewed TypeScript version increase as packed package sources.
