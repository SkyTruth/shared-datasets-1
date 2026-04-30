# AGENTS.md — shared-datasets-1 operating guide

This file is the canonical operational source of truth for AI agents and maintainers working in the `shared-datasets-1` repository or the associated shared Cloud Storage bucket. `CLAUDE.md` is a thin Claude Code shim that imports this file.

Read this file before changing repo structure, infrastructure, ingestion jobs, access protocols, bucket organization, dataset documentation, or remote GCS objects. Its scope is this repository tree and the associated shared bucket.

## 1. Mission

`shared-datasets-1` exists to make shared SkyTruth datasets easy to find, easy to understand, easy to refresh, and safe for multiple projects to consume.

The repo is the **control plane**:

- IaC for GCP resources.
- Scheduled ingestion jobs.
- Access protocols and API helpers.
- Dataset documentation templates.
- Catalog and taxonomy files.
- Agent instructions.

The bucket is the **data plane**:

- Canonical shared dataset files.
- `latest/` convenience copies.
- Optional dated `releases/`.
- Per-asset README files.
- Lightweight run records for scheduled jobs.

Do not confuse the two.

## 2. Authority order

When instructions conflict, follow this order:

1. Explicit user / issue / PR instruction.
2. This `AGENTS.md`.
3. Relevant repo-local skills in `.claude/skills/`.
4. Repo templates and docs.
5. Existing local style.
6. Your own judgment.

If a requested change would violate a safety rule, explain the conflict and propose the smallest safe alternative.

## Repo-local skills

`.claude/skills/` is the canonical checked-in repo-local skill catalog. `.agents/skills` must be a symlink mirror to `../.claude/skills` for Codex-native discovery. Do not keep a second live copy under a bare repo-root `skills/` directory.

Before substantial work, inspect `.claude/skills/*/SKILL.md` frontmatter and load any matching skill body. Repo-local skills override generic habits when they apply, after explicit user instructions and this file.

Current repo-local skills:

- `.claude/skills/align-virtual-environment/SKILL.md`
- `.claude/skills/deploy-scheduled-ingestion/SKILL.md`
- `.claude/skills/gcp-shared-datasets/SKILL.md`
- `.claude/skills/shared-datasets-compliance-audit/SKILL.md`

High-priority triggers:

- Use `.claude/skills/gcp-shared-datasets/SKILL.md` before inspecting, uploading, editing, replacing, publishing, or validating shared GCS objects.
- Use `.claude/skills/deploy-scheduled-ingestion/SKILL.md` before deploying or updating Cloud Run + Cloud Scheduler ingestion jobs.
- Use `.claude/skills/align-virtual-environment/SKILL.md` before creating, repairing, changing, or documenting Python environments.
- Use `.claude/skills/shared-datasets-compliance-audit/SKILL.md` for read-only bucket/repo compliance walkthroughs.

Keep skill examples repo-relative and maintainer-neutral. Avoid usernames, home-directory paths, shell-profile assumptions, and machine-local environment names. Treat the Git index as user-owned state: do not stage, unstage, reset, or restore staged files unless the user explicitly asks for that exact index operation.

## 3. Default environment

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

Use `uv` for local Python dependency management and repo-owned Python tools. Follow `.claude/skills/align-virtual-environment/SKILL.md` before creating, repairing, or changing Python environments. Do not create ad hoc pip virtualenvs or mamba environments for routine repo tooling.

If the actual bucket name differs, update `README.md`, this file, the skill, and any scripts/templates that mention the bucket.

## 4. Tooling decision

Use the right tool for the layer you are touching.

