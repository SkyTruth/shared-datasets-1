# Feature-ID reset installation

The pre-launch reset starts a new identity history for `wdpa-marine`,
`wdpa-terrestrial`, and `ims-sea-ice-extent`, preserving historical releases.
It uses the existing protected publisher and deployment identities. No
organization IAM export, policy registry, or new service account is required.

The invariant is: **initialize once; retries must never recreate or rewind
allocation history.** Installing state does not publish data or resume schedules.

## Identity contract

`wdpa-marine`, `wdpa-terrestrial`, and `ims-sea-ice-extent` begin the explicit
`generated-2026-v1` contract. The service has no existing users and the owner
approved retiring the old identity history. Historical releases stay readable;
the same numeric ID in an old and new contract has no implied relationship.
Consumers must join data, metadata, and translations within one resolved release
and retain its path/generation in caches. Keep optional Firestore serving inactive
during this transition.

The real WDPA and sea-ice publishers reserve IDs and claim publication ownership
before exposing new artifacts. Deletion, empty output, crashes, and retries cannot
lower the durable counter. Missing state after installation is an error, never
permission to initialize it again. Current generic publish/localization paths
cannot bypass these managed assets. EAMLIS uses provider IDs and needs no reset.

## Deployment sequence

1. Let `Scheduled ingestion deploy IAM sync` apply `cloudscheduler.jobs.pause`
   to the existing deployer role.
   Run `Ingestion schedule control` from `main`, selecting the affected job and
   `pause`. It operates only on the two ingestion schedules in project
   `shared-datasets-1`, region `us-central1`. Wait for
   its running/pending Cloud Run executions and earlier publisher/deployment
   workflow runs to finish. Keep the schedule paused through first publication.
   WDPA's two assets share `wdpa-monthly`; sea ice uses `sea-ice-daily`.
2. Capture the current object inventory, prepare and stage the reset inputs,
   and submit the immutable reset plan described below. WDPA requires one plan
   per asset. The corrected translation supplement is already staged; use the
   exact generation and SHA in the [translation evidence](wdpa-translation-reset-evidence.md).
3. After review and merge, `Approved dataset mutation` installs the reset. It
   verifies the immutable authority before authentication and rechecks review
   acceptance, the paused schedule, and every page of execution status before
   each write. A running/pending/reconciling execution or failed read stops it.
   If GitHub rejects the PR-event job because `refs/pull/.../merge` is outside
   the production environment's allowed branches, run the existing workflow
   from `main` with `pr_number` set to that merged PR. This restricted retry
   verifies the same immutable plan and review authority; no environment-policy
   change is needed. For example:

   ```bash
   gh workflow run publish-dataset.yml --ref main -f pr_number=<merged-reset-pr>
   ```
4. Run the existing ingestion deployment workflow from reviewed `main`, setting
   `canary_run_date` to the first release date in the reset plan. Sea ice searches
   upstream from this date; confirm its available source date matches the plan.
   Before image build or Terraform, it verifies the same publication state and
   receipts as the runtime. Both WDPA resets must be complete; sea ice is independent.
5. Validate the first release using the checks below before resuming the affected
   schedule with `Ingestion schedule control`.
   Its `resume` action requires a completed first-publication receipt. WDPA's
   canary is asynchronous:
   a successful dispatch is not successful publication. Preserve the paused
   state on failure and retry the owned publication rather than reset IDs.

The installer shares the `prod-terraform-state` workflow queue with deployments.
It reads scheduler/execution status as the existing `shared-datasets-terraform`
identity and writes objects as the existing `shared-datasets-publisher` identity.
Both authenticate through their existing protected-environment federation.
The existing protected IAM-sync workflow adds only `cloudscheduler.jobs.pause`
to the existing deployment role. No new identity or organization permission is
needed. Schedule control, reset installation, and deployment share the same queue.

This is an operational cutover under the project's existing administrator trust
boundary. Administrators must not restart old jobs or rerun old publishing
workflows during it. Current generic mutation/localization paths reject these
managed assets; the actual ingestion publishers use durable ownership and
reservations. The check does not claim to revoke administrator access or analyze
all inherited permissions. Pausing a schedule alone does not drain running jobs.

## Immutable authority

The `Approved dataset mutation` workflow routes an accepted `identity_reset`
plan to `install-approved-identity-reset`, rather than generic promotion or
localization. It runs in `shared-datasets-production`, checks out the captured
executor SHA, and verifies the captured authorization artifact. There is no local
installation mode, dispatch bypass, or `writers_stopped` override. Changing a PR
body cannot change an accepted plan.

## Prepare one reviewed plan per asset

With the affected job paused and drained, choose a new release date and capture all
current `latest/` object generations/hashes plus the matching old release
manifest. The new release directory and run record must be absent. Retain the
inventory and generated files in a named directory under the standard local
temp root.

