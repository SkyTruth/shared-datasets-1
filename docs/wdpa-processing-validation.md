# WDPA processing validation and rollout evidence

Readiness is **pending**. The rollout builds the complete October artifacts once,
retains the validated files, and promotes those exact bytes to the production
bucket. It does not require a second complete build or regenerate artifacts in
the production canary. `catalog/wdpa-processing-acceptance.json` remains blocked
until a retained build and its reviewed promotion plan exist.

The artifact-retaining image passed its small native fixture and deterministic
both-realm old/new sample in [run 37095105175](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37095105175)
on `1d67e856dda11e936abad95d6a92c5e421660857`. The complete hosted marine step
was skipped as requested. Version 3 `catalog/wdpa-staged-validation.json` records
processing fingerprint
`1e7d60b2e1cbcf171894e072e0b72b92ee03933d34b5a715e6951485565b5c23`
and image configuration digest
`sha256:ca0d2b90531f6273f088def35de858b40b9ae9772f37ccc9dc95d4e0d5f6c760`.
The image ZIP, actual configuration blob and report ZIPs were hash-verified;
[original reports and image metadata](wdpa-processing-evidence/37095105175/compatibility.json)
are retained unchanged. The configuration digest is not a deployed registry
manifest digest or evidence of a completed cloud build.

The small fixture produced one record with valid native contracts in 1.533
seconds (1.466 seconds in the production phase). Its production phase kernel
peak was 104,157,184 bytes and scratch was
876,544 bytes; these remain per-phase measurements. The fresh October comparison
passed in 678.281 seconds for 20 marine and 499 terrestrial records, including
193 terrestrial India records/sites, under 4 CPU / 8 GiB. Its kernel lifetime
peak was 6,467,153,920 bytes and scratch was 9,561,575,424 bytes. All sampled
artifact hashes, semantic results, baseline identities and allocation counters
match the preceding sample. These are sampled counts and compatibility evidence,
not full terrestrial acceptance or publication. After reviewed merge and no
active execution, this permits one isolated complete retaining build.

## Single build and promotion

1. Build the Linux deployment image once. Dispatch CI with `wdpa_build_smoke=true`
   for the small native fixture and deterministic old/new sample only. Record
   their matching image/source fingerprints in version 3
   `catalog/wdpa-staged-validation.json`. The complete hosted marine benchmark
   remains an optional diagnostic, not a prerequisite for this rollout.
2. After review and merge, use protected `wdpa-processing-validation-deploy.yml`
   to deploy that image and start **one** complete October build. Never replace
   or duplicate a pending/running execution. Its fixed limits remain 4 CPU,
   8 GiB memory, 100 GiB DISK, zero retries and 24 hours.
3. Each realm's validated FGB, PMTiles, canonical metadata, schema, translation
   CSV and all six locale sidecars are uploaded create-only to
   `_scratch/wdpa-builds/{cloud-execution}/{asset-slug}/` before local cleanup.
   The runtime has only `storage.objects.create` on that prefix. It cannot read,
   replace, delete or publish canonical dataset objects. Uploads are included in
   the measured processing phases. The root `build-bundle.json` is committed
   only after both realms and the full resource/count/contracts checks pass.
4. Retain the unchanged final Cloud Logging report and verify terminal success,
   the actual immutable deployed image URI, its configuration digest and every
   staged object's generation, size and SHA-256. Version 3 acceptance names
   `build`, not a replay list, and the matching separate compatibility sample.
   Require the actual kernel lifetime peak within the enforced 8 GiB limit,
   scratch <80 GiB and duration ≤24 hours. Exceeding preferred 6.4 GiB headroom
   produces an advisory warning; it does not reject otherwise valid artifacts.