| Task | Preferred tool | Reason |
|---|---|---|
| Buckets, IAM, service accounts, Cloud Scheduler, Cloud Run, APIs, monitoring | Terraform | Declarative infrastructure, reviewable plans, stable state |
| Upload/download/edit/list dataset objects | Python GCS asset tooling in `scripts/gcs_asset.py` | Safe preconditions, testable, agent-friendly, enforceable conventions |
| Human diagnostics / emergency one-off copy | `gcloud storage` | Good CLI, supports generation preconditions, easy for manual use |
| Mounted read-heavy exploration | Cloud Storage FUSE, rarely | Useful for exploration, not canonical writes |
| Managing changing data objects | Not Terraform/Pulumi | Data files change too often and should not churn IaC state |

Important: Terraform may create placeholder folders, buckets, IAM, Cloud Run jobs, Cloud Scheduler jobs, Pub/Sub topics, and APIs. It should not manage frequently changing dataset files under `latest/` or `releases/`.

## 5. Remote object safety rules

Agents must follow these rules for GCS writes:

1. Never overwrite a canonical remote object unless you know the current generation or the operation is explicitly marked as an unsafe overwrite.
2. Prefer `--replace-generation <generation>` for edits to existing remote files.
3. Prefer no-clobber uploads for new files.
4. Never delete old `releases/` during a refresh unless explicitly instructed.
5. For cron jobs, write the dated release first, validate it, then update `latest/`.
6. For text docs, download, edit locally, and upload with generation preconditions.
7. Do not use Cloud Storage FUSE for canonical writes.
8. Do not commit downloaded data files to this repo unless they are tiny examples/templates.
9. Do not commit credentials or service account JSON.
10. Record remote paths changed in the PR description.

## 6. Approved data formats

Only these formats are approved by default:

| Format identifier | Extension/path | Use |
|---|---|---|
| `fgb` | `.fgb` | Canonical geographic vector data |
| `cog` | `.tif` | Canonical raster data as Cloud Optimized GeoTIFF |
| `zarr` | `.zarr/` prefix | Canonical multidimensional/chunked array products |
| `pmtiles` | `.pmtiles` | Web map tiles / visualization artifacts |
| `geojson` | `.geojson` | Small previews, small interchange files, debugging |
| `ndgeojson` | `.ndgeojson` | Newline-delimited GeoJSON features for streamable vector interchange/debugging |
| `csv` | `.csv` | Non-geometry tables only |

Rules:

- CSV must not contain geometry columns such as WKT, WKB, GeoJSON geometry blobs, latitude/longitude pairs intended as geometry, or encoded geometries unless clearly documented as noncanonical source/debug content.
- `.fgb` is the preferred canonical vector format.
- Cloud Optimized GeoTIFF is the preferred canonical raster format. Publish COGs as `.tif`, not raw GeoTIFFs. COGs must be internally tiled, internally overviewed, georeferenced, and self-contained with no required `.aux.xml`, `.ovr`, `.tfw`, or similar sidecars.
- COG object content type should be `image/tiff; application=geotiff; profile=cloud-optimized`.
- COG defaults are `BIGTIFF=IF_SAFER`, 512 pixel blocks, internal overviews, and lossless compression. Use nearest-neighbor overviews for categorical/class/mask rasters and average or documented continuous resampling for measured continuous grids.
- Zarr is approved only for true multidimensional, time-series, variable-rich, or chunked array products where COG would be a poor access pattern.
- `.pmtiles` is a serving/display artifact, not the canonical analytical source.
- `.png`, `.jpg`, `.jpeg`, and `.webp` are allowed only under `previews/` or as tile encodings inside `.pmtiles`; they are not analytical canonical data.
- `.nc`, `.grib`, `.grib2`, `.hdf`, `.h5`, raw non-COG `.tif`, and similar source rasters are allowed only under `source/`, `sources/`, or `archive/` by documented README exception.
- `.geojson` should be small enough to inspect or transfer easily.
- `.ndgeojson` is appropriate for streamable vector interchange/debugging; prefer `.fgb` for canonical analytical vector data unless there is a documented reason.
- If another format is required, update this file and explain why in the PR.

## 7. Bucket taxonomy

