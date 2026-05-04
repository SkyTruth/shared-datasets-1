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
| Python environment/tooling alignment | `.claude/skills/align-virtual-environment/SKILL.md` |
| Bucket/repo compliance walkthroughs | `.claude/skills/shared-datasets-compliance-audit/SKILL.md` |
| Dataset categories and bucket paths | `catalog/categories.yaml`, `docs/standards/dataset-taxonomy.md` |
| Asset layout, formats, and README requirements | `docs/standards/asset-layout-and-formats.md` |
| Dataset README templates | `templates/dataset_README.template.md`, `templates/dataset_README.minimal.template.md` |
| Static catalog web preview | `.claude/skills/static-catalog-web-preview/SKILL.md`, `docs/catalog-web-preview.md` |
| Tiered PMTiles browser access | `docs/pmtiles-cdn.md` |
| Infrastructure | `terraform/` |
| Ingestion jobs | `ingestion/` or `scripts/` |
| Access protocols / APIs | `api/` and `docs/` |

When instructions conflict, follow this order:

1. User or issue/PR instructions.
2. `AGENTS.md`.
3. The relevant skill in `.claude/skills/`.
4. Other docs/templates.
5. Existing code conventions.

## Core principles

1. **The bucket is a product.** Treat every shared dataset as something another SkyTruth project may depend on.
2. **Low overhead wins.** Prefer predictable paths, simple READMEs, and generated catalogs over heavy metadata processes.
3. **Stable paths beat clever names.** Someone should be able to guess where a dataset belongs before searching.
4. **Canonical data is boring.** Approved formats are `.fgb`, COG `.tif`, `.zarr/`, `.pmtiles`, `.geojson`, `.ndgeojson`, and geometry-free `.csv`.
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
│       ├── gcp-shared-datasets/
│       │   └── SKILL.md
│       ├── publish-shared-dataset/
│       │   └── SKILL.md
│       ├── repo-alert-commit-messages/
│       │   └── SKILL.md
│       ├── shared-datasets-compliance-audit/
│       │   └── SKILL.md
│       └── static-catalog-web-preview/
│           └── SKILL.md
├── catalog/
│   ├── categories.yaml
│   └── shared-datasets-catalog.csv
├── docs/
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

COGs must be internally tiled, internally overviewed, georeferenced, and self-contained. Zarr assets publish immutable release prefixes and expose `latest/manifest.json`; do not mirror chunk objects directly under `latest/`. PNG/JPEG/WebP files are previews only, and raw source rasters such as NetCDF, GRIB, HDF, or non-COG GeoTIFF require a documented source/archive exception.

See `docs/standards/asset-layout-and-formats.md` for full layout, naming, README, COG, and Zarr rules. Do not add new canonical file formats without updating that standards doc, the templates, the catalog schema/validation, and the review checklist.

## Quick start for contributors

### Add or update a dataset

1. Read `AGENTS.md`.
2. Load `.claude/skills/publish-shared-dataset/SKILL.md`.
3. Pick the correct bucket category/subcategory using `catalog/categories.yaml` and `docs/standards/dataset-taxonomy.md`.
4. Create or edit `docs/assets/{asset_slug}.md`; this asset doc is the local source of truth for catalog metadata and bucket README content.
5. For generated vector assets, use `uv run python scripts/vector_asset.py build ...` so FGB and PMTiles are created outside the repo under the standard temp work directory.
6. Run `uv run python scripts/catalog_docs.py generate` to refresh managed asset-doc blocks, `catalog/shared-datasets-catalog.csv`, and `docs/assets/index.md`.
7. Review the generated diff, then run `uv run python scripts/catalog_docs.py check`.
8. Use `.claude/skills/gcp-shared-datasets/SKILL.md` and `scripts/gcs_asset.py` for any approved remote GCS operations.
9. Open a PR describing the remote asset paths changed.

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
`check` is the CI-safe drift detector. `export-readmes` writes upload-ready
bucket README files under category/subcategory/asset paths without touching GCS.

Optional discovery frontmatter such as `bounds`, `geometry_type`, `row_count`,
`source_url`, and `license_flags` is preserved by the docs generator and exposed
in the static catalog JSON/site when present. The CSV catalog remains the
backward-compatible core metadata table.

### Add a cron or ingestion job

1. Create a distinct package under `ingestion/<job_slug>/` with a README, `run.py`, and a Dockerfile when containerized.
2. Put shared runtime or publishing helpers in `ingestion/common/`; do not import from another job package just to reuse behavior.
3. Add focused tests under `tests/test_<job_slug>.py` and run tests for any job touched by shared helper changes.
4. Use a distinct Terraform file such as `terraform/envs/prod/<job_slug>.tf` for Cloud Scheduler, Cloud Run, service accounts, IAM, secrets references, and monitoring.
5. Make the job idempotent, write to a dated release before updating `latest/`, and include a run record using `templates/cron_run.template.json` when applicable. By default, cron jobs should write a skipped run record and leave release and `latest/` dataset artifacts unchanged when the source or generated output has not changed.

### Change infrastructure

1. Use Terraform in `terraform/`.
2. Do not use Terraform to manage changing dataset objects.
3. Keep service accounts narrowly scoped.
4. Prefer folder/prefix-level roles only when broad bucket access is inappropriate.

### Configure cron failure Slack alerts

Production Terraform defines log-based Cloud Monitoring alerts for scheduled
ingestion failures. The alerts cover two cases:

