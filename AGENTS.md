# AGENTS.md - shared-datasets-1 operating guide

This file is the canonical routing and safety contract for AI agents and
maintainers working in the `shared-datasets-1` repository or its shared Cloud
Storage bucket. `CLAUDE.md` is a thin Claude Code shim that imports this file.

Read this file before changing repo structure, infrastructure, ingestion jobs,
access protocols, bucket organization, dataset documentation, or remote GCS
objects. Load the focused skill or document named here before doing task-specific
work.

## Mission

`shared-datasets-1` makes shared SkyTruth datasets easy to find, understand,
refresh, and safely consume from multiple projects.

The repo is the control plane:

- Infrastructure as code for GCP resources.
- Scheduled ingestion jobs.
- Access protocols and API helpers.
- Dataset documentation templates.
- Catalog and taxonomy files.
- Agent instructions.

The bucket is the data plane:

- Canonical shared dataset files.
- `latest/` convenience copies.
- Optional dated `releases/`.
- Per-asset README files.
- Lightweight run records for scheduled jobs.

Do not confuse the two.

## Authority Order

When instructions conflict, follow this order:

1. Explicit user, issue, or PR instruction.
2. This `AGENTS.md`.
3. Relevant repo-local skills in `.claude/skills/`.
4. Repo templates and docs.
5. Existing local style.
6. Your own judgment.

If a requested change would violate a safety rule, explain the conflict and
propose the smallest safe alternative.

## Repo-Local Skills

`.claude/skills/` is the canonical checked-in repo-local skill catalog.
`.agents/skills` must be a symlink mirror to `../.claude/skills` for
Codex-native discovery. Do not keep a second live copy under a bare repo-root
`skills/` directory.

Before substantial work, inspect `.claude/skills/*/SKILL.md` frontmatter and
load any matching skill body. Repo-local skills override generic habits when
they apply, after explicit user instructions and this file.

Current repo-local skills:

- `.claude/skills/align-virtual-environment/SKILL.md`
- `.claude/skills/deploy-scheduled-ingestion/SKILL.md`
- `.claude/skills/gcp-shared-datasets/SKILL.md`
- `.claude/skills/publish-shared-dataset/SKILL.md`
- `.claude/skills/repo-alert-commit-messages/SKILL.md`
- `.claude/skills/shared-datasets-compliance-audit/SKILL.md`
- `.claude/skills/static-catalog-web-preview/SKILL.md`

High-priority triggers:

- Use `gcp-shared-datasets` before inspecting, downloading, uploading, editing,
  replacing, publishing, or validating shared GCS objects.
- Use `publish-shared-dataset` before manually adding, updating, publishing, or
  documenting a shared dataset asset.
- Use `deploy-scheduled-ingestion` before deploying or updating Cloud Run and
  Cloud Scheduler ingestion jobs.
- Use `align-virtual-environment` before creating, repairing, changing, or
  documenting Python environments.
- Use `repo-alert-commit-messages` before committing staged changes or preparing
  repo-alert commit-message blocks.
- Use `shared-datasets-compliance-audit` for read-only bucket/repo compliance
  walkthroughs.
- Use `static-catalog-web-preview` before building, updating, QAing, or
  deploying the static catalog web preview under `_catalog/web/`.

Keep skill examples repo-relative and maintainer-neutral. Avoid usernames,
home-directory paths, shell-profile assumptions, and machine-local environment
names.

## Default Environment

Expected GCP project:

```text
shared-datasets-1
```

Expected shared bucket:

```text
gs://skytruth-shared-datasets-1/
```

Use environment variables in scripts where possible:

```bash
export GOOGLE_CLOUD_PROJECT=shared-datasets-1
export SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
```

Use `uv` for repo-owned Python tools. Follow `align-virtual-environment` before
creating, repairing, or changing Python environments. Do not create ad hoc pip
virtualenvs or mamba environments for routine repo tooling.

If the actual bucket name differs, update `README.md`, this file, the relevant
skills/docs, scripts, and templates that mention the bucket.