The bucket uses a lightweight Dewey-like taxonomy. Classify by what the asset **is**, not by the project that first needed it.

Top-level structure:

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

Suggested full tree:

```text
gs://skytruth-shared-datasets-1/

  _catalog/
    shared-datasets-catalog.csv

  _templates/
    dataset_README.template.md
    dataset_README.minimal.template.md
    cron_run.template.json

  _scratch/
    README.md

  _deprecated/
    README.md

  000-system/
    010-catalog/
    020-templates/
    030-validation/

  100-geographic-reference/
    110-boundaries/
    120-marine-boundaries/
    130-protected-areas/
    140-grids-indexes/
    150-places-gazetteers/

  200-imagery-derived/
    210-satellite-indexes/
    220-optical-derived/
    230-sar-derived/
    240-nightlights-thermal/
    250-weather-climate/

  300-infrastructure-industrial/
    310-energy/
    320-mining/
    330-offshore-platforms/
    340-transportation/
    350-permits-leases/

  400-events-observations/
    410-pollution-spills-slicks/
    420-flaring-thermal-events/
    430-alerts-notices/
    440-field-observations/
    450-scraped-feeds/

  500-conservation-ecosystems/
    510-protected-area-effectiveness/
    520-land-cover/
    530-habitat-condition/
    540-disturbance-recovery/

  600-maritime-ocean/
    610-ais-derived/
    620-vessel-registries/
    630-fishing-activity/
    640-ocean-activity/

  700-non-geographic-reference/
    710-country-admin-crosswalks/
    720-organizations-operators/
    730-units-codes-lookups/

  800-derived-ml-products/
    810-labels/
    820-features/
    830-predictions/
    840-evaluation-benchmarks/
```

### Category guide

| Category | Put assets here when... |
|---|---|
| `100-geographic-reference` | The dataset is a reusable geography/reference boundary/grid/place dataset. |
| `200-imagery-derived` | The dataset is derived from imagery or remote sensing. |
| `300-infrastructure-industrial` | The dataset represents physical assets, facilities, permits, leases, or infrastructure. |
| `400-events-observations` | The dataset represents incidents, detections, events, observations, alerts, or feeds. |
| `500-conservation-ecosystems` | The dataset represents land cover, habitats, ecosystems, conservation status, disturbance, or recovery. |
| `600-maritime-ocean` | The dataset represents vessels, AIS-derived products, fishing, or ocean activity. |
| `700-non-geographic-reference` | The dataset is a non-spatial lookup/crosswalk/table. |
| `800-derived-ml-products` | The dataset is labels, features, predictions, benchmarks, or model-ready data. |

### Classification examples

| Asset | Correct place |
|---|---|
| Country boundaries | `100-geographic-reference/110-boundaries/` |
| EEZ boundaries | `100-geographic-reference/120-marine-boundaries/` |
| WDPA | `100-geographic-reference/130-protected-areas/` |
| H3 grid helper | `100-geographic-reference/140-grids-indexes/` |
| Offshore platforms | `300-infrastructure-industrial/330-offshore-platforms/` |
| Mine footprints | `300-infrastructure-industrial/320-mining/` or `500-conservation-ecosystems/540-disturbance-recovery/`, depending on use |
| Oil slick detections | `400-events-observations/410-pollution-spills-slicks/` |
| VIIRS flare detections | `400-events-observations/420-flaring-thermal-events/` |
| AIS vessel registry | `600-maritime-ocean/620-vessel-registries/` |
| ISO country code lookup | `700-non-geographic-reference/710-country-admin-crosswalks/` |
| Cerulean training labels | `800-derived-ml-products/810-labels/` |
| Model predictions reused by multiple projects | `800-derived-ml-products/830-predictions/` |

If unsure, choose the category that best describes the dataset's long-term identity, then document the rationale in the README.

## 8. Asset folder structure

Default asset layout:

