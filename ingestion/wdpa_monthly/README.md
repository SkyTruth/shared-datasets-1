# Monthly WDPA Job

> **First deployment:** install the reviewed feature-ID reset while the job is
> paused and drained. Deployment verifies installed publication state before
> building the image. See the [installation runbook](../../docs/feature-id-reset-installation.md).

This job publishes two bare-bones WDPA/WDOECM assets:

- `wdpa-marine`
- `wdpa-terrestrial`

It downloads the monthly Protected Planet shapefile zip, selects the source
point and polygon layers, and splits rows by the source split field. `MARINE`
is used when present; current WDPA/WDOECM shapefiles use `REALM`, with
`Marine` and `Coastal` routed to `wdpa-marine` and `Terrestrial` routed to
`wdpa-terrestrial`.

The output schema is the union of source fields. The current source has
polygon-only fields such as `GIS_M_AREA` and `GIS_AREA`; those fields are
preserved and are null for point rows rather than being dropped or renamed.

The job intentionally does not rename fields, buffer points, calculate areas,
build statistics tables, update Strapi, or write database payloads.

Name fields keep the upstream WDPA schema. `NAME` is the protected or conserved
area name as supplied by WDPA, generally in the original or local language/script
of the protected area. `NAME_ENG` is the upstream English-name source field in
canonical FGB and canonical metadata. Localized metadata sidecars preserve the
source field names, so translated display names are written back to `NAME_ENG`.
In localized sidecars, `NAME_ENG` is a misleading legacy name; consumers should
treat it as the active-locale display name, effectively `name_localized`, not as
an English-only value.

## Translation reuse

The job rebuilds `es`, `fr`, `id`, `pt`, `pt_br`, and `sw` metadata sidecars
together with each release. `ingestion/wdpa_monthly/translations.py` lists the
17 supported text fields. The translation-source CSV and all six sidecars are
part of the same owned publication as the geometry and canonical metadata.

Translations are matched first by the exact `SITE_PID` and original source-value
hash. Numeric `feature_id` values never join translations across releases or
identity contracts. An unambiguous existing translation of the same source text,
field, and locale can fill another record. Conflicting text translations remain
site-specific. Existing review states and notes are retained for direct matches;
phrase reuse is marked `reused_translation`.

The job downloads the exact canonical metadata and translation CSV generations
recorded in its last committed publication receipt. While either asset is in the
explicit first-reset state, both assets use the verified June 9 legacy source
bundles pinned in `translations.py`. Keeping that pair fixed across retries
preserves translations for protected areas that move between marine and terrestrial.
That state also binds a reviewed gap supplement by staged object path, generation,
and SHA-256. Both assets must approve the same supplement; the first build verifies
and consumes it without replacing established translations. The first new release
must have complete requested translations before the publisher reserves IDs.
Once both first publications finish, later builds use their committed CSV and
do not reload the reset supplement.
Missing state or an incomplete committed bundle fails; it does not select arbitrary
`latest/` files. One disposable SQLite index serves both WDPA assets. Large CSVs
are streamed and the downloaded copies are locally compressed.

In subsequent releases, new or changed text without a verified translation retains the canonical value.
Its CSV row is empty with `review_state=translation_failed`; the run record reports
the unresolved counts. The job does not contact a translation provider or claim
that fallback text is translated. Follow the [translation evidence and tooling](../../docs/wdpa-translation-reset-evidence.md)
to fill these tasks without retranslating the full dataset. New machine results
remain labeled as machine output rather than human review.

## Runtime

Entrypoint:

```bash
python -m ingestion.wdpa_monthly.run
```

Required environment:

```bash
GOOGLE_CLOUD_PROJECT=shared-datasets-1
SHARED_DATASETS_BUCKET=skytruth-shared-datasets-1
```

Optional environment:

```bash
RUN_DATE=YYYY-MM-DD
WDPA_SOURCE_URL_TEMPLATE=https://d1gam3xoknrgr2.cloudfront.net/current/WDPA_WDOECM_{month_token}_Public_all_shp.zip
```

