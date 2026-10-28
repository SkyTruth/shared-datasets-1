# EAMLIS PROBLEM_KEY installation proposal: October 28, 2026

This proposal installs the reviewed permanent mapping in **prepared** state.
Approval and merge authorize the protected installer to write the three state
objects below. They do not execute the first new-identity publication. Published
FGB, PMTiles, metadata, translations, release indexes and catalog identity remain
on the legacy contract until that separately directed execution succeeds.

The immutable [installation plan](../.github/dataset-plans/eamlis-abandoned-mine-land-inventory/eamlis-key-cutover-20261028/6acf9b0cff74b07614e807d9bd16e76e8dccf320a42f942d9fcc8bbe123d8680.json) is the mutation authority.
Its SHA-256 is `6acf9b0cff74b07614e807d9bd16e76e8dccf320a42f942d9fcc8bbe123d8680`. The readable PR fence must match it.
The implementation and general recovery rules are in the
[identity runbook](eamlis-feature-identity.md), delivered by
[PR #333](https://github.com/SkyTruth/shared-datasets-1/pull/333).
This proposal changes no runtime code, dependencies or Terraform.

## Verified baseline and prerequisites

Observed October 28, 2026, at 15:57 UTC:

| Evidence | Observed value |
| --- | --- |
| Complete legacy release | `2026-10-28`, 64,506 points, 57 source columns |
| Release-index generation | `1793167112193107` |
| Baseline token | `4f07e9de4ff2b8c78184132370c45357f999097527e9fcc59801c6e8192d0fb6` |
| Existing control | v1, no owner, no hold; generation `1793167112477426` |
| Scheduler | `PAUSED`; normal schedule `0 10 2 * *`, UTC |
| Running/pending executions | None, verified through all execution-list pages |
| Proposed first release label | `2026-10-29`; dated release prefix and run-record prefix unoccupied |
| Existing installation marker and target ledger | Absent; both create-only |

The schedule was paused through protected
[run 40720195190](https://github.com/SkyTruth/shared-datasets-1/actions/runs/40720195190).
Pausing was the only live operational change during preparation. It preserves
published data and prevents future automatic runs while the proposal is reviewed.
No execution was cancelled.

The live job is generation 37, observed generation 37, with image:

```text
us-central1-docker.pkg.dev/shared-datasets-1/shared-datasets-jobs/eamlis-monthly@sha256:0212c78c8c507e54a20f10beb39852b718bd327d3950d6addcfef29213f1771b
```

This matches the tested deployment of revision
`cf351ed400e21068cbb8d4a4f69cee74f4813181` in successful
[deployment job 122653274733](https://github.com/SkyTruth/shared-datasets-1/actions/runs/40637790886/job/122653274733).
Its canary `eamlis-monthly-5smtw` completed successfully at 05:58:36 UTC and
published the captured legacy baseline. No job-template `RUN_DATE` is set.

## Refreshed identity evidence

The [replay report](eamlis-identity-audit-2026-10-28.json) covers all 20 indexed
releases, April 30 through October 28. Every local input was checked against the
current index's path, generation, size and SHA-256. The staging command then
independently downloaded every pinned historical input and recomputed the exact
ledger before uploading scratch sources.

Nineteen releases have `PROBLEM_KEY`; April 30 lacks the column and contributes
no guessed assignments. The resulting ledger has 64,563 permanent assignments:
64,538 problems and 25 area-only records. It retains 57 keys absent from the
current release. Twelve new problems were observed since October 27; no formerly
absent key reappeared. The next available decimal ID is 64,564.

Current candidate profiling, with percentages over 64,506 rows:

| Candidate | Type | Distinct | Distinction | Empty | Domination | Skew | Top examples (counts) | Concern |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `OBJECTID` | integer | 64,506 | 100% | 0% | <0.01% | 1.00 | 1, 2, 3 (1 each) | Reused or renumbered between releases |
| `PROBLEM_KEY` | string | 64,481 | 99.96% | 25 / 0.04% | <0.01% | 1.00 | AK0000014, AK0000015 (1 each) | Empty only on the 25 area-only rows; no provider immutability guarantee |
| `AMLIS_KEY` | string | 24,714 | 38.31% | 0% | 0.48% | 117.62 | PA942003 (307), PA942069 (243) | Groups multiple problems |
| `NUMBER` | integer | 307 | 0.48% | 25 / 0.04% | 37.95% | 116.57 | 1 (24,483), 2 (12,982) | Repeated and historically mutable |

The proposed assignment is one permanent decimal ID per exact typed pair:
`(problem, PROBLEM_KEY)`, or `(area, AMLIS_KEY)` only when every defined
problem-side field is explicitly empty. The same 25 area-only records pass that
check throughout the keyed history. Zero counts as populated. The producer rejects
ambiguous keys, duplicate records, area/problem coexistence and cross-area key
reuse. It never derives a key from `NUMBER`, names or coordinates.

## Exact state installation

Installation ID: `c68c8bf90ed6c996bb51d4976af678fd7714d80b169dba8b0fdcc5e8f23a94c3`.
All sources are under:

```text
gs://skytruth-shared-datasets-1/_scratch/pending-publishes/eamlis-abandoned-mine-land-inventory/eamlis-key-cutover-20261028/
```

| Source file | Source generation | Bytes | CRC32C | Destination expectation |
| --- | --- | ---: | --- | --- |
| `ledger.json` | `1793202967118331` | 8,322,944 | `TxIiFA==` | Create only |
| `marker.json` | `1793202967525256` | 13,896 | `hwzA5A==` | Create only |
| `control.json` | `1793202967818457` | 1,247 | `4DLgTA==` | Replace control generation `1793167112477426` |

Canonical destinations, in installation order:

1. `gs://skytruth-shared-datasets-1/300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/publications/identity-ledgers/a5a5028f00b545503c969d253ec50d3cdbfa04fe486215cfe33e820690339455.json`
2. `gs://skytruth-shared-datasets-1/300-infrastructure-industrial/320-mining/eamlis-abandoned-mine-land-inventory/publications/identity-installation.json`
3. `gs://skytruth-shared-datasets-1/_catalog/ingestion-holds/eamlis-abandoned-mine-land-inventory.json`

Independent rereads of each staged generation matched the local bytes, size,
CRC32C and declared SHA-256. The initial mapping also matches the standalone
historical replay exactly. The control source is a template: only its ledger
reference's generation placeholder is finalized to the newly installed generation.
The selected ledger, installation and first-release date are otherwise pinned.

The protected installer rechecks exact PR authority and paused/drained jobs before
each write, replays the complete history, verifies the concrete legacy manifest
and migration inputs, then writes ledger, immutable marker and control with
create/CAS preconditions. Any changed baseline or control requires a fresh review.
The plan requests no dataset artifact, release-index, catalog or README mutation.
Its `breaking_changes` array is empty because installation alone leaves the
published identity unchanged; the subsequent first publication has the consumer
impact described below.

## Translation preview

The captured dated legacy CSV has 408,479 rows. The real migration helper was
run locally against the pinned October 28 canonical metadata and proposed ledger:

| Result | Rows |
| --- | ---: |
| Exact current feature, field, locale and source-value hash matched | 64,438 |
| Historical source-value hash no longer current | 342,944 |
| Legacy ID absent from the captured canonical metadata | 1,097 |

All 64,438 usable current Spanish `PA_NAME` translations map to the correct new
IDs. Their text and review states are preserved; migration notes record the
concrete source generation and old ID. The retained states are 63,545
`needs_review`, 564 `reused_translation`, 328 `document_translated` and one
`machine_translated`. None is upgraded to human-reviewed.

The remaining 344,041 rows stay preserved in the immutable dated legacy CSV;
they are not assigned to potentially unrelated numeric IDs. All 64,506 current
features have nonblank names; the 68 without usable current translations are
already missing before the cutover and retain source-text fallback. This preview
created no provider requests or published translation files.

## After approval and merge

1. Observe `Data / Approved mutation` to terminal success and verify the installed
   marker, selected ledger, initial ledger and v2 control phase `prepared` against
   this plan. Keep the scheduler paused. Do not start another legacy writer or an
   unrelated EAMLIS deployment/canary during installation.
2. Obtain the maintainer's explicit instruction for one execution of the verified
   deployed job with execution-only `RUN_DATE=2026-10-29`. This is a reserved release
   label, not an automatic execution time. Recheck the actual deployed image,
   quiescence and prepared state first. Do not substitute another date or rerun a
   completed deployment expecting it to create this new publication.
3. Follow that execution to terminal status. The >5% geometry-count-loss hold and
   its separate exact-candidate review remain enforced. If held, inspect the staged
   candidate through the private catalog and prepare its normal held-candidate
   PR. Installation approval never clears a source-quality hold.
4. Verify the first complete release's contract `eamlis-problem-key-v1`, permanent
   IDs, all 25 area records, translation migration report, exact geometry, manifest
   and ledger pins, latest copies, release index and active installation evidence.
5. Only after activation, update the asset document's structured identity and two
   derived columns, regenerate the catalog, and publish its README through the
   normal reviewed route. Resume with the protected schedule-control workflow;
   its resume gate requires complete active publication evidence.

The first publication changes the numeric ID namespace. Consumers must treat
legacy and new IDs as different contracts, pin old release dates for historical
citations, and rebuild cached ID lookups for the first keyed release. Equal numeric
IDs across the boundary do not mean the same problem. Cross-contract release
comparison is refused by code. Old releases remain readable. Geometry is exact;
tiny real coordinate changes still count as changes. OSMRE does not publish a
never-reuse guarantee for hosted `PROBLEM_KEY`; same-area reuse remains an
uncertainty that these checks cannot eliminate.

On partial installation, investigate and retry the exact reviewed workflow only.
Never delete the marker, replace the ledger, reset the sequence or refresh CAS
expectations to force a retry. An abandoned prepared installation needs reviewed
recovery; it must not silently resume legacy publishing. No rollback or release
deletion is included in this proposal.

## Preparation and integration record

Integration and recovery owner: this EAMLIS chat, branch
`codex/eamlis-problem-key-cutover`, based on main
`389228b201ef9cbdc3db1c2999506670df123220`. Prerequisite PR #333 is merged and
its tested image is deployed. PR #334 is already in the comparison base.
The only overlapping history is the new EAMLIS audit; existing audit reports,
asset metadata and runtime files are unchanged.

Preparation used the locked `uv` environment, `eamlis_identity_audit.py`,
`eamlis_identity_transition.py stage-plan`, the real translation remapper,
`reviewed_dataset_plan.read_document`, and independent GCS rereads of the exact
staged generations and current destination preconditions. The required frozen-head
preflight evidence and hosted checks are recorded in the PR body.
Secret scanning classified the public baseline digest as a generic API key.
`.gitleaksignore` excludes only the exact original commit/path/rule/line finding,
following the existing historical-finding pattern; all other findings remain
subject to the complete full-history scan. The immutable plan is unchanged.

Only scratch sources were uploaded. The scheduler pause is recorded above.
No canonical GCS object was changed, no first-identity execution was started,
and no dataset upload announcement was sent during preparation. The existing
protected publication workflows own their post-merge notifications.