```text
{category}/{subcategory}/{asset-slug}/
  README.md
  latest/
    {asset-slug}.{ext}
    manifest.json        # only for multi-object assets such as Zarr
  releases/
    YYYY-MM-DD/
      {asset-slug}.{ext}
      {asset-slug}.zarr/ # only for Zarr and other approved prefix formats
  previews/
    {asset-slug}-preview.png
  runs/
    YYYY-MM-DD.json
```

Minimum valid asset:

```text
{category}/{subcategory}/{asset-slug}/
  README.md
  latest/
    {asset-slug}.{ext}
```

Use `releases/YYYY-MM-DD/` when:

- The asset is cron-updated.
- The asset is used by more than one major project.
- Reproducibility matters.
- The asset is expensive or difficult to recreate.
- A downstream model or analysis depends on specific snapshots.

Use `runs/YYYY-MM-DD.json` when:

- A scheduled job generated or refreshed the asset.
- A failed run needs to be documented.
- A backfill occurred.

Single-object assets, including COGs, may use the standard `latest/` and
`releases/YYYY-MM-DD/` file copies. Multi-object assets, including Zarr, must
write immutable data under `releases/YYYY-MM-DD/{asset-slug}.zarr/` and update
only `latest/manifest.json`. Do not mirror thousands of mutable Zarr chunk
objects under `latest/`.

The required Zarr latest manifest is a small JSON object with at least:

```json
{
  "asset_slug": "example-asset",
  "canonical_format": "zarr",
  "updated": "YYYY-MM-DD",
  "release_path": "gs://skytruth-shared-datasets-1/category/subcategory/example-asset/releases/YYYY-MM-DD/example-asset.zarr/"
}
```

Use `previews/` only for lightweight PNG, JPEG, or WebP inspection images. Use
`source/`, `sources/`, or `archive/` only for documented source-format
exceptions, not as canonical analytical data.

## 9. Naming rules

Use lowercase kebab-case.

Good:

```text
wdpa
offshore-platforms
natural-earth-admin0
iso-country-codes
cerulean-slick-labels
```

Bad:

```text
WDPA_latest_FINAL
JonaUpload
UseThisOne
platforms_v2_FINAL_really
```

File naming:

```text
{asset-slug}.{ext}
{asset-slug}-{layer}.{ext}
```

Examples:

```text
wdpa.fgb
wdpa.pmtiles
wdpa-preview.geojson
offshore-platforms.fgb
offshore-platforms.pmtiles
offshore-platforms-summary.csv
iso-country-codes.csv
cerulean-slick-labels.fgb
landsat-burn-severity.tif
weather-reanalysis.zarr/
```

Avoid dates in filenames when the date is already encoded in `releases/YYYY-MM-DD/`.

Preferred:

```text
releases/2026-04-28/wdpa.fgb
```

Avoid:

```text
latest/wdpa-2026-04-28-final.fgb
```

## 10. README requirements for each asset

Every asset must have a `README.md`.

Required fields:

- Title.
- Status.
- Owner.
- Last updated.
- Update cadence.
- Canonical file.
- Source.
- License / terms.
- Short explanation of what the asset is.
- File table.
- Schema notes or field notes.
- Property/column table with names, types, and short explanations where this can be derived from the source data or source documentation. If explanations are unknown, still list names/types and say definitions need source confirmation.
- Raster metadata table for canonical COG or Zarr assets, including CRS, resolution, dimensions, band semantics, dtype, nodata, units, scale/offset, and sampling where applicable.
- Update notes.

Use `templates/dataset_README.template.md` for important assets and `templates/dataset_README.minimal.template.md` for small/simple assets.

Do not create a large formal metadata packet unless the asset is important enough to justify it.

## 11. Catalog rules

Maintain the catalog at:

```text
catalog/shared-datasets-catalog.csv
```

The bucket may also contain:

```text
gs://skytruth-shared-datasets-1/_catalog/shared-datasets-catalog.csv
```