`RUN_DATE` controls the release date and source month token. When `RUN_DATE` is
unset, the job uses the first day of the current UTC month so repeated scheduled
attempts in the source availability window target one stable release path. The
default source template supports `{run_date}`, `{year}`, `{month}`, and
`{month_token}`.

## Publishing behavior

For each asset, the job writes:

```text
100-geographic-reference/130-protected-areas/{asset}/releases/YYYY-MM-DD/{asset}.{artifact}
100-geographic-reference/130-protected-areas/{asset}/latest/{asset}.{artifact}
100-geographic-reference/130-protected-areas/{asset}/runs/YYYY-MM-DD.json
```

The twelve artifacts are FGB, PMTiles, canonical metadata, schema, manifest,
the translation-source CSV, and six locale metadata sidecars.

Release uploads use no-clobber GCS generation preconditions. `latest/` uploads
replace only the current observed generation. If a successful run record exists,
that asset is skipped. If release objects exist without a successful run record,
the job fails before touching `latest/`.

The upstream Protected Planet ZIP for a new month is not guaranteed to exist on
the first day of the month. HTTP 403/404 source responses are treated as "not
available yet"; the job exits successfully with skipped records and writes no
GCS objects. The production scheduler runs daily on days 1-10 of each month, so
the first run after the source appears publishes the stable month-start release,
and later attempts skip because the success run record exists.

## Container

Build from the repo root:

```bash
docker build -f ingestion/wdpa_monthly/Dockerfile -t wdpa-monthly .
```

## Deploy

Production deploys run through `.github/workflows/wdpa-monthly-deploy.yml` in
the `shared-datasets-production` environment. Merging reviewed changes that
touch this job, `ingestion/common/`, the copied `scripts/` modules, reviewed
`catalog/feature-identity-resolutions/` decisions, or the job Terraform builds
a fresh image from `main`, smoke-tests it, pushes an immutable digest, applies
only the worker and explicitly allowlisted observer resources/IAM, and starts an
async canary execution. If the schedule is paused, deployment skips the canary
unless the dispatch supplies an explicit `canary_run_date`. The image ships the
feature-identity resolutions directory, so merging reviewed ambiguity decisions redeploys the job and the
next scheduled attempt picks them up. Use the workflow's `canary_run_date`
dispatch input for a deliberate backfill or metadata-contract repair run. Do
not deploy this job with a local `terraform apply` or by pushing hand-built
images.

## Cost Controls and Teardown

Immediate stop:

```bash
gcloud scheduler jobs pause wdpa-monthly \
  --location=us-central1 \
  --project=shared-datasets-1
```

Pausing the scheduler stops future automatic monthly runs without deleting the
Cloud Run Job, service accounts, IAM, Terraform state, or published GCS data.

Permanent infrastructure teardown requires a reviewed PR and a constrained
protected production workflow with explicit worker, observer, scheduler and IAM
allowlists. Do not run a local Terraform destroy/apply or delete dataset releases,
latest files, run records, README files or catalog rows as part of cost teardown.

## Processing limits and local replay

See [validation and rollout evidence](../../docs/wdpa-processing-validation.md)
for compatibility results and the outstanding resource acceptance prerequisites.

The worker targets 4 vCPU / 8 GiB, a 24-hour timeout and a 100 GiB ephemeral
DISK volume at `/work`. `TMPDIR=/work/tmp` and
`SHARED_DATASETS_WORKDIR=/work/shared-datasets-1` put GDAL, Tippecanoe and SQLite
scratch on that disk. A Cloud Run execution fails before downloads when the
mount is missing or memory-backed. Tippecanoe uses at most four threads; SQLite
caches are capped at 64 MiB and sorting uses disk.