Preparations may be built before pausing to shorten downtime. Recheck their
captured generations after pausing; if anything changed, prepare a new immutable
plan. The installer always checks the live inventory and paused/drained status.

The inventory's required `translation_supplement` field is `null` for sea ice.
For each WDPA asset it is an object with exactly `path`, `generation`, and
`sha256`, identifying the completed NDJSON supplement under the same bucket's
`_scratch/pending-publishes/` prefix. Both WDPA inventories must name the same
supplement. Stage the validated gap results first and include their provenance
in review; the inventory binds their exact bytes into the adoption receipt.
Installation fails if that generation or hash is unavailable. The runtime
downloads and verifies the approved supplement for the first build, preserves
established translations, and refuses an incomplete first release before
reserving IDs. Later releases reuse their committed translation CSV and do not
reload the reset supplement. No runtime path override is accepted.

The offline preparer exports the exact three canonical JSON files:

```bash
uv run python scripts/feature_id_reset.py \
  --inventory "$WORK_DIR/inventory.json" --output "$WORK_DIR/reset-review.json" \
  --objects-directory "$WORK_DIR/reset-objects"
```

This creates `0.json` (inventory evidence), `1.json` (adoption receipt), and
`2.json` (initial state). Stage them through `scripts/gcs_asset.py` under the
asset/proposal's `_scratch/pending-publishes/` prefix with no-clobber preconditions.
Record the exact returned source generations; scratch existence grants no
publication authority.

Prepare an ordinary publish payload with an `identity_reset` object containing
only `inventory` (the complete inventory JSON object from the review envelope).
Include exactly three promotions in the envelope's
evidence/adoption/state order. Each names the staged source URI/generation and
the exact destination from `objects[].path`, with `destination_generation: ""`,
`content_type: "application/json"`, and `cache_control: "no-cache"`. Do not include
a deletion, compatibility waiver, or release-index rebuild. The installer checks
each staged object's SHA-256 and size against the candidate's canonical bytes.

Run `reviewed_dataset_plan.py prepare --publish "$WORK_DIR/publish-plan.json"`
and include its immutable document and generated publish fence in an explicit
PR. Obtain approval on the exact head, then use the protected workflow after
merge. The plan also authorizes the installer-owned journal at the deterministic
`{asset-root}/publications/reset.json`; describe this path and its transitions in
the PR. It is operational transaction state, not a fourth source promotion.

## Write order and recovery

| Durable write | Condition |
| --- | --- |
| Create `reset.json` in `prepared` phase | No existing publication namespace; create only |
| Create inventory evidence | Exact staged generation/hash; create only |
| Create adoption receipt | Exact staged generation/hash; create only |
| Advance journal to `activating` | Match the journal generation |
| Create `state.json` | Create only; never replace an allocation state |
| Advance journal to `complete` | Match journal generation; record created state generation |

The journal binds the inventory, proposal, and executor contract.
Publishers require its `complete` phase before allocating or writing. Retries
accept only matching bytes and installer metadata; competing proposals or
unrelated publication objects stop the operation. After completion, a retry
validates the existing owned state and receipts without changing its counter.

A crash after `activating` but before a confirmed state leaves an intentionally
bounded stop: **if state is absent, reviewed recovery is required**. Absence
cannot distinguish a write that never happened from a state that was created
and later lost. Do not delete the journal or rerun a different proposal to start
over. A missing state after completion also refuses automatic reset. Keep the
schedule paused while reviewing recovery; never restore an old writer.

Tests cover immutable authorization and revocation, simultaneous installers,
changed inventory/job status, every durable-write crash boundary, completed
retries after subsequent publication, and runtime refusal before completion.
These use controlled stores/API fixtures; production cutover still requires the
following checks.

## First-publication acceptance

Before resuming each job, verify:

- The manual execution finished successfully. WDPA must complete both assets;
  check its full-build memory use as well as the exit status.
- The dated release and `latest/` manifests name the planned release and
  `generated-2026-v1`, and match the recorded object generations and hashes.
- FGB, PMTiles, canonical metadata, and schema agree on feature IDs and counts.
  Verify the PMTiles archive and decode a representative tile. WDPA must also
  pass every canonical/CSV/locale join for all six locales; old aliases must not
  retain IDs from the retired contract.
- `publications/state.json` has no active owner and points to the completed
  publication receipt with its advanced allocation counter. The run record and
  `_catalog/releases/{asset}.json` report the new release. Latest-object custom
  metadata describes the new bytes.
- Historical releases remain readable in their original release context, and
  metadata/cache lookups distinguish historical and new releases.

On failure, leave the schedule paused and use the captured transaction's recovery
path. Do not delete publication state, recycle its IDs, or restart an old writer.