## Temporary Local Workspaces

Temporary local file hygiene is a repo-wide rule, not a standalone skill. Follow
`docs/standards/local-temp-workspaces.md` before creating downloads, generated
artifacts, scratch scripts, exported READMEs, catalog builds, or audit outputs.

Use the repo temp root by default:

```text
${SHARED_DATASETS_WORKDIR:-${TMPDIR:-/tmp}/shared-datasets-1}
```

Keep task files in a named child directory such as `vector-assets/{asset-slug}/`,
`catalog-web/`, `readmes/`, `downloads/{asset-slug}/`, or
`_scratch/{task-slug}-{YYYYMMDDTHHMMSSZ}/`. Do not scatter files directly under
`/tmp`, the repo root, `Downloads`, or `Desktop`.

At the end of work, report any retained local work directories that may matter.
Do not delete stale directories from prior tasks without explicit action-time
confirmation; when cleanup is appropriate, identify exact paths, size/age, and
why they are safe to remove. Never broad-delete the shared temp root.

## Source Map

| Concern | Source |
|---|---|
| Agent routing and non-negotiable safety rules | `AGENTS.md` |
| Repo purpose and human quick start | `README.md` |
| Dataset category data | `catalog/categories.yaml` |
| Dataset taxonomy guidance | `docs/standards/dataset-taxonomy.md` |
| Asset layout, formats, naming, README requirements | `docs/standards/asset-layout-and-formats.md` |
| Temporary local file/workspace hygiene | `docs/standards/local-temp-workspaces.md` |
| Manual dataset add/update/publish workflow | `.claude/skills/publish-shared-dataset/SKILL.md` |
| Remote GCS object safety and commands | `.claude/skills/gcp-shared-datasets/SKILL.md` |
| Scheduled ingestion deployment | `.claude/skills/deploy-scheduled-ingestion/SKILL.md` |
| Static catalog web preview | `.claude/skills/static-catalog-web-preview/SKILL.md` |
| Bucket/repo compliance audits | `.claude/skills/shared-datasets-compliance-audit/SKILL.md` |
| Repo-alert commit messages | `.claude/skills/repo-alert-commit-messages/SKILL.md` |
| Python environment alignment | `.claude/skills/align-virtual-environment/SKILL.md` |
| Dataset README templates | `templates/` |
| Infrastructure | `terraform/` |

## Non-Negotiable Rules

Remote GCS objects:

- Canonical writes to shared dataset prefixes must go through the approved
  publisher identity, normally the GitHub Actions
  `shared-datasets-production` environment. Humans and general-purpose agents
  may stage reviewed bytes under `_scratch/pending-publishes/`, but must not
  mutate canonical `latest/`, `releases/`, `_catalog/`, or dataset README
  objects directly from a local terminal.
- Never overwrite a canonical remote object unless you know the current
  generation or the operation is explicitly marked as an unsafe overwrite.
- Prefer generation-preconditioned replacements and no-clobber uploads through
  `scripts/gcs_asset.py`.
- Never delete old `releases/` during a refresh unless explicitly instructed.
- For cron jobs, write a dated release first, validate it, then update
  `latest/`. If the source or generated output is unchanged, write a skipped
  run record and do not write new release or `latest/` dataset artifacts.
- Do not use Cloud Storage FUSE for canonical writes.
- Record remote paths changed in the PR description or final response.
- `_scratch/` is noncanonical staging space. Do not cite `_scratch/` objects as
  shared dataset contracts, and do not treat their existence as approval to
  publish.

Dataset metadata and local files:

- `docs/assets/{asset-slug}.md` is the local metadata source for asset catalog
  rows and bucket README content, including the required citation for the
  original source publication or authoritative dataset release.
- Do not edit `catalog/shared-datasets-catalog.csv` directly for normal asset
  metadata changes; update the asset doc and regenerate catalog outputs.
