# shared-datasets-1

`shared-datasets-1` is the control-plane repository for SkyTruth's shared datasets
bucket. The bucket holds reusable data; this repository holds infrastructure,
scheduled ingestion, access helpers, templates, catalog metadata, and automation.

Start with [AGENTS.md](AGENTS.md) for authority, Git, publication, and approval
rules. Its [Source Map](AGENTS.md#source-map) identifies authoritative documents,
and its [skill routing table](AGENTS.md#repo-local-skills) selects the workflow.

## Repository layout

| Path | Contents |
|---|---|
| `AGENTS.md`, `CLAUDE.md` | Operating guide and Claude Code import shim |
| `.claude/skills/`, `.agents/skills` | Portable workflows and their symlink mirror |
| `catalog/` | Categories, generated asset registry, and policy data |
| `docs/standards/`, `docs/assets/` | Dataset contracts and asset metadata sources |
| `docs/` | Consumer and operational documentation |
| `scripts/` | Object tools, artifact builders, catalog generation, validation |
| `ingestion/` | Scheduled jobs and shared runtime helpers |
| `services/`, `web/` | Catalog and preview viewers |
| `api/python/`, `api/typescript/` | Consumer SDKs |
| `templates/` | Dataset README and cron run-record templates |
| `terraform/` | Infrastructure as code |
| `.github/` | Workflows, review template, and immutable dataset plans |

## Shared bucket layout

The expected GCP project is `shared-datasets-1`; its shared bucket is
`gs://skytruth-shared-datasets-1/`.

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

The bucket README is its human landing page. [Category data](catalog/categories.yaml)
and [taxonomy guidance](docs/standards/dataset-taxonomy.md) define classification;
the [asset standard](docs/standards/asset-layout-and-formats.md#default-asset-layout)
defines per-asset `README.md`, `latest/`, dated `releases/`, and run records.

## Approved dataset formats

The [format standard](docs/standards/asset-layout-and-formats.md#approved-formats)
defines FGB vectors, COG rasters, Zarr arrays, PMTiles display tiles, GeoJSON and
NDGeoJSON interchange, geometry-free CSV tables, and release metadata sidecars.
Read it for format requirements, naming, preview/source exceptions, and feature
identity contracts.

[SkyTruth's identity-resolution process](docs/standards/feature-identity-resolution.md)
describes conflict categories, evidence, human review, automation limits and how
to extend the rules for a new edge condition.

## Standard local setup

Use the [Python environment workflow](.claude/skills/align-virtual-environment/SKILL.md)
when setting up or repairing repo tooling. Install the locked dependencies:

```bash
UV_CACHE_DIR=.uv-cache uv sync --locked --all-groups
export GOOGLE_CLOUD_PROJECT=shared-datasets-1
export SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
```

For local cloud access, use Application Default Credentials:

```bash
gcloud auth application-default login
gcloud config set project shared-datasets-1
```

[AGENTS.md](AGENTS.md#non-negotiable-rules) governs credential safety;
[local workspace hygiene](docs/standards/local-temp-workspaces.md) governs scratch
files and generated artifacts.

## Contributor workflows

Each procedure has an authoritative home below. Load the matching skill before
acting and use its linked references for detailed contracts.

| Task | Authoritative procedure |
|---|---|
| Dataset admission and lifecycle | [Publishing workflow](.claude/skills/publish-shared-dataset/references/artifacts-and-metadata.md#dataset-admission) and [lifecycle standard](docs/standards/asset-layout-and-formats.md#dataset-lifecycle) |
| Add or update a dataset | [Publishing workflow](.claude/skills/publish-shared-dataset/SKILL.md#route-1-first-upload) |
| Upload a new version | [Existing-version runbook](.claude/skills/publish-shared-dataset/SKILL.md#route-2-existing-asset-update) |
| Delete canonical objects | [Reviewed deletion runbook](.claude/skills/publish-shared-dataset/references/reviewed-deletion.md) |
| Controlled canonical publishing | [Immutable plan and approval contract](.github/dataset-plans/README.md) |
| Generate catalog and asset docs | [Catalog commands](scripts/README.md#dataset-operations) and [asset metadata workflow](.claude/skills/publish-shared-dataset/references/artifacts-and-metadata.md#asset-docs-and-catalog) |
| Change or add an ingestion deploy target | [Ingestion deployment procedure](docs/ci-deployment.md) |
| Change infrastructure / Terraform or IAM target | [Target-change procedure](docs/terraform-reconciliation.md#change-a-terraform-or-iam-target) |
| Configure cron failure Slack alerts | [Alert routing](docs/alert-routing.md) |
| Recover a failed deployment or incident | [CI/deployment recovery](docs/ci-recovery.md) |
| Configure Slack incident transport | [Slack incident lifecycle](docs/slack-incidents.md#configure-and-reconcile) |
| Send lightweight dataset notifications | [Dataset notification policy and configuration](docs/alert-routing.md#dataset-and-repository-notifications) |
| Deploy or load the feature branch preview | [Feature preview workflow](.claude/skills/feature-preview/SKILL.md) |
| Clean up completed branches | [Branch cleanup](scripts/README.md#branch-cleanup) |
| Run local tests, lint, and Terraform validation | [Complete CI preflight](docs/ci-preflight.md) |
| Release the TypeScript SDK | [SDK validation and releases](api/typescript/README.md#maintainer-validation-and-releases) and [release workflow](.github/workflows/publish-typescript-sdk.yml) |
| Use the GCS asset CLI | [GCS operations workflow](.claude/skills/gcp-shared-datasets/SKILL.md) |
| Build vector artifacts | [Artifact commands](scripts/README.md#dataset-operations) |
| Build or deploy the catalog web preview | [Static catalog workflow](.claude/skills/static-catalog-web-preview/SKILL.md) |

For dataset consumers, use the [consumer guide](docs/consumer-guide.md),
[Python SDK](api/python/README.md), or [TypeScript SDK](api/typescript/README.md).
The catalog's **Use this dataset** examples follow the
[portable workspace contract](docs/standards/workspace-snapshot-v1.md).
Passive dataset usage visibility and collection health are described in the
[usage collector guide](ingestion/dataset_usage/README.md).

## PR expectations

Use the [PR template](.github/PULL_REQUEST_TEMPLATE.md) and
[AGENTS.md completion criteria](AGENTS.md#completion-criteria). Describe the
changed files and behavior, affected assets/paths, validation commands and
results, consumer impact, and anything unverified. The operating guide owns
review requirements; canonical mutations also use the
[checked-in plan contract](.github/dataset-plans/README.md).

Before an authorized push, run Ruff and focused checks for the changed
boundaries. [Full local preflight](docs/ci-preflight.md) is available to reproduce
hosted validation; GitHub's `ci-ready` gate validates the prospective merge.

## Non-goals

This repository is not a home for one-off notebooks, large downloaded data,
a second copy of the shared bucket, or undocumented bucket conventions.
Keep reusable data in Cloud Storage and follow the linked standards and workflows.
