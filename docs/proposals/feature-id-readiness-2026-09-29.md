# Feature-ID fresh-start readiness: 2026-09-29

This report preserves the September 29 production observations. The September 30
simplification replaces the exhaustive organization audit and blanket hold with
the [project-scoped installation runbook](../feature-id-reset-installation.md).
Production cutover is not yet performed; that is distinct from code PR readiness.

This report accompanies the [fresh-start design](feature-id-fresh-start.md).
Observations began at `2026-09-29T04:33:35Z`. They are snapshots, not a writer
freeze, approval to publish, or certification of the complete effective IAM
policy. The original audit made no cloud writes. The later translation work
staged three review objects under `_scratch/pending-publishes/`; no canonical
writes, deployments, deletes, or merges were performed.

## Dataset decisions

All paths below use `gs://skytruth-shared-datasets-1/`.

| Dataset | Observed current release | Observed latest bundle | Decision | Missing evidence/work |
| --- | --- | --- | --- | --- |
| `wdpa-marine` | `2026-09-01` | 12 objects; matching release/latest manifest bytes; core generations match the manifest | **NOT READY** | Complete locale rebuild for the new-ID release; paused/drained jobs, protected reset installation, native artifact checks, and serving cutover |
| `wdpa-terrestrial` | `2026-09-01` | 12 objects; matching release/latest manifest bytes; core generations match the manifest | **NOT READY** | Complete locale rebuild for the new-ID release; paused/drained jobs, protected reset installation, native artifact checks, and serving cutover |
| `ims-sea-ice-extent` | `2026-09-27` | Expected five objects; matching release/latest manifest bytes; core generations match the manifest | **NOT READY** | Complete writer exclusion, protected reset installation, native artifact checks, and serving cutover |

WDPA roots are `100-geographic-reference/130-protected-areas/{asset-slug}`.
Sea ice uses `200-imagery-derived/250-weather-climate/ims-sea-ice-extent`.
None had `{asset-root}/publications/state.json` at the initial snapshot.
Absence of that one state object does not establish absence of older partial
releases or all other recovery objects.

| Dataset | Latest manifest generation | Dated release manifest generation | SHA-256 of both manifest copies |
| --- | --- | --- | --- |
| `wdpa-marine` | `1788256151368457` | `1788256151130555` | `ec4e4a8ab89aa8db6cce52cfac850dbf616d99c33dc260e8b2c70e9077e6cf65` |
| `wdpa-terrestrial` | `1788264544985809` | `1788264544729991` | `4a2c9f6fcfb1aeaff5ebc312feb025f7c119063a8f5c773e13e8052bb98fa77f` |
| `ims-sea-ice-extent` | `1790607848815459` | `1790607848525322` | `62a68e515d197507edf0ca01f3be52839bf89f4a337da3e7cda2917ba3ba5e95` |

The existing manifests have generated strategies but no `contract_id` or
verified sequence state version. Their numeric next-ID fields (`17674`,
`305211`, and `14578`, respectively) are observations only. They neither prove
historical allocation ceilings nor seed the new contract.

In the initial audit, only the small manifests were downloaded and independently hashed. Current
object generations/sizes were listed, and the four core artifact generations
were compared with the captured latest manifests. Full dataset/locale bytes
were not hashed or natively validated. The current WDPA FGBs alone are about
1.30 GB and 4.95 GB. No complete reset candidate was generated from this partial
evidence, and no first-release date has been frozen.
The subsequent [translation rebuild](wdpa-translation-rebuild-2026-09-29.md)
downloaded and hashed both WDPA canonical metadata/schema snapshots and the
historical canonical metadata/translation CSV bundles. This does not establish
native validation of the FGBs or PMTiles.

## WDPA translation boundary

The deployed WDPA producer rebuilds seven objects: the five core artifacts,
Spanish metadata, and the translation-source CSV. Each live WDPA `latest/`
also contains the following five files. Their names are
`{asset-slug}.metadata.{locale}.ndjson.gz` under the corresponding `latest/`.

| Locale | Marine generation | Terrestrial generation |
| --- | --- | --- |
| `fr` | `1781544092845673` | `1781110614068145` |
| `id` | `1781544093948055` | `1781110616468063` |
| `pt` | `1781544094938014` | `1781110618867718` |
| `pt_br` | `1781544095960264` | `1781110621272233` |
| `sw` | `1781544096873065` | `1781110623716463` |

