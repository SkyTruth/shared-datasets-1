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
| Agent/maintainer operating rules | `AGENTS.md` |
| Remote GCS access/upload/edit procedure | `skills/gcp-shared-datasets/SKILL.md` |
| Dataset categories and bucket paths | `AGENTS.md`, `catalog/categories.yaml` |
| Dataset README format | `templates/dataset_README.template.md` |
| Infrastructure | `terraform/` |
| Ingestion jobs | `ingestion/` or `scripts/` |
| Access protocols / APIs | `api/` and `docs/` |

When instructions conflict, follow this order:

1. User or issue/PR instructions.
2. `AGENTS.md`.
3. The relevant skill in `skills/`.
4. Other docs/templates.
5. Existing code conventions.

## Core principles

1. **The bucket is a product.** Treat every shared dataset as something another SkyTruth project may depend on.
2. **Low overhead wins.** Prefer predictable paths, simple READMEs, and generated catalogs over heavy metadata processes.
3. **Stable paths beat clever names.** Someone should be able to guess where a dataset belongs before searching.
4. **Canonical data is boring.** Approved formats are `.fgb`, `.pmtiles`, `.geojson`, and `.csv` with no geometry.
5. **Cron jobs must be safe to retry.** Scheduled jobs should be idempotent and should not destroy previous releases.
6. **Infrastructure and data are managed differently.** Terraform manages cloud resources. The Python GCS asset tooling manages data objects.
7. **Agents must leave things clearer than they found them.** Any remote asset change should update the relevant README/catalog when appropriate.

## Repository layout

```text
.
├── README.md
├── AGENTS.md
├── catalog/
│   ├── categories.yaml
│   └── shared-datasets-catalog.csv
├── docs/
│   ├── gcp-asset-operations.md
│   └── tooling-decision-record.md
├── skills/
│   └── gcp-shared-datasets/
│       └── SKILL.md
├── scripts/
│   ├── README.md
│   └── gcs_asset.py
├── templates/
│   ├── dataset_README.template.md
│   ├── dataset_README.minimal.template.md
│   └── cron_run.template.json
├── terraform/
├── ingestion/
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

Detailed subdirectories and classification rules are in `AGENTS.md`.

## Approved dataset formats

| Format | Use for |
|---|---|
| `.fgb` | Canonical geographic vector data |
| `.pmtiles` | Map tiles / web visualization artifacts |
| `.geojson` | Small previews, interchange, debugging |
| `.csv` | Non-geometry tables only |

Do not add new canonical file formats without updating `AGENTS.md`, the templates, and the review checklist.

## Quick start for contributors

### Add or update a dataset

1. Read `AGENTS.md`.
2. Pick the correct bucket category/subcategory.
3. Use `templates/dataset_README.template.md` or the minimal template.
4. Use `skills/gcp-shared-datasets/SKILL.md` for remote GCS operations.
5. Update `catalog/shared-datasets-catalog.csv` if the asset is new or meaningfully changed.
6. Open a PR describing the remote asset paths changed.

### Add a cron or ingestion job

1. Put reusable job code under `ingestion/` or `scripts/`.
2. Use Terraform for Cloud Scheduler, Cloud Run, service accounts, IAM, secrets references, and monitoring.
3. Make the job idempotent.
4. Write to a dated release before updating `latest/`.
5. Include a run record using `templates/cron_run.template.json` when applicable.

### Change infrastructure

1. Use Terraform in `terraform/`.
2. Do not use Terraform to manage changing dataset objects.
3. Keep service accounts narrowly scoped.
4. Prefer folder/prefix-level roles only when broad bucket access is inappropriate.

## Standard local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export GOOGLE_CLOUD_PROJECT=shared-datasets-1
export SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
```

For local development, authenticate with Application Default Credentials:

```bash
gcloud auth application-default login
gcloud config set project shared-datasets-1
```

For CI, use Workload Identity Federation or a CI-provided service account. Do not commit service account JSON keys.

## GCS asset CLI

This repo includes a small Python CLI intended for AI agents and maintainers:

```bash
python scripts/gcs_asset.py list gs://skytruth-shared-datasets-1/100-geographic-reference/
python scripts/gcs_asset.py stat gs://skytruth-shared-datasets-1/README.md
python scripts/gcs_asset.py download gs://skytruth-shared-datasets-1/README.md /tmp/README.md
python scripts/gcs_asset.py upload ./README.md gs://skytruth-shared-datasets-1/README.md --replace-generation 123456789
```

Read `skills/gcp-shared-datasets/SKILL.md` before using it for write operations.

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
