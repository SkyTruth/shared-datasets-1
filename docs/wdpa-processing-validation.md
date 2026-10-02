# WDPA processing validation and rollout evidence

Readiness is **pending**. The implementation has compatibility and integration
evidence; two complete October runs at 4 CPU / 8 GiB are in progress and have
not yet passed resource acceptance.
`catalog/wdpa-processing-acceptance.json` intentionally blocks production deployment.

## Completed checks

- Full CI Python suite: 1,078 passed; five host-native checks skipped and exercised
  separately in the deployment toolchain. Earlier local validation also passed
  1,171 subtests.
- Native geospatial suite: 120 passed, 17 subtests passed, no skips. All five
  mandatory native fixtures passed, including old/new normalized metadata,
  field types and geometry, WDPA PMTiles, sea ice, EAMLIS and COG validation.
- Browser checks: three execution-status unit tests and five Chromium scenarios.
  The WDPA scenario preserves October marine and September terrestrial release
  dates alongside a failed overall execution, a newer running execution, later
  cancellation, minute refresh and stale observations.
- Protected viewer checks: 48 passed, including serving the new module and
  mapping the status route to the observer's exact `_catalog/` object. Successive
  reads use fresh observations and preserve the revalidation headers.
- Identity tests cover identical/conflicting duplicates, changed source keys,
  reviewed reuse/new-ID decisions, the 64-digit ID boundary and exhaustion.
  Native pipeline tests cover producer failure, broken pipes, disk exhaustion,
  early consumer exit and cleanup. Existing publication tests retain generation,
  reservation, claim and interrupted-publication recovery checks.
- Terraform 1.8.5 formatting and validation passed. The saved production-state
  plan passed the deployment workflow's exact resource and processing-limit
  allowlists. No apply was run.
- A disposable GDAL 3.6.2 cursor check read and updated all 10,002 records
  across the 10,000-row transaction boundary without rewinding.
- Observer image startup/classification passed under 1 CPU / 512 MiB with
  synthetic execution statuses: peak process RSS 93.4 MiB on the emulated
  development VM. This is a startup smoke check, not live API/IAM verification.
- Ruff and repository static guardrails passed.
- Pinned Gitleaks 8.24.3 full-history scanning passed. CI initially flagged a
  historical `allow_empty=generated != "approved"` Python argument expression
  as an API key. `.gitleaksignore` excludes only that verified commit/file/line
  fingerprint; no credential rule or file-wide exclusion was added.

## Deterministic October comparison

[Machine-readable sample evidence](wdpa-processing-sample-evidence.json) records
the frozen inputs and output digests. The selector used fraction `0.001`, seed
`7919`, and the existing `SITE_ID` expression. This is a deterministic subset,
not an assertion that each country is sampled proportionately.

The complete upstream ZIP was 4,786,380,946 bytes, SHA-256
`36181f18bfa2caaa3c62721f2438107c40411b1d995c60a3fc5835665a8d8fd9`.
Baselines were generation-pinned, hash/count-verified committed publications:

| Realm | Baseline release | Manifest generation | Baseline rows | Next ID before comparison |
| --- | --- | --- | --- | --- |
| Marine | 2026-10-01 | 1790866753009809 | 17,938 | 17,943 |
| Terrestrial | 2026-09-30 | 1790815952362262 | 304,817 | 304,818 |

Old and new processing agreed on all 20 marine and 499 terrestrial selected
records: IDs, hashes, source properties, geometry, field types, metadata schemas,
allocation evidence, all six localized sidecars and the canonical translation
CSV. The terrestrial subset contained 193 India records/sites and allocated 193
new IDs identically. These are **sample counts**, not whole-source India totals.
Both FGB and PMTiles passed native contracts, including archive verification and
representative tile decoding. No dataset bytes were published.

The development replay used the pinned native image with workspace code on an
emulated amd64 Docker VM limited to **2 CPU / 2 GiB**. It took 1,483.9 seconds,
including both old and new paths, input copies and baseline indexing. Scratch
peaked at 8.35 GiB; parent-process peak RSS was 457.2 MiB. Sampled cgroup usage
reached the 2 GiB limit, and this kernel did not expose `memory.peak`.
The replay did not freeze a processing digest at startup. It is compatibility
evidence only; **none of these measurements satisfies resource acceptance**.
It also reused a cached translation index. The current runner records and
verifies its processing digest across the run, requires complete replays to
rebuild the production translation index from `translation-sources.json`, and
the gate refuses cached-index runs.

An independent GDAL attribute-only scan of the **complete** frozen October
source also passed. Geometry reads are disabled for this check, and identities
and site counts use SQLite. [Source-count evidence](wdpa-processing-source-counts.json)
pins the upstream hash and published baseline generations.

| Realm | Complete October records | October India records/sites | Published baseline records | Baseline India records/sites |
| --- | --- | --- | --- | --- |
| Marine | 17,938 | 304 | 17,938 | 304 |
| Terrestrial | 497,914 | 193,174 | 304,817 | 80 |

Raw rows and distinct source identities agree in both realms. These are source
record/site counts; they do not establish that the October terrestrial release
has been published, complete artifact compatibility, or resource acceptance.