Catalog columns:

```text
asset_slug,title,category,subcategory,status,owner,update_cadence,canonical_path,canonical_format,available_formats,metadata_paths,has_pmtiles,has_geojson,has_csv,last_updated,source,license,notes
```

`canonical_format` must be one of `fgb`, `cog`, `zarr`, `pmtiles`,
`geojson`, `ndgeojson`, or `csv`. `available_formats` is a semicolon-separated
list of published format identifiers, such as `fgb;pmtiles` or `cog;pmtiles`.
`metadata_paths` is a semicolon-separated list of asset-root-relative metadata
paths such as `README.md`, `runs/YYYY-MM-DD.json`, or `latest/manifest.json`.
Keep `has_pmtiles`, `has_geojson`, and `has_csv` during the transition for
existing consumers.

Update the catalog when:

- A new asset is added.
- An asset moves.
- Owner changes.
- Update cadence changes.
- Canonical format changes.
- Asset is deprecated.
- Source/license changes.

For small edits to an existing README, catalog updates are optional unless catalog fields changed.

### Under-specified upload requests

When a user requests a dataset upload with minimal information, it is the agent's duty to learn as much as practical before choosing a README, asset slug, classification, or target directory.

Minimal user-provided context does not excuse weak metadata, vague naming, or arbitrary bucket placement. Before proposing or performing an upload, inspect and infer from all available clues, including:

- File names and local or remote directory hints.
- Source URLs, download locations, and local paths.
- Object metadata, file metadata, layer names, and embedded dataset metadata.
- Schemas, property names, property types, and example rows.
- Existing nearby bucket assets, README files, and catalog rows.
- Source documentation and internet searches when the source identity is discoverable.

Use these clues to generate an informed proposed README, lowercase kebab-case asset slug, taxonomy classification, and target asset directory. If confidence is still low, stop before remote writes and ask for the missing confirmation. Continue to ask before high-risk actions listed in `## 21. When to ask for human input`, but only after doing the discovery that repo files, bucket state, source materials, and reasonable searches can support.

## 12. Manual dataset addition workflow

1. Perform the under-specified upload discovery above when the user has not already provided complete source, schema, naming, and classification context.
2. Classify the asset using the taxonomy.
3. Pick a lowercase kebab-case asset slug.
4. Create the remote asset folder.
5. Upload files to `latest/`.
6. If needed, upload the same files to `releases/YYYY-MM-DD/`.
7. Add `README.md` using the template.
8. Add or update the catalog row.
9. Verify remote file paths.
10. Run `uv run python scripts/dataset_alerts.py upload-summary` with the asset slug and changed remote paths.
11. For canonical vector/table assets, run `uv run python scripts/dataset_alerts.py check-schema` against the successfully published local canonical file.
12. Open a PR with repo docs/catalog/script changes and list remote paths changed.

Minimal PR checklist:

```text
[ ] Folder is in the correct category/subcategory.
[ ] Asset slug is lowercase-kebab-case.
[ ] Only approved formats are used.
[ ] CSV files have no geometry.
[ ] Raster canonicals are valid COGs or documented Zarr manifests.
[ ] Preview images are under previews/ and source rasters are documented exceptions.
[ ] README.md exists and has owner/source/license/update cadence.
[ ] latest/ contains the recommended file.
[ ] Zarr latest/ contains only manifest.json.
[ ] releases/YYYY-MM-DD/ exists if cron-updated or multi-project-critical.
[ ] Catalog updated if needed.
```

## 13. Manual dataset update workflow

1. Read the existing asset README.
2. Inspect remote files and current object generations.
3. Prepare updated files locally.
4. If versioned, upload to `releases/YYYY-MM-DD/` first using no-clobber behavior.
5. Validate the release, including COG/Zarr raster checks when applicable.
6. Replace `latest/` files using generation preconditions.
7. Update `README.md` last-updated/update-notes fields.
8. Update catalog if catalog fields changed.
9. Run `uv run python scripts/dataset_alerts.py upload-summary` for meaningful manual updates.
10. For canonical vector/table assets, run `uv run python scripts/dataset_alerts.py check-schema` against the successfully published local canonical file.
11. Document changed remote paths in the PR.