They cannot silently survive a reset: the same decimal ID may identify a
different feature afterward. The user chose to rebuild these files. The local
producer now requires and rebuilds all six locales, including Spanish, for both
WDPA assets. Regression tests cover complete owned replacement and refuse a
missing locale before any publication write.

Twelve local candidates have been rebuilt against the current September metadata.
Verified historical reuse supplied 99.9736% of requested rows; the imported
machine-translation supplement filled the remaining 8,685 rows across 8,365
unique field/locale/text tasks. All 32,891,400 requested rows are present, both
pending-task files are empty, and every canonical/CSV/locale join passed.
The agent then corrected all 67 new category/description strings and rebuilt both
assets again. The user approved preserving official spelling for the 1,213 new
names; source URLs are also preserved. The final supplement and review evidence
are generation/hash-pinned in scratch staging. This is agent review, not human
language certification. These remain current-ID rehearsal files,
not the new-contract reset release. See the
[translation rebuild report](wdpa-translation-rebuild-2026-09-29.md) for exact
sources, output digests, key restoration evidence, and language-review limits.
No alias deletion is needed for the selected approach, and none is authorized.
Include the corrected supplement in the immutable review and freeze/recheck the
complete latest inventory with the affected job paused and drained before replacement.

## Observed writers and administrators

Service-account names in this table end in
`@shared-datasets-1.iam.gserviceaccount.com`. These are observed grants and
control paths, not proof of unrestricted effective access. Organization-level
allow/deny policy could not be read.

| Principal/process | Observed access relevant to cutover | Consequence |
| --- | --- | --- |
| `wdpa-monthly-job` / Cloud Run `wdpa-monthly` | Conditional bucket `storage.objectUser` on both WDPA roots and their release indexes | Existing and newly launched executions using this identity can bypass application ownership until paused/drained and replaced |
| `sea-ice-daily-job` / Cloud Run `sea-ice-daily` | Conditional bucket `storage.objectUser` on the sea-ice root and its release index | Same risk for sea ice |
| `shared-datasets-publisher` / approved mutation and localization workflows | Conditional `storage.objectUser` covers all three canonical roots and `_catalog/` | New CLI guards do not revoke older workflows or direct credential use |
| `shared-datasets-terraform` / protected infrastructure workflows | Bucket IAM management; project custom roles include job update/run, service-account policy/actAs, bucket policy management, and object deletion | This identity can alter the writer boundary indirectly; the preview-named role is not evidence of preview-only scope |
| `christian@skytruth.org`, `jona@skytruth.org`, `karl@skytruth.org`, `will@skytruth.org` | Project `roles/owner`; bucket also has project-owner/editor legacy bindings | Include these administrative paths in effective-writer review; a narrower direct bucket grant does not cancel inherited grants |
| `metadata-index-loader` | Bucket object creation under `index-loads/`, plus Firestore `datastore.user` | It writes serving state/status, not the observed allocation bundle; its activation path must be checked separately |

The bucket's project-owner/editor legacy bindings do not enumerate members by
themselves. Project policy listed the four owners above and no explicit project
editor binding. Read-only consumers and the separately scoped EAMLIS runtime
were not counted as observed allocation writers. Managed-folder IAM and all
service-agent effective permissions have not been exhaustively resolved.

Service-account policy reads for the two ingestion runtime identities returned
no direct bindings. The publisher and Terraform accounts each allow
`roles/iam.workloadIdentityUser` for the exact GitHub subject
`repo:SkyTruth/shared-datasets-1:environment:shared-datasets-production` in the
project's `github` workload identity pool. No user-managed keys were returned
for these four service accounts. These reads do not rule out inherited
impersonation, existing tokens, broader deployment control, or future grants.
Live federation-provider conditions and GitHub environment protections were
not audited.

The project has parent organization `471193686670`. Organization IAM reads
returned 403. That observation remains recorded; obtaining those policies is no
longer a rollout prerequisite. Existing administrators are trusted. The active
runbook checks paused schedules and drained executions, exact object generations,
and immutable review authority rather than attempting an organization-wide
proof of writer exclusion. No organization evidence exporter is required.