- Dataset upload announcements are operational notifications, not Git commit
  gates. Do not infer announcement state solely from commit history, and do not
  block a requested commit to send or verify an announcement. If the publish
  workflow sent an upload summary, record that in the final response. If
  announcement state is uncertain, report the uncertainty instead of attempting
  duplicate Slack notifications unless the human explicitly asks for one.
- Keep generated publishable data artifacts outside the repo tree unless they
  are tiny intentional fixtures.
- Do not commit downloaded data files to this repo unless they are tiny
  examples/templates.
- Do not add new canonical file formats without updating the standards docs,
  templates, catalog schema/validation, and review checklist.
- For publishable bytes that depend on native geospatial CLIs such as GDAL,
  Tippecanoe, or PMTiles, record resolved tool paths and versions. Use a pinned
  repo-owned toolchain when reproducibility matters.

Infrastructure and security:

- Use Terraform for buckets, IAM, service accounts, Cloud Scheduler, Cloud Run,
  Pub/Sub, monitoring, APIs, and similar infrastructure.
- Do not use Terraform or Pulumi for frequently changing dataset files under
  `latest/`, `releases/`, or `runs/`.
- Do not commit credentials, tokens, service account keys, `.env` files, private
  certs, or signed URLs unless explicitly temporary and non-sensitive.
- Prefer Application Default Credentials locally and managed identities in
  CI/runtime.
- Do not make data public without explicit approval.
- Do not add object ACL-based workflows.
- Break-glass canonical object mutation is reserved for
  `shared-datasets-breakglass@skytruth.org` or an explicitly approved emergency
  identity. Any break-glass use must be called out with changed remote paths,
  generations, and rationale.

Git and history:

- Treat the Git index and commit history as user-owned state.
- Never stage, unstage, commit, amend, reset, restore, or otherwise mutate the
  Git index/history unless the user explicitly asks for that exact Git
  operation.
- For manual dataset add/update/upload/publish/delete requests, the requested
  reviewed mutation workflow includes creating a focused branch, staging only
  related repo metadata and workflow files, committing, pushing, and opening a
  PR that requests review from `jonaraphael`, unless the user asks to stop
  before PR. This exception does not permit staging unrelated files, amending
  history, applying Terraform, or mutating canonical GCS objects from a local
  terminal.
- When committing is explicitly requested, use `repo-alert-commit-messages`
  before creating the commit.

## When To Ask For Human Input

Ask before:

- Creating a new top-level category.
- Making a dataset public.
- Deleting or moving existing releases.
- Changing canonical format standards.
- Granting broad write permissions.
- Introducing a second IaC framework.
- Renaming an existing asset slug.
- Making an incompatible schema change to a widely used asset.
- Using unsafe overwrite behavior outside `_scratch/`.

Do not ask before making routine template/docs corrections that clearly follow
this file and the relevant focused docs.

## Completion Criteria

A task is complete when:

- Files are in the correct repo or bucket location.
- Remote writes were done with safe preconditions or explicitly documented as
  unsafe.
- README/catalog/templates are updated when relevant.
- For a new asset slug or meaningful dataset release, any dataset upload
  announcement that was sent, skipped, or uncertain is reported without blocking
  otherwise complete commit or publish work.
- If a requested commit adds substantially exciting new repository
  functionality, the committing agent generated and appended any warranted
  fenced `repo-alert` block without asking the human to decide.
- The PR or final response lists changed files and remote paths.
- Any opened dataset publish PR requests review from `jonaraphael` and includes
  the staged `_scratch/pending-publishes/` source URIs, source generations,
  intended canonical destination URIs, destination-generation expectations, and
  validation performed. If the PR is expected to promote data after approval, it
  must include a fenced `shared-datasets-publish-plan` JSON block matching the
  staged objects and generation preconditions.
- Any opened dataset deletion PR requests review from `jonaraphael` and includes
  exact target object URIs, current generations, rationale, consumer impact,
  replacement/deprecation state, and a fenced `shared-datasets-delete-plan` JSON
  block. Prefix, wildcard, and generation-less deletes are not valid.
- Commands run or validation performed are stated.
- Any uncertainty is explicitly called out.