- Cloud Scheduler cannot start a configured ingestion job.
- A scheduler-created Cloud Run Job execution exits failed.

Manual canary failures are not matched by the Cloud Run alert because the log
filter requires the execution creator to be the job's Cloud Scheduler service
account. Configure Slack delivery by passing an existing Cloud Monitoring Slack
notification channel:

```bash
terraform -chdir=terraform/envs/prod apply \
  -var='cron_alert_notification_channels=["projects/shared-datasets-1/notificationChannels/CHANNEL_ID"]'
```

Alternatively, Terraform can create a Slack channel when both
`cron_alert_slack_channel_name` and sensitive `cron_alert_slack_auth_token` are
provided, but using an existing Google Cloud Slack notification channel is
preferred because it avoids putting Slack OAuth material in Terraform inputs.

### Send lightweight dataset notifications

The `repo-functionality-alert` GitHub Actions workflow runs after pushes to
`main` and posts any fenced `repo-alert` blocks found in pushed commit messages.
The committing agent decides whether the change is substantially exciting,
generates custom alert copy, and appends the block without human input.

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
  --dataset-path ./gfw-fixed-infrastructure.fgb
```

The repo commit that updates catalog metadata for a new asset slug or meaningful
dataset release is the state marker for this announcement. If that commit does
not exist yet, assume the announcement has not been sent. When creating the
catalog-update commit, send the upload summary first. Once that commit exists,
treat the release as announced and do not send duplicate summaries for
same-release cache refreshes, README wording fixes, PMTiles repairs, or other
corrective follow-ups unless explicitly requested.

For canonical vector/table assets, compare fields against the last stored schema
snapshot after a successful publish:

```bash
uv run python scripts/dataset_alerts.py check-schema \
  --asset-slug gfw-fixed-infrastructure \
  --dataset-path ./gfw-fixed-infrastructure.fgb
```

Schema snapshots are stored under
`gs://skytruth-shared-datasets-1/_catalog/schema-snapshots/`. Any field delta is
written as a structured Cloud Logging warning so the Cloud Monitoring schema
alert can notify Slack, then the snapshot is updated.

FYI Slack summaries use the Secret Manager secret
`shared-datasets-slack-webhook-url` by default. To set or rotate the webhook:

```bash
gcloud secrets versions add shared-datasets-slack-webhook-url \
  --project=shared-datasets-1 \
  --data-file=/path/to/webhook-url.txt
```

### Apply production Terraform

Prefer the wrapper so successful and failed local applies send Slack summaries:

```bash
uv run python scripts/terraform_prod_apply.py
```

The wrapper reads the current Cloud Run job images when image variables are not
provided, creates a saved plan, applies that plan, and reports the result to
Slack. Direct Terraform still works but will not send deployment summaries.

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

Geospatial integration tests run only when the required native binaries are
installed locally: GDAL CLI tools, Tippecanoe, and PMTiles. Enable the explicit
GDAL integration flag for the tests that require it:

```bash
RUN_GDAL_INTEGRATION_TESTS=1 UV_CACHE_DIR=.uv-cache uv run pytest \
  tests/test_raster_standards.py \
  tests/test_wdpa_monthly.py \
  tests/test_sea_ice_daily.py \
  tests/test_eamlis_monthly.py
```

## GCS asset CLI

This repo includes a small Python CLI intended for AI agents and maintainers:

```bash
uv run python scripts/gcs_asset.py list gs://skytruth-shared-datasets-1/100-geographic-reference/
uv run python scripts/gcs_asset.py stat gs://skytruth-shared-datasets-1/README.md
uv run python scripts/gcs_asset.py download gs://skytruth-shared-datasets-1/README.md /tmp/README.md
uv run python scripts/gcs_asset.py upload ./README.md gs://skytruth-shared-datasets-1/README.md --replace-generation 123456789
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

Read `.claude/skills/gcp-shared-datasets/SKILL.md` before using it for write operations. For manual dataset add/update work, read `.claude/skills/publish-shared-dataset/SKILL.md` first.

## Vector artifact builds

Use `scripts/vector_asset.py` to create upload-ready FGB and PMTiles artifacts
without writing generated data into git. The helper uses GDAL for canonical FGB
and Tippecanoe for direct PMTiles generation:

```bash
uv run python scripts/vector_asset.py build ./source.shp \
  --asset-slug example-asset \
  --layer-name example_asset \
  --title "Example Asset" \
  --description "Example vector tiles" \
  --maxzoom 8 \
  --tile-simplify 0.001
```

By default, outputs go under
`$TMPDIR/shared-datasets-1/vector-assets/{asset-slug}/publish/` and temporary
MBTiles intermediates go under `build/`. Set `SHARED_DATASETS_WORKDIR` or pass
`--work-dir` for a different temp root. Use `--tile-simplify` only for dense
display tiles; the canonical FGB is still generated without simplification.
Shared PMTiles should be built to maxzoom 8 or higher. The vector helper rejects
lower maxzoom values unless `--allow-low-maxzoom` is passed for a documented
exception.

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
- Whether the change is docs-only, infrastructure, ingestion code, API/access protocol, or remote data mutation.
- Whether `latest/` or `releases/` paths are changed.
- How the change was validated.
- Any consumer impact.

## Non-goals

This repo should not become:

- A dumping ground for one-off notebooks.
- A mirror of large data files.
- A second uncontrolled copy of the shared bucket.
- A place where agents invent new bucket conventions without updating the docs.
