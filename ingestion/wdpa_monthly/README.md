# Monthly WDPA Job

The production worker publishes approved retained bundles for `wdpa-marine` and
`wdpa-terrestrial`. Use the [monthly operations runbook](../../docs/wdpa-monthly-runbook.md)
for the pinned images, deployment prerequisites, execution commands, observer,
recovery and evidence paths.

## Runtime

Production entrypoint (`Dockerfile.promotion`):

```bash
python -m ingestion.wdpa_monthly.publication_only
```

Required environment:

```bash
GOOGLE_CLOUD_PROJECT=shared-datasets-1
SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
WDPA_PROMOTION_BUNDLE=<accepted bundle reference JSON>
```

`WDPA_PROMOTION_BUNDLE` is the accepted descriptor's URI, generation, size and
SHA-256. The worker verifies required staged bytes and live predecessors before
new publication. It copies exact artifacts through `OwnedGeneratedPublisher`;
it does not download or process the upstream source or rebuild translations.
An already committed realm retains its verified receipts and artifacts.

Optional `RUN_DATE=YYYY-MM-DD` controls the release date. Without an override,
the worker uses the first day of the current UTC month. The accepted bundle must
match that month; a missing or wrong-month bundle fails before publication.
Scheduler `wdpa-monthly` invokes it at 09:00 UTC on days 1–10. Repeated completed
publications verify both receipts and return skipped without artifact downloads.

Both assets require installed `generated-2026-v1` publication state. The publisher
reserves IDs and claims ownership before exposing artifacts. Runtime identity
binds `CLOUD_RUN_EXECUTION` and embedded `SHARED_DATASETS_EXECUTOR_SHA`.
Interrupted transactions retain claims, reservations, intent and checkpoints;
retry through the original owner and captured executor/inputs. Follow the
[feature-ID installation/recovery runbook](../../docs/feature-id-reset-installation.md).

### Retained-build runtime

The source-processing image uses `python -m ingestion.wdpa_monthly.run`; its
isolated build workflow retains artifacts for later reviewed promotion.
`WDPA_SOURCE_URL_TEMPLATE` defaults to
`https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_WDOECM_{month_token}_Public_all_shp.zip`
and supports `{run_date}`, `{year}`, `{month}` and `{month_token}`.

The builder selects point/polygon layers from the Protected Planet shapefile ZIP.
It uses `MARINE` when present; current `REALM` values `Marine` and `Coastal` go to
`wdpa-marine`, and `Terrestrial` goes to `wdpa-terrestrial`. The output schema is
the union of source fields; polygon-only `GIS_M_AREA`/`GIS_AREA` remain null for
points. Source fields are preserved. The builder does not buffer points,
calculate areas, build statistics tables, update Strapi or write database payloads.

Normalization and a verified baseline determine a complete allocation before
publication. FGB and canonical metadata retain `feature_id`, `geometry_hash` and
`properties_hash`; PMTiles carry geometry and `feature_id`. Scratch SQLite stores
are disposable and never replace durable publication state.

### Translation runtime

Each bundle includes a translation-source CSV and `es`, `fr`, `id`, `pt`, `pt_br`
and `sw` metadata sidecars. The builder reads the 17 translation fields and locales
from the generated catalog. It matches exact `SITE_PID` and source-value hashes;
numeric feature IDs do not join translations across releases or contracts.
Unambiguous field/locale/source-text reuse preserves review states and notes;
conflicting text stays site-specific.

Inputs come from exact committed manifest generations; legacy v1 receipts supply
extras their old manifests omit. The explicit first-reset state uses the verified
legacy source pair and approved supplement. Once both first publications finish,
later builds use their committed CSVs. Missing state or incomplete committed
bundles fail rather than selecting arbitrary `latest/` objects.

`NAME` preserves the upstream original/local name. Canonical `NAME_ENG` is the
English source field; localized `NAME_ENG` is the active-locale display name.
New/changed text without a usable translation retains canonical text. Reports
partition values into `current`, `stale` and `missing`; editorial review remains
separate from availability. Local translation-debt CSVs are build outputs.
The job never contacts a translation provider. Use the
[translation maintenance skill](../../.claude/skills/update-feature-metadata-translations/SKILL.md)
for reviewed updates.

### Published objects

For each asset, publication writes:

```text
100-geographic-reference/130-protected-areas/{asset}/releases/YYYY-MM-DD/{asset}.{artifact}
100-geographic-reference/130-protected-areas/{asset}/latest/{asset}.{artifact}
100-geographic-reference/130-protected-areas/{asset}/runs/YYYY-MM-DD.json
_catalog/releases/{asset}.json
```

The twelve artifacts are FGB, PMTiles, canonical metadata, schema, manifest,
translation-source CSV and six locale metadata sidecars. Manifests record actual
canonical generations and translation coverage. Release writes are no-clobber;
`latest/` replacements require the observed generation. Partial releases resume
only through their owned transaction. A new execution cannot abandon a claim.

## Container

Build a local source-processing/diagnostic image from the repo root:

```bash
docker build --platform linux/amd64 -f ingestion/wdpa_monthly/Dockerfile -t wdpa-monthly .
```

Production uses `Dockerfile.promotion`, inheriting the accepted producer's native
tools/dependencies and adding reviewed consumer/publication code and the current
locale catalog. `WDPA_ACCEPTED_BUILD_SOURCE_SHA256` pins the producer fingerprint.
The publication-only entrypoint rejects a missing bundle before source processing;
`WDPA_FAIL_BEFORE_WRITES=true` supports the controlled alert-delivery probe.

## Deploy

Use the [monthly operations runbook](../../docs/wdpa-monthly-runbook.md#deploy-an-accepted-month)
and [scheduled-ingestion skill](../../.claude/skills/deploy-scheduled-ingestion/SKILL.md).
Production uses protected `wdpa-monthly-deploy.yml` from reviewed `main`.
Deployments prepare a publication layer and pin its registry digest; the canary
uses the captured bundle and build date. New months require newly reviewed build
authority. Do not use a local production Terraform apply or hand-built image.

Pause future automatic executions when stopping the job:

```bash
gcloud scheduler jobs pause wdpa-monthly \
  --location=us-central1 --project=shared-datasets-1
```

Pausing does not drain running executions or release claims. Permanent teardown
requires a reviewed PR and a constrained protected workflow covering worker,
observer, scheduler and IAM. Published datasets and historical releases remain.

## Limits

Terraform configures 4 CPU / 8 GiB, `86400s`, zero retries and a 100 GiB ephemeral
DISK at `/work`. `TMPDIR=/work/tmp` and
`SHARED_DATASETS_WORKDIR=/work/shared-datasets-1` keep native/SQLite scratch there.
Cloud executions reject a missing or memory-backed mount before downloads.
Tippecanoe uses at most four threads; SQLite caches are capped at 64 MiB and
sorting uses disk. The observer has no ephemeral disk and uses 1 CPU / 512 MiB,
120 seconds and zero retries.

Processing reports emit `wdpa_phase_started`/`wdpa_phase_resources` with elapsed
time, kernel lifetime memory peak including file cache, process RSS, scratch peak,
artifact sizes and native tool versions. Scratch covers all `/work`, including
open unlinked files. Missing peak telemetry fails measurement. Acceptance limits
and recovery belong to the [operations runbook](../../docs/wdpa-monthly-runbook.md).