## 14. Cron-updated dataset workflow

Cron jobs must be designed to survive retries, partial failures, and duplicate invocations.

Required behavior:

1. Determine the source version/date.
2. Determine the target release date.
3. Check whether `releases/YYYY-MM-DD/` already exists.
4. If the release already exists and is valid, exit successfully.
5. Write new output to a temporary work prefix or local temp dir.
6. Validate output.
7. Upload `releases/YYYY-MM-DD/` with no-clobber behavior.
8. Update `latest/` only after release upload succeeds.
9. Write `runs/YYYY-MM-DD.json` with status and paths.
10. Fail loudly and leave previous `latest/` intact when validation fails.

Required run record shape:

```json
{
  "asset_slug": "example-asset",
  "run_date": "YYYY-MM-DD",
  "status": "success|failed|skipped",
  "source_version": "",
  "release_path": "",
  "latest_paths": [],
  "rows": null,
  "notes": ""
}
```

### Scheduled ingestion architecture and live dependency guardrails

Each production cron job must have a distinct home:

```text
ingestion/<job_slug>/
  README.md
  Dockerfile
  run.py
tests/test_<job_slug>.py
terraform/envs/prod/<job_slug>.tf
```

Use `ingestion/common/` only for high-utility behavior that multiple jobs can
share safely, such as generation-preconditioned GCS publishing, run-record
writes, logging setup, subprocess helpers, content type selection, hashes, and
temporary-file cleanup. Keep source-specific parsing, filtering, schema choices,
asset slugs, canonical paths, conversion rules, and scheduler configuration
inside the owning job package and its Terraform file.

Do not import from another job package, such as `ingestion.wdpa_monthly`, unless
the task is explicitly maintaining that job. Future jobs must reuse
`ingestion/common/`, Terraform modules, templates, and documented patterns
instead of depending on or editing a live job's implementation.

Do not edit a functioning live job to support a new job unless the user
explicitly requests a behavior-preserving refactor. Preserve live surfaces unless
explicitly approved: Cloud Run job names, scheduler names, service account
identities, asset slugs, canonical GCS paths, output formats, schemas,
entrypoints, and run-record shape.

Any change to `ingestion/common/` must run the focused tests for every production
job that imports it.

## 15. Infrastructure rules

Use Terraform for:

- Bucket creation and configuration.
- IAM bindings.
- Service accounts.
- Cloud Scheduler jobs.
- Cloud Run jobs/services.
- Pub/Sub topics/subscriptions.
- Secret Manager references.
- Monitoring/alerting resources.
- Enabling GCP APIs.

For local production applies, prefer:

```bash
uv run python scripts/terraform_prod_apply.py
```

The wrapper creates a saved plan, applies that plan, preserves Terraform exit
codes, and posts Slack success/failure summaries. Direct Terraform is still
allowed for exceptional cases, but it will not send deployment summaries.

Do not use Terraform for:

- Uploading routine dataset files.
- Updating `latest/` files.
- Cron run outputs.
- README/catalog object mutations in the bucket, unless bootstrapping static templates.

Terraform directories should be organized by environment/module. Keep module boundaries boring and obvious.

Recommended structure:

```text
terraform/
  README.md
  envs/
    prod/
      main.tf
      variables.tf
      outputs.tf
  modules/
    bucket/
    scheduler_job/
    cloud_run_job/
    service_account/
```

## 16. API and access protocol rules

APIs and protocols should make common consumption patterns easy without hiding the underlying canonical paths.

Good API/access helpers:

