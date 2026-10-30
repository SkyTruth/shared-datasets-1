# Monthly WDPA Job

The scheduled worker automatically builds, validates and publishes `wdpa-marine`
and `wdpa-terrestrial`. See the [operations runbook](../../docs/wdpa-monthly-runbook.md)
for deployment, activation, monitoring and owned recovery.

## Runtime

The production Dockerfile runs `python -m ingestion.wdpa_monthly.automatic`.
Cloud Run supplies `CLOUD_RUN_EXECUTION`; the image embeds
`SHARED_DATASETS_EXECUTOR_SHA`. The project and bucket are `shared-datasets-1`
and `skytruth-shared-datasets-1`.

Without an override, the worker targets the first day of the current UTC month.
Optional execution-only `RUN_DATE=YYYY-MM-DD` selects that date's month.
Scheduler invokes the worker at 09:00 UTC on days 1–10. Completed monthly releases
produce skipped check-ins without another source download. An unavailable source
also records a skip and is retried at the next scheduled invocation.

The worker freezes committed identity and translation generations, downloads the
source once, runs the deterministic compatibility sample and complete build, and
stages both validated realm bundles before committing an immutable descriptor.
Publication revalidates the descriptor, staged hashes and live predecessor states
before writing through `OwnedGeneratedPublisher`. Unchanged normalized content
skips release/latest writes. See [source fingerprints](../../docs/source-fingerprints.md#wdpa-wdpa-retained-content-v1).

No monthly approval, acceptance-file update, bundle pin or redeployment is needed.
`WDPA_PROMOTION_BUNDLE` and sampled publication are rejected by the scheduled
entrypoint. Code changes retain the normal protected deployment process.
Both assets require installed `generated-2026-v1` publication state. Interrupted
transactions retain ownership, reservations, intent and checkpoints; recover the
original transaction rather than resetting state or replacing partial releases.

`WDPA_SOURCE_URL_TEMPLATE` defaults to
`https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_WDOECM_{month_token}_Public_all_shp.zip`.

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

Production deploys the exact image tested and retained by main CI. Terraform
supplies its immutable registry reference and config digest as `WDPA_BUILD_IMAGE`
and `WDPA_BUILD_IMAGE_CONFIG_DIGEST`. These identify the executing code and stay
constant across monthly data refreshes.

## Execution monitoring

WDPA uses the shared worker and Scheduler failure alerts. Successful publication
and meaningful skips update per-asset release indexes, which supply the catalog's
Last check-in. Deployment verification follows pending deployment records.

The former five-minute observer schedule is paused, its health alarm is disabled,
and the catalog no longer polls its status feed. Dormant infrastructure is
retained for the existing update-only protected deployment paths. Do not resume
the observer during routine deployment. See [alert routing](../../docs/alert-routing.md#wdpa-execution-driven-monitoring)
for rollout order and verification.

## Limits

Terraform configures 4 CPU / 8 GiB, `86400s`, zero retries and a 100 GiB ephemeral
DISK at `/work`. `TMPDIR=/work/tmp` and
`SHARED_DATASETS_WORKDIR=/work/shared-datasets-1` keep native/SQLite scratch there.
Cloud executions reject a missing or memory-backed mount before downloads.
Tippecanoe uses at most four threads; SQLite caches are capped at 64 MiB and
sorting uses disk. The dormant observer has no ephemeral disk and uses 1 CPU / 512 MiB,
120 seconds and zero retries.

Processing reports emit `wdpa_phase_started`/`wdpa_phase_resources` with elapsed
time, kernel lifetime memory peak including file cache, process RSS, scratch peak,
artifact sizes and native tool versions. Scratch covers all `/work`, including
open unlinked files. Missing peak telemetry fails measurement. Acceptance limits
and recovery belong to the [operations runbook](../../docs/wdpa-monthly-runbook.md).