The live job inventory confirms the runtime accounts above. Image digests were:

- WDPA: `sha256:2f37b0d1a1775de6a82d78bed698b5eb694a567011cf6afd84925a5f0d849e87`.
- Sea ice: `sha256:6ce2f2358f0080a38ca84dc530d67e4e82fb047f9b7932715ad71e051e690409`.

This is an image/configuration observation, not a drain of current executions
or proof of what every historical workflow can deploy. Schedulers, pending
executions, queued GitHub runs, issued credentials, and token expiry still need
cutover-time evidence. No writer-exclusion control was applied in this audit.

## Remaining production operations

Follow the [installation runbook](../feature-id-reset-installation.md) after the
code PR is reviewed: pause/drain affected jobs and older workflow runs, capture
fresh inventories, stage immutable reset plans, install through the existing
protected publisher, deploy and validate complete first releases, then resume.
The deployment checks actual installed state; EAMLIS has no reset requirement.
No new reset identity, organization-policy export, or approval registry is needed.
The translation candidates are complete; final new-ID joins are checked during
the first new-contract publication. Keep Firestore lookup inactive.

## Local verification

- `UV_CACHE_DIR=.uv-cache uv run --no-sync pytest -q`: **1,086 passed, 4 skipped,
  1,251 subtests passed**, including protected reset installation, immutable
  authorization, paginated control-plane reads, translation rebuild integration and
  complete CSV/sidecar join validation. The default run leaves the four opt-in native checks disabled. Crash coverage uses a generation-aware in-memory store, not GCS
  failure injection in production.
- A separate opt-in native run of the six geospatial suites passed **90 tests**
  with `RUN_GDAL_INTEGRATION_TESTS=1` after unsetting inherited `PROJ_LIB` and
  `PROJ_DATA` for that command. The first attempt had three failures caused by
  the local GDAL installation reading an incompatible database from another
  PROJ installation; no repository fix was needed. Tools resolved through
  `/usr/local/bin`: GDAL 3.13.1, Tippecanoe 2.79.0, PMTiles dev. These are synthetic
  integration fixtures, not the full production reset bundle or the pinned CI
  image. Docker was unavailable locally.
- `UV_CACHE_DIR=.uv-cache uv run --no-sync ruff check .`: passed.
- `UV_CACHE_DIR=.uv-cache uv run --no-sync python scripts/catalog_docs.py check`:
  current for all 24 assets, with six existing source-confirmation warnings.
- `git diff --check`: passed. The initial six-document link check and the
  subsequent four-document translation/rebuild link check passed.

The implementation head `0947f293a050d4a5adc9416cdff98d9ea0c2ab2d`
passed all applicable checks, including the full suite and pinned native image in
[CI run 36663795825](https://github.com/SkyTruth/shared-datasets-1/actions/runs/36663795825),
as well as Chromium, SDK, catalog, hygiene, and protected-readiness checks.
This includes the supplement binding and cache regression additions. No deployed
execution or production native artifact validation is claimed by these fixtures.

## Retained evidence

Raw evidence is retained locally under the standard task root at
`_scratch/feature-id-readiness-20260929T043334Z/`. It includes:

- `objects.json` with exact latest paths/generations/sizes and state presence;
  three latest manifests, three release manifests, and `release-anchors.json`.
- `bucket-iam.json`, `project-iam.json`, `project-parent.json`, `run-jobs.json`.
- The four named service accounts' `*-iam.json` and `*-keys.json`, project custom
  role definitions, project deny listing, and the organization permission errors.
- `evidence-sha256.json` records SHA-256 hashes of the 24 retained evidence files.

Later IAM reads record endpoint, observation time, HTTP status, and response.
No private key material or access tokens are included. The evidence directory
is local review material and has not been published to the bucket or repository.

## Current deployment behavior

The blanket three-job hold was removed on September 30. WDPA and sea ice now
check real publication state/receipts before build/apply. Missing or incomplete
reset state stops only that affected deployment; valid state permits deployment.
The reset installer uses the existing deployment queue and existing identities,
checks paused/drained jobs before each write, and never requests organization IAM.
No canonical objects, schedules, jobs, or permissions were changed by this code
revision. The production observations above remain a snapshot, not a live canary.