- Resolve an asset slug to its `latest/` canonical path.
- List assets by category.
- Return README/catalog metadata.
- Generate signed URLs only when explicitly required.
- Provide stable examples for Python, curl, and web map clients.

Avoid:

- APIs that create a second undocumented taxonomy.
- APIs that mask data freshness or release identity.
- API behavior that mutates bucket objects without preconditions.

## 17. Security rules

- Do not commit credentials, tokens, service account keys, `.env` files, or private certs.
- Prefer Application Default Credentials locally.
- Prefer Workload Identity Federation or managed service accounts in CI/runtime.
- Use least-privilege service accounts for ingestion jobs.
- Separate read-only consumers from write-capable producers.
- Do not make data public without explicit approval.
- Do not add object ACL-based workflows.
- Do not expose signed URLs in logs or PR comments unless intentionally temporary and non-sensitive.

## 18. Agent GCS operation protocol

Before writing to GCS:

1. Read `.claude/skills/gcp-shared-datasets/SKILL.md`.
2. Confirm target bucket and path.
3. Confirm the asset folder follows this taxonomy.
4. Confirm approved formats.
5. Inspect existing remote object generation if replacing.
6. Use safe upload/copy behavior.
7. Verify remote object after upload.
8. Update README/catalog if needed.

Recommended commands:

```bash
uv run python scripts/gcs_asset.py list gs://skytruth-shared-datasets-1/100-geographic-reference/
uv run python scripts/gcs_asset.py stat gs://skytruth-shared-datasets-1/path/to/object
uv run python scripts/gcs_asset.py download gs://skytruth-shared-datasets-1/path/to/object /tmp/object
uv run python scripts/gcs_asset.py upload ./new-file.fgb gs://skytruth-shared-datasets-1/path/to/new-file.fgb
uv run python scripts/gcs_asset.py upload ./README.md gs://skytruth-shared-datasets-1/path/README.md --replace-generation 123456789
```

## 19. Anti-patterns

Do not do these:

```text
final_final_v3.geojson
latest.csv as undocumented canonical truth
project-specific top-level folders
manual uploads with no README
CSV files containing geometry as canonical data
raw GeoTIFF as undocumented canonical raster data
Zarr chunk objects mirrored directly under latest/
sidecar-dependent raster canonicals
silent overwrites of latest/
cron jobs that delete old releases
Terraform-managed frequently changing dataset files
Cloud Storage FUSE writes to canonical data
credentials committed to Git
new taxonomy invented in a notebook/script
asset docs only in Slack
README with no owner/source/license
```

## 20. Review expectations

For full bucket/repo compliance walkthroughs, use `.claude/skills/shared-datasets-compliance-audit/SKILL.md`. Compliance audits are read-only: flag issues, identify uploader/owner hints where available, and offer fixes only after human approval.

Review for:

- Correct category.
- Correct filename and folder names.
- Approved file formats.
- Valid raster layout and metadata for COG/Zarr assets.
- Minimal but sufficient docs.
- Safe remote mutation behavior.
- Idempotent cron behavior.
- No secrets.
- No large data files committed.
- Catalog updated if needed.
- Existing consumers not broken.

For the first version of this repository, prefer constructive corrections over hard rejection unless the issue is safety/security/data-loss related.

## 21. When to ask for human input

Ask before:

- Creating a new top-level category.
- Making a dataset public.
- Deleting or moving existing releases.
- Changing canonical format standards.
- Granting broad write permissions.
- Introducing a second IaC framework.
- Renaming an existing asset slug.
- Making an incompatible schema change to a widely used asset.

Do not ask before making routine template/docs corrections that clearly follow this file.

## 22. Completion criteria for agent tasks

A task is complete when:

- Files are in the correct repo/bucket location.
- Remote writes were done with safe preconditions or explicitly documented as unsafe.
- README/catalog/templates are updated when relevant.
- The PR or final response lists changed files and remote paths.
- Commands run or validation performed are stated.
- Any uncertainty is explicitly called out.