The [Cloud Run disk feature](https://docs.cloud.google.com/run/docs/configuring/jobs/ephemeral-disk)
is Preview. Obtain the regional per-instance quota for 100 GiB before rollout;
the default per-instance limit is 10 GiB. Disk contents are disposable and are
never publication authority.

Processing filters the source into a GeoPackage, pipes the existing GDAL 3.6.2
GeoJSONSeq normalization into a binary normalized GeoPackage and an indexed
identity plan, completes allocation, attaches IDs/hashes, and writes metadata.
FGB exports directly from the normalized store. Tippecanoe receives a pipe with
only geometry and `feature_id`; MBTiles conversion and existing tiling options
are preserved. The geometry store is removed after both geometry outputs finish.
Translations follow geometry processing. Verified translation input downloads
are removed once their reusable SQLite index is built; an approved supplement
remains available while rebuilding the locales. A published marine bundle is
removed locally before the terrestrial build.

The invariant is unchanged: normalized content and a verified baseline determine
one complete allocation before publication. Scratch changes cannot change
identity or bypass an interrupted publication. Recover durable claims/receipts
through the original publication owner; never substitute local SQLite state or
reset a sequence because a disk was lost.

Freeze benchmark inputs without publishing:

```bash
uv run python scripts/freeze_wdpa_benchmark.py --out "$SHARED_DATASETS_WORKDIR/downloads/wdpa-october-2026/frozen-inputs"
```

The freezer verifies publication state, manifest generations and compressed
sidecar hashes/counts, and downloads translation evidence at pinned generations.
Keep the upstream ZIP alongside that directory, outside the repository. Replay
uses the production builder and never instantiates a publisher:

```bash
docker run --rm --platform linux/amd64 --cpus=4 --memory=8g --memory-swap=8g \
  --mount type=bind,src="$SHARED_DATASETS_WORKDIR",dst=/inputs,readonly \
  --mount type=volume,dst=/work \
  wdpa-monthly python scripts/local_wdpa_sample.py \
  --source /inputs/downloads/wdpa-october-2026/WDPA_WDOECM_Oct2026_Public_all_shp.zip \
  --baselines /inputs/downloads/wdpa-october-2026/frozen-inputs \
  --translation-sources /inputs/downloads/wdpa-october-2026/frozen-inputs/translation-sources.json \
  --workdir /work/shared-datasets-1/october-build-1
```

Use a named disk volume and retain its reports for the two complete acceptance
runs. Specify `--fraction 0.001 --seed 7919` for debugging; samples and `--genesis`
fixtures cannot satisfy acceptance. Add `--compare-legacy` on a sample to compare
the retained old allocation/export path against IDs, hashes, properties, geometry,
field types, metadata schemas, all six locales and the canonical translation CSV.
Complete runs rebuild the reusable SQLite translation index from the frozen
generation-pinned inputs and delete their scratch copies after indexing. The
freezer's optional `--build-translation-cache` creates a sample-debugging cache;
`--translation-memory` cannot satisfy complete acceptance. The gate explicitly
requires index construction, not only geometry and locale output generation.
An independent stream of source identity/country fields verifies realm/India
counts against the outputs; identical duplicate source rows count once, matching
the allocation contract. The report records semantic identity/property
digests, realm/India counts, artifact hashes, elapsed time and structured phase
measurements. Raw metadata bytes can differ because scratch source paths appear
in provenance; compare semantic values and identities, not those paths.

Each phase emits `wdpa_phase_started` and `wdpa_phase_resources` JSON with elapsed
time, cgroup memory peak, process RSS peak, sampled scratch peak, artifact sizes
and native tool versions. Missing peak telemetry is not passing evidence.
Scratch measurements cover the entire `/work` filesystem, including native
temporary files outside the build directory and open files that were unlinked.

`scripts/wdpa_processing_gate.py` blocks protected deployment until the reviewed
`catalog/wdpa-processing-acceptance.json` matches the processing source digest,
records two complete October builds on 4 CPU / 8 GiB with matching frozen inputs,
peak memory ≤6.4 GiB, scratch <80 GiB and completion within 24 hours, verifies
source-derived realm/India counts, compatibility and artifact contracts, and
confirms disk quota approval. A missed target blocks readiness; increasing the
worker size does not satisfy the gate. Benchmark the deployment amd64 image,
record its immutable digest and verify FGB/metadata/PMTiles contracts. Re-run
benchmarks when processing code changes.

Record each retained `benchmark.json` in the acceptance document, adding the
resolved registry image digest and linking the reviewed fixture/sample
compatibility evidence. Full resource replays do not also run the old pipeline;
`compatibility_verified` in acceptance records attests to that separately
reviewed comparison, while `source_counts_verified` and `contracts_verified`
come from the replay itself.

## Execution observations and failure recovery

`wdpa-execution-observer` uses 1 CPU / 512 MiB, a 120-second timeout and a
five-minute scheduler. It reads WDPA executions independently of the worker and
writes only `gs://skytruth-shared-datasets-1/_catalog/wdpa-monthly-execution.json`
with generation preconditions and revalidation headers. It has no dataset,
release-index, claim, receipt or allocation permissions. An exact-object storage
binding and deny-policy exception permit replacing only that status document.
Before provisioning, review the protected Terraform identity's create permissions
for the observer job, scheduler and custom execution-reader role. Its existing
scheduled-ingestion role supports job updates, not those creations. Bootstrap
must use a reviewed protected workflow; do not add broad project permissions or
use a local production apply to bypass that requirement.

The version1 document contains `schema_version`, `job_name`, `observed_at`,
`latest_execution` and `latest_completed_execution`. Entries include ID,
created/started/completed timestamps, state (`pending`, `running`, `succeeded`,
`failed`, `cancelled`, `unknown`) and an allowlisted API reason code. Raw messages
and execution configuration are never published. A newer running attempt retains
the last completed failure. API or IAM failures leave the old observation stale
and fail the observer job, so the general execution alert covers them too.

Both WDPA catalog details show execution observations separately from the asset
check-in and published release. A successful marine publication does not imply
a successful overall job. Visible details revalidate every minute; observations
older than 15 minutes are marked stale. A status-document failure does not alter
any release or allocation state.
The protected viewer serves `execution-status.js` and maps
`/wdpa-monthly-execution.json` to the same observer object under `_catalog/`;
it reads that observation afresh on each request. After merge, deploy the web
bundle through `catalog-web-deploy.yml` and the viewer image through
`catalog-viewer-deploy.yml`, both protected workflows, before verifying status
on the static and protected catalog pages.

Before the normal rollout canary, execute a controlled failure with the
`WDPA_FAIL_BEFORE_WRITES=true` execution override, after checking existing
executions/publication ownership and applying monitoring through the protected
cron alert-policy workflow. The failure occurs before a publisher is created.
Verify the terminal failed execution, observer JSON and actual alert delivery;
then run the normal canary without that override, follow its terminal state,
and inspect release indexes and generation/hash metadata when it publishes.
The protected workflow starts only a controlled probe on its first attempt and
stops before a dataset canary. After verifying actual alert delivery, dispatch
with `failure_alert_verified_execution` naming that failed probe; it verifies
the probe flag and terminal failure before starting the normal canary.
If delivery has not been verified, stop before the normal canary. Do not grant
the observer write access to worker data to repair missing execution status.

## Generated-ID publication

Both assets use `OwnedGeneratedPublisher` under `generated-2026-v1`. The
[reset runbook](../../docs/feature-id-reset-installation.md) defines the one-time
transition and first-publication checks. Historical releases remain readable;
their IDs do not seed the new contract. Both assets require installed reset state
before this shared job deploys. Missing state is an error.

The publisher reserves IDs and claims the asset before exposing artifacts.
Later builds read the exact committed manifest/metadata generations and retain
the counter when features disappear. Runtime identity binds `CLOUD_RUN_EXECUTION`
and the image's embedded `SHARED_DATASETS_EXECUTOR_SHA`. Retries resume the
captured intent; an interrupted checkpoint holds its claim and reservation.
Starting another execution does not abandon them.

The first new release replaces every captured latest object, including all six
locale sidecars. It consumes the [verified translation supplement](../../docs/wdpa-translation-reset-evidence.md)
and requires complete joins before reserving IDs. Resume scheduling only after
both asset publications finish and their native artifacts and joins validate.
