# WDPA processing validation and rollout evidence

The October terrestrial release is published and independently verified.
The single retained build passed validation, and the protected rollout promoted
its exact terrestrial bytes while preserving the existing October marine release.
`catalog/wdpa-processing-acceptance.json` pins that single build, its validated
files and its immutable promotion plan. Production promotes those exact bytes
with the original build date; it does not require a second complete build or
regenerate artifacts in the production canary.

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
When staged evidence selects an isolated processing deployment, local preflight
and required `ci-ready` independently check the exact reviewed GitHub artifact
ID, original run/repository/head, ZIP digest and availability. Its expiration
must be more than 24 hours away to allow the serialized deployment queue and
download. The trusted processing workflow repeats this read-only check before
cloud authentication and downloads that artifact ID. Missing, expired, changed
or unavailable inputs fail validation; they require refreshed reviewed retained
evidence. The check hashes the original producer's fixed public source snapshot
and verifies its
configuration blob without executing historical code or rebuilding the image.
The accepted monthly publication image remains its pinned registry image and
does not depend on this expiring staging archive.
The original configuration includes the public Python signing-key fingerprint
from the [official base image](https://github.com/docker-library/python/blob/master/3.12/slim-bookworm/Dockerfile).
The secret scanner excludes only its exact verified finding; no credential rule
or file-wide exclusion is added, and the configuration bytes remain unchanged.

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

## Complete retained October build

The single retaining build `wdpa-processing-validation-cg4bc` reached terminal
success at 2026-10-03 13:12:01 UTC. Its unchanged
[raw report](wdpa-processing-evidence/wdpa-processing-validation-cg4bc/report.json)
and exact [bundle bytes](wdpa-processing-evidence/wdpa-processing-validation-cg4bc/build-bundle.json)
are retained with actual Cloud Run, registry and object facts. The committed
root descriptor has generation `1791033114607974`, size 81,647 bytes and SHA-256
`a0378f473863a95f233f0f8aa6413e57789d16500a6ba13c6bf404fad0d52170`.

Native FGB, PMTiles, canonical metadata, schema, translation CSV and all six
locale contracts passed for both realms. All 22 stored files were independently
stream-read at their exact generations and SHA-256 verified. The previously
verified marine files were reused after their descriptor references and current
object metadata matched; terrestrial verification read all 15,713,198,646 bytes.
Marine has 17,938 records, including 304 India records, and next generated ID
17,943. Terrestrial has 497,914 records, including 193,174 India records, and next
generated ID 497,919. Both independent source counts and identity contracts pass.

Actual kernel lifetime peak, including file cache, is 7,725,993,984 bytes
(7.20 GiB); the unchanged enforced limit is 8 GiB. The preferred 6.4 GiB margin
produces the recorded advisory warning. Scratch peaked at 23,677,530,112 bytes
and final cloud elapsed time is 22,205.078511625 seconds. Configuration remains
4 CPU / 8 GiB / 100 GiB DISK / zero retries. No cache improvement or second full
build is required. The complete raw report preserves
`compatibility_verified: false`; the separately reviewed deterministic old/new
sample supplies compatibility proof with matching frozen inputs, native tools
and original producer image/source fingerprints.

All locale files are present and validated. Their localization reports preserve
existing missing or unconfirmed translation rows and source-language fallback;
artifact completeness does not claim every field has a confirmed translation.

[PR #199](https://github.com/SkyTruth/shared-datasets-1/pull/199) merged version 3
acceptance and the immutable owned promotion plan after all exact-head checks.
The protected deployment, controlled worker failure-alert delivery and live
ownership checks completed before publication. Only the needed terrestrial bytes
were promoted, with the build's original `2026-10-01` run date. The staging bundle
remains noncanonical; the separate
[publication verification](wdpa-processing-evidence/wdpa-monthly-f2xnm/publication-verification.json)
records terminal success and actual canonical generations.

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
   The runtime has `storage.objects.create` on that object prefix and
   `storage.folders.create` on its matching hierarchical folder prefix. It cannot read,
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
6. With observer and alert prerequisites ready, prepare the publication image and
   execute the worker with `WDPA_PROMOTION_BUNDLE` and the build's `RUN_DATE`.
   It downloads the approved object generations, verifies all required files
   before the first new publication, and passes them to `OwnedGeneratedPublisher`.
   It never downloads the upstream source, normalizes geometry, rebuilds tiles
   or regenerates translations. Publication manifests and receipts are finalized
   with the actual canonical GCS generations; artifact bytes remain unchanged.

Before a new publication, its identity baseline, predecessor release and
reserved counter must still match those used by the build. A realm published
before the build is preserved when its frozen current manifest and owned receipt
match the source period, URL, identity contract, record count and unchanged
allocation sequence. Its unused candidate hashes need not equal previously
published bytes. This permits the existing October marine release to remain
committed while terrestrial's retained bytes are promoted.

After a retained realm is published, its current receipt instead proves the
original build predecessor and allocation. Its run record must contain the
retained artifact hashes plus the finalized manifest hash. This recognizes a
completed terrestrial promotion even though it advanced the live baseline.
A retry of the fully published October bundle verifies both receipts and skips
both realms without downloading or republishing dataset artifacts.
An interrupted publication resumes its original receipt;
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
binding, job, two custom staging/read roles and three prefix-conditioned bucket
bindings. Its reviewed plan may not delete resources, increase limits, grant
canonical data permissions or create a scheduler. Failed builds may leave
uncommitted scratch objects; absence of a complete validated root descriptor
prevents promotion. Scratch existence is not publication authority.

The shared bucket has hierarchical namespace enabled. Uploading to a missing
folder requires `storage.folders.create` as well as `storage.objects.create`
([HNS operations](https://docs.cloud.google.com/storage/docs/hns-overview)).
The build stager has exactly those two permissions. Separate conditions grant
object creation under `objects/_scratch/wdpa-builds/` and folder creation under
`folders/_scratch/wdpa-builds/`; neither permits canonical writes, replacement,
folder rename or deletion. Google supports the full HNS folder resource name in
[IAM conditions](https://docs.cloud.google.com/iam/docs/conditions-resource-attributes).
The reader still has only `storage.objects.get` on build objects.
The Terraform plan guard accepts only this exact bucket, including the provider's
refreshed `b/skytruth-shared-datasets-1` representation. Role, runtime identity,
object/folder conditions and deletion refusal remain exact checks.

Before a complete build, the protected workflow temporarily selects a reviewed
scratch probe command in the same tested image and fixed resources. That command
uses the actual runtime identity and `BuildStager.upload` to create one tiny
no-clobber object in each realm folder. It never commits a build descriptor.
Only a terminal successful probe permits processing; the workflow restores the
processing command after verifying no execution remains active. Failed probes
block a complete build. Probe objects are diagnostic scratch, not publication
artifacts or acceptance evidence.

Retaining execution `wdpa-processing-validation-kncf9` failed at
`2026-10-03T06:00:10.404328Z` (2:00 AM Eastern) during its first marine FGB upload.
Marine native validation had passed, but the HNS folder creation check denied
the old stager role. Object creation was allowed and object deletion remained
denied. The actual kernel peak was 5,732,749,312 bytes and scratch peaked at
15,196,237,824 bytes. This was a storage permission failure, separate from memory
headroom. Terrestrial processing had not started. A completed read-only listing
found zero retained objects for that execution, so all 22 artifact files and
the root descriptor are missing. The [unchanged failed report](wdpa-processing-evidence/wdpa-processing-validation-kncf9/failed-report.json)
and [derived audit/recovery assessment](wdpa-processing-evidence/wdpa-processing-validation-kncf9/retention-assessment.json)
record the failure. One complete retaining build is necessary after the upload
preflight succeeds because there is no complete retained bundle to promote.

The accepted producer image is verified by immutable registry URI, configuration
digest and its original source fingerprint. That fingerprint remains bound to
the raw build and comparison reports and the immutable reviewed promotion plan;
it is not replaced with the current publication code's fingerprint. The
pre-cloud gate runs inside the original tested producer image against the
reviewed staged document mounted read-only. Its source fingerprint and image
configuration must match that document. Consumer, workflow or IAM repairs can
therefore reuse the tested producer without rebuilding its image or relabeling
the staged evidence. A newly tested producer still needs matching new staged
evidence before deployment.
Publication fixes can therefore consume a valid retained bundle without
repeating source processing or rewriting its evidence.

`Dockerfile.promotion` starts from that verified producer image and installs the
reviewed bundle consumer, acceptance gate, publication-only entrypoint and shared
translation publisher dependencies, including the maintained locale catalog.
It inherits the producer's native tools and dependencies without installation
or conversion commands, pins `WDPA_ACCEPTED_BUILD_SOURCE_SHA256` to the accepted
producer and records the reviewed publication executor SHA separately from the
original build executor. The protected workflow smoke-checks the resulting image
and deploys its immutable digest. This small software image layer does not
rebuild dataset artifacts. The publication-only entrypoint requires a reviewed
bundle, except for the explicit pre-write failure probe; it cannot fall back to
source processing when a scheduler or manual execution omits the bundle.

A reviewed translation update captures its previous committed receipt and
manifest snapshots. A retained-build retry follows this verified history to the
original release, checking unchanged base artifact hashes and allocation at each
step. This permits repeated language-only updates without invalidating the
accepted build or rebuilding its geometry. Missing, altered or foreign receipt
history remains an error.

The production job template pins the approved bundle reference from the
acceptance document, so ordinary Scheduler calls receive the same reviewed
input as deployment canaries. It never pins `RUN_DATE`: scheduled invocations
use the current UTC month, and the bundle consumer rejects another month's
build before any publication or recovery. Explicit canaries keep the captured
build date. Deploying a new month's approved retained build is required before
that month's schedule can succeed; a missing or out-of-period build cannot
trigger automatic source rebuilding.

## Infrastructure and failure visibility

The initial worker/observer plan contained **eight creations, one update, zero
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
job IAM and scheduler bootstrap was reviewed and applied after full processing
acceptance, then its temporary project binding was retired after provisioning.

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

## Protected rollout

[PR #199](https://github.com/SkyTruth/shared-datasets-1/pull/199) merged the exact
retained-build acceptance and promotion authority. Protected
[deployment 37127094721](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37127094721)
applied eight observer additions and one worker update with no deletions. Live
worker resources are 4 CPU / 8 GiB / 100 GiB DISK at `/work`; the observer is
1 CPU / 512 MiB / 120 seconds / zero retries, scheduled every five minutes.
Its live execution-reader role is job-scoped, and its storage binding matches
only `_catalog/wdpa-monthly-execution.json`. It has no project runtime binding.

Controlled worker probe `wdpa-monthly-hbdjm` reached terminal failure at
2026-10-03 13:52:10 UTC with the explicit failure before any dataset writes.
Its [actual Monitoring Slack delivery](https://skytruth.slack.com/archives/C0B12LDMB1A/p1791035583550649)
arrived at 13:53:03 UTC. The independent observer then wrote a generation-pinned
schema 1 status document carrying that terminal result. The deployment workflow
stopped at the alert-verification gate before launching a dataset canary.

[PR #200](https://github.com/SkyTruth/shared-datasets-1/pull/200) retired the
temporary project-wide bootstrap binding. Protected
[IAM sync 37129181296](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37129181296)
completed at 14:22:56 UTC with zero additions, zero changes and one deletion:
that exact binding. A subsequent live project-policy read confirms the role has
no binding. Its original absolute expiry was 2026-10-06 00:00:00 UTC; it was not
renewed or expanded. The unbound role definition retains the three approved
provisioning permissions for auditability.

[Promotion 37127874500](https://github.com/SkyTruth/shared-datasets-1/actions/runs/37127874500)
was dispatched once from reviewed main with that verified worker probe and the
original build date `2026-10-01`. Its sole execution `wdpa-monthly-f2xnm` reached
terminal success at **2026-10-03 14:27:15.893723 UTC**, with no failed task or
active worker execution. The worker used 4 CPU / 8 GiB / 100 GiB DISK at `/work`,
zero retries and the publication-only entrypoint. Its software image inherited
the accepted producer and pinned the original processing source independently
of the reviewed consumer executor `8ec80965e5b2b89953d40df2fdb412a88b0f8fe4`.
No source processing or dataset artifact rebuild occurred.

Read-only verification at 14:41:19 UTC checked all eleven terrestrial files in
both `releases/2026-10-01/` and `latest/`: actual generations, sizes, publication
SHA-256 and transaction metadata, plus GCS MD5 and CRC32C checksums matching the
accepted stored objects. The accepted files already had independent actual-byte
SHA-256 proof, and the worker verified them at approved generations before new
canonical writes. Both manifest bytes were read at pinned generations and
matched SHA-256 `0955927bc654abaf64c75bdefbec050c4878a5634657b37475f328df6499dbd8`.
The latest manifest generation is `1791037624842824`; the release manifest
generation is `1791037623675818`.

The terrestrial receipt is `derived_complete`; active ownership is clear, the
current release is `2026-10-01`, and the reserved next ID is 497,919. Its manifest
and release index report 497,914 features; exact promoted bytes retain the
validated 193,174 India records. Marine remains at 17,938 features, next ID
17,943, and its prior state, receipt, manifests and all eleven release/latest
artifact generations are unchanged. No unused candidate marine file was
downloaded or published.

The completed terrestrial promotion phase recorded kernel peak 7,748,005,888
bytes including cache and scratch 24,217,227,264 bytes. These promotion
measurements are separate from the unchanged original build report. Both remain
within enforced limits; exceeding the preferred memory margin is advisory.

The schema 1 observer document reports `wdpa-monthly-f2xnm` succeeded, with a
fresh observation and revalidation headers. Live browser verification showed
October terrestrial with 497,914 rows and generation-pinned FGB/PMTiles links,
October marine with 17,938 rows, and the successful overall execution. Earlier
browser verification showed September terrestrial alongside the running
promotion, preserving the distinction between partial publication and job state.
The publication monitor can stop after this verified outcome. Dataset upload
announcement state is uncertain; no custom or duplicate Slack message was sent.

## Intended remote paths

The observer writes only
`gs://skytruth-shared-datasets-1/_catalog/wdpa-monthly-execution.json`.
The catalog deployment includes `_catalog/web/app.js`, `index.html` and
`release-reference.js`, which carries execution formatting within the existing
viewer module allowlist. The viewer serves the same status document through its
existing static route. Normal WDPA publication paths remain
`100-geographic-reference/130-protected-areas/{wdpa-marine,wdpa-terrestrial}/`
with the existing `releases/`, `latest/`, run records and release indexes.
Existing allocation, claim and receipt ownership governs the retained-artifact
promotion. Protected infrastructure changes and publication evidence are recorded
above; canonical artifact generations were verified after terminal publication
and are retained in the linked publication evidence.

Diagnostic benchmark inputs were staged privately, with `if_generation_match=0`,
at `gs://skytruth-shared-datasets-1/_scratch/wdpa-processing-benchmarks/20261002T040200Z/october-frozen-snapshot.tar`,
generation `1790924057375117`, 5,189,560,320 bytes, SHA-256
`70b9f8b0356de097c7392972c65b0b073485c80ea814d0284a97762a9478151b`.
This disposable scratch bundle is not a shared dataset contract. Its temporary
reader was restricted to that exact object; a read of another object was denied.
