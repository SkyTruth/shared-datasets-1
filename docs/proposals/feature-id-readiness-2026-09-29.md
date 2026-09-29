# Feature-ID fresh-start readiness: 2026-09-29

All three datasets are **NOT READY for production cutover**. The approved
retirement of their pre-launch identity contract removes the need to reconstruct
historical allocations. A fresh start is supported by the local implementation,
but current writer permissions and incomplete cutover machinery prevent a safe
production transition. Dataset lifecycle status is unchanged.

This report accompanies the [fresh-start design](feature-id-fresh-start.md).
Observations began at `2026-09-29T04:33:35Z`. They are snapshots, not a writer
freeze, approval to publish, or certification of the complete effective IAM
policy. No cloud writes, deployments, deletes, or merges were performed.

## Dataset decisions

All paths below use `gs://skytruth-shared-datasets-1/`.

| Dataset | Observed current release | Observed latest bundle | Decision | Missing evidence/work |
| --- | --- | --- | --- | --- |
| `wdpa-marine` | `2026-09-01` | 12 objects; matching release/latest manifest bytes; core generations match the manifest | **NOT READY** | Complete locale rebuild for the new-ID release; complete writer exclusion, protected reset installation, native artifact checks, and serving cutover |
| `wdpa-terrestrial` | `2026-09-01` | 12 objects; matching release/latest manifest bytes; core generations match the manifest | **NOT READY** | Complete locale rebuild for the new-ID release; complete writer exclusion, protected reset installation, native artifact checks, and serving cutover |
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

Twelve local candidates have been rebuilt against the current September metadata,
reusing 99.9736% of requested translation rows. They are structurally valid but
have 8,685 unresolved rows across 8,365 distinct field/locale/text tasks. These
are a rehearsal against current IDs, not the new-contract reset release. See the
[translation rebuild report](wdpa-translation-rebuild-2026-09-29.md) for exact
sources, outputs, and the provider/download blocker. No alias deletion is needed
for the selected approach, and none is authorized. Freeze and recheck the complete
latest inventory under the writer fence before the eventual reviewed replacement.

## Observed writers and administrators

Service-account names in this table end in
`@shared-datasets-1.iam.gserviceaccount.com`. These are observed grants and
control paths, not proof of unrestricted effective access. Organization-level
allow/deny policy could not be read.

| Principal/process | Observed access relevant to cutover | Consequence |
| --- | --- | --- |
| `wdpa-monthly-job` / Cloud Run `wdpa-monthly` | Conditional bucket `storage.objectUser` on both WDPA roots and their release indexes | Existing and newly launched executions using this identity can bypass application ownership until fenced |
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

The project has parent organization `471193686670`. Listing deny policies at
project number `12695949518` returned an empty result. The repository declares
an optional canonical destructive-action deny policy, but its source declaration
is not evidence that it is deployed. Both organization IAM and organization
deny-policy reads returned **403 PERMISSION_DENIED**. An authorized organization
administrator's policy export is required to close this evidence gap; this
report does not propose granting the auditing agent broader permissions.

The live job inventory confirms the runtime accounts above. Image digests were:

- WDPA: `sha256:2f37b0d1a1775de6a82d78bed698b5eb694a567011cf6afd84925a5f0d849e87`.
- Sea ice: `sha256:6ce2f2358f0080a38ca84dc530d67e4e82fb047f9b7932715ad71e051e690409`.

This is an image/configuration observation, not a drain of current executions
or proof of what every historical workflow can deploy. Schedulers, pending
executions, queued GitHub runs, issued credentials, and token expiry still need
cutover-time evidence. No writer-exclusion control was applied in this audit.

## Remaining engineering and rollout evidence

1. Review and activate the implemented
   [protected reset installer](../feature-id-reset-installation.md). It consumes
   exact immutable PR authority, validates the current generations/hashes under
   reviewed writer restrictions, and creates evidence/adoption/state with a
   durable installation journal. Competing installs, revoked approval, drift,
   and crash/retry boundaries have local tests. Activation remains blocked:
   the approved-fence registry is empty and this change does not provision the
   dedicated reset identity. The offline preparation CLI grants no authority;
   generic promotion tools deliberately refuse these managed paths.
2. Complete and review the IAM/deployment fence, including old job identities,
   generic publisher/localization paths, administrative/impersonation paths,
   running jobs, queued workflows, and credential propagation. Follow the
   protected Terraform workflow; do not apply locally.
3. Choose the first dated release for each asset, verify its destination and run
   record are absent, resolve the WDPA aliases, and capture a complete reset
   inventory. Current snapshots must be refreshed after publishing is held.
4. Finish serving integration. `feature_metadata_index.py` already scopes
   records by asset/release/load, but index activation and cache joins were not
   tested here. Independent `index-loads/YYYY-MM-DD/{load-id}.json` status
   records may be created through the existing loader with a no-clobber
   precondition; replacement/deletion and all identity-bearing paths remain
   blocked in generic tools. The loader's status record is written after the
   Firestore load and is not publication ownership or reset authority.
5. Run the real native geospatial artifact checks with the pinned toolchain,
   then validate first publication, matching tile/metadata/locale IDs, serving
   activation, durable counters, retries, and exclusion of older writers. Local
   adapter tests mock the native-tool boundary. Both WDPA assets must pass
   before their shared job resumes.

Keep PR #154 draft. No standalone merge or deployment gate replaces these
conditions. A failed transition must keep the new reservation and writer fence;
re-enabling the old writer is not a recovery method.

## Local verification

- `UV_CACHE_DIR=.uv-cache uv run --no-sync pytest -q`: **1,079 passed, 4 skipped,
  1,241 subtests passed**, including protected reset installation, immutable
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

No image build, new remote CI result, deployed execution, or production native
artifact validation is claimed by these results.

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

## Draft PR deployment hold

The draft now includes the strict hold-only rollout registry and checks in all
three ingestion deploy workflows. Tests execute their actual gate steps for push
and dispatch and reject bypasses before cloud authentication. This protects
against deploying these unadopted publishers if the branch is later merged; it
does not revoke any writer permission or stop existing jobs. No live hold has
been installed by this draft PR. The dataset decisions above remain NOT READY.
