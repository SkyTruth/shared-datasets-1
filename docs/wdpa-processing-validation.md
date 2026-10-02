# WDPA processing validation and rollout evidence

Readiness is **pending**. The implementation has compatibility and integration
evidence. Validation now follows the requested sequence: a small sea-ice smoke
test, complete marine WDPA with measured disk spill, then isolated Cloud Run
validation of the complete October source including terrestrial WDPA.
The previous hosted complete runs were stopped to enforce this ordering.
`catalog/wdpa-processing-acceptance.json` intentionally blocks production deployment.

## Completed checks

- After integrating catalog PRs #169 and #170 and the staged-validation workflow,
  the local Python suite passed 1,218 tests and 1,171 subtests; all 13 Chromium
  scenarios passed. The earlier
  processing CI Python suite passed 1,093 tests; five host-native checks skipped and exercised
  separately in the deployment toolchain. Local validation also passed
  1,171 subtests.
  [Processing code CI](https://github.com/SkyTruth/shared-datasets-1/actions/runs/36997692555)
  passed on `7584a5d`, including lint/Terraform and native integration.
- PR #171 merged as `f0668db` after integrating #172/#173. Current-head CI,
  native integration, 14 Chromium scenarios, SDK validation, catalog drift and
  Terraform readiness all passed. The local suite passed 1,221 tests and
  1,171 subtests; five native checks ran separately in the deployment image.
- Current native geospatial CI: 107 passed, no skips. The earlier broader
  deployment-toolchain suite passed 120 tests and 17 subtests. All five
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
one deployment image, runs the tiny synthetic sea-ice production path, and only
then runs the **marine** WDPA processing path on a fresh VM. Both jobs verify
the loaded image's configuration digest. The marine replay runs a fresh
container at exactly 4 CPU / 8 GiB, with no swap and a 100 GiB ext4 disk at
`/work`. It starts with empty scratch. This is the production processing path, including rebuilding
the translation index, with frozen baseline inputs and no publication.
The optional `wdpa_benchmark_fraction=0.001` uses the same full input preparation
and translation indexing for a shorter diagnostic probe, with separately named
debug reports. It cannot satisfy acceptance. Complete builds use the default
fraction `1`. A marine-only run cannot open the final publication gate.
Each GitHub hosted job has a six-hour ceiling, stricter than the production
24-hour target; a hosted timeout cannot establish the production timeout target.
A 330-minute processing watchdog leaves time to retain container diagnostics
before the six-hour job limit.

The first staged run
([37016755333](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37016755333))
passed image construction, lint, the Python suite and native integration, then
failed the deployment-image sea-ice smoke test. GDAL's `gdal_calc.py` could not
import NumPy: it was previously present only in the native CI dependencies.
Marine processing was correctly skipped. NumPy is now part of the locked native
runtime dependency set, and image construction verifies GDAL's array ABI.
This failed smoke test provides no resource acceptance evidence.

The corrected deployment image on `3febe15` passed sea ice in
[run 37019403196](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37019403196).
The real sea-ice production path generated one polygon and verified metadata,
FGB and PMTiles contracts in 1.023 seconds. Cgroup peak was 104,140,800 bytes;
scratch peaked at 823,296 bytes under the exact 4 CPU / 8 GiB limits.
The historical image digest and scalar report are retained in
`docs/wdpa-processing-evidence/37019403196/staged-validation.json`. The same-image complete marine stage is
also complete; this small fixture provides no complete-WDPA resource acceptance.

The complete marine replay in that run passed at 4 CPU / 8 GiB with no swap:

| Measurement | Marine result | Required target |
| --- | --- | --- |
| Processing duration | 3,004.028 seconds (50.1 minutes) | ≤24 hours |
| Cgroup memory peak | 6,603,804,672 bytes (6.15 GiB) | ≤6.4 GiB |
| Memory headroom | 23.1% | ≥20% |
| Scratch peak | 9,457,233,920 bytes (8.81 GiB) | >8 GiB and <80 GiB |
| Marine records | 17,938 | Frozen source: 17,938 |
| India records/sites | 304 / 304 | Frozen source: 304 / 304 |

The translation index was rebuilt from frozen inputs; all six locale sidecars
and the canonical CSV were generated. Metadata, indexed FGB and PMTiles passed
their contracts, including representative tile decoding. The container exited
zero with `OOMKilled=false`. The loaded image digest matches sea ice exactly.
The historical scalar report is recorded in
`docs/wdpa-processing-evidence/37019403196/staged-validation.json` with its
Actions artifact ID and report hash. This run did not repeat the old/new
comparison; `compatibility_verified` remains false rather than claiming that
comparison from artifact checks alone. Earlier fixture/sample compatibility
evidence is separate.

These historical sea-ice and marine reports meet their resource targets, but
their processing digest predates the current code and dependencies. They cannot
open the current isolated cloud gate. No dataset bytes were published; complete
terrestrial builds and final worker acceptance remain pending.

Fresh [run 37047953130](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37047953130)
passed all three stages on the current processing digest and one image. Sea ice
passed in 1.580 seconds, with a 104,988,672-byte kernel phase peak and 876,544-byte
scratch peak. The deterministic October old/new sample then agreed on all 20
marine and 499 terrestrial records, including 193 terrestrial India records,
using fraction `0.001`, seed `7919`, frozen baselines and a fresh translation
index. Its comparison covers IDs, hashes, properties, geometry, field types,
metadata, six locale sidecars and the canonical translation CSV.

Complete marine finished at `2026-10-02T19:42:44Z` with exit code zero and
`OOMKilled=false`:

| Measurement | Fresh marine result | Required target |
| --- | --- | --- |
| Processing duration | 3,027.472 seconds (50.5 minutes) | ≤24 hours |
| Kernel cgroup memory peak | 4,842,131,456 bytes (4.51 GiB) | ≤6.4 GiB |
| Memory headroom | 43.6% | ≥20% |
| Scratch peak | 9,561,579,520 bytes (8.90 GiB) | >8 GiB and <80 GiB |
| Marine records | 17,938 | Frozen source: 17,938 |
| India records/sites | 304 / 304 | Frozen source: 304 / 304 |

The complete build independently counted the frozen source and verified metadata,
indexed FGB and PMTiles contracts, including representative tile decoding. It
rebuilt translations from the frozen inputs. Its output hashes and semantic hash
also match the historical complete marine replay. Its raw
`compatibility_verified` remains false: the separate sample supplies old/new
comparison proof, and artifact validation does not impersonate that comparison.

[Version 2 staged evidence](../catalog/wdpa-staged-validation.json) now opens only
the isolated cloud validation gate. It records processing fingerprint
`ebb43140387c3951290ecd0ad119e1ba5ed595ff96c14cbc00b27f26617a8040`,
configuration digest
`sha256:595f1a3e299033f60d9619ec2a569d36b5a83de9b61e6f694ebea8542d2f6489`,
the Actions run/head and artifact IDs/hashes. Unmodified reports, image metadata
and container state are retained under `docs/wdpa-processing-evidence/37047953130/`;
the previous staged document remains intact in the historical directory. The
production acceptance gate still requires two complete cloud builds, including
terrestrial, with the same image and frozen inputs. No dataset bytes were
published by these stages.

[Reviewed public input recipe](wdpa-processing-public-inputs.json) pins the
upstream ZIP hash and the exact published baseline/translation object generations,
sizes and hashes. `download_public_wdpa_benchmark.py` reconstructs the same frozen
inputs anonymously, checking raw and compressed hashes. It neither exports
credentials nor makes private objects public. Dataset bytes are not uploaded as
Actions artifacts: only scalar measurement reports and the deployment image
shared between replay jobs are uploaded. The image artifact expires after seven
days. Unused preinstalled tooling is removed only
on the disposable job VM to make room for the disk; provisioning failure is fatal.

Set `wdpa_inputs_probe=true` for an input-preparation-only memory measurement.
It uses the deployment image and the same source preparation and verified
baseline loader as the full replay. Its report is explicitly diagnostic and
cannot satisfy acceptance; the complete-build step is skipped in that mode.
The report also records parent/child RSS and the cgroup memory breakdown so
filesystem caching can be distinguished from process allocation.

The first input-only probe on `37585d9`
([run 36995705697](https://github.com/SkyTruth/shared-datasets-1/actions/runs/36995705697))
completed in 48.136 seconds with **7,855,362,048 bytes (7.32 GiB)** total cgroup
peak, exceeding the 6.4 GiB target. Scratch peaked at 9,561,575,424 bytes;
parent RSS was 172,761,088 bytes and child RSS 96,567,296 bytes. At phase end,
`memory.stat` showed 4,928,614,400 bytes of file cache, no mapped files or shmem,
and 111,677,440 bytes of anonymous memory. This diagnostic misses the headroom
target and cannot pass acceptance.

The processing sampler now requests release of regular-file cache under memory
pressure using Linux cache advice. It retains total cgroup peak measurement,
preserves every file's bytes and fails on cache-advice errors. Production source
preparation is also measured. Fresh diagnostic and complete runs are required;
earlier processing digests cannot satisfy the updated gate.

The fresh input-only probe on `7584a5d`
([run 36997692555](https://github.com/SkyTruth/shared-datasets-1/actions/runs/36997692555))
peaked at **6,584,279,040 bytes (6.13 GiB)** total cgroup memory, below 6.4 GiB.
Scratch peaked at 9,462,734,848 bytes (8.81 GiB), and preparation took 66.077
seconds. The sampler requested two cache releases; parent RSS was 172,552,192
bytes and child RSS 96,714,752 bytes. Both probes used the identical frozen
source and verified baselines at 4 CPU / 8 GiB with no swap and a 100 GiB disk.
[Diagnostic reports](wdpa-processing-input-probes.json) include the native tool
versions and image/processing digests. This improvement is not complete-build
acceptance; both complete runs remain required.

[Superseded complete-build run 36998409031](https://github.com/SkyTruth/shared-datasets-1/actions/runs/36998409031)
used processing digest
`78387be4a53dfb00b28307ad73409c34afcc3071fcd0681c89d11a2c22bff310`
on `b5be93c`, with separate replay jobs loading the identical image. It was
cancelled at the user's request before obtaining acceptance reports. The earlier
complete-build attempt on `bbfe68b` predates the cache-pressure fix and cannot
be used as acceptance evidence for this processing digest. That attempt
([run 36976690829](https://github.com/SkyTruth/shared-datasets-1/actions/runs/36976690829))
was cancelled by GitHub's six-hour job limit at `2026-10-02T13:09:47Z`.
GitHub's termination annotation explicitly reports the six-hour limit. No
benchmark reports were retained, and the downloadable run archive contains no
processing-job log. This establishes a hosted-runner limitation; it provides
neither resource acceptance nor evidence of exceeding the production 24-hour
limit.

Local Docker now provides 4 CPU and an 8 GiB container limit with swap disabled.
Its VM has only 14.3 GiB free and Linux 5.15 lacks `memory.peak`; it cannot provide
the disk and telemetry needed for acceptance. The hosted staged tests do not
require increasing local Docker resources.

## Isolated cloud validation

The first cloud attempt failed its CPU/memory preflight before downloading
source data despite the verified 4 CPU / 8 GiB job configuration. The
[isolated runtime inspection](wdpa-runtime-inspection.md) established namespaced
cgroup v1, a measured CPU quota of 3.72, the exact 8 GiB memory limit and the
kernel's `memory.max_usage_in_bytes`. The shared reader now supports that
runtime and Docker cgroup v2; it records the real CPU quota and kernel peak.
The protected job remains configured at 4 CPU / 8 GiB. The diagnostic command
must be replaced through the protected validation deploy after fresh staged
evidence passes; a diagnostic execution cannot open acceptance.

`wdpa-processing-validation-deploy.yml` is a manual protected-main workflow in
the existing production Terraform queue. It gates deployment on reviewed small
and marine reports in `catalog/wdpa-staged-validation.json`, including the same
image/processing digest, complete marine counts/contracts, ≤6.4 GiB peak memory,
scratch greater than 8 GiB and below 80 GiB, and approved disk quota. Disk use
above the RAM limit demonstrates why the workload needs disk storage.

The workflow can change only three validation resources: the empty runtime
service account, its exact deployer binding, and a Cloud Run job. It cannot target
the production worker, bucket IAM, claims, receipts or allocation state. The
validation identity receives **no dataset permissions** and has no scheduler.
Its image uses public hash/generation-verified frozen inputs and the production
processing path at 4 CPU / 8 GiB with a 100 GiB disk and 24-hour timeout.
`wdpa_cloud_validation_report` is emitted to Cloud Logging; no dataset or status
objects are uploaded. Downloading frozen inputs is also profiled.

A separate read-only production-state plan for these isolated resources contains
**three creations, zero updates, zero deletions**. The saved plan passed the exact
resource/configuration allowlist, including the provider's nullable mount
subdirectory field. No apply was run.

After review, merge and quota/bootstrap verification, deploy this validation
job. Run the controlled pre-write failure, verify actual alert delivery, then
start complete October replays asynchronously and follow each to terminal status.
Two passing reports open the existing final acceptance gate for the production
worker. Publication ownership checks remain unchanged. A failed validation run
cannot authorize publication or a resource increase.

The production deploy pulls the immutable validation image recorded in both
accepted Cloud Run reports. It checks the image configuration digest and the
processing source fingerprint before tagging those same bytes for
`wdpa-monthly`. Native-tool and import smoke checks still run before the push,
and Terraform receives an immutable registry digest. There is no production
rebuild after acceptance. The image retains its tested executor SHA; the
deployment SHA identifies the promotion tag without replacing that provenance.

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

The protected Terraform identity already has service-account/custom-role creation
permissions. The deployment follow-up adds only `run.jobs.create` to its existing
scheduled-ingestion custom role through `scheduled-ingestion-deploy-iam-sync.yml`.
Google checks job creation on the parent project/location, so this permission is
project-wide; the isolated deploy still validates its exact three-resource plan,
empty runtime identity and absence of dataset permissions. Job IAM, scheduler
creation and deletion permissions are not added by this bootstrap. The observer's
job IAM and scheduler bootstrap remains a prerequisite for the final worker
rollout after full processing acceptance.

Google approved case `9d926638-b024-4866-8b94-898801ecdf6c` on October 2.
An authenticated Service Usage API read verifies **107,374,182,400 bytes
(100 GiB)** effective per-instance quota in `us-central1`.
[Quota observation](wdpa-processing-disk-quota.json) retains the returned values;
`catalog/wdpa-staged-validation.json` records approval. No API was enabled.
The first disk-backed validation deployment also received 100 GiB of regional
allocation, verified through Service Usage. Recheck available capacity before
starting another disk-backed job.
Run disk-backed WDPA executions sequentially. Google's
[disk documentation](https://docs.cloud.google.com/run/docs/configuring/jobs/ephemeral-disk)
describes the separate limits and initial regional grant.

## Remaining acceptance and rollout

1. Review the fresh matching small-fixture, both-realm old/new sample and
   complete marine reports. These stages passed, including verified
   counts/contracts and disk scratch above RAM. Merge the staged evidence before
   isolated deployment; earlier processing digests cannot satisfy this gate.
2. After review, merge and quota/bootstrap verification, deploy only the isolated
   cloud validation job. Run the deployment image twice on the complete frozen October inputs and
   identical verified baseline/translation snapshots. Require peak memory
   ≤6.4 GiB, scratch <80 GiB, duration ≤24 hours, verified source-derived
   realm/India counts and valid FGB/metadata/PMTiles contracts. Any ambiguity
   requires reviewed decisions before outputs can be emitted.
3. Record two distinct cloud executions, their identical immutable image URI,
   verified image configuration digest, complete raw reports and separate
   matching compatibility sample in the reviewed version 2 acceptance document.
   Sample/genesis runs, missing peak telemetry, mismatched inputs and larger
   worker sizes cannot satisfy the gate. Promote the accepted image bytes to
   production after acceptance passes.
4. The 100 GiB per-instance Preview disk quota and regional allocation are
   verified. Review/provision the observer bootstrap permissions
   through protected workflows.
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
