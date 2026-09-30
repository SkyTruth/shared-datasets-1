# Feature-ID fresh start before launch

Date: 2026-09-29. Status: identity-contract retirement approved by the user;
publisher integration and deployment hold implemented and tested; production cutover not ready.

The user confirmed that the service has no existing users and that the old
identity contract can be retired. For `wdpa-marine`, `wdpa-terrestrial`, and
`ims-sea-ice-extent`, this replaces the historical-continuity requirement in the
[earlier PR #154 rollout plan](repository-audit-2026-09/plans/pr154-merge-and-rollout.md).
Reconstructing every old allocation is no longer a prerequisite for a new
identity history. This decision does not establish that old IDs were correct,
retire the datasets themselves, or authorize deleting historical releases.

## Invariant and boundary

Within the new identity contract for an asset, an allocated or reserved ID must
never be allocated to an unrelated feature. Deletions, empty releases, failed
publications, and retries cannot lower the allocation counter. Only the owner
of a durable publication claim may expose the IDs it reserved.

The transition establishes a new contract explicitly. An old and new feature
with the same decimal ID have no implied relationship. Historical releases
remain readable in their original release context; they never become allocation
authority for the new contract. Missing state is an error after cutover, not
permission to reset again.

## Scope and code evidence

Implementation base: `codex/audit-feature-id-highwater` at `a8149e4`, including
local `main` at `a67d6ba`. Recovery-core reference: `9bb3e4b`; its core and GCS
adapter have been reused and extended in the local working tree. See the
[read-only readiness report](feature-id-readiness-2026-09-29.md) for cloud
observations and explicit evidence gaps. Local tests are not a new CI result.

| Dataset | Producer | Declared runtime account in project `shared-datasets-1` | Sequence reset |
| --- | --- | --- | --- |
| `wdpa-marine` | `ingestion/wdpa_monthly/run.py` | `wdpa-monthly-job` | Approved in principle; cutover not ready |
| `wdpa-terrestrial` | `ingestion/wdpa_monthly/run.py` | `wdpa-monthly-job` | Approved in principle; cutover not ready |
| `ims-sea-ice-extent` | `ingestion/sea_ice_daily/run.py` | `sea-ice-daily-job` | Approved in principle; cutover not ready |

EAMLIS uses source IDs and requires no sequence reset. Shared helper changes
still trigger all three ingestion deployment workflows and require regression
coverage for EAMLIS.

- `scripts/release_feature_model.py` requires an explicit contract when creating
  or consuming generated allocation state. Legacy manifests remain readable;
  absent or foreign contracts cannot become allocation authority.
- `ingestion/common/identity_reset.py` binds `generated-2026-v1` and one new dated
  release to the complete current `latest/` inventory and the old release/latest
  manifest generations and hashes. It separates the empty allocation baseline
  from the existing objects' replacement anchors. `scripts/feature_id_reset.py`
  prepares an offline review envelope; it does not authorize or install state.
- WDPA `publish_asset` and sea-ice `publish_outputs` now use
  `ingestion/common/owned_publication.py`. The durable core claims ownership and
  reserves the range before any checkpoint or ID-bearing object write. It
  captures release/latest artifacts, manifests, the run record, and the release
  index in one resumable intent with fixed generation preconditions.
- `ingestion/common/publication.py` verifies reset evidence, contract, complete
  latest replacement, and the approved first release. Missing or conflicting
  state stops publication. Later refreshes verify the persisted manifest
  baseline against the durable counter.
- The runtime uses stable `CLOUD_RUN_EXECUTION` and an executor commit embedded
  by the two deployment image builds. It resumes a captured receipt before
  upstream discovery. Lost local inputs before checkpoint completion cause a
  safe stop with the reservation held. Automated takeover is not implemented.
- `scripts/gcs_asset.py`, `scripts/publish_release.py`, and
  `scripts/publish_workflow.py` reject unsupported writes/deletes to the three
  managed roots and their release/schema indexes. New independent index-load
  status records retain their no-clobber upload path. Generic mutation tools
  cannot install reset state. These local checks do not restrict older binaries or
  direct cloud clients using still-authorized credentials.
- The catalog joins tiles and metadata through dated release references and
  exact object generations. Its metadata cache includes the sidecar path and
  generation, including the locale file. Regression coverage checks identical
  numeric IDs in unrelated old/new releases, locale separation, replacements,
  and an old download completing last. Firestore metadata lookup is intentionally
  inactive in `services/metadata_service/run.py`; activating it is outside this
  cutover's scope.
- The hold from `codex/audit-publication-rollout-gate` is integrated into this
  branch alongside the real publisher adapters. All three ingestion deploy
  workflows invoke the strict hold-only registry before cloud authentication,
  image builds, Terraform, and canaries. There is no environment/dispatch
  override. This deliberately holds routine maintenance and EAMLIS deployment
  too. Existing executions, schedules, and historical workflows are unaffected.
- The job Terraform files declare runtime write grants; the general publisher
  also has canonical write grants in `terraform/envs/prod/canonical_mutation_iam.tf`.
  These declarations do not prove the complete set of effective live writers.

## Design and remaining boundaries

1. **Make the break explicit.** The allocation/publication boundary and release
   manifest use `contract_id: generated-2026-v1`. Decimal feature IDs, asset
   slugs, canonical paths, and assignment-key/hash rules remain unchanged.
   A serialization version such as `sequence_state_version` is not itself an
   identity namespace. Readers and cached references must retain release or
   contract context when joining IDs; old tiles must never join new metadata.
2. **Prepare one reset per asset.** The local candidate binds a reset receipt to the old
   current object generations, the approved new contract, and a new dated
   release destination. The new allocation baseline is empty and starts at 1;
   expected replacement generations still describe the existing objects.
   The [protected installer](../feature-id-reset-installation.md) requires an
   immutable reviewed publish plan tied to the exact candidate and a registered
   writer-fence snapshot. The registry is empty, so activation remains blocked.
   The review envelope alone has no execution authority. There is no runtime
   reset flag or missing-manifest fallback.
3. **Connect the real publishers.** Both generated producers now use the
   existing ownership/reservation core, with one authoritative allocation
   counter. The first new publication and later refreshes acquire ownership and
   reserve their range before exposing ID-bearing objects. An interrupted
   attempt retains its reservation.
   A retry resumes the same intent or stops for explicit recovery. A bounded
   safe stop is sufficient; automatic takeover and generalized repair are not
   launch requirements.
4. **Rebuild the complete new release.** Regenerate FGB, PMTiles, metadata,
   schema/manifest, applicable locale outputs, and the release index from the
   new IDs. Old ID-based decisions and translation joins must not carry across
   the boundary by numeric coincidence. Reuse of source-keyed content requires
   its ordinary validation. Keep old releases intact and distinguish their
   identity history in release selection/documentation.
   Each WDPA reset inventory must bind the same generation/hash-pinned staged
   translation supplement. The first build consumes that input and must report
   complete translations before the publisher reserves IDs. Later new-text gaps
   remain explicit work in the translation CSV and run report.
5. **Exclude bypass writers.** Every path that changes the three assets or
   their authoritative per-asset state must participate in ownership or refuse.
   For launch, refuse unsupported manual mutation/repair paths rather than
   building every possible adapter. Verify effective runtime, publisher,
   deployer, impersonation, inherited IAM, and queued/historical workflow paths.

The reset is a bounded transition, not a permanent menu of legacy modes. Remove
obsolete compatibility paths when implementation proves they have no remaining
reader or persisted-data requirement. Retain historical readers where needed;
do not weaken validation of ordinary publication.

## Rollout sequence

Keep PR #154 draft while this integration is prepared. The deployment stop is
included in this draft with the publisher integration; it is not being merged
separately. Its presence does not make a production reset safe or complete.

1. Review the publisher integration and protected reset installer, including
   their authority and crash-recovery tests. Update #154's live checklist to replace historical
   reconstruction with the accepted reset conditions when preparing its PR
   revision. Existing archived plans and PR-body snapshots remain historical.
2. Complete the smaller read-only readiness check: current release/manifest
   anchors, internal ID dependencies, current/partial publication state, exact
   new release destinations, and effective writers. Historical allocation
   reconstruction is deliberately excluded. The first live snapshot is recorded
   in the readiness report; effective-writer and serving evidence is incomplete.
3. Use reviewed production workflows to pause/drain affected publishing and
   prevent older binaries and generic writers from writing adopted state.
   Schedules and an empty execution list alone are not an enduring fence.
   Resolve any necessary IAM changes through Terraform and protected workflows;
   include already-issued credentials and permission propagation in verification.
4. Install the reviewed publishers with automatic canary/scheduler activation
   held until their reset state is ready. Freeze/recheck current generations,
   then run each explicitly reviewed reset and first publication through the
   approved publisher. Canonical promotions require immutable checked-in plans
   and exact source/destination generation preconditions under `AGENTS.md`.
5. Validate first releases, release-index updates, matching tile/metadata context,
   counters, reservations, and old-writer exclusion. Resume routine publishing
   only for assets whose checks pass. WDPA shares one job, so both WDPA assets
   must pass before that job resumes. No global three-asset atomicity is assumed.

If cutover fails, keep publishing held and preserve new reservations and
receipts. Do not roll back to an old writer or reuse a partially exposed range.
Historical read access can remain available while the new publication is repaired.

## Completion criteria

The implementation must demonstrate through real producer/publisher entry points:

- Deletion, empty output, and later additions never reduce the new counter.
- Two jobs that build from the same baseline cannot both publish new IDs.
- Crashes and lost responses before/after durable reservations and artifact
  writes preserve ownership and the counter; retries cannot change intent.
- A reset requires explicit authority and exact captured object generations;
  absent/invalid state after reset cannot trigger another reset.
- Retained legacy releases cannot seed the new counter; old IDs cannot silently
  join new metadata, translations, or cached map features.
- Existing successful-run and unchanged-source shortcuts cannot suppress the
  first release of the new contract or restore an old release as latest.
- Stale writers and unsupported publication paths cannot mutate adopted state.
- Source-ID EAMLIS behavior remains correct; all affected ingestion suites and
  required native geospatial checks pass.

For each dataset, production readiness additionally requires recorded current
generations, effective writer controls, the reviewed reset bundle/receipt,
successful first publication, and verified serving state. All three remain
**NOT READY for production cutover** until that evidence exists. Their old-ID
history is **not required** for this approved fresh-start approach.

## Work recorded here

The local change implements the contract boundary, reset review candidate,
owned producer adapters, image executor identity, and refusal in generic mutation
paths. The regression tests exercise actual WDPA/sea-ice publication entry
points with an in-memory generation-aware store, overlapping jobs, crashes after
durable writes, retries, stale contracts, old success records, and incomplete
locale replacement. The 90 opt-in native geospatial tests also passed locally using synthetic
fixtures. The implementation head also passed the pinned CI image; the full
production reset bundle still requires separate native validation.

The read-only cloud snapshot captured current object metadata/manifests, bucket
and project IAM, job identities/images, service-account access/key metadata, and
available deny-policy evidence. No remote objects, IAM, or job configuration were changed. The implementation
and reports are on draft PR #154 for review, with the implementation checks green.
Historical releases were preserved. The
remaining work is reviewed installer activation, complete writer exclusion,
language review and new-contract use of the
[completed translation rehearsal](wdpa-translation-rebuild-2026-09-29.md),
production serving checks, and reviewed cutover. The user chose rebuilding over
retiring the ten old WDPA locale aliases. The local monthly producer now reuses
verified source-keyed translations and rebuilds all six locales for both assets;
the September rehearsal now has complete requested-row coverage and validated
CSV/locale joins. Machine language quality is not approved by those checks.

## Next review checkpoint

The reset preparation CLI remains offline. The separate protected installer now
reuses `scripts/dataset_mutation_authorization.py`: exact reviewed head, identical
head/merge plan bytes, captured executor, and acceptance rechecks. Its dedicated
workflow job requires a reviewed snapshot in an otherwise empty registry before
authentication, then rechecks live controls before every write. Generic mutation
paths still refuse these roots. No local install switch or reviewer-supplied
`writers_stopped: true` flag is accepted. See the
[installation contract](../feature-id-reset-installation.md) for its journal,
source-pinned plan, and intentionally bounded crash recovery.

The next cutover change needs these concrete inputs:

1. An authorized export of organization-level allow/deny policy for organization
   `471193686670`, together with applicable impersonation and managed-folder
   access, to close the effective-writer gaps in the readiness report.
2. A reviewed Terraform plan and constrained protected apply path for the exact
   writer restrictions. It must cover the old runtime identities, generic
   publisher, historical workflow/deployment paths, existing credentials, and
   propagation. A job pause or deployment hold is insufficient.
3. Frozen per-asset object inventory, an explicit first-release date, and
   generation/hash-pinned build inputs. Review the complete new-ID bundle,
   including source-keyed translation evidence and any gap supplement.
4. Review the implemented installer tests for authority revocation, changed
   inventory/fence, no-clobber installation, competing installers, and crashes
   after each durable write. Provision the dedicated reset identity through the
   reviewed infrastructure path and approve a time-limited fence registry entry.
   Evidence and adoption precede state; runtime publication requires the journal
   to be complete. An ambiguous missing state stops for reviewed recovery.
5. A protected canary plan that keeps schedules held until native artifact joins,
   ID counters, locale joins, and release/sidecar cache context pass. Exercise the
   actual catalog with historical and new releases containing the same numeric
   ID; each must display its own metadata in every locale. Keep Firestore lookup
   inactive. Both WDPA assets must pass before their shared job resumes.

The inherited-permission export is an external evidence requirement, not a
request to grant broad new auditor permissions. Until those inputs are available,
the installer remains held and every dataset remains NOT READY. The hold
must not be advertised as migration completion.