## Hosted complete-build runner

The existing `CI` workflow has an opt-in `wdpa_full_benchmark` input. It builds
one deployment image and runs two fresh containers at exactly 4 CPU / 8 GiB,
with no swap and a 100 GiB ext4 disk at `/work`. The second replay starts with
empty scratch. This is the production processing path, including rebuilding
the translation index, with frozen baseline inputs and no publication.
GitHub hosted jobs have a six-hour ceiling, stricter than the production
24-hour target; a hosted timeout cannot establish the production timeout target.

`wdpa_snapshot_uri`, `wdpa_snapshot_generation` and `wdpa_snapshot_sha256` must
identify one private, generation-pinned diagnostic archive under
`_scratch/wdpa-processing-benchmarks/`. Package only the frozen upstream ZIP,
baseline pins/manifests/sidecars and the files referenced by
`translation-sources.json`; omit the prebuilt translation SQLite cache. Use
no-clobber staging. The workflow checks the archive hash before extraction,
then the sandbox validates each baseline and translation snapshot.

Provide `WDPA_BENCHMARK_READ_TOKEN` as a short-lived encrypted Actions secret,
downscoped through a Cloud Storage credential access boundary to object-reader
permissions on exactly the staged object. Do not provide publisher credentials
or a broad local access token. Remove the temporary secret after download. The
token is used only by the host download step and is absent from the processing
containers. No dataset bytes are uploaded to public Actions artifacts. Retained
artifacts contain only measurements, counts, digests and container outcomes.
Unused preinstalled tooling is removed only on this disposable job VM to make
room for the disk; the job fails if the disk cannot be provisioned.

## Infrastructure and failure visibility

The read-only worker/observer plan contains **eight creations, one update, zero
deletions**: the observer service account, custom execution-reader role, four
narrow IAM bindings, observer job and scheduler; the worker changes to 4 CPU /
8 GiB and a 100 GiB DISK at `/work` with Preview launch stage. Immutable production
image resolution, the production Terraform queue and publication rollout gate
remain in the protected deployment workflow.

The proposed failure filter was queried against real audit history: it matches
the WDPA OOM event with status **8** at `2026-10-01T17:34:36.255975Z` and status
**10** failures on September 30; adding status **0** yields no matches.
Identity pauses still exit successfully and retain their separate decision alert.
This query verifies matching, **not notification delivery**. Monitoring changes
must run through `cron-alert-policy-sync.yml` after review and merge.

The protected Terraform identity currently has job-update permissions but lacks
the observer's job/scheduler/custom-role creation permissions. A reviewed
protected bootstrap is a rollout prerequisite. No broader project permissions
have been granted by this PR.

## Remaining acceptance and rollout

1. Use the manual `CI` workflow complete-benchmark input on its disposable Linux
   runner, or provide a Linux Docker runtime with 4 CPU, 8 GiB, cgroup peak
   telemetry and enough disk for 100 GiB scratch. The local VM cannot meet this
   target without interrupting an unrelated running database.
2. Run the deployment image twice on the complete frozen October inputs and
   identical verified baseline/translation snapshots. Require peak memory
   ≤6.4 GiB, scratch <80 GiB, duration ≤24 hours, verified source-derived
   realm/India counts and valid FGB/metadata/PMTiles contracts. Any ambiguity
   requires reviewed decisions before outputs can be emitted.
3. Record complete reports, immutable image digest and processing digest in the
   reviewed acceptance document. Sample/genesis runs, missing peak telemetry,
   mismatched inputs and larger worker sizes cannot satisfy the gate.
4. Obtain the 100 GiB per-instance Preview disk quota and review/provision the
   observer bootstrap permissions through protected workflows.
5. After review and merge, apply monitoring and deploy through protected
   workflows, including both catalog web and viewer deployments. Verify actual
   controlled-failure alert delivery before permitting
   the normal dataset canary. Follow the canary to terminal status and inspect
   publication ownership, release indexes, generations and artifact metadata.

## Intended remote paths

The observer writes only
`gs://skytruth-shared-datasets-1/_catalog/wdpa-monthly-execution.json`.
The catalog deployment includes `_catalog/web/app.js`, `index.html` and the new
`execution-status.js`; the protected catalog viewer image serves the module and
the same status document through its existing static route. Normal WDPA publication paths remain
`100-geographic-reference/130-protected-areas/{wdpa-marine,wdpa-terrestrial}/`
with the existing `releases/`, `latest/`, run records and release indexes.
Existing allocation, claim and receipt ownership is unchanged. No canonical
dataset objects or production infrastructure were changed during implementation.

Diagnostic benchmark inputs were staged privately, with `if_generation_match=0`,
at `gs://skytruth-shared-datasets-1/_scratch/wdpa-processing-benchmarks/20261002T040200Z/october-frozen-snapshot.tar`,
generation `1790924057375117`, 5,189,560,320 bytes, SHA-256
`70b9f8b0356de097c7392972c65b0b073485c80ea814d0284a97762a9478151b`.
This disposable scratch bundle is not a shared dataset contract. Its temporary
reader was restricted to that exact object; a read of another object was denied.