5. Add `.github/dataset-plans/wdpa-build-{bundle-sha256}.json` and the matching
   `shared-datasets-publish-plan` fence to the evidence PR. Record that PR number
   in acceptance. The protected worker workflow checks exact-head approval (or
   the existing self-authored merged exception), identical head/merge/current
   document bytes, and the accepted build's real terminal success. See the
   [owned plan contract](../.github/dataset-plans/README.md#owned-wdpa-build-promotion).
6. With observer and alert prerequisites ready, promote the accepted image and
   execute the worker with `WDPA_PROMOTION_BUNDLE` and the build's `RUN_DATE`.
   It downloads the approved object generations, verifies all required files
   before the first new publication, and passes them to `OwnedGeneratedPublisher`.
   It never downloads the upstream source, normalizes geometry, rebuilds tiles
   or regenerates translations. Publication manifests and receipts are finalized
   with the actual canonical GCS generations; artifact bytes remain unchanged.

Before promotion, both identity baselines and reserved counters must still match
those used by the build. Existing successful realms are retained only when their
artifact hashes match. An interrupted publication resumes its original receipt;
another owner, stale baseline or changed staged bytes stops publication. Never
reset claims/counters or change the date to bypass recovery. Validation's staging
permission and the worker's read permission are limited to the noncanonical build
prefix. Cloud Storage supports object-name conditions and requires delete as well
as create permission to replace an object ([IAM conditions](https://docs.cloud.google.com/storage/docs/access-control/iam),
[permissions](https://docs.cloud.google.com/storage/docs/access-control/iam-permissions)).

The earlier execution `wdpa-processing-validation-5q7zp` predates artifact
retention. Its sampler deletes each realm's outputs after validation, including
marine files already removed. Its diagnostic progress cannot supply a promotable
bundle. Let it finish; deploy the retaining image only after no execution is active.
No second replay of that old image is authorized. Historical reports below remain
unaltered evidence for their original images and delivery paths.

## Artifact acceptance and preferred headroom

The enforced memory configuration is 8 GiB. The preferred 6.4 GiB peak is an
advisory warning, separate from artifact acceptance. Preserve and report the
actual kernel lifetime peak, including file cache. A complete successful build
at 7.2 GiB can commit its retained bundle and be promoted when counts, identities,
frozen inputs, native FGB/PMTiles/sidecar contracts, image evidence, reviewed plan
and ownership checks all pass. Do not require a second matching build or rebuild
valid retained files to improve memory measurements. Cache-control improvements
are separate operational work, not prerequisites for accepting such artifacts.

The old diagnostic `wdpa-processing-validation-5q7zp` built terrestrial metadata
(70,215,228 bytes), schema (3,862 bytes) and indexed FGB (5,816,513,152 bytes).
Metadata took 5,260.409 seconds; FGB took 75.354 seconds and completed at
`2026-10-03T02:52:01Z`. Its observed real kernel lifetime peak is 7,732,400,128
bytes (7.20 GiB): advisory under this policy. Raw API/phase facts are retained
[unchanged](wdpa-processing-evidence/wdpa-processing-validation-5q7zp/terrestrial-export-logs.json).

This execution cannot supply a complete retained bundle. Its deployed sampler
deletes each realm's validated output files before moving on and has no artifact
staging code or permissions. All eleven marine artifact files were already
removed. Terrestrial FGB/metadata/schema remain temporary working files;
PMTiles, translation CSV, six locale sidecars and final contracts were still
outstanding at the latest captured phase. It has no generation/hash-pinned
staged objects or root descriptor.
The [read-only retention assessment](wdpa-processing-evidence/wdpa-processing-validation-5q7zp/retention-assessment.json)
at `2026-10-03T03:52:06Z` found **zero objects** under its exact
`_scratch/wdpa-builds/wdpa-processing-validation-5q7zp/` prefix and confirmed
the old immutable image and running execution.

The exact required files for **each** realm are `{asset-slug}.fgb`,
`{asset-slug}.pmtiles`, `{asset-slug}.metadata.ndjson.gz`,
`{asset-slug}.schema.json`, `{asset-slug}.metadata-translations.csv`, and
`{asset-slug}.metadata.{locale}.ndjson.gz` for `es`, `fr`, `id`, `pt`, `pt_br`
and `sw`. The missing retained marine files cannot be recovered from scalar
reports. After this diagnostic is terminal, the minimum supported work is one
complete build using the retaining image, upload both validated realm sets before
cleanup, commit the root descriptor, then review and promote those exact bytes.
This is required to retain the artifacts, not to improve its memory margin. No
retained complete artifact bundle is rebuilt merely because it exceeds 6.4 GiB.

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
| Cgroup memory peak | 6,603,804,672 bytes (6.15 GiB) | 6.4 GiB preferred; 8 GiB enforced |
| Memory headroom | 23.1% | ≥20% preferred |
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

Earlier [run 37047953130](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37047953130)
passed all three stages on the preceding processing digest and one image. Sea ice
passed in 1.580 seconds, with a 104,988,672-byte kernel phase peak and 876,544-byte
scratch peak. The deterministic October old/new sample then agreed on all 20
marine and 499 terrestrial records, including 193 terrestrial India records,
using fraction `0.001`, seed `7919`, frozen baselines and a fresh translation
index. Its comparison covers IDs, hashes, properties, geometry, field types,
metadata, six locale sidecars and the canonical translation CSV.

Complete marine finished at `2026-10-02T19:42:44Z` with exit code zero and
`OOMKilled=false`:

| Measurement | Earlier marine result | Required target |
| --- | --- | --- |
| Processing duration | 3,027.472 seconds (50.5 minutes) | ≤24 hours |
| Kernel cgroup memory peak | 4,842,131,456 bytes (4.51 GiB) | 6.4 GiB preferred; 8 GiB enforced |
| Memory headroom | 43.6% | ≥20% preferred |
| Scratch peak | 9,561,579,520 bytes (8.90 GiB) | >8 GiB and <80 GiB |
| Marine records | 17,938 | Frozen source: 17,938 |
| India records/sites | 304 / 304 | Frozen source: 304 / 304 |

The complete build independently counted the frozen source and verified metadata,
indexed FGB and PMTiles contracts, including representative tile decoding. It
rebuilt translations from the frozen inputs. Its output hashes and semantic hash
also match the historical complete marine replay. Its raw
`compatibility_verified` remains false: the separate sample supplies old/new
comparison proof, and artifact validation does not impersonate that comparison.

[Its archived version 2 staged evidence](wdpa-processing-evidence/37047953130/staged-validation.json)
opened only the isolated cloud validation gate. It recorded processing fingerprint
`ebb43140387c3951290ecd0ad119e1ba5ed595ff96c14cbc00b27f26617a8040`,
configuration digest
`sha256:595f1a3e299033f60d9619ec2a569d36b5a83de9b61e6f694ebea8542d2f6489`,
the Actions run/head and artifact IDs/hashes. Unmodified reports, image metadata
and container state are retained under `docs/wdpa-processing-evidence/37047953130/`;
the previous staged document remains intact in the historical directory. The
former production gate required two complete cloud builds, including
terrestrial, with the same image and frozen inputs. No dataset bytes were
published by these stages.

Corrected [run 37062181850](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37062181850)
passed all three stages after input fingerprint verification moved inside the
measured `frozen-inputs` phase. Sea ice passed in 1.545 seconds with a
103,837,696-byte kernel phase peak and 876,544-byte scratch peak. The fresh
deterministic old/new comparison verified the same 20 marine and 499 terrestrial
records, including 193 India records, all field types and every translation
output. Complete marine finished at `2026-10-02T21:53:27Z`, exit code zero and
`OOMKilled=false`:

| Measurement | Corrected marine result | Required target |
| --- | --- | --- |
| Processing duration | 3,059.526 seconds (51.0 minutes) | ≤24 hours |
| Kernel cgroup memory peak | 4,744,769,536 bytes (4.42 GiB) | 6.4 GiB preferred; 8 GiB enforced |
| Memory headroom | 44.8% | ≥20% preferred |
| Scratch peak | 9,379,639,296 bytes (8.74 GiB) | >8 GiB and <80 GiB |
| Marine records | 17,938 | Frozen source: 17,938 |
| India records/sites | 304 / 304 | Frozen source: 304 / 304 |

Both corrected reports have identical artifact hashes, semantic results,
allocation counters and baseline identities to their respective preceding
reports. The same frozen source, baseline and translation inputs and native
versions were used. The complete report still has `compatibility_verified=false`;
the separate sampled comparison supplies that proof.

The archived [version 2 staged evidence](wdpa-processing-evidence/37062181850/staged-validation.json)
records processing fingerprint
`b197f06928b5874db60574dcc1cca91e30c1be291b24e31bcd045fe16ea81337`
and configuration digest
`sha256:61442d5115fa9d32d6e5d35fc10d7f64c7d8257de2a85900d7b1c98fcdf97b4e`.
Unmodified raw reports, image metadata and container state are retained under
`docs/wdpa-processing-evidence/37062181850/`. GitHub report ZIP hashes were
verified before extracting the original bytes. This permits only isolated
validation after review and merge, and after the existing cloud execution is
terminal. That superseded gate required two complete passing cloud builds; the current
single-build path requires retained artifacts from its new image instead; the earlier execution's 7.00 GiB
lifetime peak cannot be reset, subtracted or counted toward acceptance.

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
and 111,677,440 bytes of anonymous memory. This input-only diagnostic cannot
pass acceptance because it did not build and retain complete validated artifacts;
its headroom excess is advisory under the current policy.

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
acceptance; its image predates the retained-artifact build path.

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
The protected job remains configured at 4 CPU / 8 GiB. Protected deployment
[37059026485](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37059026485)
restored the processing command using the reviewed staged image; a diagnostic
execution cannot open acceptance.

Complete execution `wdpa-processing-validation-6mvsp` passed preflight and
downloaded the frozen inputs, but its early lifetime kernel peak reached
**7,520,972,800 bytes (7.00 GiB)**, exceeding the 6.4 GiB target. The input
download phase itself peaked at 4,446,232,576 bytes. These
[early Cloud Logging resource events](wdpa-processing-evidence/wdpa-processing-validation-6mvsp/early-phase-logs.json)
remain diagnostic evidence; they cannot establish complete counts, contracts or
acceptance. That execution subsequently failed during platform hardware
maintenance at `2026-10-02T22:17:51Z`, without a final processing report. Its
preferred headroom excess is advisory under the current policy, separate from
that terminal failure and missing artifact bundle.

The replay hashed the 4.8 GB source ZIP before entering its first measured
phase. Linux file cache from that read therefore accumulated without the
sampler's cache-pressure control. Input fingerprint verification now runs
inside `frozen-inputs`, and its time, memory and verification failures are
included in the report. This changed the processing fingerprint; earlier staged
reports remain historical. The active diagnostic image incorporates that fix,
but predates retaining artifacts. The single-build sequence above replaces the
former two-run rollout. Source downloads, fingerprint reads and staging uploads
remain profiled; the kernel lifetime peak is never reset or subtracted.

The protected isolated workflow can change only its service account, deployer
binding, job, two custom staging/read roles and two prefix-conditioned bucket
bindings. Its reviewed plan may not delete resources, increase limits, grant
canonical data permissions or create a scheduler. Failed builds may leave
uncommitted scratch objects; absence of a complete validated root descriptor
prevents promotion. Scratch existence is not publication authority.

The production image is the same accepted Cloud Run image, verified by immutable
registry URI, configuration digest and source fingerprint. Tagging/pushing it
retains the tested executor SHA. The production execution consumes its retained
build bundle through the owned publisher; neither the image nor the dataset
artifacts are rebuilt after acceptance.

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
project-wide; the current isolated deploy validates its exact resource plan,
prefix-only staging rights and absence of canonical dataset permissions. Job IAM, scheduler
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

The new retaining image needs matching small/sample evidence and one complete
terminal-success build. Preserve its raw report and immutable references, then
review and merge the exact build promotion plan. Observer bootstrap approval is
still a separate prerequisite; the existing proposal does not gain approval from
this delivery change. Verify actual controlled-failure alert delivery before the
production promotion. Follow publication to terminal status and independently
check terrestrial FGB, every sidecar, both release indexes, generations and custom
metadata. The monitor remains active until terrestrial publication is verified.

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
